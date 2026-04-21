"""
Script de test — CoinGecko API
Télécharge l'historique complet du Bitcoin et l'insère dans PostgreSQL.

Usage :
    pip install requests psycopg2-binary
    python scripts/test_coingecko.py

Ce script tourne sur ta machine locale (pas dans Docker).
Il se connecte à PostgreSQL via localhost:5432.
"""

import requests
import psycopg2
from datetime import datetime

# ─── Configuration ────────────────────────────────────────────
# Adapte ces valeurs à ton .env
DB_HOST = "127.0.0.1"
DB_PORT = 5433
DB_NAME = "analyzer_db"
DB_USER = "analyzer"
DB_PASSWORD = "analyzer_pg_123"  # mot de passe du .env

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"


def create_table(cursor):
    """Crée la table de test pour les données crypto."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS test_crypto_daily (
            id SERIAL PRIMARY KEY,
            crypto_id VARCHAR(50) NOT NULL,
            crypto_symbol VARCHAR(10) NOT NULL,
            date DATE NOT NULL,
            open_usd DECIMAL(18, 4),
            high_usd DECIMAL(18, 4),
            low_usd DECIMAL(18, 4),
            close_usd DECIMAL(18, 4),
            volume_usd DECIMAL(18, 2),
            market_cap_usd DECIMAL(18, 2),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(crypto_id, date)
        );
    """)
    print("✅ Table test_crypto_daily créée (ou déjà existante)")


def fetch_bitcoin_history():
    """
    Récupère tout l'historique du Bitcoin depuis CoinGecko.
    Une seule requête API avec days=max.
    """
    print("📡 Appel API CoinGecko — historique complet Bitcoin...")

    # Endpoint OHLC — retourne Open/High/Low/Close
    # days=max retourne tout l'historique disponible
    url = f"{COINGECKO_BASE_URL}/coins/bitcoin/ohlc"
    params = {
        "vs_currency": "usd",
        "days": "365",
    }

    response = requests.get(url, params=params, timeout=30)

    if response.status_code != 200:
        print(f"❌ Erreur API : {response.status_code}")
        print(response.text)
        return None

    data = response.json()
    print(f"✅ {len(data)} points OHLC reçus")
    return data


def fetch_bitcoin_market_chart():
    """
    Récupère l'historique complet prix + volume + market cap.
    Utilise market_chart qui donne plus de données.
    """
    print("📡 Appel API CoinGecko — market_chart complet Bitcoin...")

    url = f"{COINGECKO_BASE_URL}/coins/bitcoin/market_chart"
    params = {
        "vs_currency": "usd",
        "days": "365",
        "interval": "daily",
    }

    response = requests.get(url, params=params, timeout=30)

    if response.status_code != 200:
        print(f"❌ Erreur API : {response.status_code}")
        print(response.text)
        return None

    data = response.json()
    prices = data.get("prices", [])
    volumes = data.get("total_volumes", [])
    market_caps = data.get("market_caps", [])

    print(f"✅ {len(prices)} jours de prix reçus")
    print(f"✅ {len(volumes)} jours de volume reçus")
    print(f"✅ {len(market_caps)} jours de market cap reçus")

    return data


def insert_market_chart_data(cursor, data):
    """
    Insère les données market_chart dans PostgreSQL.
    Combine prices, volumes et market_caps par date.
    """
    prices = data.get("prices", [])
    volumes = data.get("total_volumes", [])
    market_caps = data.get("market_caps", [])

    # Créer des dictionnaires indexés par date pour le merge
    volume_map = {}
    for ts, vol in volumes:
        date = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        volume_map[date] = vol

    mcap_map = {}
    for ts, mcap in market_caps:
        date = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        mcap_map[date] = mcap

    inserted = 0
    skipped = 0

    for ts, price in prices:
        date_str = datetime.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        volume = volume_map.get(date_str, 0)
        market_cap = mcap_map.get(date_str, 0)

        try:
            cursor.execute("""
                INSERT INTO test_crypto_daily
                    (crypto_id, crypto_symbol, date, close_usd, volume_usd, market_cap_usd)
                VALUES
                    (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (crypto_id, date) DO NOTHING
            """, ("bitcoin", "BTC", date_str, round(price, 4), round(volume, 2), round(market_cap, 2)))
            inserted += 1
        except Exception as e:
            skipped += 1
            if skipped <= 3:
                print(f"  ⚠ Ligne ignorée ({date_str}) : {e}")

    return inserted, skipped


def show_sample_data(cursor):
    """Affiche un échantillon des données insérées."""
    print("\n📊 Échantillon des données (10 premières lignes) :")
    print("-" * 80)
    cursor.execute("""
        SELECT date, close_usd, volume_usd, market_cap_usd
        FROM test_crypto_daily
        WHERE crypto_id = 'bitcoin'
        ORDER BY date ASC
        LIMIT 10
    """)
    rows = cursor.fetchall()
    print(f"{'Date':<14} {'Prix (USD)':<16} {'Volume (USD)':<22} {'Market Cap (USD)'}")
    print("-" * 80)
    for row in rows:
        print(f"{row[0]}     {row[1]:>12,.2f}   {row[2]:>18,.0f}   {row[3]:>18,.0f}")

    print("\n📊 Dernières 10 lignes :")
    print("-" * 80)
    cursor.execute("""
        SELECT date, close_usd, volume_usd, market_cap_usd
        FROM test_crypto_daily
        WHERE crypto_id = 'bitcoin'
        ORDER BY date DESC
        LIMIT 10
    """)
    rows = cursor.fetchall()
    print(f"{'Date':<14} {'Prix (USD)':<16} {'Volume (USD)':<22} {'Market Cap (USD)'}")
    print("-" * 80)
    for row in rows:
        print(f"{row[0]}     {row[1]:>12,.2f}   {row[2]:>18,.0f}   {row[3]:>18,.0f}")

    # Stats globales
    cursor.execute("""
        SELECT
            COUNT(*) AS total_rows,
            MIN(date) AS first_date,
            MAX(date) AS last_date,
            MIN(close_usd) AS min_price,
            MAX(close_usd) AS max_price
        FROM test_crypto_daily
        WHERE crypto_id = 'bitcoin'
    """)
    stats = cursor.fetchone()
    print(f"\n📈 Statistiques globales :")
    print(f"   Total lignes   : {stats[0]:,}")
    print(f"   Première date  : {stats[1]}")
    print(f"   Dernière date  : {stats[2]}")
    print(f"   Prix minimum   : ${stats[3]:,.2f}")
    print(f"   Prix maximum   : ${stats[4]:,.2f}")


def main():
    print("=" * 60)
    print("  TEST COINGECKO API → PostgreSQL")
    print("=" * 60)

    # 1. Connexion PostgreSQL
    print("\n🔌 Connexion à PostgreSQL...")
    try:
        print("HOST =", DB_HOST)
        print("PORT =", DB_PORT)
        print("DB   =", DB_NAME)
        print("USER =", DB_USER)
        print("PWD  =", DB_PASSWORD)
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT,
            dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        conn.autocommit = False
        cursor = conn.cursor()
        print("✅ Connecté à PostgreSQL")
    except Exception as e:
        print(f"❌ Impossible de se connecter : {e}")
        print("   Vérifie que Docker tourne et que les credentials sont corrects.")
        return

    try:
        # 2. Créer la table
        create_table(cursor)
        conn.commit()

        # 3. Télécharger les données
        data = fetch_bitcoin_market_chart()
        if data is None:
            print("❌ Impossible de récupérer les données. Arrêt.")
            return

        # 4. Insérer dans PostgreSQL
        print("\n💾 Insertion dans PostgreSQL...")
        inserted, skipped = insert_market_chart_data(cursor, data)
        conn.commit()
        print(f"✅ {inserted} lignes insérées, {skipped} ignorées")

        # 5. Afficher un échantillon
        show_sample_data(cursor)

        print("\n✅ Test terminé ! Tu peux maintenant voir les données dans PgAdmin.")
        print("   Table : test_crypto_daily")

    except Exception as e:
        conn.rollback()
        print(f"❌ Erreur : {e}")
        raise
    finally:
        cursor.close()
        conn.close()
        print("\n🔌 Connexion fermée.")


if __name__ == "__main__":
    main()
