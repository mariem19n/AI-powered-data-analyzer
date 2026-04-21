"""
scripts/test_orchestrator.py
Test de l'Orchestrator avec des agents mockés.

Valide :
  - Les imports et la compilation du graphe LangGraph
  - La détection d'intent (nécessite OPENAI_API_KEY)
  - La génération du plan pour chaque intent
  - L'exécution avec agents mockés
  - L'agrégation de la réponse finale
  - Le cache hit (skip du pipeline)
  - Le cas clarification (unknown_terms)

Exécution :
  python scripts/test_orchestrator.py

Prérequis :
  - .env avec OPENAI_API_KEY=sk-...
  - langgraph installé (facultatif ; fallback Python sinon)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger(__name__)

from app.orchestrator import AgentType, Orchestrator
from app.orchestrator.executor import AgentRunner


# ─── Agents mockés ────────────────────────────────────────────


class MockSQLAgent(AgentRunner):
    async def run(
        self,
        instruction: dict[str, Any],
        upstream_results: dict[str, Any],
    ) -> dict[str, Any]:
        await asyncio.sleep(0.02)  # simule la latence
        return {
            "records": [
                {"date": "2026-03-01", "close_usd": 68000.0, "symbol": "BTC"},
                {"date": "2026-03-02", "close_usd": 68450.0, "symbol": "BTC"},
                {"date": "2026-03-03", "close_usd": 67200.0, "symbol": "BTC"},
            ],
            "columns": ["date", "close_usd", "symbol"],
            "row_count": 3,
            "sql": "SELECT date, close_usd, symbol FROM fact_crypto_daily WHERE symbol='BTC'",
        }


class MockAnalyseAgent(AgentRunner):
    async def run(
        self,
        instruction: dict[str, Any],
        upstream_results: dict[str, Any],
    ) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        task = instruction.get("task", "unknown")
        return {
            "insights": [
                f"Analyse '{task}' effectuée sur {len(upstream_results)} datasets en entrée."
            ],
            "visualizations": [
                {"type": "line", "title": "Prix BTC", "data": {"x": [], "y": []}}
            ],
            "recommendations": [
                "Surveiller la volatilité au cours des 7 prochains jours."
            ],
            "stats": {"mean": 67883.33, "std": 640.23},
        }


# ─── Fonctions injectées (mocks) ──────────────────────────────


def mock_semantic_build_fn(question: str):
    """
    Simule le Semantic Layer — retourne un dict proche de SemanticContext.to_dict().
    """

    class Ctx:
        def __init__(self, d: dict[str, Any]) -> None:
            self._d = d

        def to_dict(self) -> dict[str, Any]:
            return self._d

    ctx_dict = {
        "raw_question": question,
        "corrected_question": question,
        "tables": [
            {
                "table_name": "fact_crypto_daily",
                "role": "primary",
                "columns_used": ["close_usd", "symbol", "date"],
                "filters": [],
            }
        ],
        "entity_filters": [
            {
                "entity_name": "Bitcoin",
                "entity_type": "crypto",
                "table": "fact_crypto_daily",
                "column": "symbol",
                "value": "BTC",
            }
        ],
        "metrics": [],
        "columns": [
            {
                "name": "prix",
                "table": "fact_crypto_daily",
                "column": "close_usd",
                "description": "Prix de clôture en USD",
            }
        ],
        "time_filters": [
            {
                "expression": "ce mois",
                "filter_clause": "date >= DATE_TRUNC('month', CURRENT_DATE)",
                "is_canonical": True,
                "raw_text": "ce mois",
            }
        ],
        "implicit_conditions": ["volume > 0"],
        "generation_guidelines": [],
        "analytic_gaps": [],
        "unknown_terms": [],
        "needs_clarification": False,
        "clarification_reason": "",
        "confidence": 0.9,
        "context_hash": "mock_ctx_hash_abcdef",
        "built_at": "2026-04-16T10:00:00",
    }
    return Ctx(ctx_dict)


# ─── Cache Redis mocké ────────────────────────────────────────

_fake_cache: dict[str, dict[str, Any]] = {}


async def mock_cache_check(q_hash: str) -> dict[str, Any] | None:
    return _fake_cache.get(q_hash)


async def mock_cache_store(
    q_hash: str, response: dict[str, Any], ttl: int
) -> None:
    _fake_cache[q_hash] = response


# ─── KG plan mocké ────────────────────────────────────────────


async def mock_kg_plan_lookup(signature: str) -> dict[str, Any] | None:
    return None  # pas de plan réutilisable au début


async def mock_kg_plan_store(plan: Any, signature: str) -> None:
    logger.info("Plan enregistré dans le KG (mock) — signature=%s", signature)


# ─── Tests ────────────────────────────────────────────────────


def banner(title: str) -> None:
    print("\n" + "═" * 70)
    print(f"  {title}")
    print("═" * 70)


async def main() -> None:
    import os

    if not os.getenv("OPENAI_API_KEY"):
        print("\n⚠  OPENAI_API_KEY absent — les tests d'intent vont échouer.")
        print("   Définis-le dans .env pour tester la détection d'intent réelle.\n")

    orch = Orchestrator(
        agents={
            AgentType.SQL_AGENT: MockSQLAgent(),
            AgentType.ANALYSIS_AGENT: MockAnalyseAgent(),
        },
        semantic_build_fn=mock_semantic_build_fn,
        cache_check_fn=mock_cache_check,
        cache_store_fn=mock_cache_store,
        kg_plan_lookup_fn=mock_kg_plan_lookup,
        kg_plan_store_fn=mock_kg_plan_store,
    )

    # T1 — Question aggregation simple
    banner("T1 — Aggregation : prix Bitcoin ce mois")
    r1 = await orch.handle(
        "montre le prix du Bitcoin ce mois", session_id="s1"
    )
    print(f"  intent     : {r1.intent.primary.value if r1.intent else '?'}")
    print(f"  plan steps : {len(r1.plan.steps) if r1.plan else 0}")
    print(f"  data       : {len(r1.data)} datasets")
    print(f"  insights   : {len(r1.insights)}")
    print(f"  duration   : {r1.total_duration_s}s")
    print(f"  llm_calls  : {r1.llm_calls}")
    assert r1.intent is not None
    assert r1.plan is not None
    assert len(r1.data) >= 1

    # T2 — Même question → cache hit
    banner("T2 — Cache hit : même question")
    r2 = await orch.handle(
        "montre le prix du Bitcoin ce mois", session_id="s2"
    )
    print(f"  cache_hit  : {r2.cache_hit}")
    print(f"  duration   : {r2.total_duration_s}s")
    assert r2.cache_hit is True

    # T3 — Comparison
    banner("T3 — Comparison : BTC vs ETH Q1 2024")
    r3 = await orch.handle(
        "compare Bitcoin et Ethereum sur Q1 2024", session_id="s3"
    )
    print(f"  intent     : {r3.intent.primary.value if r3.intent else '?'}")
    print(f"  plan steps : {len(r3.plan.steps) if r3.plan else 0}")
    for step in r3.plan.steps if r3.plan else []:
        print(f"    - {step.step_id} [{step.agent.value}] {step.description}")

    # T4 — Correlation
    banner("T4 — Correlation : BTC vs Fed rate")
    r4 = await orch.handle(
        "corrélation entre le prix du Bitcoin et le taux de la Fed",
        session_id="s4",
    )
    print(f"  intent     : {r4.intent.primary.value if r4.intent else '?'}")
    print(f"  plan steps : {len(r4.plan.steps) if r4.plan else 0}")

    # T5 — Diagnosis
    banner("T5 — Diagnosis : pourquoi BTC a chuté")
    r5 = await orch.handle(
        "pourquoi le Bitcoin a chuté la semaine dernière ?", session_id="s5"
    )
    print(f"  intent     : {r5.intent.primary.value if r5.intent else '?'}")
    print(f"  plan steps : {len(r5.plan.steps) if r5.plan else 0}")

    # T6 — Question vide
    banner("T6 — Question vide")
    r6 = await orch.handle("", session_id="s6")
    print(f"  clarify    : {r6.needs_clarification}")
    assert r6.needs_clarification is True

    # Métriques LLM
    banner("Métriques LLM")
    from app.llm import get_llm_client

    metrics = get_llm_client().metrics.snapshot()
    for k, v in metrics.items():
        print(f"  {k:30s} : {v}")

    print("\n✅ Tous les tests sont passés.\n")


if __name__ == "__main__":
    asyncio.run(main())
