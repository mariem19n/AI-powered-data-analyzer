"""
scripts/test_resolver.py
Test du KG Resolver (SP1-48).

Prend des EnrichedTerms simulés et vérifie la résolution
via le Knowledge Graph Neo4j.

Usage :
    python scripts/test_resolver.py

Prérequis :
    - Neo4j doit tourner (docker-compose up -d)
    - Le KG doit être seedé (python scripts/setup_neo4j_schema.py)
    - .env configuré avec NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.db.neo4j import Neo4jDriver
from app.semantic.resolver import KGResolver, ResolvedContext
from app.semantic.schemas import (
    ClassifiedTerm,
    EnrichedTerms,
    TermCategory,
    ResolutionStatus,
    MatchMethod,
)


# ─── Helpers ──────────────────────────────────────────────────


def build_enriched(
    terms: list[tuple[str, TermCategory]],
    unresolved: list[str] | None = None,
    question: str = "",
) -> EnrichedTerms:
    """Construit un EnrichedTerms avec tous les termes RESOLVED."""
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
    )


def build_enriched_with_statuses(
    terms: list[tuple[str, TermCategory, ResolutionStatus]],
    unresolved: list[str] | None = None,
    question: str = "",
) -> EnrichedTerms:
    """Construit un EnrichedTerms avec contrôle du resolution_status."""
    classified = []
    for text, cat, status in terms:
        classified.append(ClassifiedTerm(
            text=text,
            category=cat,
            confidence=0.9 if status == ResolutionStatus.RESOLVED else 0.5,
            resolution_status=status,
            matched_by=(
                MatchMethod.EXACT
                if status == ResolutionStatus.RESOLVED
                else MatchMethod.NONE
            ),
        ))
    return EnrichedTerms(
        raw_question=question,
        corrected_question=question,
        terms=classified,
        unresolved_terms=unresolved or [],
    )


def print_context(ctx: ResolvedContext, label: str) -> None:
    """Affiche le contexte résolu."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    if ctx.entities:
        print("\n  📍 Entities:")
        for e in ctx.entities:
            print(
                f"     {e.name} ({e.entity_type}) → "
                f"{e.table}.{e.filter_column} = '{e.filter_value}'"
            )

    if ctx.metrics:
        print("\n  📊 Metrics:")
        for m in ctx.metrics:
            preview = m.formula[:80] + "..." if len(m.formula) > 80 else m.formula
            print(f"     {m.name} → {m.source_table}")
            print(f"       formula: {preview}")

    if ctx.business_terms:
        print("\n  💼 Business Terms:")
        for bt in ctx.business_terms:
            print(f"     {bt.name} → {bt.table}.{bt.column}")

    if ctx.time_periods:
        print("\n  📅 Time Periods:")
        for tp in ctx.time_periods:
            status = "✅" if tp.is_canonical else "⚠️ non-canonique"
            print(f"     {tp.name} {status}")
            if tp.filter_expression:
                print(f"       filter: {tp.filter_expression}")

    if ctx.analytic_gaps:
        print("\n  🔍 Analytic Gaps:")
        for ag in ctx.analytic_gaps:
            print(f"     → {ag}")

    if ctx.unknown_terms:
        print("\n  ❓ Unknown Terms:")
        for u in ctx.unknown_terms:
            print(f"     → {u}")

    print(f"\n  📋 Tables: {ctx.all_tables() or '(aucune)'}")

    if ctx.resolution_log:
        print("\n  📝 Log:")
        for log in ctx.resolution_log:
            print(f"     {log}")


# ─── Tests ────────────────────────────────────────────────────


def main():
    print("🔍 Test KG Resolver (SP1-48)")
    print("=" * 60)

    driver = Neo4jDriver()
    resolver = KGResolver(driver)

    passed = 0
    total = 0

    # ── T1 : Entity simple ────────────────────────────────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[("Bitcoin", TermCategory.ENTITY)],
        question="montre le prix du Bitcoin",
    ))
    print_context(ctx, "T1 — Entity simple : Bitcoin")
    assert len(ctx.entities) == 1
    assert ctx.entities[0].filter_value == "BTC"
    assert ctx.entities[0].table == "fact_crypto_daily"
    passed += 1

    # ── T2 : Multi-entities ───────────────────────────────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("Bitcoin", TermCategory.ENTITY),
            ("Ethereum", TermCategory.ENTITY),
        ],
        question="compare Bitcoin et Ethereum",
    ))
    print_context(ctx, "T2 — Multi-entities : Bitcoin + Ethereum")
    assert len(ctx.entities) == 2
    assert {e.filter_value for e in ctx.entities} == {"BTC", "ETH"}
    passed += 1

    # ── T3 : BusinessTerm → colonne ───────────────────────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("prix", TermCategory.BUSINESS_TERM),
            ("Bitcoin", TermCategory.ENTITY),
        ],
        question="montre le prix du Bitcoin",
    ))
    print_context(ctx, "T3 — BusinessTerm 'prix' + Entity 'Bitcoin'")
    assert len(ctx.business_terms) >= 1 or len(ctx.metrics) >= 1, \
        "prix devrait être résolu"
    passed += 1

    # ── T4 : Metric calculée ──────────────────────────────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("volatilite_30j", TermCategory.METRIC),
            ("Bitcoin", TermCategory.ENTITY),
        ],
        question="quelle est la volatilité 30j du Bitcoin",
    ))
    print_context(ctx, "T4 — Metric calculée : volatilite_30j")
    assert len(ctx.metrics) >= 1 or len(ctx.business_terms) >= 1, \
        "volatilite_30j devrait être résolu"
    passed += 1

    # ── T5 : TimePeriod canonique ─────────────────────────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("Bitcoin", TermCategory.ENTITY),
            ("ce mois", TermCategory.TIME_PERIOD),
        ],
        question="prix du Bitcoin ce mois",
    ))
    print_context(ctx, "T5 — TimePeriod canonique : 'ce mois'")
    assert len(ctx.time_periods) == 1
    assert ctx.time_periods[0].is_canonical
    assert ctx.time_periods[0].filter_expression != ""
    passed += 1

    # ── T6 : TimePeriod non canonique ─────────────────────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("Bitcoin", TermCategory.ENTITY),
            ("premier trimestre 2024", TermCategory.TIME_PERIOD),
        ],
        question="Bitcoin au premier trimestre 2024",
    ))
    print_context(ctx, "T6 — TimePeriod non canonique : 'premier trimestre 2024'")
    assert len(ctx.time_periods) == 1
    passed += 1

    # ── T7 : Macro indicator (2 tables différentes) ───────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("Federal Funds Rate", TermCategory.ENTITY),
            ("Bitcoin", TermCategory.ENTITY),
        ],
        question="impact du taux de la Fed sur Bitcoin",
    ))
    print_context(ctx, "T7 — Macro : Fed Funds Rate + Bitcoin")
    assert len(ctx.entities) == 2
    tables = {e.table for e in ctx.entities}
    assert len(tables) == 2, f"2 tables attendues, obtenu {tables}"
    passed += 1

    # ── T8 : analytic_gap — unresolved brut du LLM ───────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("Ethereum", TermCategory.ENTITY),
            ("Bitcoin", TermCategory.ENTITY),
        ],
        unresolved=["évolué de la même manière"],
        question="est-ce que Bitcoin et Ethereum ont évolué de la même manière",
    ))
    print_context(ctx, "T8 — Analytic gap : 'évolué de la même manière'")
    assert "évolué de la même manière" in ctx.analytic_gaps, \
        f"Attendu dans analytic_gaps, obtenu gaps={ctx.analytic_gaps}"
    assert len(ctx.unknown_terms) == 0
    passed += 1

    # ── T9 : unknown_term — terme INVALID ─────────────────────
    total += 1
    ctx = resolver.resolve(build_enriched_with_statuses(
        terms=[
            ("Bitcoin", TermCategory.ENTITY, ResolutionStatus.RESOLVED),
            ("xyzfoobar", TermCategory.UNKNOWN, ResolutionStatus.INVALID),
        ],
        question="xyzfoobar Bitcoin",
    ))
    print_context(ctx, "T9 — Unknown term : 'xyzfoobar' (INVALID)")
    assert "xyzfoobar" in ctx.unknown_terms, \
        f"Attendu dans unknown_terms, obtenu {ctx.unknown_terms}"
    assert len(ctx.analytic_gaps) == 0
    passed += 1

    # ── T10 : analytic_gap — terme PLAUSIBLE_BUT_NEW ──────────
    total += 1
    ctx = resolver.resolve(build_enriched_with_statuses(
        terms=[
            ("Bitcoin", TermCategory.ENTITY, ResolutionStatus.RESOLVED),
            ("momentum", TermCategory.BUSINESS_TERM, ResolutionStatus.PLAUSIBLE_BUT_NEW),
        ],
        question="quel est le momentum du Bitcoin",
    ))
    print_context(ctx, "T10 — Analytic gap : 'momentum' (PLAUSIBLE_BUT_NEW)")
    assert "momentum" in ctx.analytic_gaps, \
        f"Attendu dans analytic_gaps, obtenu {ctx.analytic_gaps}"
    assert len(ctx.unknown_terms) == 0
    passed += 1

    # ── T11 : analytic_gap — terme AMBIGUOUS ──────────────────
    total += 1
    ctx = resolver.resolve(build_enriched_with_statuses(
        terms=[
            ("Bitcoin", TermCategory.ENTITY, ResolutionStatus.RESOLVED),
            ("taux", TermCategory.BUSINESS_TERM, ResolutionStatus.AMBIGUOUS),
        ],
        question="quel est le taux du Bitcoin",
    ))
    print_context(ctx, "T11 — Analytic gap : 'taux' (AMBIGUOUS)")
    # AMBIGUOUS → analytic_gap, PAS unknown_term
    # L'Orchestrateur peut désambiguïser avec le contexte
    assert "taux" in ctx.analytic_gaps, \
        f"Attendu dans analytic_gaps, obtenu gaps={ctx.analytic_gaps}, unknown={ctx.unknown_terms}"
    assert "taux" not in ctx.unknown_terms
    passed += 1

    # ── T12 : Sentiment + Entity ──────────────────────────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("sentiment", TermCategory.BUSINESS_TERM),
            ("Ethereum", TermCategory.ENTITY),
        ],
        question="quel est le sentiment autour d'Ethereum",
    ))
    print_context(ctx, "T12 — Sentiment + Entity")
    assert len(ctx.entities) == 1
    passed += 1

    # ── T13 : Pipeline complet (tous les types) ──────────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("performance", TermCategory.BUSINESS_TERM),
            ("Solana", TermCategory.ENTITY),
            ("30 derniers jours", TermCategory.TIME_PERIOD),
        ],
        question="quelle est la performance de Solana sur les 30 derniers jours",
    ))
    print_context(ctx, "T13 — Pipeline complet : performance + Solana + 30j")
    assert len(ctx.entities) >= 1
    assert len(ctx.time_periods) >= 1
    passed += 1

    # ── T14 : Mix analytic_gaps + unknown_terms ───────────────
    total += 1
    ctx = resolver.resolve(build_enriched_with_statuses(
        terms=[
            ("Bitcoin", TermCategory.ENTITY, ResolutionStatus.RESOLVED),
            ("Ethereum", TermCategory.ENTITY, ResolutionStatus.RESOLVED),
            ("abc123garbage", TermCategory.UNKNOWN, ResolutionStatus.INVALID),
        ],
        unresolved=["évolué de la même manière", "impact"],
        question="impact de abc123garbage sur l'évolution de Bitcoin et Ethereum",
    ))
    print_context(ctx, "T14 — Mix : 2 analytic_gaps + 1 unknown")
    assert len(ctx.analytic_gaps) == 2, \
        f"Attendu 2 analytic_gaps, obtenu {ctx.analytic_gaps}"
    assert len(ctx.unknown_terms) == 1, \
        f"Attendu 1 unknown_term, obtenu {ctx.unknown_terms}"
    assert "abc123garbage" in ctx.unknown_terms
    assert "évolué de la même manière" in ctx.analytic_gaps
    assert "impact" in ctx.analytic_gaps
    passed += 1

    # ── T15 : Déduplication — même terme deux fois ────────────
    total += 1
    ctx = resolver.resolve(build_enriched(
        terms=[
            ("Bitcoin", TermCategory.ENTITY),
        ],
        unresolved=["impact", "impact"],
        question="impact impact Bitcoin",
    ))
    print_context(ctx, "T15 — Déduplication : 'impact' x2")
    assert ctx.analytic_gaps.count("impact") == 1, \
        f"Attendu 1 seul 'impact', obtenu {ctx.analytic_gaps}"
    passed += 1

    # ── T16 : Aucun terme (question vide / greeting) ─────────
    total += 1
    ctx = resolver.resolve(EnrichedTerms(
        raw_question="bonjour",
        corrected_question="bonjour",
    ))
    print_context(ctx, "T16 — Aucun terme (greeting)")
    assert ctx.is_empty()
    assert len(ctx.analytic_gaps) == 0
    assert len(ctx.unknown_terms) == 0
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
