"""
Script de chargement historique — Alpha Vantage → PostgreSQL
Télécharge l'historique COMPLET de 10 cryptomonnaies et les insère
dans la table partitionnée fact_crypto_daily.

Usage :
    python scripts/load_history.py

Exécuter UNE SEULE FOIS lors du déploiement initial.

"""

import os
import sys
import time
import requests
import psycopg2
from datetime import datetime
from pathlib import Path

# Charger les variables depuis .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ─── Configuration ────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5433"))
DB_NAME = os.getenv("POSTGRES_DB", "analyzer_db")
DB_USER = os.getenv("POSTGRES_USER", "analyzer")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "analyzer_pg_123")

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"

# Délai entre chaque appel API (en secondes) pour respecter le rate limit
API_DELAY_SECONDS = 30


def get_db_connection():
    """Établit la connexion à PostgreSQL."""
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    conn.autocommit = False
    return conn


def get_cryptos(cursor):
    """Récupère la liste des cryptos actives depuis dim_crypto."""
    cursor.execute("""
        SELECT crypto_id, symbol, name, alpha_vantage_key
        FROM dim_crypto
        WHERE is_active = TRUE
        ORDER BY crypto_id
    """)
    return cursor.fetchall()


def fetch_crypto_history(symbol, alpha_vantage_key):
    """
    Récupère tout l'historique OHLCV d'une crypto depuis Alpha Vantage.
    Une seule requête API avec l'historique complet.
    """
    params = {
        "function": "DIGITAL_CURRENCY_DAILY",
        "symbol": alpha_vantage_key,
        "market": "USD",
        "apikey": ALPHA_VANTAGE_API_KEY,
    }

    response = requests.get(ALPHA_VANTAGE_BASE_URL, params=params, timeout=60)

    if response.status_code != 200:
        print(f"  ❌ Erreur HTTP {response.status_code}")
        return None

    data = response.json()

    # Vérifier les erreurs API
    if "Error Message" in data:
        print(f"  ❌ Erreur API : {data['Error Message']}")
        return None

    if "Note" in data:
        print(f"  ⚠️ Rate limit atteint : {data['Note']}")
        print(f"     Attends quelques minutes et relance le script.")
        return None

    if "Information" in data:
        print(f"  ⚠️ Info API : {data['Information']}")
        return None

    time_series = data.get("Time Series (Digital Currency Daily)", {})
    if not time_series:
        print(f"  ❌ Aucune donnée pour {symbol}")
        return None

    return time_series


def insert_crypto_data(cursor, crypto_id, symbol, time_series):
    """
    Insère les données OHLCV dans fact_crypto_daily.
    Utilise ON CONFLICT DO NOTHING pour éviter les doublons.
    """
    inserted = 0
    skipped = 0

    for date_str, values in time_series.items():
        try:
            open_usd = float(values.get("1. open", 0))
            high_usd = float(values.get("2. high", 0))
            low_usd = float(values.get("3. low", 0))
            close_usd = float(values.get("4. close", 0))
            volume = float(values.get("5. volume", 0))

            # Ignorer les lignes avec des prix à 0
            if close_usd == 0:
                skipped += 1
                continue

            cursor.execute("""
                INSERT INTO fact_crypto_daily
                    (crypto_id, symbol, date, open_usd, high_usd, low_usd, close_usd, volume, source)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, 'alpha_vantage')
                ON CONFLICT (crypto_id, date) DO NOTHING
            """, (crypto_id, symbol, date_str, open_usd, high_usd, low_usd, close_usd, volume))
            inserted += 1

        except Exception as e:
            skipped += 1
            if skipped <= 3:
                print(f"    ⚠ Ligne ignorée ({date_str}) : {e}")

    return inserted, skipped


def compute_daily_metrics(cursor):
    """
    Calcule les métriques journalières depuis fact_crypto_daily
    et les insère dans stg_daily_metrics.
    """
    print("\n📊 Calcul des métriques journalières...")

    cursor.execute("DELETE FROM stg_daily_metrics;")

    cursor.execute("""
        INSERT INTO stg_daily_metrics (
            crypto_id, symbol, date, close_usd, prev_close_usd,
            daily_change_pct, daily_range_usd, volatility_pct, volume
        )
        SELECT
            f.crypto_id,
            f.symbol,
            f.date,
            f.close_usd,
            LAG(f.close_usd) OVER (PARTITION BY f.crypto_id ORDER BY f.date) AS prev_close_usd,
            CASE
                WHEN LAG(f.close_usd) OVER (PARTITION BY f.crypto_id ORDER BY f.date) > 0
                THEN ROUND(
                    ((f.close_usd - LAG(f.close_usd) OVER (PARTITION BY f.crypto_id ORDER BY f.date))
                    / LAG(f.close_usd) OVER (PARTITION BY f.crypto_id ORDER BY f.date) * 100)::NUMERIC, 4)
                ELSE NULL
            END AS daily_change_pct,
            (f.high_usd - f.low_usd) AS daily_range_usd,
            CASE
                WHEN f.close_usd > 0
                THEN ROUND(((f.high_usd - f.low_usd) / f.close_usd * 100)::NUMERIC, 4)
                ELSE NULL
            END AS volatility_pct,
            f.volume
        FROM fact_crypto_daily f
        ORDER BY f.crypto_id, f.date
        ON CONFLICT (crypto_id, date) DO NOTHING
    """)

    cursor.execute("SELECT COUNT(*) FROM stg_daily_metrics")
    count = cursor.fetchone()[0]
    print(f"  ✅ {count:,} métriques journalières calculées")


def compute_monthly_aggregates(cursor):
    """
    Calcule les agrégations mensuelles et les insère dans agg_monthly_crypto.
    """
    print("\n📊 Calcul des agrégations mensuelles...")

    cursor.execute("DELETE FROM agg_monthly_crypto;")

    cursor.execute("""
        INSERT INTO agg_monthly_crypto (
            crypto_id, symbol, year, month,
            open_usd, close_usd, high_usd, low_usd,
            avg_close_usd, total_volume,
            monthly_change_pct, avg_daily_volatility, trading_days
        )
        SELECT
            f.crypto_id,
            f.symbol,
            t.year,
            t.month,
            -- Open = premier close du mois
            (ARRAY_AGG(f.open_usd ORDER BY f.date ASC))[1] AS open_usd,
            -- Close = dernier close du mois
            (ARRAY_AGG(f.close_usd ORDER BY f.date DESC))[1] AS close_usd,
            MAX(f.high_usd) AS high_usd,
            MIN(f.low_usd) AS low_usd,
            ROUND(AVG(f.close_usd)::NUMERIC, 8) AS avg_close_usd,
            SUM(f.volume) AS total_volume,
            -- Variation mensuelle
            CASE
                WHEN (ARRAY_AGG(f.open_usd ORDER BY f.date ASC))[1] > 0
                THEN ROUND((
                    ((ARRAY_AGG(f.close_usd ORDER BY f.date DESC))[1]
                    - (ARRAY_AGG(f.open_usd ORDER BY f.date ASC))[1])
                    / (ARRAY_AGG(f.open_usd ORDER BY f.date ASC))[1] * 100
                )::NUMERIC, 4)
                ELSE NULL
            END AS monthly_change_pct,
            -- Volatilité moyenne journalière
            ROUND(AVG(
                CASE WHEN f.close_usd > 0
                THEN (f.high_usd - f.low_usd) / f.close_usd * 100
                ELSE 0 END
            )::NUMERIC, 4) AS avg_daily_volatility,
            COUNT(*) AS trading_days
        FROM fact_crypto_daily f
        JOIN dim_time t ON f.date = t.date_id
        GROUP BY f.crypto_id, f.symbol, t.year, t.month
        ORDER BY f.crypto_id, t.year, t.month
        ON CONFLICT (crypto_id, year, month) DO NOTHING
    """)

    cursor.execute("SELECT COUNT(*) FROM agg_monthly_crypto")
    count = cursor.fetchone()[0]
    print(f"  ✅ {count:,} agrégations mensuelles calculées")


def show_summary(cursor):
    """Affiche un résumé des données chargées."""
    print("\n" + "=" * 70)
    print("  RÉSUMÉ DU CHARGEMENT")
    print("=" * 70)

    cursor.execute("""
        SELECT
            dc.symbol,
            dc.name,
            COUNT(f.id) AS total_days,
            MIN(f.date) AS first_date,
            MAX(f.date) AS last_date,
            ROUND(MIN(f.close_usd)::NUMERIC, 2) AS min_price,
            ROUND(MAX(f.close_usd)::NUMERIC, 2) AS max_price,
            ROUND(AVG(f.close_usd)::NUMERIC, 2) AS avg_price
        FROM dim_crypto dc
        LEFT JOIN fact_crypto_daily f ON dc.crypto_id = f.crypto_id
        WHERE dc.is_active = TRUE
        GROUP BY dc.crypto_id, dc.symbol, dc.name
        ORDER BY dc.crypto_id
    """)
    rows = cursor.fetchall()

    print(f"\n{'Symbol':<8} {'Nom':<15} {'Jours':<8} {'Début':<12} {'Fin':<12} {'Min $':<14} {'Max $':<14} {'Moy $'}")
    print("-" * 105)
    for row in rows:
        symbol, name, days, first, last, min_p, max_p, avg_p = row
        days = days or 0
        if days > 0:
            print(f"{symbol:<8} {name:<15} {days:<8} {first}   {last}   {min_p:>12,.2f} {max_p:>12,.2f} {avg_p:>12,.2f}")
        else:
            print(f"{symbol:<8} {name:<15} {'—':<8} {'—':<12} {'—':<12} {'—':<14} {'—':<14} {'—'}")

    # Total global
    cursor.execute("SELECT COUNT(*) FROM fact_crypto_daily")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM stg_daily_metrics")
    metrics = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM agg_monthly_crypto")
    agg = cursor.fetchone()[0]

    print(f"\n📊 Totaux :")
    print(f"   fact_crypto_daily  : {total:>10,} lignes")
    print(f"   stg_daily_metrics  : {metrics:>10,} lignes")
    print(f"   agg_monthly_crypto : {agg:>10,} lignes")


def main():
    print("=" * 70)
    print("  CHARGEMENT HISTORIQUE — Alpha Vantage → PostgreSQL")
    print("  10 cryptomonnaies × historique complet")
    print("=" * 70)

    # Vérifier la clé API
    if not ALPHA_VANTAGE_API_KEY:
        print("\n❌ Clé API Alpha Vantage manquante !")
        print("   Ajoute dans ton .env :")
        print("   ALPHA_VANTAGE_API_KEY=ta_cle_ici")
        return

    # 1. Connexion PostgreSQL
    print("\n🔌 Connexion à PostgreSQL...")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        print("✅ Connecté")
    except Exception as e:
        print(f"❌ Impossible de se connecter : {e}")
        return

    try:
        # 2. Récupérer la liste des cryptos
        cryptos = get_cryptos(cursor)
        if not cryptos:
            print("❌ Aucune crypto trouvée dans dim_crypto. Vérifie que init_postgres.sql a été exécuté.")
            return

        print(f"\n📋 {len(cryptos)} cryptomonnaies à charger :")
        for cid, symbol, name, av_key in cryptos:
            print(f"   {symbol:<6} — {name}")

        # 3. Charger chaque crypto
        total_inserted = 0
        total_skipped = 0
        failed_cryptos = []

        for i, (crypto_id, symbol, name, av_key) in enumerate(cryptos):
            print(f"\n{'─' * 50}")
            print(f"  [{i+1}/{len(cryptos)}] {symbol} — {name}")
            print(f"{'─' * 50}")

            # Vérifier si les données existent déjà
            cursor.execute(
                "SELECT COUNT(*) FROM fact_crypto_daily WHERE crypto_id = %s",
                (crypto_id,)
            )
            existing = cursor.fetchone()[0]
            if existing > 0:
                print(f"  ⏭️ Déjà chargé ({existing:,} lignes) — ignoré")
                continue

            # Appel API
            print(f"  📡 Téléchargement de l'historique...")
            time_series = fetch_crypto_history(symbol, av_key)

            if time_series is None:
                failed_cryptos.append(symbol)
                print(f"  ❌ Échec pour {symbol}")
                continue

            print(f"  ✅ {len(time_series)} jours reçus")

            # Insertion
            print(f"  💾 Insertion dans PostgreSQL...")
            inserted, skipped = insert_crypto_data(cursor, crypto_id, symbol, time_series)
            conn.commit()
            print(f"  ✅ {inserted:,} insérés, {skipped} ignorés")

            total_inserted += inserted
            total_skipped += skipped

            # Attendre entre chaque appel pour respecter le rate limit
            if i < len(cryptos) - 1:
                print(f"  ⏳ Attente {API_DELAY_SECONDS}s (rate limit)...")
                time.sleep(API_DELAY_SECONDS)

        # 4. Calculer les métriques dérivées
        if total_inserted > 0:
            compute_daily_metrics(cursor)
            conn.commit()

            compute_monthly_aggregates(cursor)
            conn.commit()

        # 5. Résumé
        show_summary(cursor)

        if failed_cryptos:
            print(f"\n⚠️ Cryptos en échec : {', '.join(failed_cryptos)}")
            print(f"   Relance le script plus tard pour les charger.")

        print(f"\n✅ Chargement terminé !")
        print(f"   Total : {total_inserted:,} lignes insérées")
        print(f"   Tu peux voir les données dans PgAdmin (localhost:{DB_PORT})")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ Erreur fatale : {e}")
        raise
    finally:
        cursor.close()
        conn.close()
        print("🔌 Connexion fermée.")


if __name__ == "__main__":
    main()