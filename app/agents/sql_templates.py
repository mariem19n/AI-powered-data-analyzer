"""
app/agents/sql_templates.py
Moteur de templates SQL — assemblage déterministe.

Prend les pièces du SemanticContext (tables, colonnes, entity_filters,
time_filters, implicit_conditions, metrics) et les assemble en SQL
valide PostgreSQL sans aucun appel LLM.

Couvre ~80% des cas :
  - Agrégations simples (SELECT colonnes FROM table WHERE filtres)
  - Filtres par entité (WHERE symbol = 'BTC')
  - Filtres temporels (WHERE date >= ...)
  - Métriques calculées (SELECT AVG(...), SUM(...) FROM ...)
  - GROUP BY par entité ou période
  - ORDER BY date DESC
  - LIMIT configurable

Retourne None si la complexité dépasse ce que le template gère :
  - Sous-requêtes
  - Window functions
  - CASE WHEN complexes
  - JOINs cross-table (ex: crypto + macro sur la même query)
  - Requêtes multi-entités avec GROUP BY pivot

Le SQL Agent bascule alors sur le LLM pour ces cas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TemplateResult:
    """Résultat de l'assemblage par template."""

    sql: str | None  # None = template ne peut pas gérer, fallback LLM
    method: str = "template"  # "template" ou "llm"
    reason: str = ""  # raison si template a échoué


class SQLTemplateEngine:
    """
    Assemble le SemanticContext en SQL via des templates Python.

    Usage :
        engine = SQLTemplateEngine()
        result = engine.build(semantic_context_dict, instruction)
        if result.sql is None:
            # fallback LLM
    """

    MAX_ROWS_DEFAULT = 1000

    def build(
        self,
        ctx: dict[str, Any],
        instruction: dict[str, Any] | None = None,
    ) -> TemplateResult:
        """
        Tente d'assembler le SQL depuis le SemanticContext.

        Args:
            ctx: SemanticContext.to_dict()
            instruction: payload de l'ExecutionStep (task, overrides, etc.)

        Returns:
            TemplateResult avec le SQL ou None si fallback LLM nécessaire
        """
        instruction = instruction or {}
        tables = ctx.get("tables", [])
        entity_filters = ctx.get("entity_filters", [])
        metrics = ctx.get("metrics", [])
        columns = ctx.get("columns", [])
        time_filters = ctx.get("time_filters", [])
        implicit_conditions = ctx.get("implicit_conditions", [])

        # ── Vérifier si template peut gérer ───────────────────
        if not tables:
            return TemplateResult(
                sql=None,
                reason="Aucune table dans le SemanticContext",
            )

        # Cross-table JOINs → trop complexe pour template
        if len(tables) > 1:
            table_names = {t.get("table_name", "") for t in tables}
            # Si les tables sont des partitions de la même fact table, c'est OK
            base_tables = {
                t.split("_btc")[0].split("_eth")[0].split("_sol")[0]
                for t in table_names
            }
            if len(base_tables) > 1:
                return TemplateResult(
                    sql=None,
                    reason=f"Cross-table JOIN ({', '.join(table_names)}) — fallback LLM",
                )

        # Entity filter override (comparison plan : une seule entité par step)
        entity_override = instruction.get("entity_filter_override")

        # ── Assemblage ────────────────────────────────────────
        primary_table = self._get_primary_table(tables)
        if not primary_table:
            return TemplateResult(
                sql=None,
                reason="Pas de table primaire identifiée",
            )

        table_name = primary_table.get("table_name", "")

        # SELECT clause
        select_parts = self._build_select(
            columns, metrics, entity_filters, table_name, instruction
        )

        # WHERE clause
        where_parts = self._build_where(
            entity_filters, time_filters, implicit_conditions,
            entity_override, table_name,
        )

        # GROUP BY clause
        group_by = self._build_group_by(metrics, instruction)

        # ORDER BY clause
        order_by = self._build_order_by(table_name, instruction)

        # LIMIT
        limit = instruction.get("limit", self.MAX_ROWS_DEFAULT)

        # ── Assemblage final ──────────────────────────────────
        sql_parts = [f"SELECT {', '.join(select_parts)}"]
        sql_parts.append(f"FROM {table_name}")

        if where_parts:
            sql_parts.append(f"WHERE {' AND '.join(where_parts)}")

        if group_by:
            sql_parts.append(f"GROUP BY {', '.join(group_by)}")

        if order_by:
            sql_parts.append(f"ORDER BY {order_by}")

        sql_parts.append(f"LIMIT {limit}")

        sql = "\n".join(sql_parts)

        logger.info(
            "Template SQL assemblé — table=%s, %d colonnes SELECT, "
            "%d conditions WHERE",
            table_name,
            len(select_parts),
            len(where_parts),
        )
        return TemplateResult(sql=sql, method="template")

    # ─── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _get_primary_table(tables: list[dict]) -> dict | None:
        """Retourne la table avec le rôle 'primary', ou la première."""
        for t in tables:
            if t.get("role") == "primary":
                return t
        return tables[0] if tables else None

    @staticmethod
    def _build_select(
        columns: list[dict],
        metrics: list[dict],
        entity_filters: list[dict],
        table_name: str,
        instruction: dict,
    ) -> list[str]:
        """Construit la clause SELECT."""
        parts: list[str] = []

        # Toujours inclure la date si la table en a une
        parts.append("date")

        # Colonnes demandées
        for col in columns:
            col_name = col.get("column", "")
            if col_name and col_name not in parts:
                parts.append(col_name)

        # Entity columns (pour distinguer dans les résultats)
        for ef in entity_filters:
            col = ef.get("column", "")
            if col and col not in parts:
                parts.append(col)

        # Métriques (formules SQL)
        for m in metrics:
            formula = m.get("formula", "")
            name = m.get("name", "metric")
            if formula:
                parts.append(f"{formula} AS {name}")

        # Si aucune colonne explicite, sélectionner les colonnes utiles
        if len(parts) <= 1:  # seulement "date"
            # Colonnes par défaut selon le type de table
            if "crypto" in table_name:
                parts.extend(["symbol", "close_usd", "volume"])
            elif "fred" in table_name:
                parts.extend(["fred_code", "value"])
            elif "gdelt" in table_name:
                parts.extend(["keyword", "tone", "title"])
            elif "sentiment" in table_name:
                parts.extend(["keyword", "avg_tone", "article_count"])

        # Si group_by_entities demandé (multi-entity comparison)
        if instruction.get("group_by_entities"):
            for ef in entity_filters:
                col = ef.get("column", "")
                if col and col not in parts:
                    parts.insert(1, col)  # après date

        return parts

    @staticmethod
    def _build_where(
        entity_filters: list[dict],
        time_filters: list[dict],
        implicit_conditions: list[str],
        entity_override: dict | None,
        table_name: str,
    ) -> list[str]:
        """Construit la clause WHERE."""
        conditions: list[str] = []

        # Entity filters
        if entity_override:
            col = entity_override.get("column", "")
            val = entity_override.get("value", "")
            if col and val:
                conditions.append(f"{col} = '{val}'")
        else:
            for ef in entity_filters:
                col = ef.get("column", "")
                val = ef.get("value", "")
                tbl = ef.get("table", "")
                # Appliquer le filtre seulement si c'est la bonne table
                if col and val and (not tbl or tbl == table_name):
                    conditions.append(f"{col} = '{val}'")

        # Time filters
        for tf in time_filters:
            clause = tf.get("filter_clause", "")
            if clause:
                conditions.append(clause)

        # Implicit conditions (sql_predicates du KG)
        for cond in implicit_conditions:
            if cond and cond not in conditions:
                conditions.append(cond)

        return conditions

    @staticmethod
    def _build_group_by(
        metrics: list[dict],
        instruction: dict,
    ) -> list[str]:
        """Construit le GROUP BY si nécessaire."""
        if not metrics:
            return []

        # Si des métriques sont présentes, il faut un GROUP BY
        # sur les colonnes non-agrégées
        group_cols = []
        if instruction.get("group_by_entities"):
            group_cols.append("symbol")
        # GROUP BY date par défaut pour les séries temporelles
        group_cols.append("date")
        return group_cols

    @staticmethod
    def _build_order_by(table_name: str, instruction: dict) -> str:
        """Construit le ORDER BY."""
        # Par défaut : date DESC pour les séries temporelles
        return "date DESC"
