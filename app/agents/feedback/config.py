"""
app/agents/feedback/config.py

Configuration du Feedback Agent.

Tous les seuils et pondérations sont paramétrables via .env.
Le score final reste sur l'échelle 1–5 (intuitif avec le rating utilisateur).
Si une composante du score composite est absente, sa pondération est
redistribuée proportionnellement sur les composantes présentes.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class FeedbackConfig(BaseSettings):
    """Paramètres du Feedback Agent — chargés depuis .env."""

    # ── Pondérations du score composite (sur 1.0) ──────────────
    # humain : feedback explicite (rating, corrections)
    # implicite : signaux d'exécution (succès sandbox, temps réponse, etc.)
    # expert : validation par data analyst
    FEEDBACK_WEIGHT_HUMAN: float = 0.50
    FEEDBACK_WEIGHT_IMPLICIT: float = 0.30
    FEEDBACK_WEIGHT_EXPERT: float = 0.20

    # ── Seuils d'action (sur l'échelle 1–5) ────────────────────
    FEEDBACK_REINFORCE_THRESHOLD: float = 4.0
    FEEDBACK_DEPRECIATE_THRESHOLD: float = 2.0

    # ── Score implicite — poids internes ────────────────────────
    # Chaque signal implicite est pondéré pour produire un score 1–5.
    IMPLICIT_WEIGHT_EXECUTION_SUCCESS: float = 0.40
    IMPLICIT_WEIGHT_RESPONSE_TIME: float = 0.20
    IMPLICIT_WEIGHT_NULL_RATE: float = 0.20
    IMPLICIT_WEIGHT_LLM_CONFIDENCE: float = 0.20

    # ── Seuils pour les signaux implicites ──────────────────────
    # Temps de réponse en secondes : en dessous = 5/5, au dessus = 1/5
    IMPLICIT_RESPONSE_TIME_FAST: float = 2.0
    IMPLICIT_RESPONSE_TIME_SLOW: float = 15.0

    # Taux de nulls : en dessous = 5/5, au dessus = 1/5
    IMPLICIT_NULL_RATE_GOOD: float = 0.01
    IMPLICIT_NULL_RATE_BAD: float = 0.20

    # ── Validation contextuelle du feedback (0.0–1.0) ────────────────
    FEEDBACK_CLASSIFIER_MIN_CONFIDENCE: float = 0.70
    FEEDBACK_CREDIBILITY_BASE: float = 0.50
    FEEDBACK_ACCEPTED_THRESHOLD: float = 0.85
    FEEDBACK_PENDING_THRESHOLD: float = 0.45
    FEEDBACK_LOW_CONFIDENCE_THRESHOLD: float = 0.20
    POSITIVE_ACCEPTED_THRESHOLD: float = 0.60
    POSITIVE_PENDING_THRESHOLD: float = 0.35
    POSITIVE_LOW_CONFIDENCE_THRESHOLD: float = 0.15
    NEGATIVE_ACCEPTED_THRESHOLD: float = 0.85
    NEGATIVE_PENDING_THRESHOLD: float = 0.45
    NEGATIVE_LOW_CONFIDENCE_THRESHOLD: float = 0.20
    FEEDBACK_READING_SPEED_THRESHOLD_MS_PER_CHAR: float = 15.0
    FEEDBACK_REFORMULATION_SIMILARITY_THRESHOLD: float = 0.60

    # Multiplicateurs positifs
    FEEDBACK_MULTIPLIER_SPECIFIC: float = 1.25
    FEEDBACK_MULTIPLIER_VERIFIABLE: float = 1.25
    FEEDBACK_MULTIPLIER_RESPONSE_WARNINGS: float = 1.25
    FEEDBACK_MULTIPLIER_DATA_NO_INSIGHTS: float = 1.35
    FEEDBACK_MULTIPLIER_SOURCE_MISSING: float = 1.25
    FEEDBACK_MULTIPLIER_OPENED_CONTEXT: float = 1.15
    FEEDBACK_MULTIPLIER_RERAN_QUESTION: float = 1.15
    FEEDBACK_MULTIPLIER_REFORMULATION: float = 1.25
    FEEDBACK_MULTIPLIER_JUDGE_SUPPORTS: float = 1.35
    POSITIVE_MULTIPLIER_OPENED_CONTEXT: float = 1.20
    POSITIVE_MULTIPLIER_EXPANDED_VISUALIZATION: float = 1.15
    POSITIVE_MULTIPLIER_EXPORTED_REPORT: float = 1.25
    POSITIVE_MULTIPLIER_COPIED_RESPONSE: float = 1.15
    POSITIVE_MULTIPLIER_EXPLICIT_COMMENT: float = 1.10
    POSITIVE_MULTIPLIER_CRITICAL_WARNINGS: float = 0.90
    POSITIVE_MIN_FAST_DWELL_MULTIPLIER: float = 0.65

    # Multiplicateurs négatifs
    FEEDBACK_MULTIPLIER_VAGUE_NO_COMMENT: float = 0.75
    FEEDBACK_MULTIPLIER_NEGATIVE_NO_COMMENT: float = 0.90
    FEEDBACK_MULTIPLIER_COPY_EXPORT_DISLIKE: float = 0.85
    FEEDBACK_MULTIPLIER_CONTRADICTS_STATS: float = 0.80
    FEEDBACK_MULTIPLIER_JUDGE_CONTRADICTS: float = 0.70
    FEEDBACK_MIN_FAST_DWELL_MULTIPLIER: float = 0.60

    # LLM Judge optionnel
    LLM_JUDGE_MAX_PER_HOUR: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


feedback_config = FeedbackConfig()
