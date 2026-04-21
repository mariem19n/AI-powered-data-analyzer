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
        sid = session_id or str(uuid.uuid4())
        state = make_initial_state(sid, question, user_context)

        if self._graph is not None:
            state = await self._graph.ainvoke(state)
        else:
            state = await self._run_without_langgraph(state)

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
                state["cache_hit"] = True
                state["cached_response"] = cached
                logger.info("Cache HIT [%s:%s]", mode, q_hash)
                return state

        state["cache_hit"] = False
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
        resolution = self._classify_semantic_result(ctx_dict, state)
        state["response_mode"] = resolution["mode"]

        if resolution["mode"] == "clarify":
            state["needs_clarification"] = True
            state["clarification"] = ClarificationRequest(
                reason=resolution["reason"],
                suggestions=resolution.get("suggestions", []),
            )

        logger.info(
            "Semantic classification : %s — %s",
            resolution["mode"],
            resolution["reason"],
        )
        return state

    @staticmethod
    def _classify_semantic_result(
        ctx_dict: dict[str, Any],
        state: OrchestratorState,
    ) -> dict[str, Any]:
        """
        Classifie le résultat du Semantic Layer en 4 catégories :

          resolved_internal → données internes suffisantes → mode "internal"
          resolved_external_candidate → terme reconnu mais absent en interne,
              bon candidat pour Tavily → mode "external"
          ambiguous → terme vague, pas assez d'info pour chercher en interne
              NI en externe → demander clarification
          hybrid → mix de données internes + gaps candidats pour enrichissement
              externe → mode "hybrid"

        La distinction clé :
          - analytic_gaps avec des termes spécifiques et concrets
            (ex: "fear and greed", "RSI divergence", "funding rate")
            → external_candidate — Tavily peut trouver quelque chose d'utile
          - analytic_gaps avec des termes vagues ou relationnels
            (ex: "évolué de la même manière", "impact", "performance")
            → ce sont des intent hints, pas des candidats externe
            → le plan interne les gère via l'Analyse Agent
        """
        has_tables = bool(ctx_dict.get("tables"))
        has_entities = bool(ctx_dict.get("entity_filters"))
        has_metrics = bool(ctx_dict.get("metrics"))
        has_columns = bool(ctx_dict.get("columns"))
        analytic_gaps = ctx_dict.get("analytic_gaps", [])
        unknown_terms = ctx_dict.get("unknown_terms", [])
        confidence = ctx_dict.get("confidence", 0.0)

        has_internal = has_tables or has_entities or has_metrics or has_columns

        # Termes vagues qui sont des intent hints, pas des candidats web
        VAGUE_PATTERNS = {
            "évolué", "évolution", "impact", "lien", "relation",
            "manière", "pareil", "similaire", "differently",
            "performance", "comportement", "tendance", "trend",
            "corrélation", "correlation", "cause", "effet",
        }

        def is_external_candidate(gap: str) -> bool:
            """
            Un analytic_gap est candidat externe s'il est spécifique
            et concret — pas un verbe vague ou un concept relationnel.
            """
            gap_lower = gap.lower()
            # Si le gap contient un pattern vague → pas candidat externe
            for pattern in VAGUE_PATTERNS:
                if pattern in gap_lower:
                    return False
            # Si le gap a au moins 2 mots et ne commence pas par un verbe
            # commun → probablement un terme spécifique
            words = gap_lower.split()
            if len(words) >= 2:
                return True
            # Un seul mot mais pas vague → candidat
            return True

        external_candidates = [g for g in analytic_gaps if is_external_candidate(g)]
        vague_gaps = [g for g in analytic_gaps if not is_external_candidate(g)]

        # Cas A : tout résolu en interne, pas de gaps
        if has_internal and not analytic_gaps:
            return {"mode": "internal", "reason": "Tout résolu en interne"}

        # Cas B : données internes + gaps vagues (intent hints)
        # → mode internal, les gaps vagues seront gérés par l'Analyse Agent
        if has_internal and vague_gaps and not external_candidates:
            return {
                "mode": "internal",
                "reason": (
                    f"Données internes + gaps vagues ({', '.join(vague_gaps)}) "
                    f"— gérés par l'Analyse Agent"
                ),
            }

        # Cas C : données internes + candidats externes
        # → mode hybrid
        if has_internal and external_candidates:
            return {
                "mode": "hybrid",
                "reason": (
                    f"Données internes + termes externes "
                    f"({', '.join(external_candidates)})"
                ),
            }

        # Cas D : pas de données internes + candidats externes spécifiques
        # → mode external — Tavily peut répondre
        if not has_internal and external_candidates:
            return {
                "mode": "external",
                "reason": (
                    f"Terme(s) non disponible(s) en interne : "
                    f"{', '.join(external_candidates)}"
                ),
            }

        # Cas E : pas de données internes + seulement des gaps vagues
        # → ambiguous — ni l'interne ni l'externe ne peuvent aider
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

        # Cas F : pas de données internes, pas de gaps du tout,
        # mais la question a quand même un intent valide
        # → external fallback — on tente Tavily
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

        # Fallback — internal par défaut
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
                "External search — provider=%s, source=%s, summary_len=%d",
                result.provider,
                result.source,
                len(result.summary),
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
        intent = state.get("intent")
        ctx = state.get("semantic_context")
        if intent is None or ctx is None:
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

        plan = self._planner.generate(intent, ctx)
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
        """Stocke la réponse dans Redis et enregistre le plan dans le KG."""
        final = state.get("final_response")
        if final is None:
            return state

        # Ne pas cacher les réponses partielles ou les clarifications
        if final.get("needs_clarification") or final.get("partial"):
            return state

        # Cache Redis — clé préfixée par mode, TTL adapté
        if self._cache_store_fn is not None:
            mode = state.get("response_mode", "internal")
            cache_key = f"{mode}:{state['question_hash']}"

            # TTL plus court pour les réponses externes (données web
            # changent plus vite que les données internes historiques)
            if mode in ("external", "external_fallback"):
                ttl = min(self._cache_ttl, 1800)  # max 30 min pour external
            elif mode == "hybrid":
                ttl = min(self._cache_ttl, 3600)  # max 1h pour hybrid
            else:
                ttl = self._cache_ttl  # TTL normal pour internal

            try:
                await self._cache_store_fn(cache_key, final, ttl)
                logger.info(
                    "Cache STORE [%s] — TTL=%ds", cache_key, ttl
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
        return "semantic"

    @staticmethod
    def _route_after_semantic(state: OrchestratorState) -> str:
        if state.get("needs_clarification"):
            return "clarify"
        mode = state.get("response_mode", "internal")
        if mode == "external":
            return "external"
        # Both "internal" and "hybrid" continue to plan_lookup.
        # For hybrid, the external search happens in parallel later
        # during execution (added as a step in the plan).
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
            {"clarify": "aggregate", "semantic": "semantic"},
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
        g.add_edge("external", "aggregate")

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

        # Cache hit : retourner la réponse cachée
        if state.get("cache_hit") and state.get("cached_response"):
            cached = state["cached_response"]
            response = OrchestratorResponse.model_validate(cached)
            response.cache_hit = True
            response.total_duration_s = round(duration, 3)
            return response

        # Clarification
        if state.get("needs_clarification"):
            return self._aggregator.aggregate(
                session_id=state["session_id"],
                question=state["raw_question"],
                intent=state.get("intent"),
                plan=None,
                step_results={},
                total_duration_s=duration,
                llm_calls=1 if state.get("intent") else 0,
                clarification=state.get("clarification"),
            )

        # Mode external : réponse 100% source externe
        if response_mode == "external":
            ext = state.get("external_result")
            response = OrchestratorResponse(
                session_id=state["session_id"],
                question=state["raw_question"],
                intent=state.get("intent"),
                response_mode=ResponseMode.EXTERNAL,
                external_data=ext,
                insights=[ext.get("summary", "")] if ext else [],
                source_disclaimer=(
                    ext.get("confidence_note", "")
                    if ext
                    else "Source externe non disponible."
                ),
                total_duration_s=round(duration, 3),
                llm_calls=2,  # intent + external LLM
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
            llm_calls=1,
            cache_hit=False,
        )

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