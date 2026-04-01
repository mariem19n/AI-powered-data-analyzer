"""
Script d'enrichissement — GDELT → Tavily → LLM → PostgreSQL
Ticket Sprint 2 : Enrichissement agentique des articles GDELT

Pipeline :
  1. Sélectionner les top articles non encore enrichis (scoring)
  2. Extraire le contenu via Tavily Extract
  3. Résumer + classifier via Claude (Anthropic API)
  4. Stocker dans article_enrichment

Critères de scoring (sélection des articles à enrichir) :
  - Tonalité extrême (abs(tone) > 3)  → signal fort, positif ou négatif
  - Catégorie crypto_direct            → priorité sur macro
  - Recency                            → articles récents en premier

Usage :
    pip install tavily-python anthropic psycopg2-binary python-dotenv
    python scripts/enrich_gdelt.py

Coût Tavily : ~1 crédit par extraction (plan gratuit = 1000 crédits/mois)
On enrichit au maximum TOP_K articles par run → contrôle des coûts.
"""

import os
import json
import time
import psycopg2
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

try:
    from tavily import TavilyClient
except ImportError:
    print("❌ Installe tavily-python : pip install tavily-python")
    exit(1)

try:
    import anthropic
except ImportError:
    print("❌ Installe anthropic : pip install anthropic")
    exit(1)

# ─── Configuration ────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT     = int(os.getenv("DB_PORT", "5433"))
DB_NAME     = os.getenv("POSTGRES_DB", "analyzer_db")
DB_USER     = os.getenv("POSTGRES_USER", "analyzer")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "analyzer_pg_123")

TAVILY_API_KEY   = os.getenv("TAVILY_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Nombre max d'articles enrichis par run (contrôle des coûts Tavily)
TOP_K = 15

# Modèle LLM utilisé
LLM_MODEL = "claude-haiku-4-5-20251001"  


# ─── Connexion DB ─────────────────────────────────────────────

def get_db_connection():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )
    conn.autocommit = False
    return conn


# ─── Scoring et sélection des articles ───────────────────────

def select_articles_to_enrich(cursor, top_k):
    """
    Sélectionne les articles non encore enrichis avec le meilleur score.

    Score composite :
      - Tonalité extrême (abs(tone) > 3) : +2 points
      - Catégorie crypto_direct           : +1 point
      - Article récent (< 7 jours)        : +1 point

    Exclut les URLs déjà dans article_enrichment (status != 'pending').
    """
    cursor.execute("""
        SELECT
            f.url,
            f.title,
            f.date,
            f.tone,
            f.category,
            f.crypto_id,
            f.theme,
            -- Score de sélection
            (
                CASE WHEN ABS(f.tone) > 3  THEN 2 ELSE 0 END +
                CASE WHEN f.category = 'crypto_direct' THEN 1 ELSE 0 END +
                CASE WHEN f.date >= CURRENT_DATE - INTERVAL '7 days' THEN 1 ELSE 0 END
            )::DECIMAL(6,4) AS selection_score
        FROM fact_gdelt_events f
        WHERE
            f.url IS NOT NULL
            AND f.tone IS NOT NULL
            AND f.url NOT IN (
                SELECT url FROM article_enrichment
                WHERE status IN ('ok', 'failed', 'skipped')
            )
        ORDER BY selection_score DESC, f.date DESC
        LIMIT %s
    """, (top_k,))

    return cursor.fetchall()


# ─── Extraction Tavily ────────────────────────────────────────

def extract_content_tavily(url):
    """
    Extrait le contenu textuel d'une URL via Tavily Extract.
    Retourne (raw_content, status) où status = 'ok' | 'failed'.
    """
    if not TAVILY_API_KEY:
        print("    ⚠ TAVILY_API_KEY manquant dans .env — skip extraction")
        return None, "skipped"

    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.extract(
            urls=[url],
            extract_depth="basic",   # basic = 1 crédit, advanced = 2 crédits
            format="markdown",
            timeout=20,
        )

        # Tavily retourne une liste de résultats
        results = response.get("results", [])
        if results and results[0].get("raw_content"):
            content = results[0]["raw_content"]
            # Limiter à 4000 caractères pour ne pas exploser le prompt LLM
            return content[:4000], "ok"
        else:
            return None, "failed"

    except Exception as e:
        print(f"    ⚠ Tavily Extract erreur : {e}")
        return None, "failed"


# ─── Enrichissement LLM ───────────────────────────────────────

def enrich_with_llm(title, content, theme, crypto_id):
    """
    Analyse l'article avec Claude et retourne un dict structuré :
    {summary, entities, impact_type, llm_sentiment}
    """
    if not ANTHROPIC_API_KEY:
        print("    ⚠ ANTHROPIC_API_KEY manquant dans .env — skip LLM")
        return None

    if not content:
        return None

    crypto_context = f"Cet article est lié à {crypto_id}." if crypto_id else \
                     "Cet article est lié au marché crypto en général ou à la macro-économie."

    prompt = f"""Tu es un analyste financier spécialisé dans les cryptomonnaies.
Analyse cet article de presse et réponds UNIQUEMENT en JSON valide, sans texte avant ou après.

Titre : {title}
Thème GDELT : {theme}
{crypto_context}

Contenu :
{content}

Réponds avec ce JSON exact :
{{
  "summary": "Résumé factuel en 2-3 phrases maximum",
  "entities": {{
    "countries": ["liste des pays mentionnés"],
    "companies": ["liste des entreprises/exchanges mentionnés"],
    "regulators": ["liste des régulateurs mentionnés (SEC, CFTC, Fed, etc.)"]
  }},
  "impact_type": "UN SEUL parmi : regulation | hack | adoption | macro | market_move | other",
  "llm_sentiment": 0.0
}}

Pour llm_sentiment : valeur entre -1.0 (très négatif) et +1.0 (très positif) pour le marché crypto.
"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=LLM_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()

        # Nettoyer les backticks si le LLM en ajoute malgré les instructions
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        return json.loads(raw)

    except json.JSONDecodeError as e:
        print(f"    ⚠ LLM JSON invalide : {e}")
        return None
    except Exception as e:
        print(f"    ⚠ LLM erreur : {e}")
        return None


# ─── Insertion dans article_enrichment ───────────────────────

def upsert_enrichment(cursor, url, selection_score, status,
                      raw_content=None, llm_result=None):
    """
    Insère ou met à jour l'entrée d'enrichissement pour une URL.
    """
    summary     = llm_result.get("summary")     if llm_result else None
    entities    = json.dumps(llm_result.get("entities", {})) if llm_result else None
    impact_type = llm_result.get("impact_type") if llm_result else None
    sentiment   = llm_result.get("llm_sentiment") if llm_result else None

    cursor.execute("""
        INSERT INTO article_enrichment (
            url, status, selection_score,
            extracted_at, raw_content,
            llm_model, summarized_at,
            summary, entities, impact_type, llm_sentiment
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (url) DO UPDATE SET
            status          = EXCLUDED.status,
            selection_score = EXCLUDED.selection_score,
            extracted_at    = EXCLUDED.extracted_at,
            raw_content     = EXCLUDED.raw_content,
            llm_model       = EXCLUDED.llm_model,
            summarized_at   = EXCLUDED.summarized_at,
            summary         = EXCLUDED.summary,
            entities        = EXCLUDED.entities,
            impact_type     = EXCLUDED.impact_type,
            llm_sentiment   = EXCLUDED.llm_sentiment
    """, (
        url,
        status,
        selection_score,
        datetime.now() if raw_content else None,
        raw_content,
        LLM_MODEL if llm_result else None,
        datetime.now() if llm_result else None,
        summary,
        entities,
        impact_type,
        sentiment,
    ))


# ─── Main ─────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  ENRICHISSEMENT GDELT — Tavily + LLM")
    print(f"  Top {TOP_K} articles sélectionnés par score")
    print("=" * 65)

    if not TAVILY_API_KEY:
        print("\n⚠ TAVILY_API_KEY absent du .env — les extractions seront skippées")
    if not ANTHROPIC_API_KEY:
        print("⚠ ANTHROPIC_API_KEY absent du .env — le LLM sera skippé")

    conn   = get_db_connection()
    cursor = conn.cursor()
    print("\n✅ Connecté à PostgreSQL")

    try:
        # ── Sélection des articles ─────────────────────────────
        articles = select_articles_to_enrich(cursor, TOP_K)
        print(f"📋 {len(articles)} articles sélectionnés pour enrichissement\n")

        if not articles:
            print("ℹ Aucun article à enrichir (tous déjà traités ou sans tone).")
            return

        stats = {"ok": 0, "failed": 0, "skipped": 0}

        for i, (url, title, date, tone, category, crypto_id, theme, score) in enumerate(articles):
            print(f"  [{i+1}/{len(articles)}] {title[:70] if title else url[:70]}")
            print(f"      score={score:.1f} | tone={tone:+.2f} | {category} | {theme}")

            # Étape 1 : extraction Tavily
            raw_content, extract_status = extract_content_tavily(url)

            if extract_status == "failed":
                print(f"      ❌ Extraction échouée")
                upsert_enrichment(cursor, url, score, "failed")
                conn.commit()
                stats["failed"] += 1
                time.sleep(2)
                continue

            if extract_status == "skipped":
                upsert_enrichment(cursor, url, score, "skipped")
                conn.commit()
                stats["skipped"] += 1
                continue

            print(f"      ✅ Contenu extrait ({len(raw_content)} caractères)")

            # Étape 2 : enrichissement LLM
            llm_result = enrich_with_llm(title, raw_content, theme, crypto_id)

            if llm_result:
                print(f"      ✅ LLM : {llm_result.get('impact_type')} | "
                      f"sentiment={llm_result.get('llm_sentiment'):+.2f}")
            else:
                print(f"      ⚠ LLM échoué — contenu stocké sans résumé")

            # Étape 3 : insertion
            upsert_enrichment(cursor, url, score, "ok", raw_content, llm_result)
            conn.commit()
            stats["ok"] += 1

            # Délai pour respecter les rate limits Tavily et Anthropic
            time.sleep(3)

        # ── Résumé ────────────────────────────────────────────
        print(f"\n{'═'*65}")
        print(f"  RÉSUMÉ ENRICHISSEMENT")
        print(f"{'═'*65}")
        print(f"  ✅ Enrichis avec succès : {stats['ok']}")
        print(f"  ❌ Échoués              : {stats['failed']}")
        print(f"  ⏭ Skippés              : {stats['skipped']}")

        # Stats globales
        cursor.execute("""
            SELECT status, COUNT(*), AVG(llm_sentiment)
            FROM article_enrichment
            GROUP BY status
        """)
        rows = cursor.fetchall()
        print(f"\n  État global de article_enrichment :")
        for status, count, avg_sent in rows:
            sent_str = f"{avg_sent:+.2f}" if avg_sent else "N/A"
            print(f"    {status:<10} : {count} articles | sentiment moyen : {sent_str}")

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
