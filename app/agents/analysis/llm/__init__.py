"""
app/agents/analysis/llm
Génération d'insights par LLM pour l'Analysis Agent.

Importer ce module déclenche l'enregistrement des templates de prompts via
le décorateur register_prompt dans prompts.py.
"""

# L'import de prompts est obligatoire pour peupler le registre des prompts.
# noqa: F401 — import de side-effect.
from app.agents.analysis.llm import prompts  # noqa: F401
from app.agents.analysis.llm.insight_generator import (
    GeneratedInsights,
    InsightGenerator,
)
from app.agents.analysis.llm.schemas import (
    Insight,
    LLMOutput,
    Recommendation,
)

__all__ = [
    "GeneratedInsights",
    "Insight",
    "InsightGenerator",
    "LLMOutput",
    "Recommendation",
]
