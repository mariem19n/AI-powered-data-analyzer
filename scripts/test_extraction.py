"""
scripts/test_extraction.py
SP1-46 — Tests de l'extraction des termes métier.

Lance 10 questions de test et affiche les résultats structurés.

Usage :
    python scripts/test_extraction.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.semantic.extractor import BusinessTermsExtractor

# ─── Questions de test ────────────────────────────────────────
TEST_QUESTIONS = [
    # Cas simples — une entité, un terme
    #"montre-moi le prix du Bitcoin",
    #"quel est le volume Ethereum aujourd'hui",

    # Cas avec période temporelle
    "compare le prix Bitcoin et le sentiment crypto sur les 30 derniers jours",
    "quel est le taux Fed ce trimestre",

    # Cas multi-termes
    "montre le VIX et le S&P 500 en 2024",
    "compare la volatilité Bitcoin et Ethereum ce mois",

    # Cas avec métrique calculée
    "quelle est la moyenne mobile 7 jours du Bitcoin",
    "montre la corrélation entre le prix et le sentiment crypto",

    # Cas avec source média
    "résume les articles Reuters sur Bitcoin",

    # Cas ambigu / terme inconnu
    "analyse le potentiel de croissance des NFTs",
]


def print_result(i: int, question: str, result) -> None:
    print(f"\n{'─'*65}")
    print(f"  [{i}] {question}")
    print(f"{'─'*65}")
    print(f"  business_terms   : {result.business_terms}")
    print(f"  entities         : {result.entities}")
    print(f"  time_periods     : {result.time_periods}")
    print(f"  metrics          : {result.metrics}")
    print(f"  unresolved_terms : {result.unresolved_terms}")
    print(f"  needs_clarif.    : {result.needs_clarification}")

    # Indicateur qualité
    if result.is_empty() and not result.needs_clarification:
        print(f"  ⚠ ATTENTION : aucun terme extrait sans clarification")
    elif result.needs_clarification:
        print(f"  ⚠ Clarification nécessaire")
    else:
        print(f"  ✅ OK")


def main():
    print("=" * 65)
    print("  SP1-46 — Test extraction termes métier")
    print("=" * 65)

    try:
        extractor = BusinessTermsExtractor()
        print(f"✅ Extracteur initialisé — modèle : {extractor._model}\n")
    except ValueError as e:
        print(f"❌ {e}")
        return

    stats = {"ok": 0, "clarification": 0, "empty": 0}

    for i, question in enumerate(TEST_QUESTIONS, 1):
        result = extractor.extract(question)
        print_result(i, question, result)

        if result.is_empty():
            stats["empty"] += 1
        elif result.needs_clarification:
            stats["clarification"] += 1
        else:
            stats["ok"] += 1

    print(f"\n{'═'*65}")
    print(f"  RÉSUMÉ — {len(TEST_QUESTIONS)} questions testées")
    print(f"{'═'*65}")
    print(f"  ✅ Extractions réussies   : {stats['ok']}")
    print(f"  ⚠ Clarification requise  : {stats['clarification']}")
    print(f"  ❌ Résultats vides        : {stats['empty']}")
    print(f"{'═'*65}")


if __name__ == "__main__":
    main()
