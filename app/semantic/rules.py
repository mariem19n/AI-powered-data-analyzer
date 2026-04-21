"""
app/semantic/rules.py
SP1-49 — Règles métier implicites + placeholder access policies.

Enrichit le ResolvedContext avec les BusinessRules du KG
qui s'appliquent aux tables impliquées dans la résolution.

Deux types de règles :
  - sql_predicate   : condition injectable dans WHERE
                      Ex: volume > 0, value IS NOT NULL
  - query_guideline : consigne de génération pour le SQL Agent
                      Ex: utiliser la table parent, pas les partitions

Certaines règles sont conditionnelles :
  - Les filtres de sentiment (crypto vs macro) ne s'appliquent
    que si le contexte implique du sentiment.
  - La règle market_cap ne s'applique que si market_cap est demandé.

Aucun appel LLM — uniquement du lookup Neo4j + logique Python.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.semantic.resolver import ResolvedContext

logger = logging.getLogger(__name__)


# ─── Modèles de sortie ───────────────────────────────────────


@dataclass
class ImplicitRule:
    """Une règle métier implicite."""
    rule_id: str
    table: str
    description: str
    sql_condition: str
    rule_type: str = "sql_predicate"  # "sql_predicate" | "query_guideline"

    def is_predicate(self) -> bool:
        """True si c'est un vrai filtre SQL injectable dans WHERE."""
        return self.rule_type == "sql_predicate"

    def is_guideline(self) -> bool:
        """True si c'est une consigne de génération SQL."""
        return self.rule_type == "query_guideline"


@dataclass
class AccessFilter:
    """Placeholder pour un filtre d'accès RBAC."""
    policy_id: str
    table: str
    sql_condition: str
    reason: str


@dataclass
class EnrichedContext:
    """
    ResolvedContext enrichi avec les règles métier implicites
    et les access policies (placeholder).
    """
    resolved: ResolvedContext
    implicit_rules: list[ImplicitRule] = field(default_factory=list)
    access_filters: list[AccessFilter] = field(default_factory=list)

    # Audit
    rules_log: list[str] = field(default_factory=list)

    def all_sql_conditions(self) -> list[str]:
        """
        Conditions SQL injectables dans WHERE — uniquement les sql_predicate.
        Les query_guideline ne sont PAS incluses ici.
        """
        conditions = [
            r.sql_condition for r in self.implicit_rules if r.is_predicate()
        ]
        conditions.extend(f.sql_condition for f in self.access_filters)
        return conditions

    def generation_guidelines(self) -> list[str]:
        """
        Consignes de génération SQL pour le SQL Agent.
        Pas des filtres WHERE — des instructions d'architecture.
        """
        return [
            r.sql_condition for r in self.implicit_rules if r.is_guideline()
        ]

    def rules_for_table(self, table: str) -> list[ImplicitRule]:
        """Règles applicables à une table spécifique."""
        return [r for r in self.implicit_rules if r.table == table]

    def predicates_for_table(self, table: str) -> list[ImplicitRule]:
        """Uniquement les sql_predicate pour une table."""
        return [
            r for r in self.implicit_rules
            if r.table == table and r.is_predicate()
        ]


# ─── Règles conditionnelles ──────────────────────────────────
# IDs exacts depuis schema_definitions.py / KG :
#   - "crypto_direct_sentiment"  → agg_daily_sentiment
#   - "macro_sentiment"          → agg_daily_sentiment
#   - "market_cap_365_days_only" → fact_crypto_daily

SENTIMENT_CONDITIONAL_RULES = frozenset({
    "crypto_direct_sentiment",
    "macro_sentiment",
})

MARKET_CAP_CONDITIONAL_RULES = frozenset({
    "market_cap_365_days_only",
})


# ─── Rules Enricher ──────────────────────────────────────────


class RulesEnricher:
    """
    Enrichit le ResolvedContext avec les règles métier implicites
    et les access policies depuis le Knowledge Graph.

    Usage :
        enricher = RulesEnricher(neo4j_driver)
        enriched = enricher.enrich(resolved_context)
    """

    def __init__(self, neo4j_driver):
        self._driver = neo4j_driver
        logger.info("RulesEnricher initialisé")

    def enrich(
        self,
        resolved: ResolvedContext,
        user_context: dict | None = None,
    ) -> EnrichedContext:
        """
        Enrichit le ResolvedContext avec les règles implicites.

        Args:
            resolved: Sortie du KG Resolver (SP1-48).
            user_context: Contexte utilisateur pour le RBAC (placeholder).

        Returns:
            EnrichedContext avec règles implicites et access filters.
        """
        ctx = EnrichedContext(resolved=resolved)

        if resolved.is_empty():
            ctx.rules_log.append("Aucune table impliquée — pas de règles à appliquer")
            return ctx

        # 1. Récupérer les règles du KG pour les tables impliquées
        tables = resolved.all_tables()
        kg_rules = self._fetch_rules_for_tables(tables)

        # 2. Filtrer les règles conditionnelles selon le contexte
        applicable_rules = self._filter_conditional_rules(kg_rules, resolved)

        # 3. Ajouter les règles applicables
        for rule in applicable_rules:
            ctx.implicit_rules.append(rule)
            type_label = "predicate" if rule.is_predicate() else "guideline"
            ctx.rules_log.append(
                f"[{type_label}] '{rule.rule_id}' sur {rule.table}: "
                f"{rule.sql_condition}"
            )

        # 4. Access policies (placeholder)
        access_filters = self._get_access_filters(user_context)
        ctx.access_filters = access_filters
        if not access_filters:
            ctx.rules_log.append(
                "Access policies: aucun filtre RBAC (pas de rôle défini)"
            )

        logger.info(
            "Rules enrichment — %d predicates, %d guidelines, "
            "%d access filters, %d tables",
            len([r for r in ctx.implicit_rules if r.is_predicate()]),
            len([r for r in ctx.implicit_rules if r.is_guideline()]),
            len(ctx.access_filters),
            len(tables),
        )

        return ctx

    # ─── Fetch depuis le KG ───────────────────────────────────

    def _fetch_rules_for_tables(
        self, tables: set[str]
    ) -> list[ImplicitRule]:
        """
        Récupère toutes les BusinessRules du KG attachées
        aux tables impliquées via APPLIES_TO.

        Propriétés Neo4j :
          - br.id         → rule_id
          - br.condition   → sql_condition (ou guideline text)
          - br.rule_type   → "sql_predicate" | "query_guideline"
          - br.description → description
        """
        if not tables:
            return []

        rules: list[ImplicitRule] = []

        for table in tables:
            rows = self._driver.run_query(
                """
                MATCH (br:BusinessRule)-[:APPLIES_TO]->(t:Table {name: $table})
                RETURN br.id AS rule_id,
                       t.name AS table_name,
                       br.description AS description,
                       br.condition AS sql_condition,
                       br.rule_type AS rule_type
                ORDER BY br.id
                """,
                {"table": table},
            )

            for row in rows:
                rules.append(ImplicitRule(
                    rule_id=row["rule_id"],
                    table=row["table_name"],
                    description=row.get("description", ""),
                    sql_condition=row.get("sql_condition", ""),
                    rule_type=row.get("rule_type", "sql_predicate"),
                ))

        logger.debug(
            "KG rules fetched: %d règles pour %d tables",
            len(rules), len(tables),
        )

        return rules

    # ─── Filtrage conditionnel ────────────────────────────────

    def _filter_conditional_rules(
        self,
        rules: list[ImplicitRule],
        resolved: ResolvedContext,
    ) -> list[ImplicitRule]:
        """
        Filtre les règles conditionnelles selon le contexte.

        Certaines règles ne doivent s'appliquer que si le contexte
        de la question le justifie :
          - crypto_direct_sentiment → seulement si sentiment + crypto
          - macro_sentiment         → seulement si sentiment + macro
          - market_cap_365_days_only → seulement si market_cap demandé
        """
        has_sentiment = self._context_involves_sentiment(resolved)
        has_market_cap = self._context_involves_market_cap(resolved)
        sentiment_type = self._detect_sentiment_type(resolved)

        filtered: list[ImplicitRule] = []

        for rule in rules:
            # Règles de sentiment — conditionnelles
            if rule.rule_id in SENTIMENT_CONDITIONAL_RULES:
                if not has_sentiment:
                    logger.debug(
                        "Règle '%s' ignorée — pas de sentiment dans le contexte",
                        rule.rule_id,
                    )
                    continue

                if sentiment_type == "crypto" and rule.rule_id == "macro_sentiment":
                    continue
                if sentiment_type == "macro" and rule.rule_id == "crypto_direct_sentiment":
                    continue

                filtered.append(rule)
                continue

            # Règle market_cap — conditionnelle
            if rule.rule_id in MARKET_CAP_CONDITIONAL_RULES:
                if not has_market_cap:
                    logger.debug(
                        "Règle '%s' ignorée — market_cap non demandé",
                        rule.rule_id,
                    )
                    continue
                filtered.append(rule)
                continue

            # Toutes les autres règles s'appliquent automatiquement
            filtered.append(rule)

        return filtered

    # ─── Détection de contexte ────────────────────────────────

    @staticmethod
    def _context_involves_sentiment(resolved: ResolvedContext) -> bool:
        """Vérifie si le contexte implique du sentiment."""
        for bt in resolved.business_terms:
            if "sentiment" in bt.name.lower() or "tone" in bt.column.lower():
                return True

        for m in resolved.metrics:
            if "sentiment" in m.name.lower():
                return True

        if "agg_daily_sentiment" in resolved.all_tables():
            return True

        for gap in resolved.analytic_gaps:
            if "sentiment" in gap.lower():
                return True

        return False

    @staticmethod
    def _context_involves_market_cap(resolved: ResolvedContext) -> bool:
        """Vérifie si le contexte implique market_cap."""
        for bt in resolved.business_terms:
            if "market_cap" in bt.column.lower() or "capitalisation" in bt.name.lower():
                return True

        for m in resolved.metrics:
            if "market_cap" in m.name.lower() or "capitalisation" in m.name.lower():
                return True

        for gap in resolved.analytic_gaps:
            gap_lower = gap.lower()
            if "market cap" in gap_lower or "capitalisation" in gap_lower:
                return True

        return False

    @staticmethod
    def _detect_sentiment_type(resolved: ResolvedContext) -> str | None:
        """
        Détecte si le sentiment est crypto ou macro.

        Returns:
            "crypto" si uniquement des entités crypto,
            "macro" si uniquement des indicateurs macro,
            None si ambigu (les deux filtres s'appliquent).
        """
        has_crypto = any(e.entity_type == "crypto" for e in resolved.entities)
        has_macro = any(e.entity_type == "macro_indicator" for e in resolved.entities)

        if has_crypto and not has_macro:
            return "crypto"
        if has_macro and not has_crypto:
            return "macro"
        return None

    # ─── Access policies (placeholder RBAC) ───────────────────

    @staticmethod
    def _get_access_filters(
        user_context: dict | None,
    ) -> list[AccessFilter]:
        """
        Placeholder pour les access policies RBAC.
        Retourne une liste vide — pas de restriction pour l'instant.
        """
        # TODO Sprint suivant :
        # 1. Définir les rôles dans le KG (nœuds AccessPolicy)
        # 2. Interroger : MATCH (ap:AccessPolicy)-[:RESTRICTS]->(t:Table)
        #                 WHERE ap.role = $user_role
        # 3. Générer les AccessFilter correspondants

        if user_context and user_context.get("role"):
            logger.info(
                "RBAC placeholder — rôle '%s' détecté mais pas encore implémenté",
                user_context["role"],
            )

        return []