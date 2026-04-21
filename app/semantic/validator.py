"""
app/semantic/validator.py
Validation sémantique post-extraction.

La validation ne consiste PAS en une vérification d'existence dans le KG.
Elle évalue la cohérence, la plausibilité et le niveau de résolution :

  - resolved            : match exact dans le KG
  - resolved_by_synonym : résolu via synonyme connu
  - resolved_by_fuzzy   : résolu via fuzzy matching
  - plausible_but_new   : absent du KG mais plausible dans le domaine
  - ambiguous           : plusieurs correspondances possibles
  - invalid             : incohérent avec la question / le domaine

Un terme plausible mais nouveau n'est PAS supprimé — il est gardé
comme candidat pour enrichissement futur du KG par le Feedback Agent.

Usage :
    validator = ExtractionValidator(vocab)
    enriched = validator.validate(extracted_terms, preprocess_result)
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from app.semantic.preprocessor import Preprocessor
from app.semantic.prompts import KGVocabulary
from app.semantic.schemas import (
    ClassifiedTerm,
    EnrichedTerms,
    ExtractedTerms,
    MatchMethod,
    PreprocessResult,
    ResolutionStatus,
    TermCategory,
    ValidationChange,
)

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────

CONFIDENCE_EXACT       = 1.0
CONFIDENCE_SYNONYM     = 0.95
CONFIDENCE_RECAT       = 0.85
CONFIDENCE_FUZZY       = 0.75
CONFIDENCE_PLAUSIBLE   = 0.60
CONFIDENCE_CORRECTED   = 0.80
CONFIDENCE_TIME_NONCAN = 0.70

FUZZY_THRESHOLD        = 0.84
PIPELINE_CONF_THRESH   = 0.55

# Vocabulaire du domaine crypto / macro — pour évaluer la plausibilité
# d'un terme absent du KG. Un terme qui contient un de ces mots
# est probablement pertinent même s'il n'est pas encore dans le KG.
DOMAIN_KEYWORDS = frozenset({
    # Crypto
    "bitcoin", "btc", "ethereum", "eth", "crypto", "token", "nft",
    "defi", "blockchain", "mining", "staking", "wallet", "exchange",
    "altcoin", "stablecoin", "liquidity", "yield", "airdrop",
    "halving", "bull", "bear", "whale", "hodl", "fomo",
    # Indicateurs / métriques crypto
    "fear", "greed", "dominance", "hashrate", "tvl", "mcap",
    "market cap", "volume", "prix", "price", "cours",
    "rsi", "macd", "bollinger", "fibonacci", "momentum",
    "support", "resistance", "trend", "breakout",
    # Macro
    "fed", "inflation", "cpi", "gdp", "pib", "taux", "rate",
    "vix", "sp500", "s&p", "treasury", "bond", "yield curve",
    "recession", "employment", "unemployment", "fomc",
    # Médias
    "reuters", "bloomberg", "coindesk", "cointelegraph",
    "financial times", "wsj", "cnbc",
    # Analyse
    "sentiment", "correlation", "volatilite", "volatilité",
    "rendement", "performance", "variation", "range",
    "moyenne", "mobile", "moving average",
})


class ExtractionValidator:
    """
    Validation sémantique des termes extraits.
    Évalue cohérence + plausibilité + résolvabilité,
    pas juste l'existence dans le KG.
    """

    def __init__(self, vocab: KGVocabulary):
        self._vocab = vocab

        # Lookup KG : norm → (canonical, category)
        self._kg_lookup: dict[str, tuple[str, TermCategory]] = {}
        self._build_kg_lookup()

        logger.info(
            "ExtractionValidator initialisé — %d termes KG, %d mots domaine",
            len(self._kg_lookup), len(DOMAIN_KEYWORDS),
        )

    def _build_kg_lookup(self) -> None:
        for bt in self._vocab.business_terms:
            self._kg_lookup[self._norm(bt)] = (bt, TermCategory.BUSINESS_TERM)
        for ent in self._vocab.entities:
            self._kg_lookup[self._norm(ent)] = (ent, TermCategory.ENTITY)
        for tp in self._vocab.time_periods:
            self._kg_lookup[self._norm(tp)] = (tp, TermCategory.TIME_PERIOD)
        for m in self._vocab.metrics:
            self._kg_lookup[self._norm(m)] = (m, TermCategory.METRIC)
        for syn, canonical in self._vocab.synonyms.items():
            norm_syn = self._norm(syn)
            if norm_syn not in self._kg_lookup:
                cat = self._find_category(canonical)
                self._kg_lookup[norm_syn] = (canonical, cat)

    def _find_category(self, term: str) -> TermCategory:
        norm = self._norm(term)
        for bt in self._vocab.business_terms:
            if self._norm(bt) == norm:
                return TermCategory.BUSINESS_TERM
        for ent in self._vocab.entities:
            if self._norm(ent) == norm:
                return TermCategory.ENTITY
        for tp in self._vocab.time_periods:
            if self._norm(tp) == norm:
                return TermCategory.TIME_PERIOD
        for m in self._vocab.metrics:
            if self._norm(m) == norm:
                return TermCategory.METRIC
        return TermCategory.UNKNOWN

    @staticmethod
    def _norm(text: str) -> str:
        return Preprocessor._normalize(text)

    # ─── API publique ─────────────────────────────────────────

    def validate(
        self,
        extracted: ExtractedTerms,
        preprocess: PreprocessResult | None = None,
    ) -> EnrichedTerms:
        """
        Validation sémantique des termes extraits.
        Ne supprime pas les termes plausibles — les marque avec le bon statut.
        """
        terms: list[ClassifiedTerm] = []
        changes: list[ValidationChange] = []

        corrected_norms: set[str] = set()
        if preprocess and preprocess.corrections:
            for c in preprocess.corrections:
                corrected_norms.add(self._norm(c.corrected))

        # Valider chaque catégorie
        for term_list, expected_cat in [
            (extracted.business_terms, TermCategory.BUSINESS_TERM),
            (extracted.entities,       TermCategory.ENTITY),
            (extracted.metrics,        TermCategory.METRIC),
        ]:
            t, ch = self._validate_terms(term_list, expected_cat, corrected_norms)
            terms.extend(t)
            changes.extend(ch)

        # Time periods — traitement spécial
        t, ch = self._validate_time_periods(extracted.time_periods, corrected_norms)
        terms.extend(t)
        changes.extend(ch)

        # Dédupliquer
        terms = self._deduplicate(terms, changes)

        # Unresolved du LLM — les évaluer aussi
        unresolved_out: list[str] = []
        for u_term in extracted.unresolved_terms:
            assessed = self._assess_unknown_term(u_term, TermCategory.UNKNOWN)
            if assessed.resolution_status == ResolutionStatus.INVALID:
                unresolved_out.append(u_term)
            else:
                # Terme plausible ou ambigu — on le garde dans terms
                terms.append(assessed)
                changes.append(ValidationChange(
                    term=u_term,
                    action="promoted",
                    resolution_status=assessed.resolution_status.value,
                    reason=f"Terme unresolved évalué comme {assessed.resolution_status.value}",
                ))

        # Confiance globale
        pipeline_confidence = self._compute_pipeline_confidence(terms, unresolved_out)

        needs_clarification = (
            len(unresolved_out) > 0
            or len(terms) == 0
            or pipeline_confidence < PIPELINE_CONF_THRESH
            or any(t.resolution_status == ResolutionStatus.AMBIGUOUS for t in terms)
        )

        corrected_question = (
            preprocess.corrected_question if preprocess else extracted.raw_question
        )

        result = EnrichedTerms(
            raw_question=extracted.raw_question,
            corrected_question=corrected_question,
            terms=terms,
            unresolved_terms=unresolved_out,
            needs_clarification=needs_clarification,
            preprocessing=preprocess,
            validation_changes=changes,
            pipeline_confidence=round(pipeline_confidence, 3),
        )

        # Log summary
        resolved = [t for t in terms if t.is_resolved()]
        candidates = [t for t in terms if t.is_candidate_for_kg()]
        invalid = [t for t in terms if t.resolution_status == ResolutionStatus.INVALID]
        logger.info(
            "Validation — %d termes : %d resolved, %d candidates, "
            "%d invalid, %d unresolved, confiance %.0f%%",
            len(terms), len(resolved), len(candidates),
            len(invalid), len(unresolved_out), pipeline_confidence * 100,
        )

        return result

    # ─── Validation d'une liste de termes ─────────────────────

    def _validate_terms(
        self,
        term_texts: list[str],
        expected_cat: TermCategory,
        corrected_norms: set[str],
    ) -> tuple[list[ClassifiedTerm], list[ValidationChange]]:
        validated: list[ClassifiedTerm] = []
        changes: list[ValidationChange] = []

        for text in term_texts:
            norm = self._norm(text)
            was_corrected = norm in corrected_norms

            kg_match = self._kg_lookup.get(norm)

            if kg_match is not None:
                canonical, actual_cat = kg_match

                conf = CONFIDENCE_CORRECTED if was_corrected else CONFIDENCE_EXACT

                if actual_cat == expected_cat:
                    validated.append(ClassifiedTerm(
                        text=canonical,
                        category=actual_cat,
                        confidence=conf,
                        resolution_status=ResolutionStatus.RESOLVED,
                        matched_by=MatchMethod.EXACT,
                        matched_kg_node=canonical,
                        original_text=text if text != canonical else None,
                        was_corrected=was_corrected,
                    ))
                    changes.append(ValidationChange(
                        term=canonical,
                        action="kept",
                        from_category=expected_cat.value,
                        to_category=actual_cat.value,
                        resolution_status="resolved",
                        reason="Match exact dans le KG",
                    ))
                else:
                    validated.append(ClassifiedTerm(
                        text=canonical,
                        category=actual_cat,
                        confidence=CONFIDENCE_RECAT,
                        resolution_status=ResolutionStatus.RESOLVED,
                        matched_by=MatchMethod.EXACT,
                        matched_kg_node=canonical,
                        original_text=text if text != canonical else None,
                        was_corrected=was_corrected,
                    ))
                    changes.append(ValidationChange(
                        term=canonical,
                        action="recategorized",
                        from_category=expected_cat.value,
                        to_category=actual_cat.value,
                        resolution_status="resolved",
                        reason=f"Terme KG trouvé comme {actual_cat.value}, recatégorisé",
                    ))
                continue

            assessed = self._assess_unknown_term(text, expected_cat)
            validated.append(assessed)
            changes.append(ValidationChange(
                term=text,
                action=f"marked_{assessed.resolution_status.value}",
                from_category=expected_cat.value,
                to_category=assessed.category.value,
                resolution_status=assessed.resolution_status.value,
                reason=self._explain_assessment(assessed),
            ))

        return validated, changes

    # ─── Time periods (traitement spécial) ────────────────────

    def _validate_time_periods(
        self,
        terms: list[str],
        corrected_norms: set[str],
    ) -> tuple[list[ClassifiedTerm], list[ValidationChange]]:
        validated: list[ClassifiedTerm] = []
        changes: list[ValidationChange] = []

        for text in terms:
            norm = self._norm(text)
            was_corrected = norm in corrected_norms
            kg_match = self._kg_lookup.get(norm)

            if kg_match is not None:
                canonical, _ = kg_match
                conf = CONFIDENCE_CORRECTED if was_corrected else CONFIDENCE_EXACT
                validated.append(ClassifiedTerm(
                    text=canonical,
                    category=TermCategory.TIME_PERIOD,
                    confidence=conf,
                    resolution_status=ResolutionStatus.RESOLVED,
                    matched_by=MatchMethod.EXACT,
                    matched_kg_node=canonical,
                    original_text=text if text != canonical else None,
                    was_corrected=was_corrected,
                ))
                changes.append(ValidationChange(
                    term=canonical, action="kept",
                    from_category="TimePeriod", to_category="TimePeriod",
                    resolution_status="resolved",
                    reason="Période canonique trouvée dans le KG",
                ))
            else:
                # Période non canonique — toujours acceptée (jamais invalid)
                validated.append(ClassifiedTerm(
                    text=text,
                    category=TermCategory.TIME_PERIOD,
                    confidence=CONFIDENCE_TIME_NONCAN,
                    resolution_status=ResolutionStatus.PLAUSIBLE_BUT_NEW,
                    matched_by=MatchMethod.NONE,
                    original_text=None,
                    was_corrected=False,
                ))
                changes.append(ValidationChange(
                    term=text, action="marked_plausible_but_new",
                    from_category="TimePeriod", to_category="TimePeriod",
                    resolution_status="plausible_but_new",
                    reason="Période non canonique — acceptée, sera résolue par le KG Resolver",
                ))

        return validated, changes

    # ─── Évaluation de plausibilité ───────────────────────────

    def _assess_unknown_term(
        self, text: str, expected_cat: TermCategory
    ) -> ClassifiedTerm:
        """
        Évalue un terme absent du KG :
        - plausible_but_new : terme cohérent avec le domaine crypto/macro
        - ambiguous         : pourrait correspondre à plusieurs choses
        - invalid           : incohérent / hallucination claire
        """
        norm = self._norm(text)
        words = set(norm.split())

        # Vérifier si des mots du terme sont dans le vocabulaire domaine
        domain_overlap = words & DOMAIN_KEYWORDS
        # Vérifier aussi les sous-chaînes (pour "fear and greed" → "fear", "greed")
        substring_match = any(kw in norm for kw in DOMAIN_KEYWORDS)

        if domain_overlap or substring_match:
            # Le terme contient des mots du domaine → plausible
            return ClassifiedTerm(
                text=text,
                category=expected_cat if expected_cat != TermCategory.UNKNOWN else TermCategory.BUSINESS_TERM,
                confidence=CONFIDENCE_PLAUSIBLE,
                resolution_status=ResolutionStatus.PLAUSIBLE_BUT_NEW,
                matched_by=MatchMethod.NONE,
                original_text=None,
                was_corrected=False,
            )

        # Vérifier si le terme est très court (1-2 mots très courts) → potentiellement ambigu
        if len(norm) < 4 or (len(words) == 1 and len(norm) < 6):
            return ClassifiedTerm(
                text=text,
                category=expected_cat if expected_cat != TermCategory.UNKNOWN else TermCategory.UNKNOWN,
                confidence=0.3,
                resolution_status=ResolutionStatus.AMBIGUOUS,
                matched_by=MatchMethod.NONE,
                original_text=None,
                was_corrected=False,
            )

        # Pas de lien avec le domaine → invalid (hallucination probable)
        return ClassifiedTerm(
            text=text,
            category=expected_cat if expected_cat != TermCategory.UNKNOWN else TermCategory.UNKNOWN,
            confidence=0.1,
            resolution_status=ResolutionStatus.INVALID,
            matched_by=MatchMethod.NONE,
            original_text=None,
            was_corrected=False,
        )

    def _explain_assessment(self, term: ClassifiedTerm) -> str:
        """Génère une explication pour l'assessment d'un terme."""
        if term.resolution_status == ResolutionStatus.PLAUSIBLE_BUT_NEW:
            return (
                f"Terme absent du KG mais plausible dans le domaine — "
                f"candidat pour enrichissement futur"
            )
        elif term.resolution_status == ResolutionStatus.AMBIGUOUS:
            return "Terme court ou ambigu — clarification recommandée"
        elif term.resolution_status == ResolutionStatus.INVALID:
            return "Terme incohérent avec le domaine crypto/macro — hallucination probable"
        return "Évaluation standard"

    # ─── Fuzzy lookup ─────────────────────────────────────────

    def _fuzzy_kg_lookup(
        self, norm_text: str
    ) -> tuple[str, TermCategory, float] | None:
        best_score = 0.0
        best_match = None
        for kg_norm, (canonical, category) in self._kg_lookup.items():
            score = SequenceMatcher(None, norm_text, kg_norm).ratio()
            if score > best_score and score >= FUZZY_THRESHOLD:
                best_score = score
                best_match = (canonical, category, score)
        return best_match

    # ─── Déduplication ────────────────────────────────────────

    def _deduplicate(
        self, terms: list[ClassifiedTerm], changes: list[ValidationChange]
    ) -> list[ClassifiedTerm]:
        """
        Déduplication plus simple :
        - un même texte normalisé ne doit idéalement apparaître qu'une fois
        - on garde le terme avec la meilleure confiance
        - en cas d'égalité, on préfère les catégories les plus structurantes
        """
        category_priority = {
            TermCategory.ENTITY: 4,
            TermCategory.METRIC: 3,
            TermCategory.TIME_PERIOD: 2,
            TermCategory.BUSINESS_TERM: 1,
            TermCategory.UNKNOWN: 0,
        }

        seen: dict[str, ClassifiedTerm] = {}

        for term in terms:
            key = self._norm(term.text)

            if key not in seen:
                seen[key] = term
                continue

            current = seen[key]

            if term.confidence > current.confidence:
                seen[key] = term
            elif term.confidence == current.confidence:
                if category_priority.get(term.category, 0) > category_priority.get(current.category, 0):
                    seen[key] = term

        deduped = list(seen.values())

        if len(deduped) < len(terms):
            removed = len(terms) - len(deduped)
            logger.info("Déduplication : %d doublons supprimés", removed)
            changes.append(ValidationChange(
                term="(doublons)",
                action="deduplicated",
                reason=f"{removed} doublons supprimés",
            ))

        return deduped

    # ─── Confiance globale ────────────────────────────────────

    def _compute_pipeline_confidence(
        self, terms: list[ClassifiedTerm], unresolved: list[str]
    ) -> float:
        if not terms and not unresolved:
            return 0.0
        if not terms:
            return 0.0

        # Confiance moyenne (en excluant les invalides)
        valid_terms = [t for t in terms if t.resolution_status != ResolutionStatus.INVALID]
        if not valid_terms:
            return 0.1

        avg_conf = sum(t.confidence for t in valid_terms) / len(valid_terms)

        # Ratio résolu vs total
        total = len(terms) + len(unresolved)
        resolved = len([t for t in terms if t.is_resolved()])
        resolution_ratio = resolved / total if total > 0 else 0

        # Bonus pour les termes plausibles (pas une pénalité !)
        plausible = len([t for t in terms if t.is_candidate_for_kg()])
        plausible_bonus = min(plausible * 0.05, 0.1)

        score = 0.6 * avg_conf + 0.3 * resolution_ratio + 0.1 + plausible_bonus
        return min(max(score, 0.0), 1.0)
