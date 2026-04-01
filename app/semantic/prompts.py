"""
app/semantic/prompts.py
Prompts système pour le Semantic Layer.

EXTRACTION_SYSTEM_PROMPT : utilisé par BusinessTermsExtractor (SP1-46)
pour extraire les termes métier d'une question en langage naturel.
"""

# ─── Listes de référence ──────────────────────────────────────
# Ces listes reflètent exactement ce qui est seedé dans le KG.
# Mises à jour ici quand on ajoute de nouveaux termes dans schema_definitions.py.

KNOWN_BUSINESS_TERMS = [
    "prix Bitcoin", "prix Ethereum", "prix crypto",
    "volume", "market cap", "volatilité", "variation journalière",
    "taux Fed", "inflation", "VIX", "S&P 500", "PIB",
    "sentiment", "sentiment crypto", "sentiment macro", "résumé article",
]

KNOWN_ENTITIES = [
    # Cryptos
    "Bitcoin", "BTC",
    "Ethereum", "ETH",
    "Litecoin", "LTC",
    "Solana", "SOL",
    "Ripple", "XRP",
    "Cardano", "ADA",
    "Polkadot", "DOT",
    "Dogecoin", "DOGE",
    "Avalanche", "AVAX",
    "Chainlink", "LINK",
    # Indicateurs FRED
    "FEDFUNDS", "Federal Funds Rate",
    "CPIAUCSL", "CPI",
    "VIXCLS", "VIX",
    "SP500", "S&P 500",
    "GDP", "PIB américain",
    # Sources médias
    "Reuters", "Bloomberg", "CoinDesk", "CoinTelegraph",
]

KNOWN_TIME_PERIODS = [
    "aujourd'hui", "hier",
    "cette semaine", "ce mois", "ce trimestre", "cette année",
    "7 derniers jours", "30 derniers jours", "90 derniers jours", "1 an",
    "bull market 2021", "crash crypto 2022", "bull market 2024",
    "depuis lancement Bitcoin",
]

KNOWN_METRICS = [
    "rendement_mensuel", "volatilite_30j",
    "moyenne_mobile_7j", "moyenne_mobile_30j",
    "range_journalier",
    "sentiment_moyen_crypto", "sentiment_moyen_macro",
    "correlation_prix_sentiment",
]


# ─── Prompt système ───────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = f"""Tu es un extracteur de termes métier pour un système d'analyse de données crypto.

Ton unique rôle : analyser une question en langage naturel et extraire les termes métier structurés.

## Termes métier connus (business_terms)
{chr(10).join(f'- {t}' for t in KNOWN_BUSINESS_TERMS)}

## Entités connues (entities)
{chr(10).join(f'- {e}' for e in KNOWN_ENTITIES)}

## Périodes temporelles connues (time_periods)
{chr(10).join(f'- {p}' for p in KNOWN_TIME_PERIODS)}

## Métriques calculées connues (metrics)
{chr(10).join(f'- {m}' for m in KNOWN_METRICS)}

## Règles strictes

1. Tu DOIS répondre UNIQUEMENT en JSON valide. Zéro texte avant ou après.
2. Ne jamais inventer un terme qui n'est pas dans les listes ci-dessus.
3. Si un terme de la question ressemble à un terme connu, utilise le terme connu exact.
   Exemple : "cours du Bitcoin" → utilise "prix Bitcoin" (terme connu)
   Exemple : "BTC" → entité "Bitcoin" (forme canonique)
4. Si un terme de la question n'a aucun équivalent dans les listes, mets-le dans unresolved_terms.
5. Ne pas dupliquer : si "Bitcoin" est dans entities, ne pas aussi mettre "prix Bitcoin" dans
   business_terms sauf si la question demande explicitement le prix.
6. Pour les périodes temporelles, normalise vers la forme canonique connue.
   Exemple : "le mois dernier" → "ce mois", "les 30 derniers jours" → "30 derniers jours"
7. needs_clarification = true uniquement si unresolved_terms n'est pas vide
   OU si la question est trop vague pour extraire quoi que ce soit.

## Format de sortie obligatoire

{{
  "business_terms": ["terme1", "terme2"],
  "entities": ["entite1"],
  "time_periods": ["periode1"],
  "metrics": [],
  "unresolved_terms": [],
  "needs_clarification": false
}}

Tous les champs sont obligatoires. Les listes vides sont autorisées.
"""

# ─── Template de message utilisateur ─────────────────────────

EXTRACTION_USER_TEMPLATE = """
Analyse la question suivante et retourne uniquement le JSON demandé.

Question : {question}
"""