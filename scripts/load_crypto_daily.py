# Script de mise à jour quotidienne — Alpha Vantage → PostgreSQL
"""
Met à jour fact_crypto_daily avec les nouvelles données Alpha Vantage.

Usage :
    python scripts/load_crypto_daily.py
"""

import time

from load_history import (
    ALPHA_VANTAGE_API_KEY,
    API_DELAY_SECONDS,
    DB_PORT,
    compute_daily_metrics,
    compute_monthly_aggregates,
    fetch_crypto_history,
    get_cryptos,
    get_db_connection,
    show_summary,
)


def insert_crypto_daily_data(cursor, crypto_id, symbol, time_series):
    """
    Insère les données OHLCV absentes dans fact_crypto_daily.
    Compte correctement les doublons ignorés par ON CONFLICT DO NOTHING.
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

            if cursor.rowcount == 1:
                inserted += 1
            else:
                skipped += 1

        except Exception as e:
            skipped += 1
            if skipped <= 3:
                print(f"    ⚠ Ligne ignorée ({date_str}) : {e}")

    return inserted, skipped


def show_latest_dates(cursor):
    """Affiche la dernière date disponible pour chaque crypto active."""
    print("\nDernière date disponible par crypto :")
    print("-" * 45)

    cursor.execute("""
        SELECT
            dc.symbol,
            MAX(f.date) AS last_available_date
        FROM dim_crypto dc
        LEFT JOIN fact_crypto_daily f ON f.crypto_id = dc.crypto_id
        WHERE dc.is_active = TRUE
        GROUP BY dc.crypto_id, dc.symbol
        ORDER BY dc.crypto_id
    """)

    for symbol, last_date in cursor.fetchall():
        print(f"  {symbol:<8} {last_date or '—'}")


def main():
    print("=" * 70)
    print("  MISE À JOUR QUOTIDIENNE — Alpha Vantage → PostgreSQL")
    print("=" * 70)

    if not ALPHA_VANTAGE_API_KEY:
        print("\n❌ Clé API Alpha Vantage manquante !")
        print("   Ajoute dans ton .env :")
        print("   ALPHA_VANTAGE_API_KEY=ta_cle_ici")
        return

    print("\n🔌 Connexion à PostgreSQL...")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        print("✅ Connecté")
    except Exception as e:
        print(f"❌ Impossible de se connecter : {e}")
        return

    try:
        cryptos = get_cryptos(cursor)
        if not cryptos:
            print("❌ Aucune crypto active trouvée dans dim_crypto.")
            return

        print(f"\n📋 {len(cryptos)} cryptomonnaies actives à mettre à jour :")
        for _, symbol, name, _ in cryptos:
            print(f"   {symbol:<6} — {name}")

        total_inserted = 0
        total_skipped = 0
        failed_cryptos = []

        for i, (crypto_id, symbol, name, av_key) in enumerate(cryptos):
            print(f"\n{'─' * 50}")
            print(f"  [{i + 1}/{len(cryptos)}] {symbol} — {name}")
            print(f"{'─' * 50}")

            print("  📡 Téléchargement des données quotidiennes...")
            time_series = fetch_crypto_history(symbol, av_key)

            if time_series is None:
                failed_cryptos.append(symbol)
                print(f"  ❌ Échec pour {symbol}")
            else:
                print(f"  ✅ {len(time_series)} jours reçus")
                print("  💾 Insertion des dates absentes dans PostgreSQL...")

                try:
                    inserted, skipped = insert_crypto_daily_data(cursor, crypto_id, symbol, time_series)
                    conn.commit()
                    print(f"  ✅ {inserted:,} nouvelles lignes, {skipped:,} ignorées/doublons")

                    total_inserted += inserted
                    total_skipped += skipped
                except Exception as e:
                    conn.rollback()
                    failed_cryptos.append(symbol)
                    print(f"  ❌ Erreur d'insertion pour {symbol} : {e}")

            if i < len(cryptos) - 1:
                print(f"  ⏳ Attente {API_DELAY_SECONDS}s (rate limit)...")
                time.sleep(API_DELAY_SECONDS)

        if total_inserted > 0:
            compute_daily_metrics(cursor)
            conn.commit()

            compute_monthly_aggregates(cursor)
            conn.commit()
        else:
            print("\nAucune nouvelle donnée à insérer — base déjà à jour.")

        show_summary(cursor)
        show_latest_dates(cursor)

        if failed_cryptos:
            print(f"\n⚠ Cryptos en échec : {', '.join(failed_cryptos)}")
            print("   Relance le script plus tard pour les mettre à jour.")

        print("\n✅ Mise à jour quotidienne terminée !")
        print(f"   Nouvelles lignes insérées : {total_inserted:,}")
        print(f"   Lignes ignorées/doublons  : {total_skipped:,}")
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
