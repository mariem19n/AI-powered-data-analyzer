"""Deterministic first-pass classifier for feedback comments."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from app.agents.feedback.config import FeedbackConfig, feedback_config
from app.agents.feedback.models import ClassificationResult, FeedbackCategory


_CATEGORY_KEYWORDS: dict[FeedbackCategory, tuple[str, ...]] = {
    FeedbackCategory.POSITIVE_FEEDBACK: (
        "merci",
        "exactement",
        "parfait",
        "super",
        "utile",
        "good",
        "great",
        "perfect",
        "correct",
        "helpful",
    ),
    FeedbackCategory.OPINION_OR_EMOTION: (
        "nul",
        "n'importe quoi",
        "ridicule",
        "frustrant",
        "bad",
        "terrible",
        "useless",
        "stupid",
    ),
    FeedbackCategory.TECHNICAL_ERROR: (
        "sql",
        "query",
        "requete",
        "jointure",
        "join",
        "where",
        "filtre",
        "syntax",
        "execute",
        "execution",
    ),
    FeedbackCategory.SEMANTIC_ERROR: (
        "mauvais actif",
        "mauvaise metrique",
        "mauvaise periode",
        "wrong asset",
        "wrong metric",
        "wrong timeframe",
        "different question",
        "pas la question",
    ),
    FeedbackCategory.DATA_ERROR: (
        "donnee",
        "data",
        "valeur",
        "number",
        "chiffre",
        "date",
        "periode",
        "timeframe",
        "faux",
        "wrong",
        "incorrect",
    ),
    FeedbackCategory.VISUALIZATION_ERROR: (
        "graphique",
        "chart",
        "visualisation",
        "visualization",
        "illisible",
        "broken chart",
        "bad visualization",
        "axis",
        "axe",
    ),
    FeedbackCategory.SOURCE_ERROR: (
        "source",
        "sources",
        "url",
        "lien",
        "provider",
        "wrong sources",
        "ne correspondent pas",
    ),
    FeedbackCategory.FORMATTING_OR_TONE: (
        "trop long",
        "too verbose",
        "verbose",
        "trop technique",
        "technical",
        "format",
        "ton",
        "wording",
        "lisible",
    ),
    FeedbackCategory.CORRECTION_SUGGESTION: (
        "je voulais dire",
        "i meant",
        "devrait",
        "should be",
        "corrige",
        "correction",
        "instead",
        "plutot",
        "plutôt",
    ),
    FeedbackCategory.VAGUE_NEGATIVE: (
        "faux",
        "wrong",
        "bad",
        "pas bon",
        "incorrect",
    ),
}

_VERIFIABLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(sql|query|requete|sources?|url|graphique|chart|data|donnee)\b"),
    re.compile(r"\b(btc|eth|sol|xrp|bnb|ada|doge|eur|usd)\b"),
    re.compile(r"\b(volume|prix|price|volatilite|volatility|moyenne|median|median)\b"),
    re.compile(r"\b\d+(?:[.,]\d+)?\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d+\s*(jours?|days?|mois|months?|semaines?|weeks?)\b"),
)

_CATEGORY_PRIORITY: tuple[FeedbackCategory, ...] = (
    FeedbackCategory.CORRECTION_SUGGESTION,
    FeedbackCategory.TECHNICAL_ERROR,
    FeedbackCategory.VISUALIZATION_ERROR,
    FeedbackCategory.SOURCE_ERROR,
    FeedbackCategory.SEMANTIC_ERROR,
    FeedbackCategory.DATA_ERROR,
    FeedbackCategory.FORMATTING_OR_TONE,
    FeedbackCategory.POSITIVE_FEEDBACK,
    FeedbackCategory.OPINION_OR_EMOTION,
    FeedbackCategory.VAGUE_NEGATIVE,
)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.lower()


def _keyword_hits(text: str, keywords: Iterable[str]) -> list[str]:
    return [keyword for keyword in keywords if _normalize(keyword) in text]


class FeedbackTextClassifier:
    """Classify feedback text using deterministic French/English keywords."""

    def __init__(self, config: FeedbackConfig | None = None) -> None:
        self._config = config or feedback_config

    def classify(self, text: str | None, *, rating: int | None = None) -> ClassificationResult:
        """Return a first-pass category without calling an LLM."""
        if not text or not text.strip():
            if rating is not None and rating >= 4:
                return ClassificationResult(
                    feedback_category=FeedbackCategory.POSITIVE_FEEDBACK,
                    category_confidence=0.75,
                    is_specific=False,
                    mentions_verifiable_element=False,
                )
            return ClassificationResult()

        normalized = _normalize(text)
        best_category = FeedbackCategory.UNCLASSIFIED
        best_hits: list[str] = []

        for category in _CATEGORY_PRIORITY:
            keywords = _CATEGORY_KEYWORDS[category]
            hits = _keyword_hits(normalized, keywords)
            if len(hits) > len(best_hits):
                best_category = category
                best_hits = hits

        confidence = min(0.95, 0.55 + 0.20 * len(best_hits)) if best_hits else 0.0
        mentions_verifiable = any(pattern.search(normalized) for pattern in _VERIFIABLE_PATTERNS)
        token_count = len(re.findall(r"\w+", normalized))
        is_specific = mentions_verifiable or token_count >= 6 or best_category in {
            FeedbackCategory.TECHNICAL_ERROR,
            FeedbackCategory.SEMANTIC_ERROR,
            FeedbackCategory.DATA_ERROR,
            FeedbackCategory.VISUALIZATION_ERROR,
            FeedbackCategory.SOURCE_ERROR,
            FeedbackCategory.CORRECTION_SUGGESTION,
        }

        if confidence < self._config.FEEDBACK_CLASSIFIER_MIN_CONFIDENCE:
            best_category = FeedbackCategory.UNCLASSIFIED
            confidence = 0.0

        return ClassificationResult(
            feedback_category=best_category,
            category_confidence=round(confidence, 3),
            extracted_claims=best_hits,
            is_specific=is_specific,
            mentions_verifiable_element=mentions_verifiable,
        )
