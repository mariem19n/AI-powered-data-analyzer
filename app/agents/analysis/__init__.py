"""
app/agents/analysis
Analysis Agent : génère des insights, visualisations et recommandations à
partir de DataFrames produits par le SQL Agent.

L'import de ce module charge tous les sous-packages dans l'ordre des
dépendances afin que tous les registries soient peuplés AVANT toute
utilisation de l'agent :
  1. tasks    — registre des AnalysisTask  (@register_task)
  2. viz      — registre des fonctions de viz  (@register_viz / @register_default_for_shape)
  3. llm      — registre des PromptTemplate (register_prompt)

Conséquence : un simple `from app.agents.analysis import AnalysisAgent`
suffit. L'utilisateur n'a pas à se préoccuper de l'ordre d'import des
modules internes — tout est résolu ici.

Usage typique (production) :
    from app.agents.analysis import AnalysisAgent

    agent = AnalysisAgent.build_default(neo4j_driver=my_driver)
    response = agent.run(
        instruction={"task": "descriptive", ...},
        upstream_results={"sql_1": {"records": [...], "columns": [...]}},
        semantic_context={...},
    )
    print(response.insights)
"""

# ─── Peuplement des registries (ORDRE IMPORTANT) ──────────────────────────
#
# Chaque sous-package charge ses fichiers via @register_*. Les imports ici
# garantissent que les registries sont remplis avant qu'un consommateur
# externe n'appelle get_task() / get_viz() / get_prompt().
#
# noqa: F401 — imports volontairement non-utilisés directement.

from app.agents.analysis import tasks  # noqa: F401
from app.agents.analysis import viz  # noqa: F401
from app.agents.analysis import llm  # noqa: F401



# ─── API publique ──────────────────────────────────────────────────────────

from app.agents.analysis.kg_writer import (
    KGWriteResult,
    KGWriter,
    compute_node_id,
    prepare_kg_payload,
)
from app.agents.analysis.llm.insight_generator import (
    GeneratedInsights,
    InsightGenerator,
)
from app.agents.analysis.runner import AnalysisAgent, AnalysisResponse
from app.agents.analysis.tasks.base import AnalysisTask, TaskResult

__all__ = [
    "AnalysisAgent",
    "AnalysisResponse",
    "AnalysisTask",
    "GeneratedInsights",
    "InsightGenerator",
    "KGWriteResult",
    "KGWriter",
    "TaskResult",
    "compute_node_id",
    "prepare_kg_payload",
]
