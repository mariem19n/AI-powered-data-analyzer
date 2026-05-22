"""
app/agents/feedback
Feedback Agent — système apprenant basé sur le feedback utilisateur.

Pipeline : collector → score_calculator → kg_updater
Orchestré par : FeedbackService

Aucun appel LLM — tout est calcul + Neo4j.
"""

from app.agents.feedback.collector import FeedbackCollector
from app.agents.feedback.config import FeedbackConfig, feedback_config
from app.agents.feedback.kg_updater import KGUpdater
from app.agents.feedback.models import (
    CompositeScore,
    FeedbackInput,
    FeedbackResult,
    FeedbackStatus,
    FeedbackType,
    ImplicitSignals,
    TargetType,
)
from app.agents.feedback.score import ScoreCalculator
from app.agents.feedback.service import FeedbackService

__all__ = [
    "CompositeScore",
    "FeedbackCollector",
    "FeedbackConfig",
    "FeedbackInput",
    "FeedbackResult",
    "FeedbackService",
    "FeedbackStatus",
    "FeedbackType",
    "ImplicitSignals",
    "KGUpdater",
    "ScoreCalculator",
    "TargetType",
    "feedback_config",
]
