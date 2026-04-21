"""
app/orchestrator/__init__.py
Orchestrator — workflow adaptatif structuré (LangGraph).

Reçoit la question en langage naturel, coordonne le pipeline complet :
  1. Réception + normalisation
  2. Cache Redis check
  3. Détection d'intent (LLM)
  4. Semantic Layer
  5. Consultation KG plans passés
  6. Génération du plan = f(intent, semantic_context)
  7. Routage conditionnel + exécution des agents
  8. Agrégation + mise en cache
"""

from app.orchestrator.graph import Orchestrator, get_orchestrator
from app.orchestrator.schemas import (
    Intent,
    IntentType,
    ExecutionPlan,
    ExecutionStep,
    AgentType,
    OrchestratorResponse,
)
from app.orchestrator.state import OrchestratorState

__all__ = [
    "Orchestrator",
    "get_orchestrator",
    "Intent",
    "IntentType",
    "ExecutionPlan",
    "ExecutionStep",
    "AgentType",
    "OrchestratorResponse",
    "OrchestratorState",
]