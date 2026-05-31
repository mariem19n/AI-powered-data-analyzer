"""
app/orchestrator/provenance.py

Traçabilité simplifiée orientée utilisateur métier.

Construit un objet ProvenanceTrace à partir des résultats déjà disponibles
dans le pipeline, sans modifier les agents.

Objectif frontend :
Afficher un modal "Sources et méthode" avec :
- données internes utilisées
- méthode d'analyse
- sources externes éventuelles
- résumé lisible

On évite volontairement les détails trop techniques :
- pas de llm_calls
- pas de tokens
- pas de hash cache
- pas de semantic_context brut
- pas de plan LangGraph brut
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field


# =====================================================================
# Models
# =====================================================================


class DataSourceTrace(BaseModel):
    """Source de données interne utilisée."""

    name: str = Field(
        description="Nom lisible de la source interne."
    )
    description: str = Field(
        description="Description courte de ce qui a été extrait."
    )
    tables: list[str] = Field(
        default_factory=list,
        description="Tables PostgreSQL utilisées."
    )
    record_count: int | None = Field(
        default=None,
        description="Nombre de lignes analysées."
    )
    time_range: str | None = Field(
        default=None,
        description="Période utilisée si disponible."
    )
    sql: str | None = Field(
        default=None,
        description="Requête SQL utilisée. À masquer par défaut côté frontend."
    )


class MethodTrace(BaseModel):
    """Méthode d'analyse appliquée."""

    name: str = Field(
        description="Nom lisible de la méthode."
    )
    description: str = Field(
        description="Explication simple de la méthode."
    )
    algorithm: str | None = Field(
        default=None,
        description="Algorithme utilisé si pertinent."
    )
    reliability_note: str | None = Field(
        default=None,
        description="Note simple sur la fiabilité ou l'incertitude."
    )


class ExternalSourceItem(BaseModel):
    """Une source externe précise."""

    title: str = Field(
        default="Source externe",
        description="Titre de la source."
    )
    url: str | None = Field(
        default=None,
        description="URL cliquable."
    )
    domain: str | None = Field(
        default=None,
        description="Domaine de la source."
    )
    snippet: str | None = Field(
        default=None,
        description="Court extrait si disponible."
    )


class ExternalSourceTrace(BaseModel):
    """Sources externes utilisées."""

    provider: str = Field(
        default="External retrieval",
        description="Fournisseur externe, ex: Tavily, GDELT."
    )
    query: str | None = Field(
        default=None,
        description="Requête de recherche si disponible."
    )
    sources: list[ExternalSourceItem] = Field(
        default_factory=list,
        description="Liste des sources externes utilisées."
    )
    confidence_note: str = Field(
        default="Ces sources ont été utilisées comme contexte externe.",
        description="Note simple de fiabilité."
    )


class ProvenanceTrace(BaseModel):
    """
    Trace simplifiée affichable côté frontend.

    Elle est orientée utilisateur métier, pas développeur.
    """

    response_mode: str = Field(
        default="internal",
        description="internal, external ou hybrid."
    )

    data_sources: list[DataSourceTrace] = Field(default_factory=list)
    methods: list[MethodTrace] = Field(default_factory=list)
    external_sources: list[ExternalSourceTrace] = Field(default_factory=list)

    summary: str = Field(
        default="Réponse construite par le système.",
        description="Résumé court pour le bouton ou tooltip."
    )


# =====================================================================
# Public builder
# =====================================================================


def build_provenance(
    *,
    response_mode: str,
    intent: str | None,
    plan: dict[str, Any] | None,
    step_results: dict[str, Any],
    external_data: dict[str, Any] | None,
    analysis_stats: dict[str, Any] | None,
) -> ProvenanceTrace:
    """
    Construit la provenance à partir des informations existantes.

    Aucun appel externe.
    Aucun appel LLM.
    Aucun accès DB.
    """

    mode = response_mode or "internal"

    plan_dict = _as_dict(plan)
    plan_steps = _extract_plan_steps(plan_dict)
    step_tasks = _map_step_tasks(plan_steps)

    data_sources = _build_internal_sources(
        step_results=step_results,
        plan_steps=plan_steps,
    )

    methods = _build_methods(
        intent=intent,
        plan_steps=plan_steps,
        step_tasks=step_tasks,
        analysis_stats=analysis_stats or {},
    )

    external_sources = _build_external_sources(
        response_mode=mode,
        external_data=external_data,
    )

    summary = _build_summary(
        mode=mode,
        data_sources=data_sources,
        methods=methods,
        external_sources=external_sources,
    )

    return ProvenanceTrace(
        response_mode=mode,
        data_sources=data_sources,
        methods=methods,
        external_sources=external_sources,
        summary=summary,
    )


# =====================================================================
# Internal data sources
# =====================================================================


def _build_internal_sources(
    *,
    step_results: dict[str, Any],
    plan_steps: list[dict[str, Any]],
) -> list[DataSourceTrace]:
    sources: list[DataSourceTrace] = []

    semantic_context_by_step = _semantic_context_by_step(plan_steps)

    for step_id, raw_result in (step_results or {}).items():
        result = _as_dict(raw_result)

        # Certains runners enveloppent le résultat dans {"data": ...}
        data = _as_dict(result.get("data", result))

        sql = data.get("sql")
        row_count = data.get("row_count")
        if row_count is None:
            row_count = data.get("record_count")

        # Un step SQL est reconnu par la présence d'une requête SQL,
        # de colonnes/records, ou par son id.
        looks_like_sql_step = (
            bool(sql)
            or "records" in data
            or "columns" in data
            or step_id.startswith("sql")
        )

        if not looks_like_sql_step:
            continue

        ctx = semantic_context_by_step.get(step_id, {})

        tables = _extract_tables_from_context(ctx)
        if not tables and sql:
            tables = _extract_tables_from_sql(sql)

        time_range = _extract_time_range_from_context(ctx)

        name = _humanize_tables(tables)
        description = _describe_internal_data(
            tables=tables,
            row_count=row_count,
            time_range=time_range,
        )

        sources.append(
            DataSourceTrace(
                name=name,
                description=description,
                tables=tables,
                record_count=row_count,
                time_range=time_range,
                sql=sql,
            )
        )

    return _deduplicate_data_sources(sources)


# =====================================================================
# Methods
# =====================================================================


def _build_methods(
    *,
    intent: str | None,
    plan_steps: list[dict[str, Any]],
    step_tasks: dict[str, str],
    analysis_stats: dict[str, Any],
) -> list[MethodTrace]:
    methods: list[MethodTrace] = []

    # 1. Extraire les méthodes depuis les steps Analysis du plan
    for step in plan_steps:
        step_id = step.get("step_id", "")
        agent = str(step.get("agent", "")).lower()
        instruction = _as_dict(step.get("instruction", {}))
        task = instruction.get("task") or step_tasks.get(step_id)

        if not task:
            continue

        is_analysis_step = (
            step_id.startswith("analyse")
            or "analysis" in agent
            or task in _TASK_TO_METHOD
        )

        if not is_analysis_step:
            continue

        methods.append(_method_from_task(task, analysis_stats.get(step_id)))

    # 2. Fallback depuis analysis_stats si le plan n'a pas suffi
    if not methods and analysis_stats:
        for step_id, stats in analysis_stats.items():
            if not isinstance(stats, dict):
                continue

            task = _infer_task_from_stats(stats)
            methods.append(_method_from_task(task, stats))

    # 3. Fallback depuis intent
    if not methods and intent:
        methods.append(_method_from_task(intent, None))

    return _deduplicate_methods(methods)


def _method_from_task(
    task: str,
    stats: dict[str, Any] | None = None,
) -> MethodTrace:
    normalized = _normalize_task(task)
    stats = stats or {}

    label, description, default_algo = _TASK_TO_METHOD.get(
        normalized,
        (
            "Analyse de données",
            "Analyse des données disponibles pour produire des insights.",
            None,
        ),
    )

    algorithm = (
        stats.get("model")
        or stats.get("algorithm")
        or _nested_get(stats, ["metadata", "model"])
        or default_algo
    )

    reliability_note = _build_reliability_note(stats)

    return MethodTrace(
        name=label,
        description=description,
        algorithm=_humanize_algorithm(algorithm),
        reliability_note=reliability_note,
    )


def _infer_task_from_stats(stats: dict[str, Any]) -> str:
    model = str(stats.get("model") or _nested_get(stats, ["metadata", "model"]) or "").lower()

    if model == "prophet" or "forecast" in stats:
        return "forecasting"

    if "correlation" in stats or "pearson" in str(stats).lower():
        return "correlation"

    if "anomaly" in stats or "outliers" in stats:
        return "anomaly_detection"

    return stats.get("analysis_type") or "descriptive"


# =====================================================================
# External sources
# =====================================================================


def _build_external_sources(
    *,
    response_mode: str,
    external_data: dict[str, Any] | None,
) -> list[ExternalSourceTrace]:
    if not external_data:
        return []

    if response_mode not in {"external", "hybrid"}:
        return []

    payload = _as_dict(external_data)

    # Plusieurs formats possibles selon ton pipeline
    sources_raw = (
        payload.get("sources")
        or _nested_get(payload, ["tavily_payload", "sources"])
        or _nested_get(payload, ["external_payload", "sources"])
        or []
    )

    provider = (
        payload.get("provider")
        or payload.get("source")
        or _nested_get(payload, ["tavily_payload", "provider"])
        or "Tavily / external sources"
    )

    query = (
        payload.get("query")
        or payload.get("search_query")
        or _nested_get(payload, ["tavily_payload", "query"])
    )

    items: list[ExternalSourceItem] = []

    if isinstance(sources_raw, list):
        for src in sources_raw:
            if not isinstance(src, dict):
                continue

            url = src.get("url") or src.get("link")
            domain = src.get("domain") or _domain_from_url(url)

            items.append(
                ExternalSourceItem(
                    title=src.get("title") or src.get("name") or domain or "Source externe",
                    url=url,
                    domain=domain,
                    snippet=src.get("snippet") or src.get("content") or src.get("summary"),
                )
            )

    # Fallback : si on a seulement des URLs
    if not items:
        urls = payload.get("urls") or []
        for url in urls:
            domain = _domain_from_url(url)
            items.append(
                ExternalSourceItem(
                    title=domain or "Source externe",
                    url=url,
                    domain=domain,
                )
            )

    if not items:
        return []

    return [
        ExternalSourceTrace(
            provider=str(provider),
            query=query,
            sources=items,
            confidence_note=payload.get(
                "confidence_note",
                "Ces sources externes ont été utilisées pour contextualiser la réponse.",
            ),
        )
    ]


# =====================================================================
# Summary
# =====================================================================


def _build_summary(
    *,
    mode: str,
    data_sources: list[DataSourceTrace],
    methods: list[MethodTrace],
    external_sources: list[ExternalSourceTrace],
) -> str:
    parts: list[str] = []

    if mode == "internal":
        parts.append("Données internes")
    elif mode == "external":
        parts.append("Sources externes")
    elif mode == "hybrid":
        parts.append("Données internes + sources externes")
    else:
        parts.append("Sources système")

    if methods:
        method_names = [m.name for m in methods[:2]]
        parts.append(" · ".join(method_names))

    if data_sources:
        total_rows = sum(s.record_count or 0 for s in data_sources)
        if total_rows > 0:
            parts.append(f"{total_rows} lignes analysées")

    if external_sources:
        n_sources = sum(len(group.sources) for group in external_sources)
        parts.append(f"{n_sources} sources externes")

    return " · ".join(parts)


# =====================================================================
# Helpers
# =====================================================================


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _extract_plan_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    steps = plan.get("steps") or []
    result: list[dict[str, Any]] = []

    for step in steps:
        step_dict = _as_dict(step)
        if step_dict:
            result.append(step_dict)

    return result


def _map_step_tasks(plan_steps: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}

    for step in plan_steps:
        step_id = step.get("step_id", "")
        instruction = _as_dict(step.get("instruction", {}))
        task = instruction.get("task")
        if step_id and task:
            mapping[step_id] = task

    return mapping


def _semantic_context_by_step(
    plan_steps: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    for step in plan_steps:
        step_id = step.get("step_id", "")
        instruction = _as_dict(step.get("instruction", {}))
        ctx = _as_dict(instruction.get("semantic_context"))

        if step_id and ctx:
            result[step_id] = ctx

    return result


def _extract_tables_from_context(ctx: dict[str, Any]) -> list[str]:
    tables: list[str] = []

    for t in ctx.get("tables", []) or []:
        if not isinstance(t, dict):
            continue

        table_name = t.get("table_name") or t.get("name")
        if table_name and table_name not in tables:
            tables.append(table_name)

    return tables


def _extract_time_range_from_context(ctx: dict[str, Any]) -> str | None:
    time_filters = ctx.get("time_filters") or []
    if not time_filters:
        return None

    first = time_filters[0]
    if not isinstance(first, dict):
        return None

    return (
        first.get("raw_text")
        or first.get("expression")
        or first.get("filter_clause")
    )


def _extract_tables_from_sql(sql: str) -> list[str]:
    if not sql:
        return []

    # Simple mais suffisant pour la provenance utilisateur.
    # On cherche FROM table et JOIN table.
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)",
        re.IGNORECASE,
    )

    tables: list[str] = []
    for match in pattern.finditer(sql):
        table = match.group(1).split(".")[-1]
        if table not in tables:
            tables.append(table)

    return tables


def _describe_internal_data(
    *,
    tables: list[str],
    row_count: int | None,
    time_range: str | None,
) -> str:
    parts = ["Données internes utilisées pour construire la réponse."]

    if row_count is not None:
        parts.append(f"{row_count} lignes analysées.")

    if time_range:
        parts.append(f"Période : {time_range}.")

    return " ".join(parts)


def _deduplicate_data_sources(
    sources: list[DataSourceTrace],
) -> list[DataSourceTrace]:
    seen: set[tuple[str, ...]] = set()
    result: list[DataSourceTrace] = []

    for src in sources:
        key = tuple(src.tables) if src.tables else (src.name,)
        if key in seen:
            continue
        seen.add(key)
        result.append(src)

    return result


def _deduplicate_methods(
    methods: list[MethodTrace],
) -> list[MethodTrace]:
    seen: set[str] = set()
    result: list[MethodTrace] = []

    for method in methods:
        key = f"{method.name}:{method.algorithm or ''}"
        if key in seen:
            continue
        seen.add(key)
        result.append(method)

    return result


def _nested_get(d: dict[str, Any], path: list[str]) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _domain_from_url(url: str | None) -> str | None:
    if not url:
        return None

    try:
        parsed = urlparse(url)
        if parsed.netloc:
            return parsed.netloc.removeprefix("www.")
    except Exception:
        return None

    return None


def _humanize_tables(tables: list[str]) -> str:
    if not tables:
        return "Données internes"

    labels = [_TABLE_LABELS.get(t, t) for t in tables]
    return ", ".join(labels)


def _normalize_task(task: str | None) -> str:
    if not task:
        return "descriptive"

    task = str(task).lower().strip()

    aliases = {
        "forecast": "forecasting",
        "forecasting": "forecasting",
        "external_summary": "external_summary",
        "hybrid_summary": "hybrid_summary",
        "anomaly": "anomaly_detection",
        "anomaly_detection": "anomaly_detection",
        "correlation": "correlation",
        "comparison": "comparison",
        "multi_dim_comparison": "comparison",
        "descriptive": "descriptive",
        "aggregation": "aggregation",
        "diagnostic_report": "diagnosis",
        "causal_correlation": "diagnosis",
    }

    return aliases.get(task, task)


def _humanize_algorithm(algorithm: Any) -> str | None:
    if algorithm is None:
        return None

    algo = str(algorithm)

    labels = {
        "prophet": "Prophet",
        "zscore": "Z-score",
        "z_score": "Z-score",
        "iqr": "IQR",
        "isolation_forest": "Isolation Forest",
        "pearson": "Pearson",
        "spearman": "Spearman",
    }

    return labels.get(algo.lower(), algo)


def _build_reliability_note(stats: dict[str, Any]) -> str | None:
    if not stats:
        return None

    evaluation = stats.get("evaluation") or {}
    diagnostics = stats.get("diagnostics") or {}

    notes: list[str] = []

    if isinstance(evaluation, dict):
        if evaluation.get("mape") is not None:
            notes.append(
                f"Évaluation par backtesting avec MAPE {round(float(evaluation['mape']), 2)}%."
            )
        elif evaluation.get("skipped") is True:
            reason = evaluation.get("skip_reason") or "données insuffisantes"
            notes.append(f"Évaluation non effectuée : {reason}.")

    if isinstance(diagnostics, dict):
        uncertainty = diagnostics.get("mean_ci_width_pct")
        if uncertainty is not None:
            notes.append(
                f"Incertitude moyenne : {round(float(uncertainty), 2)}%."
            )

    return " ".join(notes) if notes else None


# =====================================================================
# Labels
# =====================================================================


_TABLE_LABELS: dict[str, str] = {
    "fact_crypto_daily": "Données crypto historiques",
    "dim_crypto": "Référentiel des cryptomonnaies",
    "agg_daily_sentiment": "Sentiment agrégé des actualités",
    "fact_gdelt_events": "Articles et événements GDELT",
    "article_enrichment": "Articles enrichis",
    "fact_fred_observation": "Indicateurs macroéconomiques FRED",
    "dim_fred_series": "Référentiel des indicateurs macroéconomiques",
}


_TASK_TO_METHOD: dict[str, tuple[str, str, str | None]] = {
    "aggregation": (
        "Agrégation SQL",
        "Calcul d'une valeur agrégée demandée à partir des données filtrées.",
        "SQL aggregate",
    ),
    "descriptive": (
        "Analyse descriptive",
        "Calcul des indicateurs clés et synthèse des tendances observées.",
        None,
    ),
    "comparison": (
        "Comparaison",
        "Comparaison de plusieurs séries ou entités sur une période donnée.",
        None,
    ),
    "correlation": (
        "Corrélation statistique",
        "Mesure du lien statistique entre plusieurs séries temporelles.",
        "Pearson / Spearman",
    ),
    "anomaly_detection": (
        "Détection d'anomalies",
        "Identification de valeurs inhabituelles dans les prix, volumes ou indicateurs.",
        "Z-score / IQR / Isolation Forest",
    ),
    "forecasting": (
        "Prévision statistique",
        "Projection des valeurs futures à partir des tendances historiques.",
        "Prophet",
    ),
    "external_summary": (
        "Synthèse externe",
        "Synthèse construite à partir de sources externes.",
        None,
    ),
    "hybrid_summary": (
        "Analyse hybride",
        "Combinaison des données internes avec des sources externes de contexte.",
        None,
    ),
    "diagnosis": (
        "Analyse diagnostique",
        "Recherche d'explications possibles à partir des données et du contexte disponible.",
        None,
    ),
}
