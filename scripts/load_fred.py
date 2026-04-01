"""
Script de chargement — FRED → PostgreSQL
Télécharge les séries macroéconomiques (taux d'intérêt, inflation,
S&P 500, VIX, etc.) et les insère dans fact_fred_observation.

Usage :
    python scripts/load_fred.py

Nécessite une clé API FRED gratuite :
    https://fred.stlouisfed.org/docs/api/api_key.html
"""

import os
import time
import requests
import psycopg2
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

FRED_API_KEY = os.getenv("FRED_API_KEY")
FRED_BASE_URL = "https://api.stlouisfed.org/fred"

API_DELAY_SECONDS = 2  # FRED est très généreux, peu de rate limiting


def get_db_connection():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    conn.autocommit = False
    return conn


def get_fred_series(cursor):
    """Récupère la liste des séries FRED actives depuis dim_fred_series."""
    cursor.execute("""
        SELECT series_id, fred_code, name, frequency
        FROM dim_fred_series
        WHERE is_active = TRUE
        ORDER BY series_id
    """)
    return cursor.fetchall()


def fetch_fred_observations(fred_code):
    """
    Récupère toutes les observations d'une série FRED.
    Une seule requête retourne tout l'historique.
    """
    params = {
        "series_id": fred_code,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "observation_start": "2009-01-01",
        "sort_order": "asc",
    }

    response = requests.get(f"{FRED_BASE_URL}/series/observations", params=params, timeout=30)

    if response.status_code != 200:
        print(f"  ❌ Erreur HTTP : {response.status_code}")
        return None

    data = response.json()

    if "error_code" in data:
        print(f"  ❌ Erreur API : {data.get('error_message', 'Unknown')}")
        return None

    observations = data.get("observations", [])
    return observations


def insert_observations(cursor, series_id, fred_code, observations):
    """Insère les observations dans fact_fred_observation."""
    inserted = 0
    skipped = 0

    for obs in observations:
        date_str = obs.get("date")
        value_str = obs.get("value", ".")

        # FRED utilise "." pour les valeurs manquantes
        if value_str == "." or not value_str:
            skipped += 1
            continue

        try:
            value = float(value_str)
            cursor.execute("""
                INSERT INTO fact_fred_observation (series_id, fred_code, date, value)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (fred_code, date) DO NOTHING
            """, (series_id, fred_code, date_str, value))
            inserted += 1
        except (ValueError, Exception) as e:
            skipped += 1
            if skipped <= 3:
                print(f"    ⚠ Ignoré ({date_str}={value_str}) : {e}")

    return inserted, skipped


def main():
    print("=" * 60)
    print("  CHARGEMENT FRED → PostgreSQL")
    print("  Indicateurs macroéconomiques")
    print("=" * 60)

    if not FRED_API_KEY:
        print("\n❌ Clé API FRED manquante !")
        print("   Ajoute dans ton .env :")
        print("   FRED_API_KEY=ta_cle_ici")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    print("✅ Connecté à PostgreSQL")

    try:
        series_list = get_fred_series(cursor)
        if not series_list:
            print("❌ Aucune série trouvée dans dim_fred_series.")
            return

        print(f"\n📋 {len(series_list)} séries à charger :")
        for _, code, name, freq in series_list:
            print(f"   {code:<20} — {name} ({freq})")

        total_inserted = 0
        for i, (series_id, fred_code, name, freq) in enumerate(series_list):
            print(f"\n  [{i+1}/{len(series_list)}] {fred_code} — {name}")
            print(f"  📡 Téléchargement...")

            observations = fetch_fred_observations(fred_code)
            if observations is None:
                continue

            print(f"  ✅ {len(observations)} observations reçues")

            inserted, skipped = insert_observations(cursor, series_id, fred_code, observations)
            conn.commit()
            print(f"  ✅ {inserted} insérées, {skipped} ignorées")
            total_inserted += inserted

            if i < len(series_list) - 1:
                time.sleep(API_DELAY_SECONDS)

        # Résumé
        cursor.execute("""
            SELECT fs.fred_code, fs.name, COUNT(fo.id) AS obs_count,
                   MIN(fo.date) AS first_date, MAX(fo.date) AS last_date
            FROM dim_fred_series fs
            LEFT JOIN fact_fred_observation fo ON fs.fred_code = fo.fred_code
            GROUP BY fs.fred_code, fs.name
            ORDER BY fs.fred_code
        """)
        rows = cursor.fetchall()

        print(f"\n{'=' * 80}")
        print(f"  RÉSUMÉ FRED")
        print(f"{'=' * 80}")
        print(f"{'Code':<20} {'Nom':<35} {'Obs':<8} {'Début':<12} {'Fin'}")
        print("-" * 80)
        for code, name, count, first, last in rows:
            name_short = name[:33] if len(name) > 33 else name
            print(f"{code:<20} {name_short:<35} {count or 0:<8} {first or '—'!s:<12} {last or '—'}")

        print(f"\n✅ Total : {total_inserted:,} observations chargées")

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