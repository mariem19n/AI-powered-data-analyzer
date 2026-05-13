"""
app/orchestrator/graph.py
Graphe LangGraph de l'Orchestrator.

Workflow adaptatif structuré avec conditional routing.
1 seul appel LLM dans tout le workflow (intent detection).

Nœuds du graphe :
  1. normalize_node     — réception + normalisation + hash SHA-256
  2. cache_check_node   — lookup Redis (hash question)
  3. intent_node        — détection d'intent via LLM
  4. semantic_node      — appel au Semantic Layer
  5. plan_lookup_node   — consultation KG (plans passés)
  6. plan_gen_node      — génération du plan = f(intent, semantic_context)
  7. route_node         — routage selon intent (retourné comme edge condition)
  8. execute_node       — exécution du plan via PlanExecutor
  9. aggregate_node     — assemblage de la réponse
 10. cache_store_node   — stockage Redis + enregistrement plan dans KG

Conditional edges :
  - cache_check → (hit) return OR (miss) intent
  - intent → (needs_clarification) aggregate OR (ok) semantic
  - semantic → (needs_clarification) aggregate OR (ok) plan_lookup

NOTE : LangGraph est une dépendance optionnelle. Si non installée,
on utilise le fallback synchrone (mêmes étapes en code Python pur).
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

from app.cache.policy import get_cache_ttl, should_cache_response
from app.llm import reset_llm_request_id, set_llm_request_id
from app.orchestrator.aggregator import ResponseAggregator
from app.orchestrator.executor import AgentRunner, PlanExecutor
from app.orchestrator.intent import IntentDetector
from app.orchestrator.planner import PlanGenerator, compute_plan_signature
from app.orchestrator.schemas import (
    AgentType,
    ClarificationRequest,
    IntentType,
    OrchestratorResponse,
    ResponseMode,
)
from app.orchestrator.external_tool import (
    ExternalResult,
    TavilyExternalTool,
)
from app.orchestrator.state import OrchestratorState, make_initial_state

logger = logging.getLogger(__name__)


# ─── Import conditionnel de LangGraph ────────────────────────

try:
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    logger.warning(
        "langgraph non installé — fallback sur l'orchestration Python pure. "
        "`pip install langgraph` pour activer le graphe."
    )


# ─── Types de fonctions externes (injectables) ───────────────

SemanticBuildFn = Any  # (question: str) -> SemanticContext
CacheCheckFn = Any  # async (hash: str) -> dict | None
CacheStoreFn = Any  # async (hash: str, response: dict, ttl: int) -> None
KGPlanLookupFn = Any  # async (signature: str) -> dict | None
KGPlanStoreFn = Any  # async (plan: ExecutionPlan, signature: str) -> None


# ─── Orchestrator ─────────────────────────────────────────────


class Orchestrator:
    """
    Orchestrator principal.

    Instancié avec :
      - IntentDetector (utilise LLM client)
      - PlanGenerator (Python pur)
      - PlanExecutor (avec agents injectés)
      - ResponseAggregator
      - semantic_build_fn : fonction pour lancer le Semantic Layer
      - cache_*_fn : fonctions Redis (injectables)
      - kg_plan_*_fn : fonctions Neo4j (injectables)

    Usage :
        orch = Orchestrator(
            agents={AgentType.SQL_AGENT: sql, AgentType.ANALYSIS_AGENT: ana},
            semantic_build_fn=build_semantic_context,
            cache_check_fn=redis.get_response_cache,
            cache_store_fn=redis.set_response_cache,
            kg_plan_lookup_fn=kg.find_plan_by_signature,
            kg_plan_store_fn=kg.store_plan,
        )
        response = await orch.handle("prix Bitcoin ce mois", session_id="abc")
    """

    def __init__(
        self,
        agents: dict[AgentType, AgentRunner],
        semantic_build_fn: SemanticBuildFn,
        external_tool: Any | None = None,
        cache_check_fn: CacheCheckFn | None = None,
        cache_store_fn: CacheStoreFn | None = None,
        kg_plan_lookup_fn: KGPlanLookupFn | None = None,
        kg_plan_store_fn: KGPlanStoreFn | None = None,
        confidence_threshold: float = 0.55,
        cache_ttl_s: int = 3600,
        plan_reuse_min_score: float = 4.0,
    ):
        self._intent_detector = IntentDetector(
            confidence_threshold=confidence_threshold
        )
        self._planner = PlanGenerator()
        self._executor = PlanExecutor(agents=agents)
        self._aggregator = ResponseAggregator()

        self._semantic_build_fn = semantic_build_fn
        self._external_tool = external_tool
        self._cache_check_fn = cache_check_fn
        self._cache_store_fn = cache_store_fn
        self._kg_plan_lookup_fn = kg_plan_lookup_fn
        self._kg_plan_store_fn = kg_plan_store_fn

        self._cache_ttl = cache_ttl_s
        self._plan_reuse_min_score = plan_reuse_min_score

        self._graph = self._build_graph() if HAS_LANGGRAPH else None

    # ═════════════════════════════════════════════════════════
    #  API PUBLIQUE
    # ═════════════════════════════════════════════════════════

    async def handle(
        self,
        question: str,
        session_id: str | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> OrchestratorResponse:
        """
        Point d'entrée — traite une question en langage naturel.

        Returns:
            OrchestratorResponse prêt à être streamé au frontend.
        """
        # Capturer le nombre d'appels LLM avant cette requête
        # pour calculer le delta réel à la fin.
        from app.llm import get_llm_client

        llm_metrics = get_llm_client().metrics
        llm_calls_before = llm_metrics.total_calls
        llm_trace_start = len(llm_metrics.call_traces)

        sid = session_id or str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        state = make_initial_state(sid, question, user_context)
        state["request_id"] = request_id
        state["llm_calls_before"] = llm_calls_before
        state["llm_trace_start"] = llm_trace_start

        token = set_llm_request_id(request_id)
        try:
            if self._graph is not None:
                state = await self._graph.ainvoke(state)
            else:
                state = await self._run_without_langgraph(state)
        finally:
            reset_llm_request_id(token)

        return self._build_response(state)

    # ═════════════════════════════════════════════════════════
    #  NŒUDS DU GRAPHE
    # ═════════════════════════════════════════════════════════

    async def _normalize_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        raw = state["raw_question"]
        normalized = " ".join(raw.strip().lower().split())
        q_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        state["normalized_question"] = normalized
        state["question_hash"] = q_hash
        logger.info("Q[%s] normalized : '%s'", q_hash, normalized[:80])
        return state

    async def _cache_check_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        if self._cache_check_fn is None:
            state["cache_hit"] = False
            return state

        q_hash = state["question_hash"]

        # Chercher dans les 3 caches par ordre de priorité :
        # internal d'abord, puis external, puis hybrid
        for mode in ("internal", "external", "hybrid", "external_fallback"):
            cache_key = f"{mode}:{q_hash}"
            try:
                cached = await self._cache_check_fn(cache_key)
            except Exception as e:
                logger.warning("Cache check [%s] failed : %s", mode, e)
                continue

            if cached is not None:
                if not should_cache_response(cached):
                    logger.info(
                        "Cached final response ignored because policy failed",
                        extra={"cache_key": cache_key, "mode": mode},
                    )
                    continue
                state["cache_hit"] = True
                state["cached_response"] = cached
                logger.info(
                    "Cache hit for final response",
                    extra={"cache_key": cache_key, "mode": mode},
                )
                return state

        state["cache_hit"] = False
        logger.info(
            "Cache miss for final response",
            extra={"question_hash": q_hash},
        )
        return state

    async def _intent_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        intent = self._intent_detector.detect(state["normalized_question"])
        state["intent"] = intent

        if intent.needs_clarification:
            state["needs_clarification"] = True
            state["clarification"] = ClarificationRequest(
                reason=intent.reasoning or "Question ambiguë",
                suggestions=intent.suggested_questions or [],
            )
        return state

    async def _semantic_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        """
        Appelle le Semantic Layer. semantic_build_fn est injectée
        pour ne pas coupler l'Orchestrator à l'implémentation.
        """
        try:
            # semantic_build_fn peut être sync ou async
            result = self._semantic_build_fn(state["normalized_question"])
            if hasattr(result, "__await__"):
                result = await result
        except Exception as e:
            logger.error("Semantic layer failure : %s", e)
            state.setdefault("errors", []).append(f"semantic: {e}")
            state["needs_clarification"] = True
            state["clarification"] = ClarificationRequest(
                reason=f"Erreur du Semantic Layer : {type(e).__name__}"
            )
            return state

        # result est un SemanticContext — le sérialiser
        ctx_dict = result.to_dict() if hasattr(result, "to_dict") else result
        state["semantic_context"] = ctx_dict
        state["semantic_hash"] = ctx_dict.get("context_hash", "")

        # ── Correction d'intent basée sur les périodes résolues ──
        # Si l'intent est "forecasting" mais que toutes les périodes
        # temporelles sont dans le passé → corriger en "aggregation".
        # Le LLM d'intent ne connaît pas le contexte réel des dates,
        # mais le TimeParser sait objectivement si c'est passé ou futur.
        intent = state.get("intent")
        if intent and intent.primary == IntentType.FORECASTING:
            time_filters = ctx_dict.get("time_filters", [])
            has_resolved_times = any(
                tf.get("filter_clause") for tf in time_filters
            )
            all_past = has_resolved_times and all(
                "CURRENT_DATE" not in tf.get("filter_clause", "").upper()
                and "NOW()" not in tf.get("filter_clause", "").upper()
                for tf in time_filters
                if tf.get("filter_clause")
            )
            if all_past:
                # Extraire les périodes pour un reasoning propre
                period_texts = [
                    tf.get("raw_text", "") for tf in time_filters
                    if tf.get("raw_text")
                ]
                period_str = ", ".join(period_texts) if period_texts else "la période demandée"

                logger.info(
                    "Intent correction : forecasting → aggregation "
                    "(toutes les périodes sont dans le passé)"
                )
                intent.primary = IntentType.AGGREGATION
                intent.reasoning = (
                    f"La question porte sur une période passée ({period_str}). "
                    f"Extraction de données historiques, pas de prévision."
                )
                state["intent"] = intent

        # Cas 1 : le Semantic Layer a explicitement demandé une clarification
        # (unknown_terms = termes vraiment incompréhensibles)
        if ctx_dict.get("needs_clarification"):
            state["needs_clarification"] = True
            state["clarification"] = ClarificationRequest(
                reason=ctx_dict.get("clarification_reason")
                or "Termes non résolus dans le Knowledge Graph",
                unknown_terms=ctx_dict.get("unknown_terms", []),
            )
            return state

        # Cas 2 : Classification fine du résultat sémantique
        # pour décider du mode de réponse.
        resolution = self._classify_semantic_result(ctx_dict)
        state["response_mode"] = resolution["mode"]

        if resolution["mode"] == "clarify":
            state["needs_clarification"] = True
            state["clarification"] = ClarificationRequest(
                reason=resolution["reason"],
                suggestions=resolution.get("suggestions", []),
            )

        # Cas 3 : Mode hybrid — lancer la recherche externe ICI
        # avant de continuer vers plan_lookup. Le résultat externe
        # sera combiné avec les résultats internes dans _build_response.
        if resolution["mode"] == "hybrid" and self._external_tool is not None:
            analytic_gaps = ctx_dict.get("analytic_gaps", [])
            external_candidates = resolution.get("external_candidates", [])
            search_query = " ".join(external_candidates) if external_candidates else state["raw_question"]

            try:
                ext_result = await self._external_tool.search(
                    query=search_query,
                    context=f"Intent: {state.get('intent')}, Gaps: {analytic_gaps}",
                )
                state["external_result"] = ext_result.to_dict()
                logger.info(
                    "Hybrid external search — %d sources, answer_len=%d",
                    len(ext_result.sources),
                    len(ext_result.answer),
                )
            except Exception as e:
                logger.warning("Hybrid external search failed : %s", e)
                state["external_result"] = None

        logger.info(
            "Semantic classification : %s — %s",
            resolution["mode"],
            resolution["reason"],
        )
        return state

    @staticmethod
    def _classify_semantic_result(
        ctx_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Classifie le résultat du Semantic Layer pour décider du mode de réponse.

        Catégories :
          internal → données internes suffisantes
          external → terme reconnu mais absent en interne, candidat Tavily
          hybrid → données internes + termes externes à enrichir
          clarify → trop vague pour l'interne ET l'externe

        Retourne un dict avec :
          mode : str — le mode de réponse
          reason : str — justification (pour logs et debug)
          external_candidates : list[str] — termes à chercher en externe (hybrid/external)
          suggestions : list[str] — reformulations (clarify uniquement)
        """
        has_tables = bool(ctx_dict.get("tables"))
        has_entities = bool(ctx_dict.get("entity_filters"))
        has_metrics = bool(ctx_dict.get("metrics"))
        has_columns = bool(ctx_dict.get("columns"))
        analytic_gaps = ctx_dict.get("analytic_gaps", [])
        unknown_terms = ctx_dict.get("unknown_terms", [])
        confidence = ctx_dict.get("confidence", 0.0)

        has_internal = has_tables or has_entities or has_metrics or has_columns

        # Si des unknown_terms existent, c'est que le Semantic Layer
        # a des termes vraiment incompréhensibles — ça renforce le besoin
        # de clarification si on n'a rien en interne non plus.

        # Termes vagues = intent hints (à gérer en interne), pas des candidats web.
        # On y ajoute les TERMES ANALYTIQUES liés aux intents : quand l'utilisateur
        # dit "anomalies de BTC", le mot "anomalies" peut atterrir ici comme gap
        # — il ne faut SURTOUT PAS chercher des articles externes sur "anomalies"
        # (Tavily renverrait des résultats génériques sans rapport avec les
        # données de l'utilisateur). Ces termes sont déjà capturés par l'intent
        # classifier en amont et seront traités par l'Analysis Agent en interne.
        VAGUE_PATTERNS = {
            # Vagues classiques
            "évolué", "évolution", "impact", "lien", "relation",
            "manière", "pareil", "similaire", "differently",
            "performance", "comportement", "tendance", "trend",
            "corrélation", "correlation", "cause", "effet",
            # Termes analytiques liés aux intents — TOUJOURS gérés en interne
            "anomalie", "anomalies", "anomaly",
            "détection", "detect", "detection",
            "prévision", "prévisions", "forecast", "prediction",
            "comparaison", "compare", "comparison",
            "diagnostic", "diagnose", "diagnosis",
            "agrégation", "aggregation",
        }

        def is_external_candidate(gap: str) -> bool:
            gap_lower = gap.lower()
            for pattern in VAGUE_PATTERNS:
                if pattern in gap_lower:
                    return False
            words = gap_lower.split()
            if len(words) >= 2:
                return True
            return True

        external_candidates = [g for g in analytic_gaps if is_external_candidate(g)]
        vague_gaps = [g for g in analytic_gaps if not is_external_candidate(g)]

        # Cas A : tout résolu en interne, pas de gaps ni d'unknowns
        if has_internal and not analytic_gaps and not unknown_terms:
            return {"mode": "internal", "reason": "Tout résolu en interne"}

        # Cas B : données internes + gaps vagues (intent hints)
        if has_internal and vague_gaps and not external_candidates:
            return {
                "mode": "internal",
                "reason": (
                    f"Données internes + gaps vagues ({', '.join(vague_gaps)}) "
                    f"— gérés par l'Analyse Agent"
                ),
            }

        # Cas C : données internes + candidats externes → hybrid
        if has_internal and external_candidates:
            return {
                "mode": "hybrid",
                "reason": (
                    f"Données internes + termes externes "
                    f"({', '.join(external_candidates)})"
                ),
                "external_candidates": external_candidates,
            }

        # Cas D : pas de données internes + candidats externes → external
        if not has_internal and external_candidates:
            return {
                "mode": "external",
                "reason": (
                    f"Terme(s) non disponible(s) en interne : "
                    f"{', '.join(external_candidates)}"
                ),
                "external_candidates": external_candidates,
            }

        # Cas E : pas de données internes + unknown_terms
        # → clarification obligatoire, termes incompréhensibles
        if not has_internal and unknown_terms:
            return {
                "mode": "clarify",
                "reason": (
                    f"Termes non reconnus : {', '.join(unknown_terms)}"
                ),
                "suggestions": [
                    "Vérifie l'orthographe de ta question",
                    "Précise une crypto (ex: Bitcoin, Ethereum)",
                    "Indique une métrique (ex: prix, volume, sentiment)",
                ],
            }

        # Cas F : pas de données internes + seulement des gaps vagues
        if not has_internal and vague_gaps and not external_candidates:
            return {
                "mode": "clarify",
                "reason": (
                    "La question contient des termes trop vagues pour "
                    "identifier les données pertinentes."
                ),
                "suggestions": [
                    "Précise une crypto (ex: Bitcoin, Ethereum)",
                    "Indique une métrique (ex: prix, volume, sentiment)",
                    "Ajoute une période (ex: ce mois, Q1 2024)",
                ],
            }

        # Cas G : pas de données internes, pas de gaps du tout
        if not has_internal and not analytic_gaps:
            if confidence > 0.3:
                return {
                    "mode": "external",
                    "reason": "Aucune donnée interne, tentative de recherche externe",
                }
            else:
                return {
                    "mode": "clarify",
                    "reason": "Question non interprétable par le système",
                    "suggestions": [
                        "Quel est le prix du Bitcoin ce mois ?",
                        "Quel est le sentiment médiatique autour des cryptos ?",
                    ],
                }

        # Fallback
        return {"mode": "internal", "reason": "Fallback — mode interne"}

    async def _external_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        """
        Recherche externe quand le SemanticContext est vide.

        Mode external : 100% source externe
        Mode hybrid : sera combiné avec les résultats internes plus tard

        Utilise l'ExternalKnowledgeTool injecté dans l'Orchestrator.
        """
        if self._external_tool is None:
            logger.warning(
                "Aucun ExternalKnowledgeTool configuré — "
                "impossible de répondre en mode external"
            )
            state["needs_clarification"] = True
            state["clarification"] = ClarificationRequest(
                reason="Cette information n'est pas disponible dans nos données internes "
                "et aucun outil de recherche externe n'est configuré.",
            )
            return state

        question = state["raw_question"]
        analytic_gaps = (state.get("semantic_context") or {}).get("analytic_gaps", [])
        context = f"Intent: {state.get('intent', {})}, Gaps: {analytic_gaps}"

        try:
            result = await self._external_tool.search(
                query=question,
                context=context,
            )
            state["external_result"] = result.to_dict()

            logger.info(
                "External search — provider=%s, source=%s, answer_len=%d",
                result.provider,
                result.source,
                len(result.answer),
            )
        except Exception as e:
            logger.error("External tool failure : %s", e)
            state.setdefault("errors", []).append(f"external_tool: {e}")
            state["external_result"] = None

        return state

    async def _plan_lookup_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        """Cherche un plan passé dans le KG avec score ≥ seuil."""
        intent = state.get("intent")
        ctx = state.get("semantic_context")
        if intent is None or ctx is None:
            return state

        signature = compute_plan_signature(intent, ctx)
        state["plan_signature"] = signature

        if self._kg_plan_lookup_fn is None:
            return state

        try:
            past_plan = await self._kg_plan_lookup_fn(signature)
        except Exception as e:
            logger.warning("KG plan lookup failed : %s", e)
            state.setdefault("warnings", []).append(f"kg_plan_lookup: {e}")
            return state

        if past_plan is not None:
            score = past_plan.get("feedback_score", 0)
            if score >= self._plan_reuse_min_score:
                state["kg_plan"] = past_plan
                logger.info(
                    "Plan réutilisé depuis le KG (signature=%s, score=%.1f)",
                    signature,
                    score,
                )
        return state

    async def _plan_gen_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        from app.orchestrator.schemas import IntentType
 
        intent = state.get("intent")
        ctx = state.get("semantic_context")
 
        if intent is None:
            return state
 
        # pour EXTERNAL_KNOWLEDGE, on n'a pas de SemanticContext
        # (on a sauté le semantic_node). On injecte un ctx minimal.
        if ctx is None:
            if intent.primary == IntentType.EXTERNAL_KNOWLEDGE:
                ctx = {"raw_question": state.get("raw_question", "")}
            else:
                # Comportement existant : pas de plan sans ctx pour les autres intents.
                return state
 
        # Si un plan KG réutilisable existe, on le charge ; sinon on génère.
        if state.get("kg_plan"):
            try:
                from app.orchestrator.schemas import ExecutionPlan
 
                plan = ExecutionPlan.model_validate(state["kg_plan"])
                plan.reused_from_kg = True
                state["plan"] = plan
                return state
            except Exception as e:
                logger.warning(
                    "KG plan invalide — régénération locale : %s", e
                )
 
        # NOUVEAU : on passe external_result au planner pour les modes
        # external (mono-step Analysis) et hybrid (SQL + Analysis avec payload).
        external_result = state.get("external_result")
        plan = self._planner.generate(intent, ctx, external_result=external_result)
        state["plan"] = plan
        return state

    async def _execute_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        plan = state.get("plan")
        if plan is None:
            return state

        results = await self._executor.execute(plan)
        state["step_results"] = results
        return state

    async def _aggregate_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        response = self._build_response(state)
        state["final_response"] = response.model_dump(mode="json")
        return state

    async def _cache_store_node(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        """Stocke la reponse dans Redis et enregistre le plan dans le KG."""
        final = state.get("final_response")
        if final is None:
            return state

        if self._cache_store_fn is not None:
            mode = state.get("response_mode", "internal")
            cache_key = f"{mode}:{state['question_hash']}"

            try:
                if should_cache_response(final):
                    ttl = get_cache_ttl(final)
                    await self._cache_store_fn(cache_key, final, ttl)
                    logger.info(
                        "Final response cached",
                        extra={"cache_key": cache_key, "ttl": ttl},
                    )
                else:
                    logger.info(
                        "Final response not cached because policy failed",
                        extra={"cache_key": cache_key},
                    )
            except Exception as e:
                logger.warning("Cache store failed : %s", e)
                state.setdefault("warnings", []).append(f"cache_store: {e}")

        # Enregistrement du plan dans le KG (score initial 0)
        plan = state.get("plan")
        if (
            self._kg_plan_store_fn is not None
            and plan is not None
            and not plan.reused_from_kg
        ):
            try:
                await self._kg_plan_store_fn(plan, state["plan_signature"])
            except Exception as e:
                logger.warning("KG plan store failed : %s", e)
                state.setdefault("warnings", []).append(f"kg_plan_store: {e}")

        return state

    # ═════════════════════════════════════════════════════════
    #  CONDITIONAL EDGES
    # ═════════════════════════════════════════════════════════

    @staticmethod
    def _route_after_cache(state: OrchestratorState) -> str:
        return "cached" if state.get("cache_hit") else "intent"

    @staticmethod
    def _route_after_intent(state: OrchestratorState) -> str:
        intent = state.get("intent")
        if intent is None or intent.needs_clarification:
            return "clarify"
 
        # intent EXTERNAL_KNOWLEDGE → on saute semantic, direct external.
        # Le semantic layer ne saurait pas résoudre une question de définition
        # qui ne parle pas de tables internes.
        from app.orchestrator.schemas import IntentType
        if intent.primary == IntentType.EXTERNAL_KNOWLEDGE:
            return "external"
 
        return "semantic"

    @staticmethod
    def _route_after_semantic(state: OrchestratorState) -> str:
        if state.get("needs_clarification"):
            return "clarify"
        mode = state.get("response_mode", "internal")
        if mode == "external":
            return "external"
        # internal et hybrid vont vers plan_lookup.
        # Pour hybrid, la recherche externe a DÉJÀ été faite
        # dans _semantic_node — le résultat est dans state["external_result"].
        # _build_response combine les deux dans la réponse finale.
        return "plan_lookup"

    # ═════════════════════════════════════════════════════════
    #  CONSTRUCTION DU GRAPHE
    # ═════════════════════════════════════════════════════════

    def _build_graph(self):
        """
        Construit le graphe LangGraph.

        Flux :
            normalize → cache_check → (hit?) → aggregate ou intent
            intent → (clarify?) → aggregate ou semantic
            semantic → (clarify?) → aggregate
                     → (external?) → external → aggregate → cache_store → END
                     → (internal/hybrid?) → plan_lookup → plan_gen → execute → aggregate → cache_store → END
        """
        if not HAS_LANGGRAPH:
            return None

        g = StateGraph(OrchestratorState)

        g.add_node("normalize", self._normalize_node)
        g.add_node("cache_check", self._cache_check_node)
        g.add_node("intent", self._intent_node)
        g.add_node("semantic", self._semantic_node)
        g.add_node("external", self._external_node)
        g.add_node("plan_lookup", self._plan_lookup_node)
        g.add_node("plan_gen", self._plan_gen_node)
        g.add_node("execute", self._execute_node)
        g.add_node("aggregate", self._aggregate_node)
        g.add_node("cache_store", self._cache_store_node)

        g.set_entry_point("normalize")
        g.add_edge("normalize", "cache_check")

        # cache_check : hit → END, miss → intent
        g.add_conditional_edges(
            "cache_check",
            self._route_after_cache,
            {"cached": "aggregate", "intent": "intent"},
        )

        # intent : needs_clarification → aggregate, sinon → semantic
        g.add_conditional_edges(
            "intent",
            self._route_after_intent,
            {
                "clarify": "aggregate",
                "semantic": "semantic",
                "external": "external",  # NOUVEAU
            },
        )

        # semantic : 3-way routing
        #   clarify → aggregate (needs_clarification)
        #   external → external node (SemanticContext vide)
        #   plan_lookup → plan normal (internal ou hybrid)
        g.add_conditional_edges(
            "semantic",
            self._route_after_semantic,
            {
                "clarify": "aggregate",
                "external": "external",
                "plan_lookup": "plan_lookup",
            },
        )

        # external → aggregate (pas de plan SQL, juste la réponse externe)
        g.add_edge("external", "plan_gen")

        g.add_edge("plan_lookup", "plan_gen")
        g.add_edge("plan_gen", "execute")
        g.add_edge("execute", "aggregate")
        g.add_edge("aggregate", "cache_store")
        g.add_edge("cache_store", END)

        return g.compile()

    # ═════════════════════════════════════════════════════════
    #  FALLBACK SANS LANGGRAPH
    # ═════════════════════════════════════════════════════════

    async def _run_without_langgraph(
        self, state: OrchestratorState
    ) -> OrchestratorState:
        """Exécution séquentielle si langgraph n'est pas installé."""
        state = await self._normalize_node(state)
        state = await self._cache_check_node(state)

        if state.get("cache_hit"):
            state = await self._aggregate_node(state)
            return state

        state = await self._intent_node(state)
        if state.get("needs_clarification"):
            state = await self._aggregate_node(state)
            return state

        state = await self._semantic_node(state)
        if state.get("needs_clarification"):
            state = await self._aggregate_node(state)
            return state

        mode = state.get("response_mode", "internal")

        if mode == "external":
            # Pas de données internes — recherche externe uniquement
            state = await self._external_node(state)
            state = await self._aggregate_node(state)
            state = await self._cache_store_node(state)
            return state

        # Mode internal ou hybrid — plan + exécution normaux
        state = await self._plan_lookup_node(state)
        state = await self._plan_gen_node(state)
        state = await self._execute_node(state)
        state = await self._aggregate_node(state)
        state = await self._cache_store_node(state)
        return state

    # ═════════════════════════════════════════════════════════
    #  BUILD RESPONSE
    # ═════════════════════════════════════════════════════════

    def _build_response(
        self, state: OrchestratorState
    ) -> OrchestratorResponse:
        started = state.get("started_at", time.perf_counter())
        duration = time.perf_counter() - started
        response_mode = state.get("response_mode", "internal")

        # Calculer le nombre réel d'appels LLM pour cette requête
        from app.llm import get_llm_client

        llm_metrics = get_llm_client().metrics
        llm_before = state.get("llm_calls_before", 0)
        llm_trace_start = state.get("llm_trace_start", 0)
        request_id = state.get("request_id")
        llm_calls_actual = llm_metrics.total_calls - llm_before
        llm_trace = llm_metrics.traces_since(llm_trace_start)
        if request_id:
            llm_trace = [
                trace for trace in llm_trace
                if trace.get("request_id") == request_id
            ]
            llm_calls_actual = len(llm_trace)
        logger.info(
            "Request LLM calls computed",
            extra={"request_id": request_id, "llm_calls": llm_calls_actual},
        )

        # Cache hit : retourner la réponse cachée
        if state.get("cache_hit") and state.get("cached_response"):
            cached = state["cached_response"]
            response = OrchestratorResponse.model_validate(cached)
            response.cache_hit = True
            response.total_duration_s = round(duration, 3)
            response.llm_calls = 0  # cache hit = 0 appels LLM
            response.llm_trace = []
            return response

        # Clarification
        if state.get("needs_clarification"):
            response = self._aggregator.aggregate(
                session_id=state["session_id"],
                question=state["raw_question"],
                intent=state.get("intent"),
                plan=None,
                step_results={},
                total_duration_s=duration,
                llm_calls=llm_calls_actual,
                clarification=state.get("clarification"),
            )
            response.llm_trace = llm_trace
            return response

        # Mode external : réponse 100% source externe
        if response_mode == "external":
            ext = state.get("external_result")
            insights = []
            if ext:
                answer = ext.get("answer", "")
                if answer:
                    insights.append(answer)
                source_urls = ext.get("source_urls", [])

            response = OrchestratorResponse(
                session_id=state["session_id"],
                question=state["raw_question"],
                intent=state.get("intent"),
                response_mode=ResponseMode.EXTERNAL,
                external_data=ext,
                insights=insights,
                recommendations=[
                    f"Source : {url}" for url in (source_urls if ext else [])
                ],
                source_disclaimer=(
                    ext.get("confidence_note", "")
                    if ext
                    else "Source externe non disponible."
                ),
                total_duration_s=round(duration, 3),
                llm_calls=llm_calls_actual,
                llm_trace=llm_trace,
            )
            return response

        # Mode internal ou hybrid
        response = self._aggregator.aggregate(
            session_id=state["session_id"],
            question=state["raw_question"],
            intent=state.get("intent"),
            plan=state.get("plan"),
            step_results=state.get("step_results", {}),
            total_duration_s=duration,
            llm_calls=llm_calls_actual,
            cache_hit=False,
        )
        response.llm_trace = llm_trace

        if response_mode == "hybrid":
            ext = state.get("external_result")
            response.response_mode = ResponseMode.HYBRID
            response.external_data = ext
            response.source_disclaimer = (
                "Cette réponse combine des données internes vérifiées "
                "et des informations de sources externes. "
                "Les sections marquées 'source externe' n'ont pas été "
                "vérifiées par le système."
            )
        else:
            response.response_mode = ResponseMode.INTERNAL

        return response


# ─── Singleton helper ─────────────────────────────────────────

_default_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """
    Retourne l'Orchestrator par défaut.
    Doit être initialisé avec setup_orchestrator() au démarrage de l'app.
    """
    if _default_orchestrator is None:
        raise RuntimeError(
            "Orchestrator non initialisé. Appelle setup_orchestrator(...) "
            "au démarrage de l'application (lifespan FastAPI)."
        )
    return _default_orchestrator


def setup_orchestrator(**kwargs) -> Orchestrator:
    """Initialise le singleton."""
    global _default_orchestrator
    _default_orchestrator = Orchestrator(**kwargs)
    return _default_orchestrator