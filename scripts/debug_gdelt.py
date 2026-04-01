"""
Script de diagnostic GDELT — affiche les erreurs exactes.
Lance ce script avant load_gdelt.py pour comprendre pourquoi certains
keywords échouent.
"""

from gdeltdoc import GdeltDoc, Filters
import traceback

TESTS = [
    "Bitcoin",
    "Bitcoin ETF",
    "cryptocurrency market",
    "Federal Reserve interest rate",
    "inflation CPI data",
]

START = "2026-01-01"
END   = "2026-03-27"

gd = GdeltDoc()

for keyword in TESTS:
    print(f"\n{'─'*60}")
    print(f"Keyword : \"{keyword}\"")

    # Test timeline tone
    print(f"  → timeline_search('timelinetone') :")
    try:
        f = Filters(keyword=keyword, start_date=START, end_date=END)
        df = gd.timeline_search("timelinetone", f)
        if df is None or df.empty:
            print(f"     ⚠ DataFrame vide ou None")
        else:
            print(f"     ✅ {len(df)} lignes — colonnes : {list(df.columns)}")
            print(f"     Exemple : {df.iloc[0].to_dict()}")
    except Exception as e:
        print(f"     ❌ ERREUR EXACTE : {type(e).__name__}: {e}")
        traceback.print_exc()

    # Test article_search
    print(f"  → article_search() :")
    try:
        f = Filters(keyword=keyword, start_date=START, end_date=END, num_records=10)
        df = gd.article_search(f)
        if df is None or df.empty:
            print(f"     ⚠ DataFrame vide ou None")
        else:
            print(f"     ✅ {len(df)} articles — colonnes : {list(df.columns)}")
    except Exception as e:
        print(f"     ❌ ERREUR EXACTE : {type(e).__name__}: {e}")
        traceback.print_exc()
