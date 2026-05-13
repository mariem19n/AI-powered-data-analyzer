"""
app/agents/analysis/tasks/external_summary.py
Task : résumé pédagogique à partir d'un payload Tavily (sources + contenu).

Activée quand :
  - Intent = EXTERNAL_KNOWLEDGE (questions de définition / explication / actu)
  - Mode = ResponseMode.EXTERNAL ou EXTERNAL_FALLBACK

Cette task ne consomme PAS de DataFrame. Le SQL Agent n'a pas tourné, il n'y
a aucune donnée interne. Tout vient du payload Tavily injecté par le planner
dans `instruction["tavily_payload"]`.

Sortie : un TaskResult avec :
  - insights : 2-4 phrases NL en français qui expliquent le concept en citant
    les sources par leur URL.
  - recommendations : éventuelles actions de suivi (vide la plupart du temps
    pour ce type de question).
  - stats : vide.
  - visualizations : vide.
  - metadata.sources : liste structurée des sources utilisées (titre, URL,
    snippet) — l'Orchestrator s'en sert pour construire response.external_data.
  - kg_payload : vide pour l'instant (on ne stocke pas les insights externes
    dans le KG — pas critique, à voir avec le Feedback Agent plus tard).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd

from app.agents.analysis.llm.insight_generator import (
    GeneratedInsights,
    InsightGenerator,
)
from app.agents.analysis.tasks.base import (
    AnalysisTask,
    TaskResult,
    register_task,
)

logger = logging.getLogger(__name__)


# ─── Constantes paramétrables ─────────────────────────────────────────────


# Limite de caractères du contenu extrait envoyé au LLM. Garde-fou contre
# les pages très longues qui exploseraient le contexte / latence.
MAX_EXTRACTED_CONTENT_CHARS = 6000

# Limite sur le nombre de sources passées au LLM. On garde les meilleures
# selon le score Tavily.
MAX_SOURCES_FOR_LLM = 5

# Confidence par défaut quand le payload Tavily est complètement vide.
EMPTY_PAYLOAD_CONFIDENCE = 0.2


# ─── Helpers internes ─────────────────────────────────────────────────────


def _validate_payload(payload: Any) -> tuple[bool, str | None]:
    """
    Valide que `tavily_payload` est exploitable.

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(payload, dict):
        return False, "tavily_payload absent ou non-dict"

    sources = payload.get("sources") or []
    extracted = payload.get("extracted_content") or ""

    if not sources and not extracted:
        return False, "tavily_payload vide (ni sources ni extracted_content)"

    return True, None


def _trim_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Trie par score décroissant et tronque à MAX_SOURCES_FOR_LLM.
    Sécurise les types pour éviter les surprises côté prompt builder.
    """
    safe = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        safe.append(
            {
                "title": str(s.get("title") or "").strip(),
                "url": str(s.get("url") or "").strip(),
                "snippet": str(s.get("snippet") or "")[:300],
                "score": float(s.get("score") or 0.0),
            }
        )
    safe.sort(key=lambda x: x["score"], reverse=True)
    return safe[:MAX_SOURCES_FOR_LLM]


def _trim_extracted(content: str | None) -> str:
    """Tronque l'extracted_content à MAX_EXTRACTED_CONTENT_CHARS chars."""
    if not isinstance(content, str):
        return ""
    if len(content) <= MAX_EXTRACTED_CONTENT_CHARS:
        return content
    return content[:MAX_EXTRACTED_CONTENT_CHARS]


# ─── La task ──────────────────────────────────────────────────────────────


@register_task
class ExternalSummaryTask(AnalysisTask):
    """
    Task qui produit un résumé NL à partir d'un payload Tavily.

    Le DataFrame `df` reçu est ignoré (toujours vide pour cette task).
    Le payload Tavily est lu dans `instruction["tavily_payload"]`.
    """

    task_name = "external_summary"

    def __init__(self, insight_generator: InsightGenerator | None = None) -> None:
        self._insight_generator: InsightGenerator | None = insight_generator

    def set_insight_generator(self, generator: InsightGenerator) -> None:
        """Permet au runner d'injecter le générateur après instanciation."""
        self._insight_generator = generator

    # ─── run() ────────────────────────────────────────────────────────────

    def run(
        self,
        df: pd.DataFrame,
        instruction: dict[str, Any],
        semantic_context: dict[str, Any] | None = None,
    ) -> TaskResult:
        start_time = time.perf_counter()
        warnings: list[str] = []

        payload = instruction.get("tavily_payload")
        is_valid, err = _validate_payload(payload)

        if not is_valid:
            warnings.append(f"external_summary: {err}")
            return self._build_empty_result(
                start_time=start_time,
                warnings=warnings,
                payload=payload,
            )

        # À ce stade, payload est garanti dict avec sources OU extracted_content.
        sources_trimmed = _trim_sources(payload.get("sources") or [])
        extracted_trimmed = _trim_extracted(payload.get("extracted_content"))
        query = str(payload.get("query") or "").strip()
        provider = str(payload.get("provider") or "tavily")

        if not sources_trimmed and not extracted_trimmed:
            warnings.append(
                "external_summary: payload Tavily présent mais vide après nettoyage"
            )
            return self._build_empty_result(
                start_time=start_time,
                warnings=warnings,
                payload=payload,
            )

        # Génération des insights via le LLM.
        generated = self._generate_insights(
            query=query,
            sources=sources_trimmed,
            extracted_content=extracted_trimmed,
            semantic_context=semantic_context,
            warnings_so_far=warnings,
        )
        warnings.extend(generated.warnings)

        # Assemblage du TaskResult.
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        metadata: dict[str, Any] = {
            "task": self.task_name,
            "subtype": "tavily",
            "confidence": generated.overall_confidence,
            "n_rows": 0,
            "method": payload.get("source") or "tavily_extract",
            "duration_ms": duration_ms,
            "fallback_used": generated.used_fallback,
            "sources": sources_trimmed,        # citation explicite côté UI
            "tavily_query": query,
            "provider": provider,
        }
        if generated.llm_metadata:
            metadata["llm"] = generated.llm_metadata

        return TaskResult(
            insights=[i.text for i in generated.insights],
            visualizations=[],
            recommendations=[r.text for r in generated.recommendations],
            stats={},
            metadata=metadata,
            warnings=warnings,
            kg_payload=[],
        )

    # ─── Helpers privés ───────────────────────────────────────────────────

    def _generate_insights(
        self,
        *,
        query: str,
        sources: list[dict[str, Any]],
        extracted_content: str,
        semantic_context: dict[str, Any] | None,
        warnings_so_far: list[str],
    ) -> GeneratedInsights:
        """Appelle l'InsightGenerator avec un prompt dédié 'external_summary'."""
        if self._insight_generator is None:
            from app.agents.analysis.llm.schemas import Insight

            warning = (
                "InsightGenerator non injecté dans ExternalSummaryTask. "
                "Aucun insight NL généré."
            )
            logger.warning(warning)
            return GeneratedInsights(
                insights=[
                    Insight(
                        text=(
                            f"Sources externes trouvées ({len(sources)}). "
                            f"Résumé non généré (LLM indisponible)."
                            if sources
                            else "Contenu externe disponible (résumé non généré)."
                        ),
                        confidence=EMPTY_PAYLOAD_CONFIDENCE,
                        supporting_stats=[],
                    )
                ],
                recommendations=[],
                overall_confidence=EMPTY_PAYLOAD_CONFIDENCE,
                warnings=[warning],
                llm_metadata={"fallback": True, "no_generator": True},
                used_fallback=True,
            )

        # Pour ce type de task, on n'a pas de "stats" classiques. On passe le
        # nombre de sources et la longueur du contenu pour que le validator
        # de supporting_stats ait des clés à matcher (sinon il filtrerait tout).
        pseudo_stats = {
            "n_sources": len(sources),
            "extracted_chars": len(extracted_content),
            "query": query,
        }

        return self._insight_generator.generate(
            task_name=self.task_name,
            stats=pseudo_stats,
            prompt_kwargs={
                "query": query,
                "sources": sources,
                "extracted_content": extracted_content,
                "semantic_hints": semantic_context,
                "warnings": list(warnings_so_far),
            },
        )

    def _build_empty_result(
        self,
        *,
        start_time: float,
        warnings: list[str],
        payload: Any,
    ) -> TaskResult:
        """Construit un TaskResult valide quand le payload est inutilisable."""
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        sources = []
        if isinstance(payload, dict):
            sources = _trim_sources(payload.get("sources") or [])
        return TaskResult(
            insights=[],
            visualizations=[],
            recommendations=[],
            stats={},
            metadata={
                "task": self.task_name,
                "subtype": "tavily_empty",
                "confidence": EMPTY_PAYLOAD_CONFIDENCE,
                "n_rows": 0,
                "method": "tavily_extract",
                "duration_ms": duration_ms,
                "fallback_used": False,
                "sources": sources,
            },
            warnings=warnings,
            kg_payload=[],
        )
