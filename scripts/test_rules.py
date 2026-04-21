"""
scripts/test_rules.py
Test du Rules Enricher (SP1-49).

Usage :
    python scripts/test_rules.py

Prérequis :
    - Neo4j doit tourner (docker-compose up -d)
    - Le KG doit être seedé avec le BUSINESS_RULES mis à jour
      (rule_type ajouté) : python scripts/setup_neo4j_schema.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.db.neo4j import Neo4jDriver
from app.semantic.resolver import (
    KGResolver,
    ResolvedContext,
    ResolvedEntity,
    ResolvedBusinessTerm,
    ResolvedMetric,
    ResolvedTimePeriod,
)
from app.semantic.rules import RulesEnricher, EnrichedContext
from app.semantic.schemas import (
    ClassifiedTerm,
    EnrichedTerms,
    TermCategory,
    ResolutionStatus,
    MatchMethod,
)


# ─── Helpers ──────────────────────────────────────────────────


def build_resolved(
    entities: list[tuple[str, str, str, str, str]] | None = None,
    business_terms: list[tuple[str, str, str]] | None = None,
    metrics: list[tuple[str, str, str]] | None = None,
    time_periods: list[tuple[str, str, str, bool]] | None = None,
    analytic_gaps: list[str] | None = None,
) -> ResolvedContext:
    """Construit un ResolvedContext de test."""
    ctx = ResolvedContext()

    for name, etype, table, col, val in (entities or []):
        ctx.entities.append(ResolvedEntity(
            name=name, entity_type=etype, table=table,
            filter_column=col, filter_value=val,
        ))
        ctx.tables_involved.add(table)

    for name, table, col in (business_terms or []):
        ctx.business_terms.append(ResolvedBusinessTerm(
            name=name, table=table, column=col,
        ))
        ctx.tables_involved.add(table)

    for name, formula, table in (metrics or []):
        ctx.metrics.append(ResolvedMetric(
            name=name, formula=formula, source_table=table,
        ))
        ctx.tables_involved.add(table)

    for name, sql_expr, filter_expr, is_canon in (time_periods or []):
        ctx.time_periods.append(ResolvedTimePeriod(
            name=name, sql_expression=sql_expr,
            filter_expression=filter_expr, is_canonical=is_canon,
        ))

    ctx.analytic_gaps = list(analytic_gaps or [])

    return ctx


def print_enriched(enriched: EnrichedContext, label: str) -> None:
    """Affiche le contexte enrichi."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    predicates = [r for r in enriched.implicit_rules if r.is_predicate()]
    guidelines = [r for r in enriched.implicit_rules if r.is_guideline()]

    if predicates:
        print("\n  📋 SQL Predicates (injectables dans WHERE):")
        for r in predicates:
            print(f"     [{r.rule_id}] {r.table} → {r.sql_condition}")

    if guidelines:
        print("\n  📐 Query Guidelines (consignes SQL Agent):")
        for r in guidelines:
            print(f"     [{r.rule_id}] {r.table} → {r.sql_condition}")

    if not predicates and not guidelines:
        print("\n  📋 Règles: (aucune)")

    if enriched.access_filters:
        print("\n  🔒 Access Filters:")
        for f in enriched.access_filters:
            print(f"     [{f.policy_id}] {f.table} → {f.sql_condition}")
    else:
        print("\n  🔒 Access Filters: (aucun — RBAC placeholder)")

    # Résumé méthodes de sortie
    sql_conds = enriched.all_sql_conditions()
    gen_guides = enriched.generation_guidelines()
    print(f"\n  → all_sql_conditions(): {sql_conds}")
    print(f"  → generation_guidelines(): {gen_guides}")

    if enriched.rules_log:
        print("\n  📝 Log:")
        for log in enriched.rules_log:
            print(f"     {log}")


# ─── Tests ────────────────────────────────────────────────────


def main():
    print("📋 Test Rules Enricher (SP1-49)")
    print("=" * 60)

    driver = Neo4jDriver()
    enricher = RulesEnricher(driver)

    passed = 0
    total = 0

    # ── T1 : Crypto simple — predicates + guideline ───────────
    total += 1
    resolved = build_resolved(
        entities=[("Bitcoin", "crypto", "fact_crypto_daily", "symbol", "BTC")],
    )
    enriched = enricher.enrich(resolved)
    print_enriched(enriched, "T1 — Crypto simple : Bitcoin")

    rule_ids = {r.rule_id for r in enriched.implicit_rules}
    assert "exclude_zero_volume" in rule_ids
    assert "use_parent_table" in rule_ids

    # use_parent_table doit être guideline, pas predicate
    use_parent = next(r for r in enriched.implicit_rules if r.rule_id == "use_parent_table")
    assert use_parent.is_guideline(), \
        f"use_parent_table devrait être guideline, got rule_type={use_parent.rule_type}"

    # all_sql_conditions ne doit PAS contenir use_parent_table
    sql_conds = enriched.all_sql_conditions()
    assert "volume > 0" in sql_conds
    assert all("parent" not in c.lower() for c in sql_conds), \
        f"use_parent_table ne devrait PAS être dans all_sql_conditions: {sql_conds}"

    # generation_guidelines DOIT contenir use_parent_table
    guidelines = enriched.generation_guidelines()
    assert len(guidelines) >= 1, f"Au moins 1 guideline attendue, obtenu {guidelines}"

    # Pas de sentiment
    assert "crypto_direct_sentiment" not in rule_ids
    assert "macro_sentiment" not in rule_ids
    passed += 1

    # ── T2 : Crypto + sentiment → filtre crypto uniquement ───
    total += 1
    resolved = build_resolved(
        entities=[("Bitcoin", "crypto", "fact_crypto_daily", "symbol", "BTC")],
        business_terms=[("sentiment", "agg_daily_sentiment", "avg_tone")],
    )
    enriched = enricher.enrich(resolved)
    print_enriched(enriched, "T2 — Crypto + Sentiment")

    rule_ids = {r.rule_id for r in enriched.implicit_rules}
    assert "crypto_direct_sentiment" in rule_ids
    assert "macro_sentiment" not in rule_ids

    # crypto_direct_sentiment doit être un predicate
    crypto_sent = next(r for r in enriched.implicit_rules if r.rule_id == "crypto_direct_sentiment")
    assert crypto_sent.is_predicate()
    assert "keyword IN" in enriched.all_sql_conditions()[0] or \
           any("keyword IN" in c for c in enriched.all_sql_conditions())
    passed += 1

    # ── T3 : Macro + sentiment → filtre macro ────────────────
    total += 1
    resolved = build_resolved(
        entities=[
            ("Federal Funds Rate", "macro_indicator",
             "fact_fred_observation", "fred_code", "FEDFUNDS"),
        ],
        business_terms=[("sentiment", "agg_daily_sentiment", "avg_tone")],
    )
    enriched = enricher.enrich(resolved)
    print_enriched(enriched, "T3 — Macro + Sentiment")

    rule_ids = {r.rule_id for r in enriched.implicit_rules}
    assert "macro_sentiment" in rule_ids
    assert "crypto_direct_sentiment" not in rule_ids
    assert "valid_fred_values" in rule_ids
    passed += 1

    # ── T4 : Crypto + Macro + sentiment → les deux ───────────
    total += 1
    resolved = build_resolved(
        entities=[
            ("Bitcoin", "crypto", "fact_crypto_daily", "symbol", "BTC"),
            ("Federal Funds Rate", "macro_indicator",
             "fact_fred_observation", "fred_code", "FEDFUNDS"),
        ],
        business_terms=[("sentiment", "agg_daily_sentiment", "avg_tone")],
    )
    enriched = enricher.enrich(resolved)
    print_enriched(enriched, "T4 — Crypto + Macro + Sentiment")

    rule_ids = {r.rule_id for r in enriched.implicit_rules}
    assert "crypto_direct_sentiment" in rule_ids
    assert "macro_sentiment" in rule_ids
    passed += 1

    # ── T5 : FRED seul ────────────────────────────────────────
    total += 1
    resolved = build_resolved(
        entities=[
            ("Federal Funds Rate", "macro_indicator",
             "fact_fred_observation", "fred_code", "FEDFUNDS"),
        ],
    )
    enriched = enricher.enrich(resolved)
    print_enriched(enriched, "T5 — FRED seul")

    rule_ids = {r.rule_id for r in enriched.implicit_rules}
    assert "valid_fred_values" in rule_ids
    assert "crypto_direct_sentiment" not in rule_ids
    assert "macro_sentiment" not in rule_ids
    passed += 1

    # ── T6 : Contexte vide ────────────────────────────────────
    total += 1
    enriched = enricher.enrich(ResolvedContext())
    print_enriched(enriched, "T6 — Contexte vide")

    assert len(enriched.implicit_rules) == 0
    assert len(enriched.all_sql_conditions()) == 0
    assert len(enriched.generation_guidelines()) == 0
    passed += 1

    # ── T7 : RBAC placeholder ─────────────────────────────────
    total += 1
    resolved = build_resolved(
        entities=[("Bitcoin", "crypto", "fact_crypto_daily", "symbol", "BTC")],
    )
    enriched = enricher.enrich(resolved, user_context={"role": "analyst"})
    print_enriched(enriched, "T7 — RBAC placeholder")

    assert len(enriched.access_filters) == 0
    passed += 1

    # ── T8 : Pipeline complet SP1-46 → SP1-48 → SP1-49 ──────
    total += 1
    print(f"\n{'='*60}")
    print("  T8 — Pipeline complet : extraction → résolution → règles")
    print(f"{'='*60}")

    resolver = KGResolver(driver)

    enriched_terms = EnrichedTerms(
        raw_question="quel est le sentiment autour de Solana ce mois",
        corrected_question="quel est le sentiment autour de Solana ce mois",
        terms=[
            ClassifiedTerm(
                text="sentiment", category=TermCategory.BUSINESS_TERM,
                confidence=0.9, resolution_status=ResolutionStatus.RESOLVED,
                matched_by=MatchMethod.EXACT,
            ),
            ClassifiedTerm(
                text="Solana", category=TermCategory.ENTITY,
                confidence=1.0, resolution_status=ResolutionStatus.RESOLVED,
                matched_by=MatchMethod.EXACT,
            ),
            ClassifiedTerm(
                text="ce mois", category=TermCategory.TIME_PERIOD,
                confidence=0.9, resolution_status=ResolutionStatus.RESOLVED,
                matched_by=MatchMethod.EXACT,
            ),
        ],
    )

    resolved_ctx = resolver.resolve(enriched_terms)
    print(f"\n  Résolution: {len(resolved_ctx.entities)} entities, "
          f"{len(resolved_ctx.business_terms)} BT, "
          f"{len(resolved_ctx.time_periods)} TP")
    print(f"  Tables: {resolved_ctx.all_tables()}")

    enriched_ctx = enricher.enrich(resolved_ctx)
    print_enriched(enriched_ctx, "T8 — Pipeline : sentiment + Solana + ce mois")

    rule_ids = {r.rule_id for r in enriched_ctx.implicit_rules}
    assert "exclude_zero_volume" in rule_ids
    assert "crypto_direct_sentiment" in rule_ids
    assert "use_parent_table" in rule_ids

    # Vérifier la séparation predicate / guideline
    sql_conds = enriched_ctx.all_sql_conditions()
    guidelines = enriched_ctx.generation_guidelines()
    assert any("volume > 0" in c for c in sql_conds)
    assert any("keyword IN" in c for c in sql_conds)
    # use_parent_table dans guidelines, PAS dans sql_conditions
    assert len(guidelines) >= 1
    assert all("parent" not in c.lower() for c in sql_conds)
    passed += 1

    # ── Résumé ────────────────────────────────────────────────
    print(f"\n{'='*60}")
    if passed == total:
        print(f"  ✅ {passed}/{total} tests passent !")
    else:
        print(f"  ⚠️  {passed}/{total} tests passent")
    print(f"{'='*60}")

    driver.close()


if __name__ == "__main__":
    main()