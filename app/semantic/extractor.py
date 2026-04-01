"""
app/semantic/extractor.py
Semantic Layer : Business Terms Extraction.

BusinessTermsExtractor prend une question en langage naturel
et retourne un ExtractedTerms avec les termes métier structurés.

Ce composant est le SEUL appel LLM du Semantic Layer.
La résolution dans le KG et la construction du
SemanticContext n'appellent pas le LLM.

Usage :
    extractor = BusinessTermsExtractor()
    result = extractor.extract("prix Bitcoin ce mois")
    # result.business_terms = ["prix Bitcoin"]
    # result.entities       = ["Bitcoin"]
    # result.time_periods   = ["ce mois"]
"""

import json
import logging
import os
from typing import Any


#import google.generativeai as genai
from openai import OpenAI

from app.semantic.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_TEMPLATE,
)
from app.semantic.schemas import ExtractedTerms

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1"
#DEFAULT_TIMEOUT = 15  # secondes


class BusinessTermsExtractor:
    """
    Extrait les termes métier d'une question en langage naturel via LLM.

    première étape du Semantic Layer.
    Entrée  : question str
    Sortie  : ExtractedTerms (Pydantic)

    Stratégie:
      - 1er appel LLM → parse JSON → valide avec Pydantic
      - Si JSON invalide → 1 retry avec température réduite
      - Si retry échoue → retourne ExtractedTerms vide avec needs_clarification=True
    """

    def __init__(self):
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            raise ValueError(
                "NVIDIA_API_KEY manquant dans .env — "
                "nécessaire pour le Semantic Layer."
            )
        self._model = os.getenv("SEMANTIC_LLM_MODEL", DEFAULT_MODEL)
        print("NVIDIA key loaded:", bool(api_key), "length:", len(api_key) if api_key else 0)
        print("Model:", self._model)
        self._client = OpenAI(
            api_key=api_key,
            base_url="https://integrate.api.nvidia.com/v1",
        )
        logger.info("BusinessTermsExtractor initialisé — modèle : %s", self._model)

    def extract(self, question: str) -> ExtractedTerms:
        """
        Extrait les termes métier de la question.
        Synchrone — appeler depuis un thread ou wrapper async si nécessaire.

        Args:
            question: Question en langage naturel de l'utilisateur.

        Returns:
            ExtractedTerms avec les termes structurés.
            En cas d'échec total : ExtractedTerms vide avec needs_clarification=True.
        """
        if not question or not question.strip():
            logger.warning("Question vide reçue par l'extracteur.")
            return ExtractedTerms(
                raw_question=question,
                needs_clarification=True,
            )

        question = question.strip()
        logger.info("Extraction termes métier — question : %s", question[:100])

        # Tentative 1 — température standard
        result = self._call_llm(question, temperature=0.0)
        if result is not None:
            return result

        # Tentative 2 — retry avec température réduite et prompt simplifié
        logger.warning("Retry extraction LLM pour : %s", question[:100])
        result = self._call_llm(question, temperature=0.0, is_retry=True)
        if result is not None:
            return result

        # Fallback total
        logger.error("Échec extraction LLM après retry — retour vide.")
        return ExtractedTerms(
            raw_question=question,
            unresolved_terms=[question],
            needs_clarification=True,
        )

    def _call_llm(
        self,
        question: str,
        temperature: float = 0.0,
        is_retry: bool = False,
    ) -> ExtractedTerms | None:
        """
        Appelle le LLM et parse la réponse JSON.
        Retourne ExtractedTerms si succès, None si échec.
        """
        try:
            system_prompt = EXTRACTION_SYSTEM_PROMPT
            if is_retry:
                system_prompt += (
                    "\n\nATTENTION : Ta réponse précédente était invalide. "
                    "Réponds UNIQUEMENT avec du JSON valide, rien d'autre."
                )

            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": EXTRACTION_USER_TEMPLATE.format(question=question),
                    },
                ],
                temperature=temperature,
                max_tokens=512,
            )

            raw_text = response.choices[0].message.content.strip()
            logger.debug("Réponse LLM brute : %s", raw_text[:200])

            parsed = self._parse_json(raw_text)
            if parsed is None:
                return None

            return self._build_result(question, parsed)

        except Exception as e:
            logger.error("Erreur NVIDIA API : %s", e)
            return None

    def _parse_json(self, raw_text: str) -> dict[str, Any] | None:
        """
        Parse le JSON retourné par le LLM.
        Gère les cas où le LLM ajoute des backticks malgré les instructions.
        """
        text = raw_text.strip()

        # Nettoyer les backticks JSON si présents
        if text.startswith("```"):
            lines = text.split("\n")
            # Supprimer première ligne (```json ou ```) et dernière (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                logger.warning("JSON parsé n'est pas un dict : %s", type(data))
                return None
            return data
        except json.JSONDecodeError as e:
            logger.warning("JSON invalide — erreur : %s — texte : %s", e, text[:200])
            return None

    def _build_result(self, question: str, data: dict[str, Any]) -> ExtractedTerms:
        """
        Construit l'ExtractedTerms depuis le dict JSON parsé.
        Normalise et déduplique les termes.
        """
        def dedupe(items: list[str]) -> list[str]:
            seen = set()
            result = []
            for item in items:
                if item not in seen:
                    seen.add(item)
                    result.append(item)
            return result
        
        def get_list(key: str) -> list[str]:
            val = data.get(key, [])
            if not isinstance(val, list):
                return []
            cleaned = [str(item).strip() for item in val if item and str(item).strip()]
            return dedupe(cleaned)

        business_terms   = get_list("business_terms")
        entities         = get_list("entities")
        time_periods     = get_list("time_periods")
        metrics          = get_list("metrics")
        unresolved_terms = get_list("unresolved_terms")

        # needs_clarification : depuis le LLM ou calculé si unresolved non vide
        llm_needs_clarification = bool(data.get("needs_clarification", False))
        needs_clarification = llm_needs_clarification or len(unresolved_terms) > 0

        result = ExtractedTerms(
            raw_question=question,
            business_terms=business_terms,
            entities=entities,
            time_periods=time_periods,
            metrics=metrics,
            unresolved_terms=unresolved_terms,
            needs_clarification=needs_clarification,
        )

        logger.info(
            "Extraction réussie — %d business_terms, %d entities, "
            "%d time_periods, %d metrics, %d unresolved",
            len(business_terms), len(entities),
            len(time_periods), len(metrics), len(unresolved_terms),
        )

        return result
