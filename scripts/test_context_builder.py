"""
scripts/test_context_builder.py
Test du SemanticContext Builder (SP1-50).

Teste la construction du SemanticContext JSON à partir du pipeline
complet : extraction → résolution → règles → context.

Usage :
    python scripts/test_context_builder.py

Prérequis :
    - Neo4j doit tourner (docker-compose up -d)
    - Le KG doit être seedé (python scripts/setup_neo4j_schema.py)
"""

import json
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
from app.semantic.context_builder import SemanticContextBuilder, SemanticContext
from app.semantic.schemas import (
    ClassifiedTerm,
    EnrichedTerms,
    TermCategory,
    ResolutionStatus,
    MatchMethod,
)


# ─── Helpers ──────────────────────────────────────────────────


def build_enriched_terms(
    terms: list[tuple[str, TermCategory]],
    unresolved: list[str] | None = None,
    question: str = "",
) -> EnrichedTerms:
    classified = []
    for text, cat in terms:
        classified.append(ClassifiedTerm(
            text=text,
            category=cat,
            confidence=0.9,
            resolution_status=ResolutionStatus.RESOLVED,
            matched_by=MatchMethod.EXACT,
        ))
    return EnrichedTerms(
        raw_question=question,
        corrected_question=question,
        terms=classified,
        unresolved_terms=unresolved or [],
        pipeline_confidence=0.85,
    )


def print_semantic_context(ctx: SemanticContext, label: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    print(f"\n  📝 Question: {ctx.raw_question}")

    if ctx.tables:
        print(f"\n  📊 Tables ({len(ctx.tables)}):")
        for t in ctx.tables:
            cols = ", ".join(t.columns_used) if t.columns_used else "(aucune)"
            filters_str = " AND ".join(t.filters) if t.filters else "(aucun)"
            print(f"     [{t.role}] {t.table_name}")
            print(f"       colonnes: {cols}")
            print(f"       filtres: {filters_str}")

    if ctx.entity_filters:
        print(f"\n  🏷  Entity Filters ({len(ctx.entity_filters)}):")
        for e in ctx.entity_filters:
            print(f"     {e.entity_name} → {e.table}.{e.column} = '{e.value}'")

    if ctx.metrics:
        print(f"\n  📐 Metrics ({len(ctx.metrics)}):")
        for m in ctx.metrics:
            preview = m.formula[:60] + "..." if len(m.formula) > 60 else m.formula
            print(f"     {m.name} → {preview}")

    if ctx.columns:
        print(f"\n  📋 Columns ({len(ctx.columns)}):")
        for c in ctx.columns:
            print(f"     {c.name} → {c.table}.{c.column}")

    if ctx.time_filters:
        print(f"\n  📅 Time Filters ({len(ctx.time_filters)}):")
        for tf in ctx.time_filters:
            status = "✅" if tf.is_canonical else "⚠️"
            print(f"     {status} {tf.raw_text}: {tf.filter_clause or '(non résolu)'}")

    if ctx.implicit_conditions:
        print(f"\n  🔒 SQL Conditions ({len(ctx.implicit_conditions)}):")
        for c in ctx.implicit_conditions:
            print(f"     WHERE {c}")

    if ctx.generation_guidelines:
        print(f"\n  📐 Guidelines ({len(ctx.generation_guidelines)}):")
        for g in ctx.generation_guidelines:
            print(f"     → {g}")

    if ctx.analytic_gaps:
        print(f"\n  🔍 Analytic Gaps: {ctx.analytic_gaps}")

    if ctx.unknown_terms:
        print(f"\n  ❓ Unknown: {ctx.unknown_terms}")

    print(f"\n  ⚙️  needs_clarification={ctx.needs_clarification}")
    if ctx.clarification_reason:
        print(f"     reason: {ctx.clarification_reason}")
    print(f"  🔑 hash={ctx.context_hash}")
    print(f"  📊 confidence={ctx.confidence}")


# ─── Tests ────────────────────────────────────────────────────


def main():
    print("🏗  Test SemanticContext Builder (SP1-50)")
    print("=" * 70)

    driver = Neo4jDriver()
    resolver = KGResolver(driver)
    enricher = RulesEnricher(driver)
    builder = SemanticContextBuilder()

    passed = 0
    total = 0

    # ── T1 : Prix du Bitcoin ce mois ──────────────────────────
    total += 1
    question = "montre le prix du Bitcoin ce mois"
    terms = build_enriched_terms(
        terms=[
            ("prix", TermCategory.BUSINESS_TERM),
            ("Bitcoin", TermCategory.ENTITY),
            ("ce mois", TermCategory.TIME_PERIOD),
        ],
        question=question,
    )
    resolved = resolver.resolve(terms)
    enriched = enricher.enrich(resolved)
    ctx = builder.build(
        enriched,
        raw_question=question,
        corrected_question=question,
        pipeline_confidence=0.85,
    )
    print_semantic_context(ctx, "T1 — Prix du Bitcoin ce mois")

    assert len(ctx.entity_filters) == 1
    assert ctx.entity_filters[0].value == "BTC"
    assert len(ctx.time_filters) == 1
    assert ctx.time_filters[0].is_canonical
    assert len(ctx.tables) >= 1
    assert not ctx.needs_clarification
    assert ctx.context_hash != ""

    # Vérifier le JSON
    json_str = ctx.to_json()
    parsed = json.loads(json_str)
    assert "entity_filters" in parsed
    assert "tables" in parsed
    passed += 1

    # ── T2 : Sentiment Solana ce mois (pipeline complet) ──────
    total += 1
    question = "quel est le sentiment autour de Solana ce mois"
    terms = build_enriched_terms(
        terms=[
            ("sentiment", TermCategory.BUSINESS_TERM),
            ("Solana", TermCategory.ENTITY),
            ("ce mois", TermCategory.TIME_PERIOD),
        ],
        question=question,
    )
    resolved = resolver.resolve(terms)
    enriched = enricher.enrich(resolved)
    ctx = builder.build(enriched, raw_question=question, pipeline_confidence=0.9)
    print_semantic_context(ctx, "T2 — Sentiment Solana ce mois")

    assert any(e.value == "SOL" for e in ctx.entity_filters)
    assert any(c.column == "avg_tone" for c in ctx.columns)
    assert any("keyword IN" in c for c in ctx.implicit_conditions)
    # use_parent_table dans guidelines, PAS dans conditions
    assert len(ctx.generation_guidelines) >= 1
    assert all("parent" not in c.lower() for c in ctx.implicit_conditions)
    assert not ctx.needs_clarification
    passed += 1

    # ── T3 : Comparaison Bitcoin/Ethereum (analytic_gap) ──────
    total += 1
    question = "est-ce que Bitcoin et Ethereum ont évolué de la même manière"
    terms = build_enriched_terms(
        terms=[
            ("Bitcoin", TermCategory.ENTITY),
            ("Ethereum", TermCategory.ENTITY),
        ],
        unresolved=["évolué de la même manière"],
        question=question,
    )
    resolved = resolver.resolve(terms)
    enriched = enricher.enrich(resolved)
    ctx = builder.build(enriched, raw_question=question, pipeline_confidence=0.7)
    print_semantic_context(ctx, "T3 — Comparaison Bitcoin/Ethereum (analytic_gap)")

    assert len(ctx.entity_filters) == 2
    assert "évolué de la même manière" in ctx.analytic_gaps
    # analytic_gaps ne déclenchent PAS la clarification
    assert not ctx.needs_clarification
    passed += 1

    # ── T4 : Macro + Crypto (deux tables) ─────────────────────
    total += 1
    question = "impact du taux de la Fed sur Bitcoin en 2024"
    terms = build_enriched_terms(
        terms=[
            ("Federal Funds Rate", TermCategory.ENTITY),
            ("Bitcoin", TermCategory.ENTITY),
        ],
        unresolved=["impact"],
        question=question,
    )
    resolved = resolver.resolve(terms)
    enriched = enricher.enrich(resolved)
    ctx = builder.build(enriched, raw_question=question, pipeline_confidence=0.75)
    print_semantic_context(ctx, "T4 — Macro + Crypto")

    tables = {t.table_name for t in ctx.tables}
    assert "fact_crypto_daily" in tables
    assert "fact_fred_observation" in tables
    assert any("value IS NOT NULL" in c for c in ctx.implicit_conditions)
    assert "impact" in ctx.analytic_gaps
    # fact_fred_observation n'a pas de colonnes SELECT ni métriques ici,
    # seulement un entity filter → rôle "filter"
    fred_table = next(t for t in ctx.tables if t.table_name == "fact_fred_observation")
    assert fred_table.role == "filter", \
        f"fact_fred_observation devrait être 'filter', obtenu '{fred_table.role}'"
    # fact_crypto_daily a un entity filter mais c'est la table principale
    # (si pas de business_terms/metrics, les deux sont entity-only,
    # mais avec analytic_gap "impact", on vérifie juste la structure)
    passed += 1

    # ── T5 : Metric calculée ──────────────────────────────────
    total += 1
    question = "volatilité 30 jours du Bitcoin"
    terms = build_enriched_terms(
        terms=[
            ("volatilite_30j", TermCategory.METRIC),
            ("Bitcoin", TermCategory.ENTITY),
        ],
        question=question,
    )
    resolved = resolver.resolve(terms)
    enriched = enricher.enrich(resolved)
    ctx = builder.build(enriched, raw_question=question, pipeline_confidence=0.9)
    print_semantic_context(ctx, "T5 — Metric calculée : volatilite_30j")

    assert len(ctx.metrics) >= 1
    assert any("STDDEV" in m.formula for m in ctx.metrics)
    passed += 1

    # ── T6 : Question vide → clarification ────────────────────
    total += 1
    question = "bonjour"
    terms = EnrichedTerms(
        raw_question=question,
        corrected_question=question,
    )
    resolved = resolver.resolve(terms)
    enriched = enricher.enrich(resolved)
    ctx = builder.build(enriched, raw_question=question, pipeline_confidence=0.0)
    print_semantic_context(ctx, "T6 — Question vide (greeting)")

    assert ctx.needs_clarification
    assert ctx.clarification_reason != ""
    assert len(ctx.tables) == 0
    passed += 1

    # ── T7 : Hash déterministe ────────────────────────────────
    total += 1
    question = "prix du Bitcoin ce mois"
    terms1 = build_enriched_terms(
        terms=[
            ("prix", TermCategory.BUSINESS_TERM),
            ("Bitcoin", TermCategory.ENTITY),
            ("ce mois", TermCategory.TIME_PERIOD),
        ],
        question=question,
    )
    terms2 = build_enriched_terms(
        terms=[
            ("prix", TermCategory.BUSINESS_TERM),
            ("Bitcoin", TermCategory.ENTITY),
            ("ce mois", TermCategory.TIME_PERIOD),
        ],
        question=question,
    )
    r1 = resolver.resolve(terms1)
    r2 = resolver.resolve(terms2)
    e1 = enricher.enrich(r1)
    e2 = enricher.enrich(r2)
    c1 = builder.build(e1, raw_question=question)
    c2 = builder.build(e2, raw_question=question)
    print(f"\n{'='*70}")
    print("  T7 — Hash déterministe")
    print(f"{'='*70}")
    print(f"  hash1 = {c1.context_hash}")
    print(f"  hash2 = {c2.context_hash}")

    assert c1.context_hash == c2.context_hash, \
        f"Hash devrait être identique : {c1.context_hash} != {c2.context_hash}"
    passed += 1

    # ── T8 : JSON sérialisation complète ──────────────────────
    total += 1
    question = "quel est le sentiment autour de Solana ce mois"
    terms = build_enriched_terms(
        terms=[
            ("sentiment", TermCategory.BUSINESS_TERM),
            ("Solana", TermCategory.ENTITY),
            ("ce mois", TermCategory.TIME_PERIOD),
        ],
        question=question,
    )
    resolved = resolver.resolve(terms)
    enriched = enricher.enrich(resolved)
    ctx = builder.build(enriched, raw_question=question, pipeline_confidence=0.9)

    json_str = ctx.to_json()
    parsed = json.loads(json_str)
    print(f"\n{'='*70}")
    print("  T8 — JSON sérialisation")
    print(f"{'='*70}")
    print(f"\n{json_str}")

    # Vérifier la structure
    assert isinstance(parsed["tables"], list)
    assert isinstance(parsed["entity_filters"], list)
    assert isinstance(parsed["metrics"], list)
    assert isinstance(parsed["columns"], list)
    assert isinstance(parsed["time_filters"], list)
    assert isinstance(parsed["implicit_conditions"], list)
    assert isinstance(parsed["generation_guidelines"], list)
    assert isinstance(parsed["needs_clarification"], bool)
    assert isinstance(parsed["context_hash"], str)
    assert len(parsed["context_hash"]) == 16
    passed += 1

    # ── Résumé ────────────────────────────────────────────────
    print(f"\n{'='*70}")
    if passed == total:
        print(f"  ✅ {passed}/{total} tests passent !")
    else:
        print(f"  ⚠️  {passed}/{total} tests passent")
    print(f"{'='*70}")

    driver.close()


if __name__ == "__main__":
    main()