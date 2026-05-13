"""
app/agents/analysis/enrichment/gdelt.py

Enrichissement contextuel des analyses via les articles GDELT déjà
ingérés en base PostgreSQL (table `fact_gdelt_events`).

Cas d'usage principal : détection d'anomalies. Quand on détecte qu'un
prix a chuté anormalement le 10 mars 2025, on requête GDELT sur cette
date pour récupérer les titres d'articles + leur tone (sentiment GDELT)
et les transmettre au LLM pour qu'il corrèle les deux.

═══════════════════════════════════════════════════════════════════════
STRATÉGIE DE FILTRAGE — 3 niveaux
═══════════════════════════════════════════════════════════════════════

Le champ `crypto_id` dans fact_gdelt_events n'est rempli que pour les
keywords spécifiques à une crypto particulière (ex: "Bitcoin", "Bitcoin
ETF", "Ethereum cryptocurrency"). Pour les keywords généraux ("crypto
regulation", "stablecoin", "DeFi"...) crypto_id est NULL — c'est le
design, pas un bug d'ingestion.

On utilise donc une stratégie en CASCADE par niveaux de pertinence :

  Tier 1 — Direct match crypto
           crypto_id = 'BTC' OR keyword ILIKE '%Bitcoin%'
           → articles spécifiques à la crypto

  Tier 2 — Crypto général (si Tier 1 ne remplit pas le quota)
           category = 'crypto_direct' AND crypto_id IS NULL
           → articles sur le marché crypto en général

  Tier 3 — Macro (si Tier 1+2 ne remplissent pas le quota)
           category = 'macro'
           → événements macro-économiques qui influencent les cryptos

Cette stratégie maximise la pertinence des articles tout en garantissant
qu'on récupère TOUJOURS du contexte si la base contient des articles à
ces dates.

Pas de logique métier hardcodée :
  - L'entity_name est passé en paramètre par l'appelant
  - Les filtres de tone, le LIMIT, et le tri sont des constantes du module
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)


# ─── Constantes du module ─────────────────────────────────────────────────

# Nombre maximum d'articles retournés par appel.
MAX_ARTICLES_PER_QUERY = 8

# Tone minimum (en valeur absolue) pour qu'un article soit retourné.
# Un tone proche de 0 est neutre, donc peu informatif pour expliquer
# une anomalie. On garde les articles avec un signal de sentiment net.
MIN_ABS_TONE = 1.0


# ─── Mapping entity_name → patterns de recherche ─────────────────────────
#
# Pour chaque crypto, on définit :
#   - le code stocké dans crypto_id (BTC, ETH, etc.)
#   - les patterns ILIKE à chercher dans le champ keyword
#
# Cette table est DELIBEREMENT minimale — pas de mapping complet de toutes
# les cryptos. Si une crypto n'est pas listée ici, l'enrichissement
# retombera silencieusement sur Tier 2 + Tier 3 (articles génériques
# crypto + macro), ce qui reste informatif.
ENTITY_TO_GDELT_PATTERNS: dict[str, dict[str, list[str]]] = {
    "Bitcoin": {
        "crypto_id_codes": ["BTC"],
        "keyword_patterns": ["%Bitcoin%"],
    },
    "BTC": {
        "crypto_id_codes": ["BTC"],
        "keyword_patterns": ["%Bitcoin%"],
    },
    "Ethereum": {
        "crypto_id_codes": ["ETH"],
        "keyword_patterns": ["%Ethereum%"],
    },
    "ETH": {
        "crypto_id_codes": ["ETH"],
        "keyword_patterns": ["%Ethereum%"],
    },
    "Solana": {
        "crypto_id_codes": ["SOL"],
        "keyword_patterns": ["%Solana%"],
    },
    "SOL": {
        "crypto_id_codes": ["SOL"],
        "keyword_patterns": ["%Solana%"],
    },
    "Cardano": {
        "crypto_id_codes": ["ADA"],
        "keyword_patterns": ["%Cardano%"],
    },
    "ADA": {
        "crypto_id_codes": ["ADA"],
        "keyword_patterns": ["%Cardano%"],
    },
}


# ─── Fonction principale ──────────────────────────────────────────────────


def fetch_gdelt_context(
    dates: Iterable[str],
    *,
    entity_name: str | None = None,
    db_session_factory: Callable[[], Any] | None,
    max_articles: int = MAX_ARTICLES_PER_QUERY,
    min_abs_tone: float = MIN_ABS_TONE,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Récupère les articles GDELT publiés aux dates spécifiées pour
    enrichir une analyse.

    Args:
        dates : liste de dates au format ISO 'YYYY-MM-DD' (typiquement
                les dates d'anomalies détectées).
        entity_name : nom de l'entité concernée ("Bitcoin", "Ethereum",
                      "BTC", "ETH"...). Si None ou inconnu, on n'utilise
                      pas de Tier 1 spécifique (fallback Tier 2+3).
        db_session_factory : factory injectée qui retourne une connexion
                             PostgreSQL.
        max_articles : limite globale d'articles retournés (toutes tiers
                       confondues).
        min_abs_tone : tone minimum en valeur absolue (filtre les
                       articles trop neutres).

    Returns:
        (articles, warnings) où articles est trié par |tone| décroissant.
        Chaque article a un champ supplémentaire `relevance_tier` (1, 2 ou 3)
        pour traçabilité côté LLM.
    """
    warnings: list[str] = []
    dates_list = _normalize_dates(dates)

    # ─── Garde-fous ───────────────────────────────────────────────────
    if not dates_list:
        warnings.append("gdelt_enrichment: aucune date fournie, skip")
        return [], warnings

    if db_session_factory is None:
        warnings.append(
            "gdelt_enrichment: db_session_factory non fourni — "
            "l'enrichissement GDELT est désactivé"
        )
        return [], warnings

    # ─── Résolution de l'entity en patterns ──────────────────────────
    patterns = _resolve_entity_patterns(entity_name)
    if entity_name and not patterns:
        warnings.append(
            f"gdelt_enrichment: entity_name='{entity_name}' non mappé — "
            f"fallback sur Tier 2+3 (articles crypto+macro génériques)"
        )

    # ─── Récupération en cascade Tier 1 → 2 → 3 ──────────────────────
    all_articles: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # Tier 1 : direct match (si entity connue)
    if patterns:
        try:
            rows = _query_tier_1(
                db_session_factory,
                dates_list,
                patterns,
                min_abs_tone,
                limit=max_articles,
            )
            tier_1_articles = _format_articles(rows, tier=1)
            for art in tier_1_articles:
                url = art.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_articles.append(art)
        except Exception as e:  # noqa: BLE001
            warnings.append(
                f"gdelt_enrichment: Tier 1 échec ({type(e).__name__}: {e})"
            )
            logger.warning("gdelt Tier 1 SQL error", exc_info=True)

    # Tier 2 : crypto général (si quota pas atteint)
    remaining = max_articles - len(all_articles)
    if remaining > 0:
        try:
            rows = _query_tier_2(
                db_session_factory,
                dates_list,
                min_abs_tone,
                limit=remaining,
            )
            tier_2_articles = _format_articles(rows, tier=2)
            for art in tier_2_articles:
                url = art.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_articles.append(art)
                    if len(all_articles) >= max_articles:
                        break
        except Exception as e:  # noqa: BLE001
            warnings.append(
                f"gdelt_enrichment: Tier 2 échec ({type(e).__name__}: {e})"
            )
            logger.warning("gdelt Tier 2 SQL error", exc_info=True)

    # Tier 3 : macro (si quota pas atteint)
    remaining = max_articles - len(all_articles)
    if remaining > 0:
        try:
            rows = _query_tier_3(
                db_session_factory,
                dates_list,
                min_abs_tone,
                limit=remaining,
            )
            tier_3_articles = _format_articles(rows, tier=3)
            for art in tier_3_articles:
                url = art.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_articles.append(art)
                    if len(all_articles) >= max_articles:
                        break
        except Exception as e:  # noqa: BLE001
            warnings.append(
                f"gdelt_enrichment: Tier 3 échec ({type(e).__name__}: {e})"
            )
            logger.warning("gdelt Tier 3 SQL error", exc_info=True)

    # ─── Tri global par |tone| décroissant ───────────────────────────
    all_articles.sort(
        key=lambda a: abs(a.get("tone") or 0.0),
        reverse=True,
    )

    if not all_articles:
        warnings.append(
            f"gdelt_enrichment: aucun article trouvé pour {len(dates_list)} "
            f"date(s) (entity='{entity_name}')"
        )

    return all_articles, warnings


# ─── Helpers de résolution ────────────────────────────────────────────────


def _resolve_entity_patterns(
    entity_name: str | None,
) -> dict[str, list[str]] | None:
    """
    Convertit un entity_name en patterns SQL (crypto_id_codes + keyword_patterns).

    Returns:
        dict avec 'crypto_id_codes' et 'keyword_patterns', ou None si
        l'entity n'est pas dans le mapping connu.
    """
    if not entity_name:
        return None
    return ENTITY_TO_GDELT_PATTERNS.get(entity_name)


def _normalize_dates(dates: Iterable[str]) -> list[str]:
    """Filtre les dates valides au format YYYY-MM-DD, déduplique."""
    seen: set[str] = set()
    out: list[str] = []
    for d in dates:
        if not d:
            continue
        s = str(d).strip()[:10]
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


# ─── Requêtes par tier ────────────────────────────────────────────────────


def _query_tier_1(
    db_session_factory: Callable[[], Any],
    dates: list[str],
    patterns: dict[str, list[str]],
    min_abs_tone: float,
    limit: int,
) -> list[tuple[Any, ...]]:
    """
    Tier 1 — articles directement liés à l'entité.

    Match si :
      - crypto_id IN ('BTC', ...) (codes officiels)
      - OU keyword ILIKE ANY (%Bitcoin%, ...) (patterns textuels)
    """
    sql = """
        SELECT
            date::text AS date,
            title,
            tone,
            source_domain,
            category,
            url
        FROM fact_gdelt_events
        WHERE date::text = ANY(%(dates)s)
          AND title IS NOT NULL
          AND tone IS NOT NULL
          AND ABS(tone) >= %(min_abs_tone)s
          AND (
              crypto_id = ANY(%(crypto_codes)s)
              OR keyword ILIKE ANY(%(keyword_patterns)s)
          )
        ORDER BY ABS(tone) DESC, date DESC
        LIMIT %(limit)s
    """
    params = {
        "dates": dates,
        "min_abs_tone": min_abs_tone,
        "crypto_codes": patterns.get("crypto_id_codes", []),
        "keyword_patterns": patterns.get("keyword_patterns", []),
        "limit": limit,
    }
    return _execute_query(db_session_factory, sql, params)


def _query_tier_2(
    db_session_factory: Callable[[], Any],
    dates: list[str],
    min_abs_tone: float,
    limit: int,
) -> list[tuple[Any, ...]]:
    """
    Tier 2 — articles crypto générique (pas liés à une crypto précise).

    Match si :
      - category = 'crypto_direct'
      - ET crypto_id IS NULL
    """
    sql = """
        SELECT
            date::text AS date,
            title,
            tone,
            source_domain,
            category,
            url
        FROM fact_gdelt_events
        WHERE date::text = ANY(%(dates)s)
          AND title IS NOT NULL
          AND tone IS NOT NULL
          AND ABS(tone) >= %(min_abs_tone)s
          AND category = 'crypto_direct'
          AND crypto_id IS NULL
        ORDER BY ABS(tone) DESC, date DESC
        LIMIT %(limit)s
    """
    params = {
        "dates": dates,
        "min_abs_tone": min_abs_tone,
        "limit": limit,
    }
    return _execute_query(db_session_factory, sql, params)


def _query_tier_3(
    db_session_factory: Callable[[], Any],
    dates: list[str],
    min_abs_tone: float,
    limit: int,
) -> list[tuple[Any, ...]]:
    """
    Tier 3 — articles macro-économiques (Fed, inflation, etc.).

    Ces événements influencent indirectement les cryptos.
    """
    sql = """
        SELECT
            date::text AS date,
            title,
            tone,
            source_domain,
            category,
            url
        FROM fact_gdelt_events
        WHERE date::text = ANY(%(dates)s)
          AND title IS NOT NULL
          AND tone IS NOT NULL
          AND ABS(tone) >= %(min_abs_tone)s
          AND category = 'macro'
        ORDER BY ABS(tone) DESC, date DESC
        LIMIT %(limit)s
    """
    params = {
        "dates": dates,
        "min_abs_tone": min_abs_tone,
        "limit": limit,
    }
    return _execute_query(db_session_factory, sql, params)


# ─── Exécution & formatage ────────────────────────────────────────────────


def _execute_query(
    db_session_factory: Callable[[], Any],
    sql: str,
    params: dict[str, Any],
) -> list[tuple[Any, ...]]:
    """Exécute la requête via le factory injecté."""
    with db_session_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _format_articles(
    rows: list[tuple[Any, ...]],
    tier: int,
) -> list[dict[str, Any]]:
    """Transforme les tuples DB en dicts JSON-safe."""
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            date_str, title, tone, source_domain, category, url = row
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "date": str(date_str) if date_str else None,
                "title": str(title) if title else None,
                "tone": float(tone) if tone is not None else None,
                "source_domain": str(source_domain) if source_domain else None,
                "category": str(category) if category else None,
                "url": str(url) if url else None,
                "relevance_tier": tier,
            }
        )
    return out


# ─── Helper d'extraction depuis SemanticContext ──────────────────────────


def extract_entity_name_from_context(
    semantic_context: dict[str, Any] | None,
) -> str | None:
    """
    Extrait le nom de l'entité crypto depuis le SemanticContext pour
    l'utiliser dans le filtrage GDELT.

    Stratégie :
      1. SemanticContext.entity_filters[0].entity_name (priorité)
      2. SemanticContext.entity_filters[0].value (le symbol, BTC, ETH...)

    Returns:
        nom d'entité (str) ou None si pas trouvable.
    """
    if not isinstance(semantic_context, dict):
        return None

    entity_filters = semantic_context.get("entity_filters") or []
    if not isinstance(entity_filters, list) or not entity_filters:
        return None

    for ef in entity_filters:
        if not isinstance(ef, dict):
            continue
        # On ne s'intéresse qu'aux entités crypto pour GDELT
        if ef.get("entity_type") != "crypto":
            continue
        # Priorité 1 : entity_name (forme canonique : "Bitcoin")
        name = ef.get("entity_name")
        if isinstance(name, str) and name:
            return name
        # Priorité 2 : value (le symbol : "BTC")
        val = ef.get("value")
        if isinstance(val, str) and val:
            return val

    return None


# ─── Compat ascendante avec l'ancien nom (pour ne rien casser) ───────────
#
# L'ancienne API exportait `extract_crypto_id_from_context`. On garde un
# alias qui retourne None systématiquement maintenant (le crypto_id int
# n'est plus utilisé), pour que les imports existants ne plantent pas.
# La task anomaly_detection.py devra basculer sur extract_entity_name_from_context.

def extract_crypto_id_from_context(
    semantic_context: dict[str, Any] | None,
    df_records: list[dict[str, Any]] | None = None,
) -> int | None:
    """
    DEPRECATED — utiliser extract_entity_name_from_context.

    Garde l'API existante pour ne pas casser les imports. Retourne
    toujours None car le filtrage GDELT n'utilise plus crypto_id (int).
    """
    return None