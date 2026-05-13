"""
app/semantic/extractor.py
Semantic Layer : Business Terms Extraction — Pipeline simplifié.

Pipeline :
  1. Pré-traitement minimal (preprocessor.py) — normalisation + alias/synonymes sûrs + fuzzy léger
  2. Extraction LLM              — appel OpenAI GPT-4o mini avec prompt dynamique KG
  3. Validation légère     (validator.py)     — validation sémantique / recatégorisation / plausibilité

Entrée  : question str (langage naturel)
Sortie  : EnrichedTerms (termes classifiés + scores de confiance + audit trail)

L'ancienne sortie ExtractedTerms est toujours disponible via extract_raw()
pour la rétro-compatibilité.
"""

import json
import logging
import os
from typing import Any

from app.llm import LLMClient
from app.semantic.preprocessor import Preprocessor
from app.semantic.prompts import (
    KGVocabulary,
    build_extraction_prompt,
    load_kg_vocabulary,
    EXTRACTION_USER_TEMPLATE,
)
from app.semantic.schemas import (
    EnrichedTerms,
    ExtractedTerms,
    PreprocessResult,
)
from app.semantic.validator import ExtractionValidator

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"


class BusinessTermsExtractor:
    """
    Pipeline complet d'extraction des termes métier.

    Orchestre les 3 phases :
      1. Preprocessor — nettoyage léger et correction minimale
      2. LLM          — extraction des termes
      3. Validator    — validation sémantique post-extraction
    """

    def __init__(self, neo4j_driver=None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY manquant dans .env")

        self._model = os.getenv("SEMANTIC_LLM_MODEL", DEFAULT_MODEL)
        self._client = LLMClient(model=self._model, api_key=api_key)

        self._vocab = KGVocabulary()
        self._prompt = ""
        self._preprocessor: Preprocessor | None = None
        self._validator: ExtractionValidator | None = None

        if neo4j_driver is not None:
            self._reload_vocabulary(neo4j_driver)
        else:
            logger.warning(
                "BusinessTermsExtractor initialisé sans neo4j_driver — "
                "pipeline en mode dégradé (pas de pré-traitement ni validation)."
            )

        logger.info(
            "BusinessTermsExtractor prêt — modèle : %s — "
            "%d business_terms, %d entities, %d time_periods, %d metrics — "
            "preprocessor: %s, validator: %s",
            self._model,
            len(self._vocab.business_terms),
            len(self._vocab.entities),
            len(self._vocab.time_periods),
            len(self._vocab.metrics),
            "✅" if self._preprocessor else "❌",
            "✅" if self._validator else "❌",
        )

    def reload_vocabulary(self, neo4j_driver) -> None:
        """Recharge le vocabulaire depuis le KG (hot reload)."""
        self._reload_vocabulary(neo4j_driver)

    def _reload_vocabulary(self, neo4j_driver) -> None:
        self._vocab = load_kg_vocabulary(neo4j_driver)
        self._prompt = build_extraction_prompt(self._vocab)
        self._preprocessor = Preprocessor(self._vocab)
        self._validator = ExtractionValidator(self._vocab)

    # ─── Pipeline complet ─────────────────────────────────────

    def extract(self, question: str) -> EnrichedTerms:
        """
        Pipeline complet : pré-traitement → extraction LLM → validation.

        Args:
            question: Question en langage naturel.

        Returns:
            EnrichedTerms avec termes classifiés, scores de confiance
            et audit trail complet.
        """
        if not question or not question.strip():
            return EnrichedTerms(
                raw_question=question or "",
                corrected_question=question or "",
                needs_clarification=True,
            )

        question = question.strip()
        logger.info("Pipeline extraction — question : %s", question[:120])

        preprocess = self._run_preprocess(question)
        extraction_input = preprocess.corrected_question if preprocess else question

        if preprocess and preprocess.is_corrected:
            logger.info(
                "Pré-traitement — %d corrections : '%s' → '%s'",
                len(preprocess.corrections),
                question[:60],
                extraction_input[:60],
            )

        raw_result = self._extract_llm(extraction_input)

        if self._validator and raw_result is not None:
            enriched = self._validator.validate(raw_result, preprocess)
            enriched.raw_question = question
            return enriched

        if raw_result is not None:
            return self._raw_to_enriched(raw_result, preprocess)

        return EnrichedTerms(
            raw_question=question,
            corrected_question=extraction_input,
            needs_clarification=True,
            preprocessing=preprocess,
            pipeline_confidence=0.0,
        )

    # ─── Extraction brute (rétro-compatibilité) ──────────────

    def extract_raw(self, question: str) -> ExtractedTerms:
        """
        Extraction LLM uniquement — sans pré-traitement ni validation.
        """
        if not question or not question.strip():
            return ExtractedTerms(raw_question=question or "", needs_clarification=True)

        question = question.strip()
        result = self._extract_llm(question)
        if result is not None:
            return result

        return ExtractedTerms(
            raw_question=question,
            unresolved_terms=[question],
            needs_clarification=True,
        )

    # ─── Phase 1 : Pré-traitement ────────────────────────────

    def _run_preprocess(self, question: str) -> PreprocessResult | None:
        if self._preprocessor is None:
            return None
        try:
            return self._preprocessor.preprocess(question)
        except Exception as e:
            logger.warning("Erreur pré-traitement : %s — on continue sans", e)
            return None

    # ─── Phase 2 : Extraction LLM ────────────────────────────

    def _extract_llm(self, question: str) -> ExtractedTerms | None:
        logger.info("Extraction LLM — question : %s", question[:120])

        result = self._call_llm(question)
        if result is not None:
            return result

        logger.warning("Retry extraction pour : %s", question[:80])
        result = self._call_llm(question, is_retry=True)
        if result is not None:
            return result

        logger.error("Échec extraction LLM après retry.")
        return None

    def _call_llm(
        self, question: str, is_retry: bool = False
    ) -> ExtractedTerms | None:
        """Appelle le LLM et parse la réponse JSON."""
        try:
            system = self._prompt
            if is_retry:
                system += (
                    "\n\nATTENTION : ta réponse précédente était invalide. "
                    "Réponds UNIQUEMENT avec du JSON valide, sans markdown, sans commentaire."
                )

            if not system:
                logger.error("Prompt vide — vocabulaire KG non chargé.")
                return None

            raw = self._client.chat(
                system=system,
                user=EXTRACTION_USER_TEMPLATE.format(question=question),
                purpose=(
                    "semantic_term_extraction_retry"
                    if is_retry
                    else "semantic_term_extraction"
                ),
                temperature=0.0,
                max_tokens=512,
            )
            raw = raw.strip()
            logger.debug("Réponse LLM : %s", raw[:500])

            parsed = self._parse_json(raw)
            if parsed is None:
                return None

            return self._build_result(question, parsed)

        except Exception as e:
            logger.error("Erreur LLM (%s): %s", type(e).__name__, e)
            return None

    def _parse_json(self, raw: str) -> dict[str, Any] | None:
        """Parse le JSON — nettoie les backticks si présents."""
        text = raw.strip()

        if text.startswith("```"):
            lines = [l for l in text.split("\n") if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError as e:
            logger.warning("JSON invalide : %s — texte : %s", e, text[:300])
            return None

    def _build_result(self, question: str, data: dict) -> ExtractedTerms:
        """Construit ExtractedTerms depuis le dict parsé."""
        def get_list(key: str) -> list[str]:
            val = data.get(key, [])
            if not isinstance(val, list):
                return []
            return [str(i).strip() for i in val if i and str(i).strip()]

        unresolved = get_list("unresolved_terms")
        needs_clarif = bool(data.get("needs_clarification", False)) or bool(unresolved)

        result = ExtractedTerms(
            raw_question=question,
            business_terms=get_list("business_terms"),
            entities=get_list("entities"),
            time_periods=get_list("time_periods"),
            metrics=get_list("metrics"),
            unresolved_terms=unresolved,
            needs_clarification=needs_clarif,
        )

        logger.info(
            "LLM → %d business_terms, %d entities, %d periods, %d metrics, %d unresolved",
            len(result.business_terms),
            len(result.entities),
            len(result.time_periods),
            len(result.metrics),
            len(result.unresolved_terms),
        )
        return result

    # ─── Conversion dégradée ──────────────────────────────────

    @staticmethod
    def _raw_to_enriched(
        raw: ExtractedTerms,
        preprocess: PreprocessResult | None,
    ) -> EnrichedTerms:
        """
        Convertit un ExtractedTerms en EnrichedTerms minimal
        quand le validateur n'est pas disponible.
        """
        from app.semantic.schemas import ClassifiedTerm, TermCategory

        terms = []
        for bt in raw.business_terms:
            terms.append(
                ClassifiedTerm(
                    text=bt,
                    category=TermCategory.BUSINESS_TERM,
                    confidence=0.5,
                )
            )
        for ent in raw.entities:
            terms.append(
                ClassifiedTerm(
                    text=ent,
                    category=TermCategory.ENTITY,
                    confidence=0.5,
                )
            )
        for tp in raw.time_periods:
            terms.append(
                ClassifiedTerm(
                    text=tp,
                    category=TermCategory.TIME_PERIOD,
                    confidence=0.5,
                )
            )
        for m in raw.metrics:
            terms.append(
                ClassifiedTerm(
                    text=m,
                    category=TermCategory.METRIC,
                    confidence=0.5,
                )
            )

        return EnrichedTerms(
            raw_question=raw.raw_question,
            corrected_question=(
                preprocess.corrected_question if preprocess else raw.raw_question
            ),
            terms=terms,
            unresolved_terms=raw.unresolved_terms,
            needs_clarification=raw.needs_clarification,
            preprocessing=preprocess,
            pipeline_confidence=0.5,
        )
