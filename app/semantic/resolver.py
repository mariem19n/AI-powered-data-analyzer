"""
app/semantic/resolver.py
KG Lookup for Semantic Resolution.

Prend les termes classifiés (EnrichedTerms de SP1-46) et les résout
vers les structures techniques PostgreSQL via des requêtes Cypher
dans le Knowledge Graph Neo4j.

Pipeline :
  EnrichedTerms → KG Lookup → ResolvedContext

Résolution par catégorie :
  - Entity       → table, colonne de filtre, valeur
  - Metric       → formule SQL, table source, colonnes utilisées
  - BusinessTerm → colonne(s) cible(s) via RESOLVES_TO
  - TimePeriod   → expression SQL de filtre temporel

Aucun appel LLM — uniquement du lookup Neo4j.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.semantic.schemas import (
    ClassifiedTerm,
    EnrichedTerms,
    ResolutionStatus,
    TermCategory,
)

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 0.80


# ─── Modèles de sortie ───────────────────────────────────────


@dataclass
class ResolvedEntity:
    """Une entité résolue vers sa table/colonne/filtre PostgreSQL."""
    name: str
    entity_type: str
    table: str
    filter_column: str
    filter_value: str
    description: str = ""


@dataclass
class ResolvedMetric:
    """Une métrique résolue vers sa formule SQL."""
    name: str
    formula: str
    source_table: str
    description: str = ""
    domain: str = ""


@dataclass
class ResolvedBusinessTerm:
    """Un business term résolu vers sa colonne PostgreSQL."""
    name: str
    table: str
    column: str
    description: str = ""
    domain: str = ""


@dataclass
class ResolvedTimePeriod:
    """Une période temporelle résolue vers son filtre SQL."""
    name: str
    sql_expression: str
    filter_expression: str
    is_canonical: bool = True


@dataclass
class ResolvedContext:
    """
    Résultat complet du KG Lookup.
    Contient toutes les résolutions nécessaires au composant suivant.
    """
    entities: list[ResolvedEntity] = field(default_factory=list)
    metrics: list[ResolvedMetric] = field(default_factory=list)
    business_terms: list[ResolvedBusinessTerm] = field(default_factory=list)
    time_periods: list[ResolvedTimePeriod] = field(default_factory=list)

    # Tables impliquées (déduit des résolutions)
    tables_involved: set[str] = field(default_factory=set)

    # ── Termes non résolus — séparés en deux catégories ─────

    # analytic_gaps : termes compréhensibles mais non mappables
    # techniquement dans le KG.
    
    analytic_gaps: list[str] = field(default_factory=list)

    # unknown_terms : termes non interprétables, hors domaine,
    # ou incohérents après préprocessing, validation et lookup KG.
    # Ex: terme sans rapport, hallucination de l'extracteur, expression incohérente avec le domaine.
    # on Déclenche une demande de clarification.
    unknown_terms: list[str] = field(default_factory=list)

    # Audit
    resolution_log: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not self.entities
            and not self.metrics
            and not self.business_terms
            and not self.time_periods
        )

    def all_tables(self) -> set[str]:
        """Retourne toutes les tables impliquées dans la résolution."""
        tables = set(self.tables_involved)
        for entity in self.entities:
            tables.add(entity.table)
        for metric in self.metrics:
            tables.add(metric.source_table)
        for bt in self.business_terms:
            tables.add(bt.table)
        return tables

    def add_analytic_gap(self, text: str) -> None:
        """Ajoute un analytic gap sans doublon."""
        if text and text not in self.analytic_gaps:
            self.analytic_gaps.append(text)

    def add_unknown_term(self, text: str) -> None:
        """Ajoute un unknown term sans doublon."""
        if text and text not in self.unknown_terms:
            self.unknown_terms.append(text)

    def log(self, message: str) -> None:
        """Ajoute une ligne de log sans doublon consécutif."""
        if not self.resolution_log or self.resolution_log[-1] != message:
            self.resolution_log.append(message)


# ─── Resolver ─────────────────────────────────────────────────


class KGResolver:
    """
    Résout les termes extraits vers les structures PostgreSQL
    via le Knowledge Graph Neo4j.

    Usage :
        resolver = KGResolver(neo4j_driver)
        context = resolver.resolve(enriched_terms)
    """

    def __init__(self, neo4j_driver):
        """
        Args:
            neo4j_driver: Instance de Neo4jDriver avec méthode
                          run_query(cypher, params).
        """
        self._driver = neo4j_driver
        logger.info("KGResolver initialisé")

    def resolve(self, enriched: EnrichedTerms) -> ResolvedContext:
        """
        Résout tous les termes classifiés vers le KG.

        Args:
            enriched: Sortie du pipeline d'extraction / validation.

        Returns:
            ResolvedContext avec toutes les résolutions disponibles.
        """
        ctx = ResolvedContext()

        if enriched.is_empty() and not enriched.unresolved_terms:
            ctx.log("Aucun terme à résoudre")
            return ctx

        # 1. Résolution des termes classifiés
        for term in enriched.terms:
            self._resolve_term(term, ctx)

        # 2. Tentative de lookup technique sur les unresolved_terms bruts
        for text in enriched.unresolved_terms:
            self._resolve_unresolved(text, enriched, ctx)

        logger.info(
            "KG Lookup — %d entities, %d metrics, %d business_terms, "
            "%d time_periods, %d analytic_gaps, %d unknown, %d tables",
            len(ctx.entities),
            len(ctx.metrics),
            len(ctx.business_terms),
            len(ctx.time_periods),
            len(ctx.analytic_gaps),
            len(ctx.unknown_terms),
            len(ctx.all_tables()),
        )

        return ctx

    # ─── Dispatch par catégorie ───────────────────────────────

    def _resolve_term(self, term: ClassifiedTerm, ctx: ResolvedContext) -> None:
        """Dispatch de résolution selon la catégorie du terme."""
        if term.category == TermCategory.ENTITY:
            self._resolve_entity(term.text, ctx, term)
        elif term.category == TermCategory.METRIC:
            self._resolve_metric(term.text, ctx, term)
        elif term.category == TermCategory.BUSINESS_TERM:
            self._resolve_business_term(term.text, ctx, term)
        elif term.category == TermCategory.TIME_PERIOD:
            self._resolve_time_period(term.text, ctx)
        else:
            self._classify_unresolved(term.text, term, ctx)

    # ─── Entity resolution ────────────────────────────────────

    def _resolve_entity(self,text: str,ctx: ResolvedContext,term: ClassifiedTerm | None = None, ) -> None:
        """
        Résout une entité vers sa table et son filtre.
        Cherche par label exact, puis par alias, puis par fuzzy.
        """
        rows = self._driver.run_query(
            """
            MATCH (e:Entity)-[:REPRESENTS]->(c:Column)<-[:HAS_COLUMN]-(t:Table)
            WHERE toLower(e.label) = toLower($text)
            RETURN e.label AS name,
                   e.entity_type AS entity_type,
                   t.name AS table_name,
                   c.name AS filter_column,
                   e.filter_value AS filter_value,
                   e.description AS description
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.entities.append(
                ResolvedEntity(
                    name=row["name"],
                    entity_type=row.get("entity_type", ""),
                    table=row["table_name"],
                    filter_column=row["filter_column"],
                    filter_value=row["filter_value"],
                    description=row.get("description", ""),
                )
            )
            ctx.tables_involved.add(row["table_name"])
            ctx.log(
                f"Entity '{text}' → "
                f"{row['table_name']}.{row['filter_column']} = '{row['filter_value']}'"
            )
            return

        rows = self._driver.run_query(
            """
            MATCH (e:Entity)-[:REPRESENTS]->(c:Column)<-[:HAS_COLUMN]-(t:Table)
            WHERE toLower(e.id) = toLower($text)
               OR any(alias IN coalesce(e.aliases, [])
                      WHERE toLower(alias) = toLower($text))
            RETURN e.label AS name,
                   e.entity_type AS entity_type,
                   t.name AS table_name,
                   c.name AS filter_column,
                   e.filter_value AS filter_value,
                   e.description AS description
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.entities.append(
                ResolvedEntity(
                    name=row["name"],
                    entity_type=row.get("entity_type", ""),
                    table=row["table_name"],
                    filter_column=row["filter_column"],
                    filter_value=row["filter_value"],
                    description=row.get("description", ""),
                )
            )
            ctx.tables_involved.add(row["table_name"])
            ctx.log(
                f"Entity '{text}' (via alias) → "
                f"{row['table_name']}.{row['filter_column']} = '{row['filter_value']}'"
            )
            return

        resolved = self._fuzzy_entity_match(text)
        if resolved:
            ctx.entities.append(resolved)
            ctx.tables_involved.add(resolved.table)
            ctx.log(
                f"Entity '{text}' (fuzzy) → "
                f"{resolved.table}.{resolved.filter_column} = '{resolved.filter_value}'"
            )
            return

        self._classify_unresolved(text, term, ctx)
        ctx.log(f"Entity '{text}' — non trouvée dans le KG")

    def _fuzzy_entity_match(self, text: str) -> ResolvedEntity | None:
        """Fuzzy match contre toutes les entités du KG."""
        rows = self._driver.run_query(
            """
            MATCH (e:Entity)-[:REPRESENTS]->(c:Column)<-[:HAS_COLUMN]-(t:Table)
            RETURN e.label AS name,
                   e.entity_type AS entity_type,
                   t.name AS table_name,
                   c.name AS filter_column,
                   e.filter_value AS filter_value,
                   e.description AS description
            """
        )

        text_lower = text.lower()
        best_score = 0.0
        best_row = None

        for row in rows:
            label_lower = (row.get("name") or "").lower()
            score = SequenceMatcher(None, text_lower, label_lower).ratio()
            if score > best_score and score >= FUZZY_THRESHOLD:
                best_score = score
                best_row = row

        if not best_row:
            return None

        return ResolvedEntity(
            name=best_row["name"],
            entity_type=best_row.get("entity_type", ""),
            table=best_row["table_name"],
            filter_column=best_row["filter_column"],
            filter_value=best_row["filter_value"],
            description=best_row.get("description", ""),
        )

    # ─── Metric resolution ────────────────────────────────────

    def _resolve_metric(
        self,
        text: str,
        ctx: ResolvedContext,
        term: ClassifiedTerm | None = None,
    ) -> None:
        """
        Résout une métrique vers sa formule SQL et sa table source.
        """
        rows = self._driver.run_query(
            """
            MATCH (m:Metric)-[:COMPUTED_FROM]->(t:Table)
            WHERE toLower(m.name) = toLower($text)
            RETURN m.name AS name,
                   m.formula AS formula,
                   t.name AS source_table,
                   m.description AS description,
                   m.domain AS domain
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.metrics.append(
                ResolvedMetric(
                    name=row["name"],
                    formula=row["formula"],
                    source_table=row["source_table"],
                    description=row.get("description", ""),
                    domain=row.get("domain", ""),
                )
            )
            ctx.tables_involved.add(row["source_table"])
            ctx.log(f"Metric '{text}' → {row['name']} ({row['source_table']})")
            return

        self._try_business_term_as_metric(text, ctx, term)

    def _try_business_term_as_metric(
        self,
        text: str,
        ctx: ResolvedContext,
        term: ClassifiedTerm | None = None,
    ) -> None:
        """
        Fallback : cherche le terme comme BusinessTerm avec RESOLVES_TO.
        Utile pour des termes comme 'prix' mappés à une colonne.
        """
        rows = self._driver.run_query(
            """
            MATCH (bt:BusinessTerm)-[:RESOLVES_TO]->(c:Column)<-[:HAS_COLUMN]-(t:Table)
            WHERE toLower(bt.name) = toLower($text)
            RETURN bt.name AS name,
                   t.name AS table_name,
                   c.name AS column_name,
                   bt.description AS description,
                   bt.domain AS domain
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.business_terms.append(
                ResolvedBusinessTerm(
                    name=row["name"],
                    table=row["table_name"],
                    column=row["column_name"],
                    description=row.get("description", ""),
                    domain=row.get("domain", ""),
                )
            )
            ctx.tables_involved.add(row["table_name"])
            ctx.log(
                f"Metric '{text}' → résolu comme BusinessTerm: "
                f"{row['table_name']}.{row['column_name']}"
            )
            return

        resolved = self._fuzzy_metric_match(text)
        if resolved:
            ctx.metrics.append(resolved)
            ctx.tables_involved.add(resolved.source_table)
            ctx.log(
                f"Metric '{text}' (fuzzy) → {resolved.name} ({resolved.source_table})"
            )
            return

        self._classify_unresolved(text, term, ctx)
        ctx.log(f"Metric '{text}' — non trouvée dans le KG")

    def _fuzzy_metric_match(self, text: str) -> ResolvedMetric | None:
        """Fuzzy match contre toutes les métriques du KG."""
        rows = self._driver.run_query(
            """
            MATCH (m:Metric)-[:COMPUTED_FROM]->(t:Table)
            RETURN m.name AS name,
                   m.formula AS formula,
                   t.name AS source_table,
                   m.description AS description,
                   m.domain AS domain
            """
        )

        text_lower = text.lower()
        best_score = 0.0
        best_row = None

        for row in rows:
            name_lower = (row.get("name") or "").lower()
            score = SequenceMatcher(None, text_lower, name_lower).ratio()
            if score > best_score and score >= FUZZY_THRESHOLD:
                best_score = score
                best_row = row

            desc_lower = (row.get("description") or "").lower()
            if text_lower and text_lower in desc_lower and 0.85 > best_score:
                best_score = 0.85
                best_row = row

        if not best_row:
            return None

        return ResolvedMetric(
            name=best_row["name"],
            formula=best_row["formula"],
            source_table=best_row["source_table"],
            description=best_row.get("description", ""),
            domain=best_row.get("domain", ""),
        )

    # ─── BusinessTerm resolution ──────────────────────────────

    def _resolve_business_term(
        self,
        text: str,
        ctx: ResolvedContext,
        term: ClassifiedTerm | None = None,
    ) -> None:
        """
        Résout un business term vers sa colonne PostgreSQL.
        """
        rows = self._driver.run_query(
            """
            MATCH (bt:BusinessTerm)-[:RESOLVES_TO]->(c:Column)<-[:HAS_COLUMN]-(t:Table)
            WHERE toLower(bt.name) = toLower($text)
            RETURN bt.name AS name,
                   t.name AS table_name,
                   c.name AS column_name,
                   bt.description AS description,
                   bt.domain AS domain
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.business_terms.append(
                ResolvedBusinessTerm(
                    name=row["name"],
                    table=row["table_name"],
                    column=row["column_name"],
                    description=row.get("description", ""),
                    domain=row.get("domain", ""),
                )
            )
            ctx.tables_involved.add(row["table_name"])
            ctx.log(f"BusinessTerm '{text}' → {row['table_name']}.{row['column_name']}")
            return

        rows = self._driver.run_query(
            """
            MATCH (bt:BusinessTerm)-[:HAS_SYNONYM]->(s:Synonym)
            WHERE toLower(s.text) = toLower($text)
            WITH bt
            MATCH (bt)-[:RESOLVES_TO]->(c:Column)<-[:HAS_COLUMN]-(t:Table)
            RETURN bt.name AS name,
                   t.name AS table_name,
                   c.name AS column_name,
                   bt.description AS description,
                   bt.domain AS domain
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.business_terms.append(
                ResolvedBusinessTerm(
                    name=row["name"],
                    table=row["table_name"],
                    column=row["column_name"],
                    description=row.get("description", ""),
                    domain=row.get("domain", ""),
                )
            )
            ctx.tables_involved.add(row["table_name"])
            ctx.log(
                f"BusinessTerm '{text}' (via synonyme → '{row['name']}') → "
                f"{row['table_name']}.{row['column_name']}"
            )
            return

        resolved = self._fuzzy_business_term_match(text)
        if resolved:
            ctx.business_terms.append(resolved)
            ctx.tables_involved.add(resolved.table)
            ctx.log(
                f"BusinessTerm '{text}' (fuzzy) → {resolved.table}.{resolved.column}"
            )
            return

        self._classify_unresolved(text, term, ctx)
        ctx.log(f"BusinessTerm '{text}' — non trouvé dans le KG")

    def _fuzzy_business_term_match(self, text: str) -> ResolvedBusinessTerm | None:
        """Fuzzy match contre tous les business terms du KG."""
        rows = self._driver.run_query(
            """
            MATCH (bt:BusinessTerm)-[:RESOLVES_TO]->(c:Column)<-[:HAS_COLUMN]-(t:Table)
            RETURN bt.name AS name,
                   t.name AS table_name,
                   c.name AS column_name,
                   bt.description AS description,
                   bt.domain AS domain
            """
        )

        text_lower = text.lower()
        best_score = 0.0
        best_row = None

        for row in rows:
            name_lower = (row.get("name") or "").lower()
            score = SequenceMatcher(None, text_lower, name_lower).ratio()
            if score > best_score and score >= FUZZY_THRESHOLD:
                best_score = score
                best_row = row

        if not best_row:
            return None

        return ResolvedBusinessTerm(
            name=best_row["name"],
            table=best_row["table_name"],
            column=best_row["column_name"],
            description=best_row.get("description", ""),
            domain=best_row.get("domain", ""),
        )

    # ─── TimePeriod resolution ────────────────────────────────

    def _resolve_time_period(self, text: str, ctx: ResolvedContext) -> None:
        """
        Résout une période temporelle vers son filtre SQL.
        Si elle n'est pas canonique, on la conserve telle quelle
        comme période non normalisée pour traitement ultérieur.
        """
        rows = self._driver.run_query(
            """
            MATCH (tp:TimePeriod)
            WHERE toLower(tp.name) = toLower($text)
            RETURN tp.name AS name,
                   tp.sql_expression AS sql_expression,
                   tp.filter_expression AS filter_expression
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.time_periods.append(
                ResolvedTimePeriod(
                    name=row["name"],
                    sql_expression=row["sql_expression"],
                    filter_expression=row["filter_expression"],
                    is_canonical=True,
                )
            )
            ctx.log(f"TimePeriod '{text}' → {row['filter_expression']}")
            return

        resolved = self._fuzzy_time_period_match(text)
        if resolved:
            ctx.time_periods.append(resolved)
            ctx.log(f"TimePeriod '{text}' (fuzzy) → {resolved.filter_expression}")
            return

        ctx.time_periods.append(
            ResolvedTimePeriod(
                name=text,
                sql_expression="",
                filter_expression="",
                is_canonical=False,
            )
        )
        ctx.log(
            f"TimePeriod '{text}' — non canonique, conservée pour traitement ultérieur"
        )

    def _fuzzy_time_period_match(self, text: str) -> ResolvedTimePeriod | None:
        """Fuzzy match contre toutes les périodes du KG."""
        rows = self._driver.run_query(
            """
            MATCH (tp:TimePeriod)
            RETURN tp.name AS name,
                   tp.sql_expression AS sql_expression,
                   tp.filter_expression AS filter_expression
            """
        )

        text_lower = text.lower()
        best_score = 0.0
        best_row = None

        for row in rows:
            name_lower = (row.get("name") or "").lower()
            score = SequenceMatcher(None, text_lower, name_lower).ratio()
            if score > best_score and score >= FUZZY_THRESHOLD:
                best_score = score
                best_row = row

        if not best_row:
            return None

        return ResolvedTimePeriod(
            name=best_row["name"],
            sql_expression=best_row["sql_expression"],
            filter_expression=best_row["filter_expression"],
            is_canonical=True,
        )

    # ─── Unresolved terms — dernière chance ───────────────────

    def _resolve_unresolved(
        self,
        text: str,
        enriched: EnrichedTerms,
        ctx: ResolvedContext,
    ) -> None:
        """
        Tente un lookup technique minimal sur un terme marqué unresolved
        par l'extracteur, sans interprétation sémantique.
        """
        rows = self._driver.run_query(
            """
            MATCH (e:Entity)-[:REPRESENTS]->(c:Column)<-[:HAS_COLUMN]-(t:Table)
            WHERE toLower(e.label) = toLower($text)
            RETURN e.label AS name,
                   e.entity_type AS entity_type,
                   t.name AS table_name,
                   c.name AS filter_column,
                   e.filter_value AS filter_value,
                   e.description AS description
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.entities.append(
                ResolvedEntity(
                    name=row["name"],
                    entity_type=row.get("entity_type", ""),
                    table=row["table_name"],
                    filter_column=row["filter_column"],
                    filter_value=row["filter_value"],
                    description=row.get("description", ""),
                )
            )
            ctx.tables_involved.add(row["table_name"])
            ctx.log(
                f"Unresolved '{text}' → résolu comme Entity: "
                f"{row['table_name']}.{row['filter_column']} = '{row['filter_value']}'"
            )
            return

        rows = self._driver.run_query(
            """
            MATCH (bt:BusinessTerm)-[:RESOLVES_TO]->(c:Column)<-[:HAS_COLUMN]-(t:Table)
            WHERE toLower(bt.name) = toLower($text)
            RETURN bt.name AS name,
                   t.name AS table_name,
                   c.name AS column_name
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.business_terms.append(
                ResolvedBusinessTerm(
                    name=row["name"],
                    table=row["table_name"],
                    column=row["column_name"],
                )
            )
            ctx.tables_involved.add(row["table_name"])
            ctx.log(
                f"Unresolved '{text}' → résolu comme BusinessTerm: "
                f"{row['table_name']}.{row['column_name']}"
            )
            return

        rows = self._driver.run_query(
            """
            MATCH (m:Metric)-[:COMPUTED_FROM]->(t:Table)
            WHERE toLower(m.name) = toLower($text)
            RETURN m.name AS name,
                   m.formula AS formula,
                   t.name AS source_table,
                   m.description AS description
            LIMIT 1
            """,
            {"text": text},
        )

        if rows:
            row = rows[0]
            ctx.metrics.append(
                ResolvedMetric(
                    name=row["name"],
                    formula=row["formula"],
                    source_table=row["source_table"],
                    description=row.get("description", ""),
                )
            )
            ctx.tables_involved.add(row["source_table"])
            ctx.log(f"Unresolved '{text}' → résolu comme Metric: {row['name']}")
            return

        matching_term = self._find_matching_classified_term(text, enriched)
        self._classify_unresolved(text, matching_term, ctx)
        ctx.log(f"Unresolved '{text}' — aucune correspondance dans le KG")

    def _find_matching_classified_term(
        self,
        text: str,
        enriched: EnrichedTerms,
    ) -> ClassifiedTerm | None:
        """Retrouve le ClassifiedTerm correspondant si possible."""
        for term in enriched.terms:
            if term.text == text:
                return term
            if getattr(term, "original_text", None) == text:
                return term
        return None

    # ─── Classification des termes non résolus ────────────────

    def _classify_unresolved(
        self,
        text: str,
        term: ClassifiedTerm | None,
        ctx: ResolvedContext,
    ) -> None:
        """
        Classe un terme non résolu dans le KG.

        Règles :
          - term is None (vient de unresolved_terms bruts)
              → analytic_gap
                Le LLM a compris quelque chose mais n'a pas su le classer.

          - INVALID
              → unknown_term
                Le validator a marqué ce terme comme incohérent / hallucination.

          - PLAUSIBLE_BUT_NEW
              → analytic_gap
                Terme plausible dans le domaine mais absent du KG.

          - AMBIGUOUS
              → analytic_gap
                Plusieurs correspondances possibles — l'Orchestrateur
                peut désambiguïser avec le contexte de la question.
                Ce n'est pas un terme incompréhensible.

          - Autre (fallback)
              → analytic_gap
        """
        if term is None:
            ctx.add_analytic_gap(text)
            return

        if term.resolution_status == ResolutionStatus.INVALID:
            ctx.add_unknown_term(text)
            return

        # PLAUSIBLE_BUT_NEW, AMBIGUOUS, ou autre
        ctx.add_analytic_gap(text)