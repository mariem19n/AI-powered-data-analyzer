"""
app/agents/feedback/models.py

Modèles Pydantic du Feedback Agent.

FeedbackInput : ce que le frontend envoie.
ImplicitSignals : signaux automatiques capturés par le pipeline.
CompositeScore : résultat du calcul pondéré.
FeedbackResult : réponse finale retournée au frontend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────────────────


class FeedbackType(str, Enum):
    """Type de feedback soumis par l'utilisateur."""

    RATING = "rating"                      # note 1–5
    CORRECTION_SQL = "correction_sql"      # correction manuelle du SQL
    CORRECTION_INSIGHT = "correction_insight"  # reformulation d'un insight
    FALSE_POSITIVE = "false_positive"      # anomalie signalée comme fausse
    USELESS = "useless"                    # réponse jugée inutile


class TargetType(str, Enum):
    """Sur quoi porte le feedback."""

    ANSWER = "answer"
    INSIGHT = "insight"
    SQL = "sql"
    ANOMALY = "anomaly"
    FORECAST = "forecast"
    CORRELATION = "correlation"


class FeedbackStatus(str, Enum):
    """Statut assigné après le calcul du score composite."""

    REINFORCED = "reinforced"
    DEPRECATED = "deprecated"
    NEUTRAL = "neutral"


# ── Input : ce que le frontend envoie ──────────────────────────────────────


class FeedbackInput(BaseModel):
    """Payload d'un feedback utilisateur via POST /api/feedback."""

    # Identifiants de contexte
    response_id: str = Field(
        ..., description="ID unique de la réponse évaluée."
    )
    session_id: str = Field(
        ..., description="ID de la session conversationnelle."
    )

    # Question originale
    question: str = Field(
        ..., description="Question posée par l'utilisateur."
    )
    resolved_question: str | None = Field(
        default=None,
        description="Question après résolution sémantique (si disponible).",
    )

    # Contexte d'exécution
    intent: str | None = Field(
        default=None,
        description="Intent détecté par l'Orchestrateur.",
    )

    # Feedback explicite
    rating: int = Field(
        ..., ge=1, le=5,
        description="Note de 1 à 5 donnée par l'utilisateur.",
    )
    comment: str | None = Field(
        default=None,
        description="Commentaire libre optionnel.",
    )
    feedback_type: FeedbackType = Field(
        default=FeedbackType.RATING,
        description="Type de feedback.",
    )

    # Cible précise
    target_type: TargetType = Field(
        default=TargetType.ANSWER,
        description="Type d'objet évalué.",
    )
    target_id: str | None = Field(
        default=None,
        description="ID du nœud KG cible (Insight, SQLQuery, Anomaly…).",
    )

    # Corrections manuelles
    corrected_sql: str | None = Field(
        default=None,
        description="SQL corrigé manuellement par l'utilisateur.",
    )
    corrected_text: str | None = Field(
        default=None,
        description="Texte d'insight corrigé par l'utilisateur.",
    )

    @field_validator("rating")
    @classmethod
    def rating_in_range(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("Le rating doit être entre 1 et 5.")
        return v


# ── Signaux implicites (collectés automatiquement) ─────────────────────────


class ImplicitSignals(BaseModel):
    """Signaux d'exécution capturés par le pipeline.

    Ces signaux sont disponibles à 100% des requêtes, contrairement au
    feedback humain explicite (~5-10% de taux de retour en production).
    """

    execution_success: bool = Field(
        default=True,
        description="Le pipeline s'est exécuté sans erreur.",
    )
    response_time_seconds: float | None = Field(
        default=None,
        description="Temps de réponse total en secondes.",
    )
    null_rate: float | None = Field(
        default=None,
        description="Taux de valeurs nulles dans le DataFrame résultat.",
    )
    llm_confidence: float | None = Field(
        default=None,
        description="Confiance retournée par le LLM (0.0–1.0).",
    )
    row_count: int | None = Field(
        default=None,
        description="Nombre de lignes dans le résultat.",
    )
    warnings_count: int = Field(
        default=0,
        description="Nombre de warnings non-bloquants émis.",
    )


# ── Score composite ────────────────────────────────────────────────────────


class CompositeScore(BaseModel):
    """Résultat du calcul du score composite pondéré.

    Échelle : 1–5. Les pondérations sont redistribuées si une
    composante est absente (ex: pas d'expert → humain 62.5%, implicite 37.5%).
    """

    score_human: float | None = Field(
        default=None,
        description="Score humain (1–5). None si pas de feedback explicite.",
    )
    score_implicit: float | None = Field(
        default=None,
        description="Score implicite (1–5). None si pas de signaux.",
    )
    score_expert: float | None = Field(
        default=None,
        description="Score expert (1–5). None si pas de validation.",
    )

    weight_human: float = Field(description="Pondération effective humain.")
    weight_implicit: float = Field(description="Pondération effective implicite.")
    weight_expert: float = Field(description="Pondération effective expert.")

    composite: float = Field(
        description="Score final composite (1–5).",
    )
    status: FeedbackStatus = Field(
        description="Statut déduit du score : reinforced, deprecated, neutral.",
    )


# ── Résultat final ─────────────────────────────────────────────────────────


class FeedbackResult(BaseModel):
    """Réponse retournée par POST /api/feedback."""

    feedback_id: str = Field(
        description="ID unique du feedback enregistré.",
    )
    response_id: str = Field(
        description="ID de la réponse évaluée.",
    )
    composite_score: CompositeScore = Field(
        description="Détail du score composite calculé.",
    )
    kg_updates: list[str] = Field(
        default_factory=list,
        description="Liste des opérations effectuées dans le KG.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Avertissements non-bloquants.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
