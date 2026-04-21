"""
scripts/test_extraction.py
SP1-46 — Tests complets du pipeline d'extraction sémantique.

3 niveaux de tests :
  A. Pré-traitement (preprocessor) — correction ortho, synonymes, normalisation
  B. Validation (validator)        — vérif KG, recatégorisation, anti-hallucination
  C. Pipeline complet (extractor)  — bout en bout avec LLM

Usage :
    python scripts/test_extraction.py              # tout
    python scripts/test_extraction.py --preprocess  # pré-traitement seul
    python scripts/test_extraction.py --validate    # validation seule
    python scripts/test_extraction.py --pipeline    # pipeline complet
"""

import json
import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s — %(levelname)s — %(message)s",
)

from app.semantic.prompts import KGVocabulary, load_kg_vocabulary
from app.semantic.preprocessor import Preprocessor
from app.semantic.validator import ExtractionValidator
from app.semantic.schemas import (
    ExtractedTerms,
    EnrichedTerms,
    TermCategory,
    ResolutionStatus,
)
from app.db.neo4j import neo4j_driver


# ═══════════════════════════════════════════════════════════════
# A. TESTS PRÉ-TRAITEMENT
# ═══════════════════════════════════════════════════════════════

PREPROCESS_TESTS = [
    # (question brute, corrections attendues : [(original, corrigé, type)])
    {
        "question": "quelle est la performnace de solana ce mois",
        "expect_corrections": True,
        "description": "Faute de frappe — 'performnace' → terme KG proche",
    },
    {
        "question": "montre le prix du Bitcoin aujourd'hui",
        "expect_corrections": False,
        "description": "Pas de faute — aucune correction attendue",
    },
    {
        "question": "compare la volatlité du BTC et ETH",
        "expect_corrections": True,
        "description": "Faute sur 'volatlité' → 'volatilité'",
    },
    {
        "question": "quel est le   prix   du bitcoin   ce  mois",
        "expect_corrections": False,
        "description": "Espaces multiples — normalisation uniquement",
    },
    {
        "question": "montre le rendment du Bitcoin",
        "expect_corrections": True,
        "description": "Faute sur 'rendment' → terme KG proche",
    },
    {
        "question": "analyse le marché crypto",
        "expect_corrections": False,
        "description": "'marché' est un accent légitime, pas une faute",
    },
    {
        "question": "montre le cours du Bitcoin ce mois",
        "expect_corrections": True,
        "description": "Synonyme multi-mots → canonicalisation réelle",
    },
    {
        "question": "compare BTC et ETH",
        "expect_corrections": True,
        "description": "Alias/synonymes courts",
    },
]


def run_preprocess_tests(vocab: KGVocabulary) -> dict:
    """Teste le pré-traitement."""
    print("\n" + "=" * 65)
    print("  A. TESTS PRÉ-TRAITEMENT (preprocessor.py)")
    print("=" * 65)

    pp = Preprocessor(vocab)
    stats = {"ok": 0, "fail": 0}

    for i, test in enumerate(PREPROCESS_TESTS, 1):
        result = pp.preprocess(test["question"])

        print(f"\n{'─' * 65}")
        print(f"  [{i}] {test['description']}")
        print(f"  Question  : {test['question']}")
        print(f"  Corrigée  : {result.corrected_question}")
        print(f"  Corrections: {len(result.corrections)}")

        for c in result.corrections:
            print(f"    → '{c.original}' → '{c.corrected}' "
                  f"({c.correction_type.value}, {c.confidence:.0%})")

        # Vérifier si le résultat est conforme à l'attente
        got_corrections = result.is_corrected
        expected = test["expect_corrections"]

        if got_corrections == expected:
            print(f"  ✅ OK")
            stats["ok"] += 1
        else:
            print(f"  ❌ FAIL — attendu corrections={expected}, obtenu={got_corrections}")
            stats["fail"] += 1

    return stats


# ═══════════════════════════════════════════════════════════════
# B. TESTS VALIDATION
# ═══════════════════════════════════════════════════════════════

VALIDATION_TESTS = [
    {
        "description": "Terme KG exact — resolved",
        "extracted": ExtractedTerms(
            raw_question="test",
            business_terms=["prix Bitcoin"],
            entities=["Bitcoin"],
            time_periods=["ce mois"],
        ),
        "expect_status": {"prix Bitcoin": "resolved", "Bitcoin": "resolved", "ce mois": "resolved"},
    },
    {
        "description": "Metric classé en business_term → recatégorisation",
        "extracted": ExtractedTerms(
            raw_question="test",
            business_terms=["moyenne_mobile_7j"],
            entities=["Ethereum"],
        ),
        "expect_recategorized": "moyenne_mobile_7j",
    },
    {
        "description": "Terme domaine absent du KG → plausible_but_new (pas supprimé)",
        "extracted": ExtractedTerms(
            raw_question="test",
            business_terms=["fear and greed"],
            entities=["Bitcoin"],
        ),
        "expect_status": {"fear and greed": "plausible_but_new", "Bitcoin": "resolved"},
    },
    {
        "description": "Terme totalement incohérent → invalid",
        "extracted": ExtractedTerms(
            raw_question="test",
            business_terms=["recette de gâteau"],
            entities=["Bitcoin"],
        ),
        "expect_status": {"recette de gâteau": "invalid"},
    },
    {
        "description": "Time period non canonique → plausible_but_new",
        "extracted": ExtractedTerms(
            raw_question="test",
            time_periods=["été 2024"],
        ),
        "expect_status": {"été 2024": "plausible_but_new"},
    },
    {
        "description": "'comparaison' comme metric → plausible ou invalid (pas dans KG)",
        "extracted": ExtractedTerms(
            raw_question="test",
            business_terms=["prix Bitcoin"],
            metrics=["comparaison"],
            entities=["Bitcoin"],
        ),
        "expect_not_resolved": "comparaison",
    },
    {
        "description": "Terme unresolved domaine → promu en plausible_but_new",
        "extracted": ExtractedTerms(
            raw_question="test",
            entities=["Bitcoin"],
            unresolved_terms=["indicateur RSI"],
        ),
        "expect_promoted": "indicateur RSI",
    },
    {
        "description": "Doublon — même terme dans deux catégories",
        "extracted": ExtractedTerms(
            raw_question="test",
            business_terms=["Bitcoin"],
            entities=["Bitcoin"],
        ),
        "expect_dedup": True,
    },
]


def run_validation_tests(vocab: KGVocabulary) -> dict:
    """Teste la validation sémantique."""
    print("\n" + "=" * 65)
    print("  B. TESTS VALIDATION SÉMANTIQUE (validator.py)")
    print("=" * 65)

    validator = ExtractionValidator(vocab)
    stats = {"ok": 0, "fail": 0}

    for i, test in enumerate(VALIDATION_TESTS, 1):
        enriched = validator.validate(test["extracted"])

        print(f"\n{'─' * 65}")
        print(f"  [{i}] {test['description']}")
        for t in enriched.terms:
            print(f"    {t.text:30s} │ {t.category.value:15s} │ {t.resolution_status.value:20s} │ {t.confidence:.0%}")

        for ch in enriched.validation_changes:
            if ch.action not in ("kept",):
                print(f"    → {ch.action}: '{ch.term}' — {ch.reason}")

        print(f"  Confiance : {enriched.pipeline_confidence:.0%}")
        print(f"  Candidates KG : {[t.text for t in enriched.candidate_terms()]}")

        ok = True

        # Vérifier les statuts attendus
        if "expect_status" in test:
            for term_text, expected_status in test["expect_status"].items():
                matching = [t for t in enriched.terms if t.text.lower() == term_text.lower()]
                if not matching:
                    print(f"  ❌ Terme '{term_text}' non trouvé dans les résultats")
                    ok = False
                elif matching[0].resolution_status.value != expected_status:
                    print(f"  ❌ '{term_text}' : attendu {expected_status}, obtenu {matching[0].resolution_status.value}")
                    ok = False

        # Recatégorisation
        if "expect_recategorized" in test:
            recat = [ch for ch in enriched.validation_changes if ch.action == "recategorized"]
            found = any(
                test["expect_recategorized"].lower().replace("_", " ") in ch.term.lower().replace("_", " ")
                for ch in recat
            )
            if not found:
                print(f"  ❌ Recatégorisation manquante pour '{test['expect_recategorized']}'")
                ok = False

        # Terme non résolu qui ne doit pas être resolved
        if "expect_not_resolved" in test:
            term_name = test["expect_not_resolved"]
            matching = [t for t in enriched.terms if t.text.lower() == term_name.lower()]
            if matching and matching[0].is_resolved():
                print(f"  ❌ '{term_name}' ne devrait pas être resolved")
                ok = False

        # Terme unresolved promu
        if "expect_promoted" in test:
            promoted = [ch for ch in enriched.validation_changes if ch.action == "promoted"]
            if not promoted:
                # Aussi vérifier si le terme est dans terms avec plausible status
                plausible = [t for t in enriched.terms
                             if test["expect_promoted"].lower() in t.text.lower()
                             and t.resolution_status == ResolutionStatus.PLAUSIBLE_BUT_NEW]
                if not plausible:
                    print(f"  ❌ Terme '{test['expect_promoted']}' non promu")
                    ok = False

        if ok:
            print(f"  ✅ OK")
            stats["ok"] += 1
        else:
            stats["fail"] += 1

    return stats


# ═══════════════════════════════════════════════════════════════
# C. TESTS PIPELINE COMPLET
# ═══════════════════════════════════════════════════════════════

PIPELINE_TESTS = [
    "compare le prix du Bitcoin et de l'Ethereum avec le sentiment crypto sur les 90 derniers jours",
    "montre l'évolution du taux Fed, du VIX et du S&P 500 cette année",
    "quelle est la moyenne mobile 30 jours du Bitcoin sur 1 an",
    "compare la volatilité de Solana et de Cardano ce trimestre",
    "résume les articles Bloomberg sur Ethereum ce mois",
    "montre la variation journalière et le range journalier du BTC aujourd'hui",
    "analyse la relation entre le prix du Bitcoin et l'inflation sur les 30 derniers jours",
    "calcule la corrélation entre le prix du Bitcoin et le sentiment crypto",
    "résume les articles Reuters et CoinDesk sur le Bitcoin et l'Ethereum",
    "analyse le momentum du Bitcoin ce mois",
    "prédis le prix futur des NFTs et leur adoption mondiale",
    "compare BTC, ETH et SOL sur 30 derniers jours avec leur volume",
    "montre FEDFUNDS, CPIAUCSL et GDP sur 1 an",
    "montre le prix du Bitcoin pendant l'été 2024",
    "compare la market cap du Bitcoin et de l'Ethereum sur 1 an",
    "quel est le sentiment des articles Reuters sur le Bitcoin",
    "donne la moyenne mobile 7 jours et la moyenne mobile 30 jours de l'Ethereum",
    "résume les articles du Financial Times sur le Bitcoin",
    "est-ce que le prix du Bitcoin a suivi le même mouvement que le sentiment crypto ce trimestre",
    "analyse le marché crypto",
    # Tests avec fautes de frappe
    "quelle est la performnace de solana ce mois",
    "compare la volatlité du Bitcoin et Ethereum",
]


def print_enriched(i: int, question: str, enriched: EnrichedTerms, elapsed: float) -> None:
    """Affiche un résultat enrichi."""
    print(f"\n{'─' * 65}")
    print(f"  [{i}] {question}")
    print(f"{'─' * 65}")

    # Corrections pré-traitement
    if enriched.preprocessing and enriched.preprocessing.is_corrected:
        print(f"  🔧 Question corrigée : {enriched.corrected_question}")
        for c in enriched.preprocessing.corrections:
            print(f"     '{c.original}' → '{c.corrected}' ({c.correction_type.value}, {c.confidence:.0%})")

    # Termes par catégorie
    for cat in TermCategory:
        if cat == TermCategory.UNKNOWN:
            continue
        cat_terms = [t for t in enriched.terms if t.category == cat]
        if cat_terms:
            label = cat.value
            terms_str = ", ".join(f"{t.text} ({t.confidence:.0%})" for t in cat_terms)
            print(f"  {label:15s}: {terms_str}")

    # Unresolved
    if enriched.unresolved_terms:
        print(f"  {'Unresolved':15s}: {enriched.unresolved_terms}")

    # Validation changes
    recats = [ch for ch in enriched.validation_changes if ch.action == "recategorized"]
    removed = [ch for ch in enriched.validation_changes if ch.action == "removed"]
    if recats:
        print(f"  🔀 Recatégorisés : {[ch.term for ch in recats]}")
    if removed:
        print(f"  🗑  Supprimés     : {[ch.term for ch in removed]}")

    # Status
    print(f"  📊 Confiance pipeline : {enriched.pipeline_confidence:.0%}")
    print(f"  ⏱  Temps : {elapsed:.2f}s")

    if enriched.needs_clarification:
        print(f"  ⚠ Clarification nécessaire")
    elif enriched.is_empty():
        print(f"  ❌ Aucun terme extrait")
    else:
        print(f"  ✅ OK")


def run_pipeline_tests() -> dict:
    """Teste le pipeline complet avec le LLM."""
    print("\n" + "=" * 65)
    print("  C. TESTS PIPELINE COMPLET (preprocess → LLM → validate)")
    print("=" * 65)

    from app.semantic.extractor import BusinessTermsExtractor

    try:
        extractor = BusinessTermsExtractor(neo4j_driver)
        print(f"\n✅ Pipeline initialisé — modèle : {extractor._model}")
    except ValueError as e:
        print(f"\n❌ {e}")
        return {"ok": 0, "fail": 0}

    stats = {"ok": 0, "clarification": 0, "empty": 0}

    for i, question in enumerate(PIPELINE_TESTS, 1):
        start = time.time()
        enriched = extractor.extract(question)
        elapsed = time.time() - start

        print_enriched(i, question, enriched, elapsed)

        if enriched.needs_clarification:
            stats["clarification"] += 1
        elif enriched.is_empty():
            stats["empty"] += 1
        else:
            stats["ok"] += 1

    return stats


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    args = set(sys.argv[1:])
    run_all = not args or args == {"--all"}

    # Charger le vocabulaire KG
    print("Chargement du vocabulaire KG...")
    vocab = load_kg_vocabulary(neo4j_driver)
    print(f"✅ {len(vocab.business_terms)} BT, {len(vocab.entities)} ENT, "
          f"{len(vocab.time_periods)} TP, {len(vocab.metrics)} MET, "
          f"{len(vocab.synonyms)} SYN\n")

    all_stats = {}

    # A. Pré-traitement
    if run_all or "--preprocess" in args:
        pp_stats = run_preprocess_tests(vocab)
        all_stats["preprocess"] = pp_stats

    # B. Validation
    if run_all or "--validate" in args:
        val_stats = run_validation_tests(vocab)
        all_stats["validate"] = val_stats

    # C. Pipeline complet
    if run_all or "--pipeline" in args:
        pipe_stats = run_pipeline_tests()
        all_stats["pipeline"] = pipe_stats

    # ── Résumé final ──────────────────────────────────────────
    print(f"\n{'═' * 65}")
    print(f"  RÉSUMÉ FINAL")
    print(f"{'═' * 65}")

    if "preprocess" in all_stats:
        s = all_stats["preprocess"]
        print(f"  A. Pré-traitement  : ✅ {s['ok']} OK  ❌ {s['fail']} FAIL")

    if "validate" in all_stats:
        s = all_stats["validate"]
        print(f"  B. Validation      : ✅ {s['ok']} OK  ❌ {s['fail']} FAIL")

    if "pipeline" in all_stats:
        s = all_stats["pipeline"]
        total = s["ok"] + s["clarification"] + s["empty"]
        print(f"  C. Pipeline ({total} questions)")
        print(f"     ✅ Extractions réussies   : {s['ok']}")
        print(f"     ⚠ Clarification requise  : {s['clarification']}")
        print(f"     ❌ Résultats vides        : {s['empty']}")

    print(f"{'═' * 65}")


if __name__ == "__main__":
    main()