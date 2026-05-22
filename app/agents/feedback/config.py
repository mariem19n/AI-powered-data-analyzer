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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


feedback_config = FeedbackConfig()
