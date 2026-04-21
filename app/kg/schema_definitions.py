"""
app/kg/schema_definitions.py
Définitions complètes du Knowledge Graph.
Importé par scripts/setup_neo4j_schema.py pour le seed.

Contient :
  PG_TABLES      — tables + colonnes (avec is_nullable)
  TABLE_JOINS    — relations entre tables
  ENTITIES       — entités métier (cryptos, séries FRED, sources médias)
  BUSINESS_TERMS — termes métier + synonymes + résolution vers colonnes
  METRICS        — formules SQL calculées
  BUSINESS_RULES — règles implicites toujours appliquées
  TIME_PERIODS   — expressions temporelles en langage naturel
"""

# ─── Tables PostgreSQL ────────────────────────────────────────
# Structure : (table_name, category, description, columns)
# columns   : (col_name, data_type, is_key, is_nullable, description)

PG_TABLES = [

    # ── Dimensions ──────────────────────────────────────────
    ("dim_crypto", "dimension",
     "Référentiel des 10 cryptomonnaies suivies. "
     "Source : Alpha Vantage (OHLCV) + CoinGecko (market_cap).", [
        ("crypto_id",         "integer", True,  False, "Identifiant interne auto-incrémenté"),
        ("symbol",            "varchar", False, False, "Symbole ticker : BTC, ETH, LTC, SOL, XRP, ADA, DOT, DOGE, AVAX, LINK"),
        ("name",              "varchar", False, False, "Nom complet : Bitcoin, Ethereum, Litecoin..."),
        ("alpha_vantage_key", "varchar", False, False, "Clé endpoint Alpha Vantage (ex: BTC)"),
        ("coingecko_key",     "varchar", False, True,  "Clé endpoint CoinGecko (ex: bitcoin)"),
        ("category",          "varchar", False, True,  "Catégorie : Layer1 | DeFi | Meme | Oracle | Exchange"),
        ("launch_date",       "date",    False, True,  "Date de lancement de la crypto"),
        ("description",       "text",    False, True,  "Description courte de la crypto"),
        ("is_active",         "boolean", False, True,  "True si la crypto est active dans le suivi"),
        ("created_at",        "timestamp", False, True, "Date d'insertion"),
        ("updated_at",        "timestamp", False, True, "Date de mise à jour"),
    ]),

    ("dim_fred_series", "dimension",
     "Référentiel des 5 séries macroéconomiques FRED suivies : "
     "FEDFUNDS (taux Fed), CPIAUCSL (inflation), VIXCLS (VIX), SP500, GDP.", [
        ("series_id",   "integer", True,  False, "Identifiant interne auto-incrémenté"),
        ("fred_code",   "varchar", False, False, "Code FRED : FEDFUNDS | CPIAUCSL | VIXCLS | SP500 | GDP"),
        ("name",        "varchar", False, False, "Nom lisible : Federal Funds Rate, CPI..."),
        ("description", "text",    False, True,  "Description détaillée de la série"),
        ("frequency",   "varchar", False, False, "Fréquence : Daily | Monthly | Quarterly"),
        ("units",       "varchar", False, True,  "Unité : Percent | Index | Billions of Dollars"),
        ("category",    "varchar", False, True,  "Catégorie : interest_rate | inflation | market | gdp"),
        ("is_active",   "boolean", False, True,  "True si la série est active dans le suivi"),
        ("created_at",  "timestamp", False, True, "Date d'insertion"),
    ]),

    ("dim_time", "dimension",
     "Calendrier — table de dates pour les jointures temporelles.", [
        ("date_id",     "date",    True,  False, "Date — clé primaire"),
        ("year",        "integer", False, False, "Année (ex: 2024)"),
        ("quarter",     "integer", False, False, "Trimestre 1-4"),
        ("month",       "integer", False, False, "Mois 1-12"),
        ("month_name",  "varchar", False, False, "Nom du mois (January...)"),
        ("week",        "integer", False, False, "Numéro de semaine ISO"),
        ("day_of_week", "integer", False, False, "Jour de la semaine : 0=Lundi, 6=Dimanche"),
        ("day_name",    "varchar", False, False, "Nom du jour (Monday...)"),
        ("is_weekend",  "boolean", False, False, "True si samedi ou dimanche"),
        ("is_holiday",  "boolean", False, True,  "True si jour férié américain"),
    ]),

    # ── Tables de faits ─────────────────────────────────────
    ("fact_crypto_daily", "fact",
     "Prix journaliers OHLCV des cryptomonnaies. "
     "Table partitionnée par symbol. "
     "Partitions : fact_crypto_daily_btc, _eth, _ltc, _sol, _xrp, _ada, _dot, _doge, _avax, _link. "
     "Toujours interroger la table parent avec WHERE symbol = 'BTC', jamais les partitions directement. "
     "Source OHLCV : Alpha Vantage. Source market_cap : CoinGecko (365 derniers jours uniquement).", [
        ("id",             "bigint",  True,  False, "Identifiant unique auto-incrémenté"),
        ("crypto_id",      "integer", False, False, "FK → dim_crypto.crypto_id"),
        ("symbol",         "varchar", False, False, "Symbole ticker — clé de partition (BTC, ETH...)"),
        ("date",           "date",    False, False, "Date de la bougie journalière"),
        ("open_usd",       "numeric", False, False, "Prix d'ouverture en USD"),
        ("high_usd",       "numeric", False, False, "Prix le plus haut de la journée en USD"),
        ("low_usd",        "numeric", False, False, "Prix le plus bas de la journée en USD"),
        ("close_usd",      "numeric", False, False, "Prix de clôture en USD — référence principale pour les analyses"),
        ("volume",         "numeric", False, False, "Volume échangé en unités de crypto"),
        ("market_cap_usd", "numeric", False, True,  "Capitalisation boursière USD — nullable avant 365j (CoinGecko)"),
        ("source",         "varchar", False, True,  "Source : alpha_vantage | coingecko"),
        ("created_at",     "timestamp", False, True, "Date d'insertion"),
    ]),

    ("fact_fred_observation", "fact",
     "Observations des séries macroéconomiques FRED. "
     "Historique depuis 2009 pour la plupart des séries. "
     "Fréquences mixtes : quotidien (VIX, SP500), mensuel (CPI, Fed rate), trimestriel (GDP).", [
        ("id",        "bigint",  True,  False, "Identifiant unique auto-incrémenté"),
        ("series_id", "integer", False, False, "FK → dim_fred_series.series_id"),
        ("fred_code", "varchar", False, False, "Code FRED — filtre direct sans JOIN (FEDFUNDS, CPIAUCSL, VIXCLS, SP500, GDP)"),
        ("date",      "date",    False, False, "Date de l'observation"),
        ("value",     "numeric", False, True,  "Valeur dans l'unité de la série — nullable si donnée non publiée"),
        ("source",    "varchar", False, True,  "Source : fred_api"),
        ("created_at","timestamp", False, True, "Date d'insertion"),
    ]),

    ("fact_gdelt_events", "fact",
     "Articles de presse collectés via GDELT DOC API. "
     "Deux niveaux : crypto_direct (Bitcoin, crypto regulation...) "
     "et macro (Fed rate, inflation, stock market crash...). "
     "Le tone est le sentiment GDELT : négatif=bearish, positif=bullish. "
     "Typiquement entre -10 et +10. "
     "positive_score, negative_score, polarity sont NULL — non fournis par timelinetone.", [
        ("id",             "bigint",  True,  False, "Identifiant unique auto-incrémenté"),
        ("date",           "date",    False, False, "Date de publication de l'article"),
        ("title",          "text",    False, True,  "Titre de l'article"),
        ("url",            "text",    False, True,  "URL unique — contrainte UNIQUE sur la table"),
        ("source_domain",  "varchar", False, True,  "Domaine source : reuters.com, bloomberg.com..."),
        ("source_country", "varchar", False, True,  "Code pays source : US, GB, FR..."),
        ("language",       "varchar", False, True,  "Langue : English, Russian, French..."),
        ("tone",           "numeric", False, True,  "Score de tonalité GDELT — signal sentiment principal"),
        ("positive_score", "numeric", False, True,  "Score positif GDELT — NULL (non fourni par timelinetone)"),
        ("negative_score", "numeric", False, True,  "Score négatif GDELT — NULL (non fourni par timelinetone)"),
        ("polarity",       "numeric", False, True,  "Polarité GDELT — NULL (non fourni par timelinetone)"),
        ("word_count",     "integer", False, True,  "Nombre de mots de l'article"),
        ("theme",          "varchar", False, True,  "Thème GDELT : ECON_BITCOINS | ECON_CRYPTOCURRENCY | ECON_CENTRALBANK | ECON_INFLATION | ECON_STOCKMARKET | GOV_REGULATION_FINANCIAL"),
        ("keyword",        "varchar", False, True,  "Keyword de recherche utilisé pour collecter l'article"),
        ("category",       "varchar", False, True,  "Niveau de collecte : crypto_direct | macro"),
        ("crypto_id",      "varchar", False, True,  "Symbole crypto concerné (BTC, ETH...) — NULL si macro global"),
        ("created_at",     "timestamp", False, True, "Date d'insertion"),
    ]),

    # ── Tables d'agrégation ─────────────────────────────────
    ("agg_daily_sentiment", "aggregation",
     "Sentiment médiatique agrégé par jour et par thème GDELT. "
     "Calculé depuis fact_gdelt_events GROUP BY date, theme. "
     "avg_tone est le signal principal utilisé par l'Analyse Agent "
     "pour corréler sentiment et mouvements de prix.", [
        ("id",            "bigint",  True,  False, "Identifiant unique auto-incrémenté"),
        ("date",          "date",    False, False, "Date d'agrégation"),
        ("keyword",       "varchar", False, False, "Thème GDELT agrégé (= colonne theme de fact_gdelt_events)"),
        ("article_count", "integer", False, True,  "Nombre d'articles du jour pour ce thème"),
        ("avg_tone",      "numeric", False, True,  "Tonalité moyenne du jour — signal sentiment principal"),
        ("avg_positive",  "numeric", False, True,  "Score positif moyen — NULL si non disponible"),
        ("avg_negative",  "numeric", False, True,  "Score négatif moyen — NULL si non disponible"),
        ("avg_polarity",  "numeric", False, True,  "Polarité moyenne — NULL si non disponible"),
        ("created_at",    "timestamp", False, True, "Date d'insertion"),
    ]),

    ("agg_monthly_crypto", "aggregation",
     "Agrégats mensuels des prix crypto. "
     "Calculé depuis fact_crypto_daily GROUP BY symbol, year, month. "
     "Utile pour les analyses de tendances long terme.", [
        ("agg_id",               "bigint",  True,  False, "Identifiant unique auto-incrémenté"),
        ("crypto_id",            "integer", False, False, "FK → dim_crypto.crypto_id"),
        ("symbol",               "varchar", False, False, "Symbole ticker"),
        ("year",                 "integer", False, False, "Année"),
        ("month",                "integer", False, False, "Mois 1-12"),
        ("open_usd",             "numeric", False, True,  "Prix d'ouverture du mois"),
        ("close_usd",            "numeric", False, True,  "Prix de clôture du mois"),
        ("high_usd",             "numeric", False, True,  "Plus haut du mois"),
        ("low_usd",              "numeric", False, True,  "Plus bas du mois"),
        ("avg_close_usd",        "numeric", False, True,  "Prix de clôture moyen du mois"),
        ("total_volume",         "numeric", False, True,  "Volume total mensuel"),
        ("monthly_change_pct",   "numeric", False, True,  "Variation mensuelle en %"),
        ("avg_daily_volatility", "numeric", False, True,  "Volatilité journalière moyenne du mois"),
        ("trading_days",         "integer", False, True,  "Jours avec données dans le mois"),
        ("created_at",           "timestamp", False, True, "Date d'insertion"),
    ]),

    # ── Staging ─────────────────────────────────────────────
    ("stg_daily_metrics", "staging",
     "Métriques journalières dérivées de fact_crypto_daily. "
     "Contient : variation %, range journalier, volatilité. "
     "Utilisé par l'Analyse Agent pour la détection d'anomalies.", [
        ("metric_id",        "bigint",  True,  False, "Identifiant unique auto-incrémenté"),
        ("crypto_id",        "integer", False, False, "FK → dim_crypto.crypto_id"),
        ("symbol",           "varchar", False, False, "Symbole ticker"),
        ("date",             "date",    False, False, "Date"),
        ("close_usd",        "numeric", False, False, "Prix de clôture"),
        ("prev_close_usd",   "numeric", False, True,  "Prix de clôture J-1 — NULL pour le premier jour"),
        ("daily_change_pct", "numeric", False, True,  "Variation journalière % = (close - prev) / prev * 100"),
        ("daily_range_usd",  "numeric", False, True,  "Range journalier USD = high - low"),
        ("volatility_pct",   "numeric", False, True,  "Volatilité journalière % = range / close * 100"),
        ("volume",           "numeric", False, True,  "Volume journalier"),
        ("created_at",       "timestamp", False, True, "Date d'insertion"),
    ]),

    # ── Enrichissement ──────────────────────────────────────
    ("article_enrichment", "enrichment",
     "Contenu extrait et résumés LLM des articles GDELT. "
     "Pipeline Sprint 2 : Tavily Extract → Claude → summary + entities + impact_type. "
     "Enrichit uniquement les top articles sélectionnés par score (TOP_K=15/run). "
     "Score = abs(tone)>3 (+2 pts) + crypto_direct (+1 pt) + récent 7j (+1 pt).", [
        ("id",             "integer",   True,  False, "Identifiant unique auto-incrémenté"),
        ("url",            "text",      False, False, "URL de l'article — FK → fact_gdelt_events.url — UNIQUE"),
        ("status",         "varchar",   False, True,  "Statut : pending | ok | failed | skipped"),
        ("selection_score","numeric",   False, True,  "Score de sélection composite (0-4)"),
        ("extracted_at",   "timestamp", False, True,  "Date d'extraction Tavily"),
        ("raw_content",    "text",      False, True,  "Contenu brut Tavily (tronqué à 4000 chars)"),
        ("llm_model",      "text",      False, True,  "Modèle LLM utilisé (claude-haiku-4-5-20251001)"),
        ("summarized_at",  "timestamp", False, True,  "Date de résumé LLM"),
        ("summary",        "text",      False, True,  "Résumé 2-3 phrases généré par Claude"),
        ("entities",       "jsonb",     False, True,  "Entités extraites : {countries, companies, regulators}"),
        ("impact_type",    "varchar",   False, True,  "Type : regulation | hack | adoption | macro | market_move | other"),
        ("llm_sentiment",  "numeric",   False, True,  "Score LLM : -1.0 (très négatif) à +1.0 (très positif)"),
        ("created_at",     "timestamp", False, True,  "Date d'insertion"),
    ]),
]

# ─── Relations entre tables ───────────────────────────────────
# Structure : (from_table, from_col, to_table, to_col, rel_type, description)

TABLE_JOINS = [
    ("fact_crypto_daily",     "crypto_id",  "dim_crypto",        "crypto_id",
     "JOIN",        "JOIN sur crypto_id → symbol, name, category, launch_date"),

    ("fact_crypto_daily",     "date",       "dim_time",          "date_id",
     "JOIN",        "JOIN sur date → year, quarter, month, week, is_weekend"),

    ("fact_fred_observation", "series_id",  "dim_fred_series",   "series_id",
     "JOIN",        "JOIN sur series_id → fred_code, name, units, frequency, category"),

    ("stg_daily_metrics",     "crypto_id",  "fact_crypto_daily", "crypto_id",
     "DERIVED_FROM","Calculé depuis fact_crypto_daily — même date et crypto_id"),

    ("agg_monthly_crypto",    "crypto_id",  "fact_crypto_daily", "crypto_id",
     "AGGREGATES",  "GROUP BY symbol, year, month depuis fact_crypto_daily"),

    ("agg_daily_sentiment",   "keyword",    "fact_gdelt_events", "theme",
     "AGGREGATES",  "GROUP BY date, theme depuis fact_gdelt_events"),

    ("article_enrichment",    "url",        "fact_gdelt_events", "url",
     "ENRICHES",    "Enrichit fact_gdelt_events via Tavily Extract + Claude LLM"),
]

# ─── Entités métier ───────────────────────────────────────────
# Objets du monde réel représentés dans les données.
# Structure : (entity_id, entity_type, label, description, represents_table, represents_column, filter_value)

ENTITIES = [
    # ── Cryptomonnaies ───────────────────────────────────────
    ("entity_BTC",  "crypto", "Bitcoin",
     "Première et plus grande cryptomonnaie par capitalisation. Créée en 2009 par Satoshi Nakamoto.",
     "fact_crypto_daily", "symbol", "BTC"),

    ("entity_ETH",  "crypto", "Ethereum",
     "Plateforme de smart contracts. Deuxième crypto par capitalisation. Créée par Vitalik Buterin en 2015.",
     "fact_crypto_daily", "symbol", "ETH"),

    ("entity_LTC",  "crypto", "Litecoin",
     "Fork de Bitcoin avec des transactions plus rapides. Créé en 2011.",
     "fact_crypto_daily", "symbol", "LTC"),

    ("entity_SOL",  "crypto", "Solana",
     "Blockchain haute performance. Créée en 2020. Connue pour sa vitesse et ses faibles frais.",
     "fact_crypto_daily", "symbol", "SOL"),

    ("entity_XRP",  "crypto", "Ripple XRP",
     "Crypto de Ripple Labs pour les paiements transfrontaliers. Sujet à un procès SEC depuis 2020.",
     "fact_crypto_daily", "symbol", "XRP"),

    ("entity_ADA",  "crypto", "Cardano",
     "Blockchain proof-of-stake fondée sur la recherche académique. Créée par Charles Hoskinson.",
     "fact_crypto_daily", "symbol", "ADA"),

    ("entity_DOT",  "crypto", "Polkadot",
     "Protocole d'interopérabilité entre blockchains. Créé par Gavin Wood, co-fondateur d'Ethereum.",
     "fact_crypto_daily", "symbol", "DOT"),

    ("entity_DOGE", "crypto", "Dogecoin",
     "Memecoin créé en 2013. Popularisé par Elon Musk. Communauté très active.",
     "fact_crypto_daily", "symbol", "DOGE"),

    ("entity_AVAX", "crypto", "Avalanche",
     "Plateforme smart contracts haute vitesse. Alternative à Ethereum. Créée en 2020.",
     "fact_crypto_daily", "symbol", "AVAX"),

    ("entity_LINK", "crypto", "Chainlink",
     "Oracle décentralisé connectant les smart contracts aux données du monde réel.",
     "fact_crypto_daily", "symbol", "LINK"),

    # ── Séries macroéconomiques FRED ─────────────────────────
    ("entity_FEDFUNDS", "macro_indicator", "Federal Funds Rate",
     "Taux directeur de la Réserve Fédérale américaine. "
     "Influence directe sur les marchés crypto : hausse = bearish, baisse = bullish.",
     "fact_fred_observation", "fred_code", "FEDFUNDS"),

    ("entity_CPIAUCSL", "macro_indicator", "CPI — Consumer Price Index",
     "Indice des prix à la consommation américain. Mesure l'inflation. "
     "Publié mensuellement. CPI élevé → Fed hawkish → bearish crypto.",
     "fact_fred_observation", "fred_code", "CPIAUCSL"),

    ("entity_VIXCLS", "macro_indicator", "VIX — Volatility Index",
     "Fear Index — mesure la volatilité implicite du S&P 500. "
     "VIX > 30 = peur sur les marchés = souvent bearish crypto.",
     "fact_fred_observation", "fred_code", "VIXCLS"),

    ("entity_SP500", "macro_indicator", "S&P 500",
     "Indice des 500 plus grandes entreprises américaines. "
     "Corrélation positive avec Bitcoin depuis 2020.",
     "fact_fred_observation", "fred_code", "SP500"),

    ("entity_GDP", "macro_indicator", "GDP — Gross Domestic Product",
     "PIB américain trimestriel en milliards USD. "
     "Indicateur de santé économique globale.",
     "fact_fred_observation", "fred_code", "GDP"),

    # ── Sources médias GDELT ─────────────────────────────────
    ("entity_reuters",    "media_source", "Reuters",
     "Agence de presse internationale — source majeure dans fact_gdelt_events.",
     "fact_gdelt_events", "source_domain", "reuters.com"),

    ("entity_bloomberg",  "media_source", "Bloomberg",
     "Media financier américain — forte couverture crypto et macro.",
     "fact_gdelt_events", "source_domain", "bloomberg.com"),

    ("entity_coindesk",   "media_source", "CoinDesk",
     "Media spécialisé crypto — forte couverture crypto_direct.",
     "fact_gdelt_events", "source_domain", "coindesk.com"),

    ("entity_cointelegraph", "media_source", "CoinTelegraph",
     "Media crypto — couverture Bitcoin, Ethereum, DeFi, régulation.",
     "fact_gdelt_events", "source_domain", "cointelegraph.com"),
]

# ─── Termes métier ────────────────────────────────────────────
# Structure : (term, domain, description, synonyms, resolves_to_table, resolves_to_column)

BUSINESS_TERMS = [

    # ── Prix crypto ──────────────────────────────────────────
    ("prix",            "crypto",
    "Prix de clôture d'une cryptomonnaie en USD. "
    "Terme générique qui doit être combiné avec une entité crypto "
    "comme Bitcoin, Ethereum, Solana, etc.",
    ["price", "cours", "valeur", "close", "closing price", "prix de clôture"],
    "fact_crypto_daily", "close_usd"),

    ("prix Bitcoin",    "crypto",
     "Prix de clôture journalier du Bitcoin en USD",
     ["BTC price", "Bitcoin price", "cours Bitcoin", "valeur Bitcoin", "BTC close", "prix BTC"],
     "fact_crypto_daily", "close_usd"),

    ("prix Ethereum",   "crypto",
     "Prix de clôture journalier de l'Ethereum en USD",
     ["ETH price", "Ethereum price", "cours ETH", "valeur ETH", "prix ETH"],
     "fact_crypto_daily", "close_usd"),

    ("prix crypto",     "crypto",
     "Prix de clôture d'une cryptomonnaie — toujours filtrer par symbol",
     ["crypto price", "cours crypto", "valeur crypto", "close price", "prix de clôture"],
     "fact_crypto_daily", "close_usd"),

    ("volume",          "crypto",
     "Volume journalier de crypto échangé en unités de crypto",
     ["trading volume", "volume d'échange", "volume de transactions", "daily volume", "volume échangé"],
     "fact_crypto_daily", "volume"),

    ("market cap",      "crypto",
     "Capitalisation boursière en USD — disponible sur 365 derniers jours seulement",
     ["capitalisation", "market capitalization", "cap boursière", "capitalisation boursière"],
     "fact_crypto_daily", "market_cap_usd"),

    ("volatilité",      "crypto",
     "Volatilité journalière = daily_range / close * 100",
     ["volatility", "fluctuation", "daily volatility", "volatilité journalière", "price volatility"],
     "stg_daily_metrics", "volatility_pct"),

    ("variation journalière", "crypto",
     "Variation du prix en % par rapport au jour précédent",
     ["daily change", "daily return", "rendement journalier", "daily pct change",
      "variation quotidienne", "daily variation", "price change"],
     "stg_daily_metrics", "daily_change_pct"),

    # ── Indicateurs macro ────────────────────────────────────
    ("taux Fed",        "macro",
     "Taux directeur de la Réserve Fédérale américaine — fred_code=FEDFUNDS",
     ["Fed rate", "federal funds rate", "taux directeur", "taux d'intérêt Fed",
      "interest rate", "Fed interest rate", "FEDFUNDS", "taux banque centrale"],
     "fact_fred_observation", "value"),

    ("inflation",       "macro",
     "Indice des prix à la consommation US — fred_code=CPIAUCSL",
     ["CPI", "inflation rate", "taux d'inflation", "consumer price index",
      "CPIAUCSL", "prix à la consommation", "indice des prix"],
     "fact_fred_observation", "value"),

    ("VIX",             "macro",
     "Indice de volatilité des marchés — Fear Index — fred_code=VIXCLS",
     ["volatility index", "fear index", "indice de peur", "VIXCLS",
      "market volatility", "VIX index", "indice volatilité"],
     "fact_fred_observation", "value"),

    ("S&P 500",         "macro",
     "Indice boursier américain — fred_code=SP500",
     ["SP500", "S&P500", "indice américain", "marché actions US",
      "US stock market", "bourse américaine", "indice boursier"],
     "fact_fred_observation", "value"),

    ("PIB",             "macro",
     "Produit Intérieur Brut américain trimestriel — fred_code=GDP",
     ["GDP", "gross domestic product", "croissance économique",
      "economic growth", "PIB américain", "croissance PIB"],
     "fact_fred_observation", "value"),

    # ── Sentiment ────────────────────────────────────────────
    ("sentiment",       "sentiment",
     "Score de tonalité moyen GDELT (avg_tone). "
     "Négatif=bearish, positif=bullish. Typiquement entre -5 et +5.",
     ["tone", "tonalité", "sentiment médiatique", "news sentiment",
      "media sentiment", "GDELT tone", "score de sentiment"],
     "agg_daily_sentiment", "avg_tone"),

    ("sentiment crypto", "sentiment",
     "Sentiment des articles crypto_direct "
     "(keyword IN ECON_BITCOINS, ECON_CRYPTOCURRENCY)",
     ["crypto sentiment", "Bitcoin sentiment", "sentiment crypto direct",
      "sentiment marché crypto"],
     "agg_daily_sentiment", "avg_tone"),

    ("sentiment macro",  "sentiment",
     "Sentiment des articles macro "
     "(keyword IN ECON_CENTRALBANK, ECON_INFLATION, ECON_STOCKMARKET, GOV_REGULATION_FINANCIAL)",
     ["macro sentiment", "sentiment macroéconomique", "sentiment Fed",
      "sentiment inflation", "sentiment régulation"],
     "agg_daily_sentiment", "avg_tone"),

    ("résumé article",   "sentiment",
     "Résumé LLM d'un article enrichi (généré par Claude via Tavily Extract) — Sprint 2",
     ["article summary", "news summary", "résumé actualité", "LLM summary", "article enrichi"],
     "article_enrichment", "summary"),
]

# ─── Métriques calculées ──────────────────────────────────────
# Structure : (name, domain, description, sql_formula, source_table)

METRICS = [
    ("rendement_mensuel",
     "crypto",
     "Variation du prix de clôture en % sur 30 jours glissants",
     "((close_usd - LAG(close_usd, 30) OVER (PARTITION BY symbol ORDER BY date)) "
     "/ LAG(close_usd, 30) OVER (PARTITION BY symbol ORDER BY date)) * 100",
     "fact_crypto_daily"),

    ("volatilite_30j",
     "crypto",
     "Volatilité sur 30 jours glissants = écart-type des variations journalières",
     "STDDEV(daily_change_pct) OVER (PARTITION BY symbol ORDER BY date ROWS 29 PRECEDING)",
     "stg_daily_metrics"),

    ("moyenne_mobile_7j",
     "crypto",
     "Moyenne mobile du prix de clôture sur 7 jours",
     "AVG(close_usd) OVER (PARTITION BY symbol ORDER BY date ROWS 6 PRECEDING)",
     "fact_crypto_daily"),

    ("moyenne_mobile_30j",
     "crypto",
     "Moyenne mobile du prix de clôture sur 30 jours",
     "AVG(close_usd) OVER (PARTITION BY symbol ORDER BY date ROWS 29 PRECEDING)",
     "fact_crypto_daily"),

    ("range_journalier",
     "crypto",
     "Range journalier en USD = high_usd - low_usd",
     "high_usd - low_usd",
     "fact_crypto_daily"),

    ("sentiment_moyen_crypto",
     "sentiment",
     "Sentiment médiatique moyen des articles crypto_direct",
     "AVG(avg_tone) FILTER (WHERE keyword IN ('ECON_BITCOINS', 'ECON_CRYPTOCURRENCY'))",
     "agg_daily_sentiment"),

    ("sentiment_moyen_macro",
     "sentiment",
     "Sentiment médiatique moyen des articles macro",
     "AVG(avg_tone) FILTER (WHERE keyword IN "
     "('ECON_CENTRALBANK', 'ECON_INFLATION', 'ECON_STOCKMARKET', 'GOV_REGULATION_FINANCIAL'))",
     "agg_daily_sentiment"),

    ("correlation_prix_sentiment",
     "analytics",
     "Corrélation de Pearson entre prix de clôture et sentiment du même jour",
     "CORR(f.close_usd, s.avg_tone) FROM fact_crypto_daily f "
     "JOIN agg_daily_sentiment s ON f.date = s.date "
     "WHERE f.symbol = :symbol",
     "fact_crypto_daily"),
]

# ─── Règles métier implicites ─────────────────────────────────
# Structure : (rule_id, table, description, sql_condition)

BUSINESS_RULES = [
    ("exclude_zero_volume",
     "fact_crypto_daily",
     "Exclure les jours sans volume — données manquantes ou marchés fermés",
     "volume > 0",
     "sql_predicate"),
 
    ("active_cryptos_only",
     "dim_crypto",
     "Utiliser uniquement les cryptos actives dans le suivi",
     "is_active = true",
     "sql_predicate"),
 
    ("valid_fred_values",
     "fact_fred_observation",
     "Exclure les observations FRED avec valeur NULL — données non encore publiées",
     "value IS NOT NULL",
     "sql_predicate"),
 
    ("enriched_articles_only",
     "article_enrichment",
     "Pour les analyses de contenu LLM, utiliser uniquement les articles enrichis avec succès",
     "status = 'ok'",
     "sql_predicate"),
 
    ("crypto_direct_sentiment",
     "agg_daily_sentiment",
     "Pour le sentiment crypto, filtrer sur les thèmes crypto_direct",
     "keyword IN ('ECON_BITCOINS', 'ECON_CRYPTOCURRENCY')",
     "sql_predicate"),
 
    ("macro_sentiment",
     "agg_daily_sentiment",
     "Pour le sentiment macro, filtrer sur les thèmes macro",
     "keyword IN ('ECON_CENTRALBANK', 'ECON_INFLATION', 'ECON_STOCKMARKET', 'GOV_REGULATION_FINANCIAL')",
     "sql_predicate"),
 
    ("use_parent_table",
     "fact_crypto_daily",
     "Toujours interroger la table parent fact_crypto_daily, "
     "jamais les partitions _btc/_eth/etc. directement. "
     "Filtrer par symbol pour cibler une crypto spécifique.",
     "Utiliser fact_crypto_daily avec WHERE symbol = :symbol, "
     "ne jamais interroger les partitions fact_crypto_daily_btc/eth/etc.",
     "query_guideline"),
 
    ("market_cap_365_days_only",
     "fact_crypto_daily",
     "market_cap_usd est NULL avant les 365 derniers jours — "
     "toujours filtrer sur la période récente pour les analyses de capitalisation",
     "date >= CURRENT_DATE - INTERVAL '365 days' AND market_cap_usd IS NOT NULL",
     "sql_predicate"),
]

# ─── Périodes temporelles ─────────────────────────────────────
# Structure : (name, sql_expression, filter_expression)

TIME_PERIODS = [
    ("aujourd'hui",
     "CURRENT_DATE",
     "date = CURRENT_DATE"),

    ("hier",
     "CURRENT_DATE - INTERVAL '1 day'",
     "date = CURRENT_DATE - INTERVAL '1 day'"),

    ("cette semaine",
     "DATE_TRUNC('week', CURRENT_DATE)",
     "date >= DATE_TRUNC('week', CURRENT_DATE)"),

    ("ce mois",
     "DATE_TRUNC('month', CURRENT_DATE)",
     "date >= DATE_TRUNC('month', CURRENT_DATE)"),

    ("ce trimestre",
     "DATE_TRUNC('quarter', CURRENT_DATE)",
     "date >= DATE_TRUNC('quarter', CURRENT_DATE)"),

    ("cette année",
     "DATE_TRUNC('year', CURRENT_DATE)",
     "date >= DATE_TRUNC('year', CURRENT_DATE)"),

    ("7 derniers jours",
     "CURRENT_DATE - INTERVAL '7 days'",
     "date >= CURRENT_DATE - INTERVAL '7 days'"),

    ("30 derniers jours",
     "CURRENT_DATE - INTERVAL '30 days'",
     "date >= CURRENT_DATE - INTERVAL '30 days'"),

    ("90 derniers jours",
     "CURRENT_DATE - INTERVAL '90 days'",
     "date >= CURRENT_DATE - INTERVAL '90 days'"),

    ("1 an",
     "CURRENT_DATE - INTERVAL '1 year'",
     "date >= CURRENT_DATE - INTERVAL '1 year'"),

    ("bull market 2021",
     "'2021-01-01'",
     "date BETWEEN '2021-01-01' AND '2021-11-09'"),

    ("crash crypto 2022",
     "'2022-01-01'",
     "date BETWEEN '2022-01-01' AND '2022-12-31'"),

    ("bull market 2024",
     "'2024-01-01'",
     "date BETWEEN '2024-01-01' AND '2024-12-31'"),

    ("depuis lancement Bitcoin",
     "'2010-07-17'",
     "date >= '2010-07-17'"),
]