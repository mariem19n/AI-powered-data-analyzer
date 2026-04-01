"""
Script de test — Alpha Vantage API
Télécharge l'historique COMPLET du Bitcoin et l'insère dans PostgreSQL.

Usage :
    pip install requests psycopg2-binary
    python scripts/test_alphavantage.py

Ce script tourne en local (pas dans Docker).
Il se connecte à PostgreSQL via 127.0.0.1:5433.
"""

import requests
import psycopg2
from datetime import datetime

# ─── Configuration ────────────────────────────────────────────
DB_HOST = "127.0.0.1"
DB_PORT = 5433
DB_NAME = "analyzer_db"
DB_USER = "analyzer"
DB_PASSWORD = "analyzer_pg_123"

ALPHA_VANTAGE_API_KEY = "9NF06MHZHK2LZPJQ"

ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"


def create_table(cursor):
    """Crée la table de test pour les données crypto Alpha Vantage."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS test_alpha_crypto_daily (
            id SERIAL PRIMARY KEY,
            crypto_id VARCHAR(10) NOT NULL,
            date DATE NOT NULL,
            open_usd DECIMAL(18, 4),
            high_usd DECIMAL(18, 4),
            low_usd DECIMAL(18, 4),
            close_usd DECIMAL(18, 4),
            volume DECIMAL(18, 4),
            market_cap_usd DECIMAL(18, 2),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(crypto_id, date)
        );
    """)
    print("✅ Table test_alpha_crypto_daily créée (ou déjà existante)")


def fetch_bitcoin_history():
    """
    Récupère tout l'historique du Bitcoin depuis Alpha Vantage.
    Une seule requête API avec outputsize=full.
    Retourne les données OHLCV complètes.
    """
    print("📡 Appel API Alpha Vantage — historique complet Bitcoin...")
    print("   (ça peut prendre 10-20 secondes)")

    params = {
        "function": "DIGITAL_CURRENCY_DAILY",
        "symbol": "BTC",
        "market": "USD",
        "apikey": ALPHA_VANTAGE_API_KEY,
    }

    response = requests.get(ALPHA_VANTAGE_BASE_URL, params=params, timeout=60)

    if response.status_code != 200:
        print(f"❌ Erreur HTTP : {response.status_code}")
        return None

    data = response.json()

    # Vérifier les erreurs API
    if "Error Message" in data:
        print(f"❌ Erreur API : {data['Error Message']}")
        return None

    if "Note" in data:
        print(f"⚠️ Limite atteinte : {data['Note']}")
        return None

    if "Information" in data:
        print(f"⚠️ Info : {data['Information']}")
        return None

    time_series = data.get("Time Series (Digital Currency Daily)", {})
    if not time_series:
        print("❌ Aucune donnée reçue. Vérifie ta clé API.")
        print(f"   Réponse : {str(data)[:500]}")
        return None

    print(f"✅ {len(time_series)} jours de données OHLCV reçus")

    # Afficher la première et dernière date
    dates = sorted(time_series.keys())
    print(f"   Première date : {dates[0]}")
    print(f"   Dernière date : {dates[-1]}")

    return time_series


def insert_data(cursor, time_series):
    """
    Insère les données Alpha Vantage dans PostgreSQL.
    Les clés Alpha Vantage pour les cryptos sont :
        1a. open (USD), 2a. high (USD), 3a. low (USD), 4a. close (USD), 5. volume, 6. market cap (USD)
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
            market_cap = 0

            cursor.execute("""
                INSERT INTO test_alpha_crypto_daily
                    (crypto_id, date, open_usd, high_usd, low_usd, close_usd, volume, market_cap_usd)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (crypto_id, date) DO NOTHING
            """, ("BTC", date_str, open_usd, high_usd, low_usd, close_usd, volume, market_cap))
            inserted += 1
        except Exception as e:
            skipped += 1
            if skipped <= 3:
                print(f"  ⚠ Ligne ignorée ({date_str}) : {e}")

    return inserted, skipped


def show_sample_data(cursor):
    """Affiche un échantillon des données insérées."""
    print("\n📊 10 premières lignes (plus anciennes) :")
    print("-" * 100)
    cursor.execute("""
        SELECT date, open_usd, high_usd, low_usd, close_usd, volume
        FROM test_alpha_crypto_daily
        WHERE crypto_id = 'BTC'
        ORDER BY date ASC
        LIMIT 10
    """)
    rows = cursor.fetchall()
    print(f"{'Date':<14} {'Open (USD)':<16} {'High (USD)':<16} {'Low (USD)':<16} {'Close (USD)':<16} {'Volume'}")
    print("-" * 100)
    for row in rows:
        print(f"{row[0]}     {row[1]:>12,.2f}   {row[2]:>12,.2f}   {row[3]:>12,.2f}   {row[4]:>12,.2f}   {row[5]:>12,.4f}")

    print("\n📊 10 dernières lignes (plus récentes) :")
    print("-" * 100)
    cursor.execute("""
        SELECT date, open_usd, high_usd, low_usd, close_usd, volume
        FROM test_alpha_crypto_daily
        WHERE crypto_id = 'BTC'
        ORDER BY date DESC
        LIMIT 10
    """)
    rows = cursor.fetchall()
    print(f"{'Date':<14} {'Open (USD)':<16} {'High (USD)':<16} {'Low (USD)':<16} {'Close (USD)':<16} {'Volume'}")
    print("-" * 100)
    for row in rows:
        print(f"{row[0]}     {row[1]:>12,.2f}   {row[2]:>12,.2f}   {row[3]:>12,.2f}   {row[4]:>12,.2f}   {row[5]:>12,.4f}")

    # Stats globales
    cursor.execute("""
        SELECT
            COUNT(*) AS total_rows,
            MIN(date) AS first_date,
            MAX(date) AS last_date,
            MIN(close_usd) AS min_price,
            MAX(close_usd) AS max_price,
            ROUND(AVG(close_usd), 2) AS avg_price
        FROM test_alpha_crypto_daily
        WHERE crypto_id = 'BTC'
    """)
    stats = cursor.fetchone()
    print(f"\n📈 Statistiques globales :")
    print(f"   Total lignes   : {stats[0]:,}")
    print(f"   Première date  : {stats[1]}")
    print(f"   Dernière date  : {stats[2]}")
    print(f"   Prix minimum   : ${stats[3]:,.2f}")
    print(f"   Prix maximum   : ${stats[4]:,.2f}")
    print(f"   Prix moyen     : ${stats[5]:,.2f}")

    # Compter les années couvertes
    cursor.execute("""
        SELECT EXTRACT(YEAR FROM date)::INT AS year, COUNT(*) AS days
        FROM test_alpha_crypto_daily
        WHERE crypto_id = 'BTC'
        GROUP BY year
        ORDER BY year
    """)
    years = cursor.fetchall()
    print(f"\n📅 Données par année :")
    for year, days in years:
        print(f"   {year} : {days} jours")


def main():
    print("=" * 60)
    print("  TEST ALPHA VANTAGE API → PostgreSQL")
    print("=" * 60)

    # Vérifier la clé API
    if ALPHA_VANTAGE_API_KEY == "REMPLACE_PAR_TA_CLE":
        print("\n❌ Tu dois d'abord mettre ta clé API Alpha Vantage !")
        print("   1. Va sur https://www.alphavantage.co/support/#api-key")
        print("   2. Crée un compte gratuit et copie ta clé")
        print("   3. Remplace REMPLACE_PAR_TA_CLE dans ce script")
        return

    # 1. Connexion PostgreSQL
    print("\n🔌 Connexion à PostgreSQL...")
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT,
            dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        conn.autocommit = False
        cursor = conn.cursor()
        print("✅ Connecté à PostgreSQL")
    except Exception as e:
        print(f"❌ Impossible de se connecter : {e}")
        return

    try:
        # 2. Créer la table
        create_table(cursor)
        conn.commit()

        # 3. Télécharger les données
        time_series = fetch_bitcoin_history()
        if time_series is None:
            print("❌ Impossible de récupérer les données. Arrêt.")
            return

        # 4. Insérer dans PostgreSQL
        print("\n💾 Insertion dans PostgreSQL...")
        inserted, skipped = insert_data(cursor, time_series)
        conn.commit()
        print(f"✅ {inserted} lignes insérées, {skipped} ignorées")

        # 5. Afficher un échantillon
        show_sample_data(cursor)

        print("\n✅ Test terminé ! Tu peux voir les données dans PgAdmin.")
        print("   Table : test_alpha_crypto_daily")
        print("\n📊 Comparaison avec CoinGecko :")
        print("   CoinGecko gratuit : 365 jours, close seulement (pas d'OHLC)")
        print("   Alpha Vantage gratuit : historique COMPLET, OHLCV complet")

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