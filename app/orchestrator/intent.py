"""
app/orchestrator/intent.py
Détection d'intent — SEUL appel LLM de l'Orchestrator.

Classifie la question parmi 6 intents :
  - aggregation      : "montre le prix du BTC ce mois"
  - comparison       : "compare BTC et ETH sur Q1"
  - correlation      : "lien entre Fed rate et BTC"
  - anomaly_detection: "anomalies du volume SOL"
  - forecasting      : "prévois ETH sur 30 jours"
  - diagnosis        : "pourquoi BTC a chuté la semaine dernière"

L'Orchestrator utilise l'intent pour router vers le bon sous-flow,
mais c'est le plan generator qui intègre aussi le SemanticContext
pour adapter finement le plan à la forme de la question.
"""

from __future__ import annotations

import logging

from app.llm import get_llm_client
from app.llm.client import LLMError, LLMSchemaError
from app.orchestrator.schemas import Intent, IntentType

logger = logging.getLogger(__name__)

# Seuil configurable — si confidence < threshold, needs_clarification=True
DEFAULT_CONFIDENCE_THRESHOLD = 0.55


INTENT_SYSTEM_PROMPT = """Tu es un classifieur d'intent pour un système d'analyse de données crypto et macroéconomique.

Tu reçois une question en langage naturel et tu retournes son intent parmi ces 7 catégories :

1. **aggregation** — Demande une valeur ou un ensemble de valeurs direct.
   Exemples : "montre le prix du Bitcoin ce mois", "volume de SOL hier", "liste des cryptos"

2. **comparison** — Compare deux entités ou deux périodes entre elles.
   Exemples : "compare BTC et ETH en Q1 2024", "ventes Q1 vs Q2", "Bitcoin vs Ethereum sur 2024"

3. **correlation** — Cherche une relation statistique entre deux variables ou entités.
   Exemples : "lien entre Fed rate et BTC", "corrélation volume/prix", "quand le S&P monte, que fait ETH"

4. **anomaly_detection** — Identifie des valeurs inhabituelles, pics, creux.
   Exemples : "anomalies du volume SOL", "mouvements bizarres sur BTC", "jours où ETH a sur-réagi"

5. **forecasting** — Prédit des valeurs futures basées sur l'historique.
   Exemples : "prévois ETH sur 30 jours", "où va le prix du BTC en fin 2026", "tendance Solana"

6. **diagnosis** — Cherche les causes d'un phénomène observé. Souvent composite
   (implique anomaly + correlation).
   Exemples : "pourquoi BTC a chuté la semaine dernière", "qu'est-ce qui a causé le crash de mai"

7. **external_knowledge** — Demande de définition, explication ou contexte sur un
   concept, indicateur ou actualité qui n'est pas dans la base de données interne.
   La réponse viendra d'une recherche web (Tavily) suivie d'un résumé synthétique.
   Exemples :
     - "explique le fear and greed index"
     - "qu'est-ce que le halving Bitcoin"
     - "définition du funding rate"
     - "actualités sur la régulation crypto en Europe"
     - "qui est Vitalik Buterin"
   À ne PAS confondre avec aggregation : si la question demande une VALEUR concrète
   sur les données internes (prix, volume, ratio), c'est aggregation. Si la question
   demande de COMPRENDRE un concept ou de RAFRAÎCHIR avec des actualités web, c'est
   external_knowledge.

Règles de classification :

- Choisis l'intent PRINCIPAL qui décrit le mieux ce que l'utilisateur veut.
- Si la question combine plusieurs intents (ex: diagnosis implique anomaly + correlation),
  liste les secondaires dans `secondary`.
- `confidence` = ta certitude sur la classification (0.0 à 1.0).
- `reasoning` : une phrase courte justifiant ton choix.

Gestion de l'ambiguïté et suggestions :

- Si la question est trop vague, ambiguë ou hors domaine :
  - `needs_clarification: true`
  - `primary: "unknown"`
  - `suggested_questions` : génère 2-3 reformulations CONCRÈTES et DIRECTEMENT POSABLES
    que l'utilisateur pourrait copier-coller. Utilise le contexte de la question originale
    pour proposer des questions pertinentes et spécifiques.
    
- Exemples de BONNES suggestions (contextuelles, concrètes) :
  Question vague : "qu'est-ce qui se passe avec les cryptos récemment"
  suggested_questions : [
    "Quel est le prix du Bitcoin et de l'Ethereum ce mois ?",
    "Y a-t-il des anomalies de volume sur les principales cryptos cette semaine ?",
    "Quel est le sentiment médiatique autour des cryptos ces 30 derniers jours ?"
  ]

- Exemples de MAUVAISES suggestions (génériques, inutiles) :
  "Précise une entité", "Indique la période" — trop vagues, pas des questions posables.

- Si la question EST classifiable (confidence suffisante), `suggested_questions` doit être vide.

Données disponibles dans le système :
- 10 cryptos : Bitcoin, Ethereum, Solana, Litecoin, XRP, Cardano, Polkadot, Dogecoin, Avalanche, Chainlink
- Indicateurs macro : Fed funds rate, CPI, VIX, S&P 500, GDP, taux 10 ans, taux 2 ans, dollar index, pétrole WTI, chômage, M2
- Sentiment médiatique : articles GDELT avec tone (positif/négatif)
- Métriques : prix (OHLCV), volume, market cap, volatilité, moyennes mobiles

IMPORTANT :
- Ne résous PAS les termes métier — tu ne connais pas les tables ni les colonnes.
- Classifie uniquement l'INTENTION analytique, pas le contenu.
- Réponds UNIQUEMENT en JSON valide respectant le schéma fourni.
"""


class IntentDetector:
    """
    Détecte l'intent d'une question via LLM.

    Utilise le client LLM centralisé (gpt-4o-mini par défaut).
    Un seul appel LLM par question.
    """

    def __init__(
        self,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ):
        self._llm = get_llm_client()
        self._threshold = confidence_threshold

    def detect(self, question: str) -> Intent:
        """
        Classifie la question.

        Args:
            question : question normalisée de l'utilisateur

        Returns:
            Intent validé. En cas d'erreur LLM, retourne un Intent
            UNKNOWN avec needs_clarification=True.
        """
        if not question or not question.strip():
            return Intent(
                primary=IntentType.UNKNOWN,
                confidence=0.0,
                reasoning="Question vide",
                needs_clarification=True,
            )

        try:
            intent = self._llm.chat_json_schema(
                system=INTENT_SYSTEM_PROMPT,
                user=f"Question : {question}",
                schema=Intent,
                purpose="intent_detection",
                temperature=0.0,
                max_tokens=512,
            )
        except (LLMError, LLMSchemaError) as e:
            logger.error("Intent detection LLM failure : %s", e)
            return Intent(
                primary=IntentType.UNKNOWN,
                confidence=0.0,
                reasoning=f"Erreur LLM : {type(e).__name__}",
                needs_clarification=True,
            )

        # Application du seuil de confiance
        if intent.confidence < self._threshold:
            logger.info(
                "Intent confidence %.2f sous le seuil %.2f — needs_clarification",
                intent.confidence,
                self._threshold,
            )
            intent.needs_clarification = True

        logger.info(
            "Intent détecté : %s (conf=%.2f) — %s",
            intent.primary.value,
            intent.confidence,
            intent.reasoning[:80],
        )
        return intent