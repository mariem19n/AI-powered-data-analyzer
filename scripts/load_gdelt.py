"""
Script de chargement — GDELT → PostgreSQL
Récupère les articles et le sentiment médiatique liés aux cryptomonnaies
et aux événements macro-économiques via l'API GDELT DOC 2.0.

Stratégie à deux niveaux :
  Niveau 1 — crypto_direct : keywords spécifiques aux cryptos
  Niveau 2 — macro         : keywords macro qui influencent les cryptos indirectement

NOTE IMPORTANTE sur les thèmes GDELT :
  La library gdeltdoc supporte uniquement keyword=, pas theme=.
  Les thèmes GDELT (ECON_CRYPTOCURRENCY, etc.) ne sont accessibles qu'via
  l'API BigQuery de GDELT. On utilise donc des keywords sémantiquement précis
  qui couvrent le même périmètre, et on stocke le label de thème dans la
  colonne theme pour nos analyses internes.

Usage :
    pip install gdeltdoc psycopg2-binary python-dotenv
    python scripts/load_gdelt.py
"""

import os
import time
import psycopg2
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

try:
    from gdeltdoc import GdeltDoc, Filters
except ImportError:
    print("❌ Installe gdeltdoc d'abord : pip install gdeltdoc")
    exit(1)

# ─── Configuration PostgreSQL ─────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", "5433"))
DB_NAME     = os.getenv("POSTGRES_DB", "analyzer_db")
DB_USER     = os.getenv("POSTGRES_USER", "analyzer")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "analyzer_pg_123")

# ─── Stratégie à deux niveaux ────────────────────────────────
#
# Chaque entrée : (keyword, theme_label, crypto_id)
#   keyword    → terme envoyé à l'API GDELT (keyword=)
#   theme      → label interne stocké dans la colonne `theme` pour nos analyses
#   crypto_id  → BTC/ETH/etc. si spécifique à une crypto, None si global

KEYWORD_GROUPS = {
    # ── Niveau 1 : Crypto direct ──────────────────────────────
    # Articles qui mentionnent explicitement les cryptos
    "crypto_direct": [
        ("Bitcoin",                    "ECON_BITCOINS",        "BTC"),
        ("Ethereum cryptocurrency",    "ECON_CRYPTOCURRENCY",  "ETH"),
        ("crypto regulation",          "ECON_CRYPTOCURRENCY",  None),
        ("cryptocurrency market",      "ECON_CRYPTOCURRENCY",  None),
        ("Bitcoin ETF",                "ECON_BITCOINS",        "BTC"),
        ("stablecoin",                 "ECON_CRYPTOCURRENCY",  None),
        ("DeFi decentralized finance", "ECON_CRYPTOCURRENCY",  None),
        ("crypto exchange hack",       "ECON_CRYPTOCURRENCY",  None),
        ("SEC cryptocurrency",         "ECON_CRYPTOCURRENCY",  None),
    ],

    # ── Niveau 2 : Macro / Régulation ─────────────────────────
    # Ces événements font bouger le marché crypto sans mentionner Bitcoin
    "macro": [
        ("Federal Reserve interest rate", "ECON_CENTRALBANK",         None),
        ("inflation CPI data",            "ECON_INFLATION",           None),
        ("stock market crash",            "ECON_STOCKMARKET",         None),
        ("financial regulation SEC",      "GOV_REGULATION_FINANCIAL", None),
        ("US dollar monetary policy",     "ECON_CENTRALBANK",         None),
    ],
}

# Période : 3 derniers mois (limite de l'API GDELT DOC gratuite)
DAYS_BACK         = 90
API_DELAY_SECONDS = 12   # Délai entre requêtes (GDELT ~5 req/min)
RETRY_DELAY       = 30   # Délai avant retry en cas de rate limit
MAX_RETRIES       = 3


# ─── Connexion DB ─────────────────────────────────────────────

def get_db_connection():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    conn.autocommit = False
    return conn


# ─── Retry wrapper ────────────────────────────────────────────

def fetch_with_retry(fn, label):
    """
    Exécute fn() avec retry automatique en cas de timeout réseau.
    Retourne le résultat ou None si tous les essais échouent.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            err = str(e).lower()
            if "timeout" in err or "retries exceeded" in err or "connection" in err or "ratelimit" in err or "rate" in err or type(e).__name__ == "RateLimitError":
                if attempt < MAX_RETRIES:
                    print(f"    ⏳ Timeout (tentative {attempt}/{MAX_RETRIES}), "
                          f"retry dans {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"    ❌ Échec après {MAX_RETRIES} tentatives pour '{label}'")
                    return None
            else:
                print(f"    ⚠ Erreur pour '{label}': {e}")
                return None


# ─── Récupération du tone via timeline_search ─────────────────
#
# article_search() retourne les métadonnées des articles mais PAS les scores
# de sentiment — d'où les NULLs dans l'ancienne version.
#
# timeline_search("timelinetone") retourne avg_tone, avg_positive,
# avg_negative, avg_polarity PAR DATE pour un keyword donné.
# On joint ensuite ces scores aux articles par date.

def fetch_tone_timeline(keyword, start_date, end_date):
    """
    Récupère la timeline de tonalité pour un keyword.
    Retourne un dict {date_str: {avg_tone, avg_positive, avg_negative, avg_polarity}}
    """
    def _fetch():
        f = Filters(keyword=keyword, start_date=start_date, end_date=end_date)
        return GdeltDoc().timeline_search("timelinetone", f)

    df = fetch_with_retry(_fetch, label=f"tone:{keyword}")
    if df is None or df.empty:
        return {}

    tone_by_date = {}
    for _, row in df.iterrows():
        # Le nom de la colonne date varie selon la version de gdeltdoc
        date_col = next((c for c in ["datetime", "Date", "date"] if c in row.index), None)
        if not date_col:
            continue

        raw_date = str(row[date_col])[:10]  # YYYY-MM-DD

        def get_val(keys):
            for k in keys:
                if k in row.index and row[k] is not None:
                    try:
                        return float(row[k])
                    except (ValueError, TypeError):
                        pass
            return None

        tone_by_date[raw_date] = {
            "avg_tone":     get_val(["Average Tone",   "avg_tone"]),
            "avg_positive": None,  # Non disponible dans timelinetone
            "avg_negative": None,  # Non disponible dans timelinetone
            "avg_polarity": None,  # Non disponible dans timelinetone
        }

    return tone_by_date


# ─── Récupération des articles ────────────────────────────────

def fetch_articles(keyword, start_date, end_date):
    """
    Récupère les articles pour un keyword.
    Retourne un DataFrame pandas ou None.
    """
    def _fetch():
        f = Filters(
            keyword=keyword,
            start_date=start_date,
            end_date=end_date,
            num_records=250,
        )
        return GdeltDoc().article_search(f)

    return fetch_with_retry(_fetch, label=f"articles:{keyword}")


# ─── Parsing de date ──────────────────────────────────────────

def parse_gdelt_date(raw):
    """Convertit une date GDELT (YYYYMMDDTHHMMSSZ ou YYYY-MM-DD) en str YYYY-MM-DD."""
    s = str(raw)
    if "T" in s:
        s = s.split("T")[0]
    s = s.replace("-", "")
    if len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


# ─── Insertion des articles ───────────────────────────────────

def insert_articles(cursor, keyword, theme, category, crypto_id, articles_df, tone_by_date):
    """
    Insère les articles dans fact_gdelt_events.
    Joint les scores de tone depuis tone_by_date par date.
    """
    if articles_df is None or articles_df.empty:
        return 0

    inserted = 0
    skipped  = 0

    for _, row in articles_df.iterrows():
        try:
            date_str = parse_gdelt_date(row.get("seendate", ""))
            if not date_str:
                skipped += 1
                continue

            url = str(row.get("url", ""))[:1000] if row.get("url") else None
            if not url:
                skipped += 1
                continue

            # Joindre le tone de la timeline par date
            tone_data = tone_by_date.get(date_str, {})

            cursor.execute("""
                INSERT INTO fact_gdelt_events (
                    date, title, url, source_domain, source_country, language,
                    tone, positive_score, negative_score, polarity,
                    theme, keyword, category, crypto_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
            """, (
                date_str,
                str(row.get("title", ""))[:500] if row.get("title") else None,
                url,
                str(row.get("domain", ""))[:200] if row.get("domain") else None,
                str(row.get("sourcecountry", ""))[:10] if row.get("sourcecountry") else None,
                str(row.get("language", ""))[:10] if row.get("language") else None,
                tone_data.get("avg_tone"),
                tone_data.get("avg_positive"),
                tone_data.get("avg_negative"),
                tone_data.get("avg_polarity"),
                theme,
                keyword,
                category,
                crypto_id,
            ))
            inserted += 1

        except Exception:
            skipped += 1
            continue

    if skipped > 0:
        print(f"    ⚠ {skipped} articles ignorés (URL manquante ou date invalide)")

    return inserted


# ─── Agrégation du sentiment quotidien ───────────────────────

def compute_daily_sentiment(cursor):
    """
    Recalcule agg_daily_sentiment depuis fact_gdelt_events.
    Agrège par date + theme (label interne).
    """
    print("\n📊 Calcul du sentiment quotidien agrégé...")

    cursor.execute("DELETE FROM agg_daily_sentiment;")

    cursor.execute("""
        INSERT INTO agg_daily_sentiment (
            date, keyword, article_count,
            avg_tone, avg_positive, avg_negative, avg_polarity
        )
        SELECT
            date,
            theme                                      AS keyword,
            COUNT(*)                                   AS article_count,
            ROUND(AVG(tone)::NUMERIC, 4)           AS avg_tone,
            ROUND(AVG(positive_score)::NUMERIC, 4) AS avg_positive,
            ROUND(AVG(negative_score)::NUMERIC, 4) AS avg_negative,
            ROUND(AVG(polarity)::NUMERIC, 4)       AS avg_polarity
        FROM fact_gdelt_events
        WHERE theme IS NOT NULL
        GROUP BY date, theme
        ORDER BY date, theme
        ON CONFLICT (date, keyword) DO UPDATE SET
            article_count = EXCLUDED.article_count,
            avg_tone      = EXCLUDED.avg_tone,
            avg_positive  = EXCLUDED.avg_positive,
            avg_negative  = EXCLUDED.avg_negative,
            avg_polarity  = EXCLUDED.avg_polarity
    """)

    cursor.execute("SELECT COUNT(*) FROM agg_daily_sentiment")
    count = cursor.fetchone()[0]
    print(f"  ✅ {count:,} agrégations de sentiment calculées")


# ─── Main ─────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  CHARGEMENT GDELT → PostgreSQL")
    print("  Stratégie keywords (2 niveaux : crypto_direct + macro)")
    print("=" * 65)

    end_date   = datetime.now()
    start_date = end_date - timedelta(days=DAYS_BACK)
    start_str  = start_date.strftime("%Y-%m-%d")
    end_str    = end_date.strftime("%Y-%m-%d")

    n_crypto = len(KEYWORD_GROUPS["crypto_direct"])
    n_macro  = len(KEYWORD_GROUPS["macro"])
    print(f"\n📅 Période  : {start_str} → {end_str} ({DAYS_BACK} jours)")
    print(f"📋 Keywords : {n_crypto} crypto_direct + {n_macro} macro\n")

    conn   = get_db_connection()
    cursor = conn.cursor()
    print("✅ Connecté à PostgreSQL\n")

    try:
        total_inserted = 0
        index          = 0

        for category, entries in KEYWORD_GROUPS.items():
            print(f"{'─'*65}")
            print(f"  Catégorie : {category.upper()}")
            print(f"{'─'*65}")

            for (keyword, theme, crypto_id) in entries:
                index += 1
                crypto_label = f"  crypto: {crypto_id}" if crypto_id else ""
                print(f"\n  [{index}] \"{keyword}\"  →  thème: {theme}{crypto_label}")

                # Étape 1 : timeline de tone (valeurs réelles de sentiment)
                print(f"      📈 Récupération timeline de tone...")
                tone_by_date = fetch_tone_timeline(keyword, start_str, end_str)
                print(f"      ✅ Tone disponible pour {len(tone_by_date)} jours")

                time.sleep(API_DELAY_SECONDS)

                # Étape 2 : articles
                print(f"      📰 Récupération articles...")
                articles_df = fetch_articles(keyword, start_str, end_str)

                if articles_df is not None and not articles_df.empty:
                    print(f"      ✅ {len(articles_df)} articles trouvés")
                    inserted = insert_articles(
                        cursor, keyword, theme, category, crypto_id,
                        articles_df, tone_by_date
                    )
                    conn.commit()
                    print(f"      ✅ {inserted} articles insérés")
                    total_inserted += inserted
                else:
                    print(f"      ⚠ Aucun article trouvé")

                time.sleep(API_DELAY_SECONDS)

        # ── Agrégation finale ──────────────────────────────────
        if total_inserted > 0:
            compute_daily_sentiment(cursor)
            conn.commit()

        # ── Résumé ────────────────────────────────────────────
        cursor.execute("""
            SELECT
                category,
                theme,
                COUNT(*)                         AS articles,
                ROUND(AVG(tone)::NUMERIC, 2)     AS avg_tone,
                COUNT(tone)                      AS with_tone
            FROM fact_gdelt_events
            GROUP BY category, theme
            ORDER BY category, articles DESC
        """)
        rows = cursor.fetchall()

        print(f"\n{'═'*75}")
        print(f"  RÉSUMÉ FINAL")
        print(f"{'═'*75}")
        print(f"{'Catégorie':<16} {'Thème':<30} {'Articles':<10} {'Tone moy.':<12} {'Avec tone'}")
        print("─" * 75)
        for cat, theme, count, tone, with_tone in rows:
            cat_str   = cat or "N/A"
            theme_str = theme or "N/A"
            tone_str  = f"{tone:+.2f}" if tone is not None else "N/A"
            print(f"{cat_str:<16} {theme_str:<30} {count:<10} {tone_str:<12} {with_tone}/{count}")

        print(f"\n✅ Total articles insérés : {total_inserted:,}")
        print(f"📅 Période couverte       : {start_str} → {end_str}")

        cursor.execute("SELECT COUNT(*) FROM fact_gdelt_events WHERE tone IS NOT NULL")
        not_null = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM fact_gdelt_events")
        total = cursor.fetchone()[0]
        pct   = int(not_null / total * 100) if total > 0 else 0
        print(f"📊 Articles avec tone     : {not_null}/{total} ({pct}%) "
              f"{'✅ OK' if pct > 50 else '⚠ Faible — normal pour certains keywords'}")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ Erreur : {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        cursor.close()
        conn.close()
        print("\n🔌 Connexion fermée.")


if __name__ == "__main__":
    main()