"""
app/orchestrator/state.py
State partagé du graphe LangGraph.

Chaque nœud du graphe lit et écrit dans ce state.
TypedDict est le format recommandé par LangGraph.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from app.orchestrator.schemas import (
    ClarificationRequest,
    ExecutionPlan,
    Intent,
    StepResult,
)


class OrchestratorState(TypedDict, total=False):
    """
    State complet du graphe Orchestrator.

    Tous les champs sont optionnels (total=False) car ils sont
    remplis progressivement par les nœuds du graphe.
    """

    # ── Entrée ────────────────────────────────────────────────
    session_id: str
    raw_question: str
    normalized_question: str
    question_hash: str  # SHA-256 de la question normalisée
    user_context: dict[str, Any]  # role, preferences, etc.

    # ── Cache Redis (niveau question) ────────────────────────
    cache_hit: bool
    cached_response: dict[str, Any] | None

    # ── Intent (LLM) ─────────────────────────────────────────
    intent: Intent | None

    # ── Semantic Layer ───────────────────────────────────────
    semantic_context: dict[str, Any] | None  # SemanticContext.to_dict()
    semantic_hash: str  # hash du SemanticContext pour le cache SQL

    # ── KG plan reuse ────────────────────────────────────────
    plan_signature: str
    kg_plan: dict[str, Any] | None  # plan trouvé dans le KG

    # ── Plan d'exécution ─────────────────────────────────────
    plan: ExecutionPlan | None

    # ── Exécution ────────────────────────────────────────────
    step_results: dict[str, StepResult]  # step_id -> résultat

    # ── Clarification ────────────────────────────────────────
    needs_clarification: bool
    clarification: ClarificationRequest | None

    # ── Source externe ────────────────────────────────────────
    response_mode: str  # "internal", "hybrid", "external"
    external_result: dict[str, Any] | None  # ExternalResult.to_dict()

    # ── Réponse finale ───────────────────────────────────────
    final_response: dict[str, Any] | None

    # ── Métriques ────────────────────────────────────────────
    started_at: float
    errors: list[str]
    warnings: list[str]


def make_initial_state(
    session_id: str,
    raw_question: str,
    user_context: dict[str, Any] | None = None,
) -> OrchestratorState:
    """Crée un state initial prêt pour l'entrée du graphe."""
    import time

    return OrchestratorState(
        session_id=session_id,
        raw_question=raw_question,
        normalized_question="",
        question_hash="",
        user_context=user_context or {},
        cache_hit=False,
        cached_response=None,
        intent=None,
        semantic_context=None,
        semantic_hash="",
        plan_signature="",
        kg_plan=None,
        plan=None,
        step_results={},
        needs_clarification=False,
        clarification=None,
        response_mode="internal",
        external_result=None,
        final_response=None,
        started_at=time.perf_counter(),
        errors=[],
        warnings=[],
    )