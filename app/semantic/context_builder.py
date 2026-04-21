"""
app/semantic/context_builder.py
 Construction du SemanticContext JSON.

Point de sortie du Semantic Layer. Compile les résolutions
et les règles implicites en un objet JSON structuré
directement exploitable par le SQL Agent.

Le SemanticContext contient tout ce dont le SQL Agent a besoin
pour assembler une requête SQL correcte, sans ambiguïté :
  - Tables impliquées et leurs colonnes pertinentes
  - Filtres d'entités (WHERE symbol = 'BTC')
  - Formules de métriques calculées
  - Colonnes de business terms résolus
  - Filtres temporels
  - Conditions SQL implicites (volume > 0, value IS NOT NULL...)
  - Guidelines de génération SQL
  - Signaux pour l'Orchestrateur (analytic_gaps, unknown_terms, clarification)

Aucun appel LLM — assemblage Python pur.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime

from app.semantic.rules import EnrichedContext
from app.semantic.resolver import ResolvedContext

logger = logging.getLogger(__name__)


# ─── SemanticContext — sortie finale du Semantic Layer ────────


@dataclass
class TableContext:
    """Contexte d'une table impliquée dans la requête."""
    table_name: str
    role: str  # "primary" | "filter" | "join" | "aggregation"
    columns_used: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)


@dataclass
class EntityFilter:
    """Filtre d'entité pour la clause WHERE."""
    entity_name: str
    entity_type: str
    table: str
    column: str
    value: str


@dataclass
class MetricSpec:
    """Spécification d'une métrique à calculer."""
    name: str
    formula: str
    source_table: str
    description: str = ""


@dataclass
class ColumnSpec:
    """Spécification d'une colonne à sélectionner."""
    name: str
    table: str
    column: str
    description: str = ""


@dataclass
class TimeFilter:
    """Filtre temporel pour la clause WHERE."""
    expression: str
    filter_clause: str
    is_canonical: bool = True
    raw_text: str = ""


@dataclass
class SemanticContext:
    """
    Sortie finale du Semantic Layer.

    Objet JSON-serializable contenant tout ce que le SQL Agent
    a besoin pour générer la requête SQL.
    """

    # ── Question originale ────────────────────────────────────
    raw_question: str
    corrected_question: str

    # ── Tables impliquées ─────────────────────────────────────
    tables: list[TableContext] = field(default_factory=list)

    # ── Ce qu'on cherche (SELECT) ─────────────────────────────
    entity_filters: list[EntityFilter] = field(default_factory=list)
    metrics: list[MetricSpec] = field(default_factory=list)
    columns: list[ColumnSpec] = field(default_factory=list)

    # ── Filtres (WHERE) ───────────────────────────────────────
    time_filters: list[TimeFilter] = field(default_factory=list)
    implicit_conditions: list[str] = field(default_factory=list)

    # ── Consignes pour le SQL Agent ───────────────────────────
    generation_guidelines: list[str] = field(default_factory=list)

    # ── Signaux pour l'Orchestrateur ──────────────────────────
    analytic_gaps: list[str] = field(default_factory=list)
    unknown_terms: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_reason: str = ""

    # ── Métadonnées ───────────────────────────────────────────
    confidence: float = 0.0
    context_hash: str = ""
    built_at: str = ""

    def to_dict(self) -> dict:
        """Sérialise en dict JSON-compatible."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Sérialise en JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ─── Builder ──────────────────────────────────────────────────


class SemanticContextBuilder:
    """
    Construit le SemanticContext final à partir du pipeline :
      EnrichedTerms → ResolvedContext → EnrichedContext → SemanticContext

    Usage :
        builder = SemanticContextBuilder()
        context = builder.build(enriched_context, enriched_terms)
    """
    def build_empty(
        self,
        raw_question: str = "",
        reason: str = "",
    ) -> SemanticContext:
        """
        Construit un SemanticContext vide avec needs_clarification=True.
 
        Utilisé quand l'extraction LLM échoue ou ne produit aucun
        terme exploitable.
        """
        from datetime import datetime
 
        return SemanticContext(
            raw_question=raw_question,
            corrected_question=raw_question,
            needs_clarification=True,
            clarification_reason=reason or "Aucun terme métier extrait.",
            confidence=0.0,
            built_at=datetime.utcnow().isoformat(),
            context_hash="",
        )
 

    def build(
        self,
        enriched: EnrichedContext,
        raw_question: str = "",
        corrected_question: str = "",
        pipeline_confidence: float = 0.0,
    ) -> SemanticContext:
        """
        Construit le SemanticContext à partir de l'EnrichedContext.

        Args:
            enriched: Sortie du RulesEnricher (SP1-49).
            raw_question: Question originale de l'utilisateur.
            corrected_question: Question après pré-traitement.
            pipeline_confidence: Score de confiance du pipeline d'extraction.

        Returns:
            SemanticContext JSON-serializable.
        """
        resolved = enriched.resolved

        ctx = SemanticContext(
            raw_question=raw_question,
            corrected_question=corrected_question or raw_question,
        )

        # 1. Entity filters
        ctx.entity_filters = self._build_entity_filters(resolved)

        # 2. Metrics
        ctx.metrics = self._build_metrics(resolved)

        # 3. Columns (business terms résolus)
        ctx.columns = self._build_columns(resolved)

        # 4. Time filters
        ctx.time_filters = self._build_time_filters(resolved)

        # 5. Tables impliquées avec rôles
        ctx.tables = self._build_tables(resolved, enriched)

        # 6. Implicit SQL conditions (predicates uniquement)
        ctx.implicit_conditions = enriched.all_sql_conditions()

        # 7. Generation guidelines
        ctx.generation_guidelines = enriched.generation_guidelines()

        # 8. Signaux pour l'Orchestrateur
        ctx.analytic_gaps = list(resolved.analytic_gaps)
        ctx.unknown_terms = list(resolved.unknown_terms)
        ctx.needs_clarification = self._needs_clarification(resolved, enriched)
        ctx.clarification_reason = self._clarification_reason(resolved, enriched)

        # 9. Métadonnées
        ctx.confidence = pipeline_confidence
        ctx.built_at = datetime.utcnow().isoformat()
        ctx.context_hash = self._compute_hash(ctx)

        logger.info(
            "SemanticContext construit — %d tables, %d entities, "
            "%d metrics, %d columns, %d time_filters, "
            "%d conditions, %d guidelines, clarification=%s",
            len(ctx.tables),
            len(ctx.entity_filters),
            len(ctx.metrics),
            len(ctx.columns),
            len(ctx.time_filters),
            len(ctx.implicit_conditions),
            len(ctx.generation_guidelines),
            ctx.needs_clarification,
        )

        return ctx

    # ─── Entity filters ───────────────────────────────────────

    @staticmethod
    def _build_entity_filters(resolved: ResolvedContext) -> list[EntityFilter]:
        return [
            EntityFilter(
                entity_name=e.name,
                entity_type=e.entity_type,
                table=e.table,
                column=e.filter_column,
                value=e.filter_value,
            )
            for e in resolved.entities
        ]

    # ─── Metrics ──────────────────────────────────────────────

    @staticmethod
    def _build_metrics(resolved: ResolvedContext) -> list[MetricSpec]:
        return [
            MetricSpec(
                name=m.name,
                formula=m.formula,
                source_table=m.source_table,
                description=m.description,
            )
            for m in resolved.metrics
        ]

    # ─── Columns ──────────────────────────────────────────────

    @staticmethod
    def _build_columns(resolved: ResolvedContext) -> list[ColumnSpec]:
        return [
            ColumnSpec(
                name=bt.name,
                table=bt.table,
                column=bt.column,
                description=bt.description,
            )
            for bt in resolved.business_terms
        ]

    # ─── Time filters ─────────────────────────────────────────

    @staticmethod
    def _build_time_filters(resolved: ResolvedContext) -> list[TimeFilter]:
        return [
            TimeFilter(
                expression=tp.sql_expression,
                filter_clause=tp.filter_expression,
                is_canonical=tp.is_canonical,
                raw_text=tp.name,
            )
            for tp in resolved.time_periods
        ]

    # ─── Tables avec rôles ────────────────────────────────────

    @staticmethod
    def _build_tables(
        resolved: ResolvedContext,
        enriched: EnrichedContext,
    ) -> list[TableContext]:
        """
        Construit la liste des tables avec leurs rôles et filtres.

        Rôles :
          - "primary"     : table d'où on extrait des colonnes SELECT
                            ou des métriques calculées
          - "filter"      : table référencée uniquement par un entity filter
                            (ex: fact_fred_observation quand on veut juste
                            le taux Fed pour contextualiser Bitcoin)
          - "aggregation" : table d'agrégation (préfixe agg_)
          - "join"        : table de dimension ou staging (préfixe dim_/stg_)
        """
        table_info: dict[str, TableContext] = {}
        all_tables = resolved.all_tables()

        # Tables qui ont des colonnes SELECT ou métriques → primary candidates
        tables_with_select: set[str] = set()
        for bt in resolved.business_terms:
            tables_with_select.add(bt.table)
        for m in resolved.metrics:
            tables_with_select.add(m.source_table)

        # Tables référencées uniquement par entity filters
        tables_entity_only: set[str] = set()
        for e in resolved.entities:
            tables_entity_only.add(e.table)
        # Retirer celles qui ont aussi des colonnes SELECT
        tables_entity_only -= tables_with_select

        # Initialiser toutes les tables avec le bon rôle
        for table_name in all_tables:
            if "agg_" in table_name:
                role = "aggregation"
            elif "dim_" in table_name:
                role = "join"
            elif "stg_" in table_name:
                role = "join"
            elif table_name in tables_entity_only and len(all_tables) > 1:
                # Table utilisée uniquement pour filtrer une entité,
                # ET il y a d'autres tables → c'est un filtre contextuel.
                # Exception : si c'est la seule table, elle reste primary.
                role = "filter"
            else:
                role = "primary"

            table_info[table_name] = TableContext(
                table_name=table_name,
                role=role,
                columns_used=[],
                filters=[],
            )

        # Ajouter les colonnes utilisées par les entities
        for e in resolved.entities:
            if e.table in table_info:
                tc = table_info[e.table]
                if e.filter_column not in tc.columns_used:
                    tc.columns_used.append(e.filter_column)
                tc.filters.append(f"{e.filter_column} = '{e.filter_value}'")

        # Ajouter les colonnes des business terms
        for bt in resolved.business_terms:
            if bt.table in table_info:
                tc = table_info[bt.table]
                if bt.column not in tc.columns_used:
                    tc.columns_used.append(bt.column)

        # Ajouter les conditions implicites par table
        for rule in enriched.implicit_rules:
            if rule.is_predicate() and rule.table in table_info:
                table_info[rule.table].filters.append(rule.sql_condition)

        return list(table_info.values())

    # ─── Clarification ────────────────────────────────────────

    @staticmethod
    def _needs_clarification(
        resolved: ResolvedContext,
        enriched: EnrichedContext,
    ) -> bool:
        """Le SQL Agent ne peut pas travailler si..."""
        # Termes vraiment incompréhensibles
        if resolved.unknown_terms:
            return True

        # Rien n'a été résolu du tout
        if resolved.is_empty() and not resolved.analytic_gaps:
            return True

        return False

    @staticmethod
    def _clarification_reason(
        resolved: ResolvedContext,
        enriched: EnrichedContext,
    ) -> str:
        if resolved.unknown_terms:
            terms = ", ".join(resolved.unknown_terms)
            return f"Termes non compris : {terms}"

        if resolved.is_empty() and not resolved.analytic_gaps:
            return "Aucun terme résolu — la question est trop vague ou hors domaine"

        return ""

    # ─── Hash ─────────────────────────────────────────────────

    @staticmethod
    def _compute_hash(ctx: SemanticContext) -> str:
        """
        Hash du SemanticContext pour le cache Redis.
        Basé sur les éléments structurels, pas les métadonnées.
        """
        hashable = {
            "entity_filters": [
                (e.table, e.column, e.value) for e in ctx.entity_filters
            ],
            "metrics": [(m.name, m.source_table) for m in ctx.metrics],
            "columns": [(c.table, c.column) for c in ctx.columns],
            "time_filters": [
                (t.filter_clause, t.is_canonical) for t in ctx.time_filters
            ],
            "implicit_conditions": sorted(ctx.implicit_conditions),
        }
        raw = json.dumps(hashable, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]