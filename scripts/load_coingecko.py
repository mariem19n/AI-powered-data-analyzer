"""
Script d'enrichissement — CoinGecko → PostgreSQL
Enrichit fact_crypto_daily avec le market cap de chaque crypto.
Récupère les 365 derniers jours (limite du plan gratuit).

Usage :
    python scripts/load_coingecko.py

"""

import os
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

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
API_DELAY_SECONDS = 12  # Respecter le rate limit CoinGecko


def get_db_connection():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    conn.autocommit = False
    return conn


def get_cryptos(cursor):
    cursor.execute("""
        SELECT crypto_id, symbol, name, coingecko_key
        FROM dim_crypto
        WHERE is_active = TRUE AND coingecko_key IS NOT NULL
        ORDER BY crypto_id
    """)
    return cursor.fetchall()


def fetch_market_chart(coingecko_key):
    """Récupère prix + market cap + volume des 365 derniers jours."""
    url = f"{COINGECKO_BASE_URL}/coins/{coingecko_key}/market_chart"
    params = {"vs_currency": "usd", "days": "365", "interval": "daily"}

    response = requests.get(url, params=params, timeout=30)
    if response.status_code == 429:
        print("  ⚠️ Rate limit — attente 60s...")
        time.sleep(60)
        response = requests.get(url, params=params, timeout=30)

    if response.status_code != 200:
        print(f"  ❌ Erreur API : {response.status_code}")
        return None

    return response.json()


def update_market_cap(cursor, crypto_id, data):
    """Met à jour le market cap dans fact_crypto_daily."""
    market_caps = data.get("market_caps", [])
    updated = 0

    for ts, mcap in market_caps:
        date_str = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        if mcap and mcap > 0:
            cursor.execute("""
                UPDATE fact_crypto_daily
                SET market_cap_usd = %s, source = 'alpha_vantage+coingecko'
                WHERE crypto_id = %s AND date = %s AND (market_cap_usd IS NULL OR market_cap_usd = 0)
            """, (round(mcap, 2), crypto_id, date_str))
            if cursor.rowcount > 0:
                updated += 1

    return updated


def main():
    print("=" * 60)
    print("  ENRICHISSEMENT COINGECKO → PostgreSQL")
    print("  Market cap pour les 365 derniers jours")
    print("=" * 60)

    conn = get_db_connection()
    cursor = conn.cursor()
    print("✅ Connecté à PostgreSQL")

    try:
        cryptos = get_cryptos(cursor)
        print(f"\n📋 {len(cryptos)} cryptos à enrichir")

        total_updated = 0
        for i, (crypto_id, symbol, name, cg_key) in enumerate(cryptos):
            print(f"\n  [{i+1}/{len(cryptos)}] {symbol} — {name}")
            print(f"  📡 Appel CoinGecko ({cg_key})...")

            data = fetch_market_chart(cg_key)
            if data is None:
                continue

            mcaps = data.get("market_caps", [])
            print(f"  ✅ {len(mcaps)} jours de market cap reçus")

            updated = update_market_cap(cursor, crypto_id, data)
            conn.commit()
            print(f"  ✅ {updated} lignes enrichies avec market cap")
            total_updated += updated

            if i < len(cryptos) - 1:
                print(f"  ⏳ Attente {API_DELAY_SECONDS}s...")
                time.sleep(API_DELAY_SECONDS)

        print(f"\n✅ Enrichissement terminé : {total_updated} lignes mises à jour")

    except Exception as e:
        conn.rollback()
        print(f"❌ Erreur : {e}")
        raise
    finally:
        cursor.close()
        conn.close()
        print("🔌 Connexion fermée.")


if __name__ == "__main__":
    main()