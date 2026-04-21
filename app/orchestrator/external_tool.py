"""
app/orchestrator/external_tool.py
External Knowledge Tool — Tavily Search + Extract.

Flow pour les termes non disponibles en base interne :

  1. Semantic Layer détecte : terme compris mais non résolu
  2. Orchestrator route vers cet outil
  3. Tavily Search → trouve les sources pertinentes
  4. Tavily Extract → récupère le contenu propre des meilleures sources
  5. Reformatage en objet structuré (ExternalResult)
  6. L'Analyse Agent résume/explique (étape suivante dans le pipeline)

La réponse finale indique clairement :
  - source = "tavily_search" ou "tavily_extract"
  - les URLs des sources pour que l'utilisateur peut les visiter
  - un disclaimer de confiance

Usage :
  tool = TavilyExternalTool()
  result = await tool.search("crypto fear and greed index")
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ─── Modèle de résultat ──────────────────────────────────────


@dataclass
class ExternalSource:
    """Une source individuelle trouvée par Tavily."""

    title: str
    url: str
    snippet: str = ""
    content: str = ""
    score: float = 0.0


@dataclass
class ExternalResult:
    """
    Résultat structuré d'une recherche externe.

    Toujours étiqueté avec la provenance pour la traçabilité.
    L'utilisateur voit les sources et peut les visiter.
    """

    query: str
    source: str  # "tavily_search", "tavily_extract"
    provider: str = "tavily"
    sources: list[ExternalSource] = field(default_factory=list)
    extracted_content: str = ""
    answer: str = ""
    confidence_note: str = (
        "Information provenant de sources web externes via Tavily. "
        "Vérifiez les sources citées pour confirmer l'exactitude."
    )

    def is_empty(self) -> bool:
        return not self.sources and not self.extracted_content and not self.answer

    def source_urls(self) -> list[str]:
        return [s.url for s in self.sources if s.url]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "source": self.source,
            "provider": self.provider,
            "answer": self.answer,
            "extracted_content": self.extracted_content,
            "sources": [
                {
                    "title": s.title,
                    "url": s.url,
                    "snippet": s.snippet[:300],
                    "score": s.score,
                }
                for s in self.sources
            ],
            "source_urls": self.source_urls(),
            "confidence_note": self.confidence_note,
        }


# ─── Interface ────────────────────────────────────────────────


class ExternalKnowledgeTool(Protocol):
    async def search(self, query: str, context: str = "") -> ExternalResult:
        ...


# ─── Implémentation Tavily ────────────────────────────────────


class TavilyExternalTool:
    """
    Recherche externe via Tavily Search + Extract.

    Flow :
      1. Tavily Search — trouve les 5 sources les plus pertinentes
         (filtré par whitelist de domaines de confiance)
      2. Tavily Extract — récupère le contenu nettoyé des 2-3 meilleures
      3. Structure le tout en ExternalResult

    Sécurité :
      Les résultats sont filtrés par une whitelist de domaines par topic.
      Seules les sources de confiance sont retournées à l'utilisateur.
    """

    SEARCH_URL = "https://api.tavily.com/search"
    EXTRACT_URL = "https://api.tavily.com/extract"

    # ── Domaines de confiance par topic ───────────────────────
    # L'Orchestrator sélectionne le topic avant l'appel.
    # Si aucun topic ne matche, on utilise la whitelist "general".
    TRUSTED_DOMAINS: dict[str, list[str]] = {
        "crypto_market": [
            "coingecko.com",
            "coinmarketcap.com",
            "tradingview.com",
            "messari.io",
            "glassnode.com",
        ],
        "crypto_sentiment": [
            "alternative.me",
            "coinglass.com",
            "santiment.net",
            "lunarcrush.com",
        ],
        "crypto_news": [
            "coindesk.com",
            "cointelegraph.com",
            "theblock.co",
            "decrypt.co",
            "reuters.com",
            "bloomberg.com",
        ],
        "macro": [
            "fred.stlouisfed.org",
            "tradingeconomics.com",
            "reuters.com",
            "bloomberg.com",
            "investing.com",
        ],
        "general": [
            "reuters.com",
            "bloomberg.com",
            "coindesk.com",
            "cointelegraph.com",
            "coingecko.com",
            "coinmarketcap.com",
            "tradingview.com",
            "investing.com",
            "alternative.me",
            "fred.stlouisfed.org",
        ],
    }

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.getenv("TAVILY_API_KEY")
        if not self._api_key:
            logger.warning(
                "TAVILY_API_KEY manquant — l'outil externe ne fonctionnera pas"
            )

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _detect_topic(self, query: str) -> str:
        """
        Détecte le topic de la question pour choisir la bonne whitelist.
        Logique simple par mots-clés — pas d'appel LLM.
        """
        q = query.lower()

        if any(w in q for w in ["sentiment", "fear", "greed", "peur"]):
            return "crypto_sentiment"
        if any(w in q for w in ["fed", "taux", "inflation", "macro", "gdp",
                                 "cpi", "chômage", "unemployment"]):
            return "macro"
        if any(w in q for w in ["news", "actualité", "annonce", "régulation"]):
            return "crypto_news"
        if any(w in q for w in ["prix", "price", "volume", "market cap",
                                 "crypto", "bitcoin", "ethereum"]):
            return "crypto_market"
        return "general"

    async def search(
        self,
        query: str,
        context: str = "",
        topic: str | None = None,
    ) -> ExternalResult:
        if not self._api_key:
            logger.error("Tavily non configuré — TAVILY_API_KEY manquant")
            return ExternalResult(
                query=query,
                source="tavily_search",
                confidence_note="Outil de recherche externe non configuré.",
            )

        import httpx

        # Détection du topic pour la whitelist
        resolved_topic = topic or self._detect_topic(query)
        include_domains = self.TRUSTED_DOMAINS.get(
            resolved_topic,
            self.TRUSTED_DOMAINS["general"],
        )

        logger.info(
            "Tavily search — topic=%s, %d trusted domains",
            resolved_topic,
            len(include_domains),
        )

        search_results = await self._tavily_search(
            query, httpx, include_domains
        )
        if not search_results.sources:
            return search_results

        top_urls = [s.url for s in search_results.sources[:3] if s.url]
        if top_urls:
            extracted = await self._tavily_extract(top_urls, httpx)
            search_results.extracted_content = extracted
            search_results.source = "tavily_extract"

        logger.info(
            "Tavily — %d sources, answer_len=%d, extract_len=%d",
            len(search_results.sources),
            len(search_results.answer),
            len(search_results.extracted_content),
        )
        return search_results

    async def _tavily_search(
        self,
        query: str,
        httpx_module: Any,
        include_domains: list[str] | None = None,
    ) -> ExternalResult:
        """Appelle Tavily Search API avec filtrage par domaines."""
        try:
            payload: dict[str, Any] = {
                "api_key": self._api_key,
                "query": query,
                "search_depth": "advanced",
                "max_results": 5,
                "include_answer": True,
                "include_raw_content": False,
            }
            # Tavily supporte include_domains pour filtrer les résultats
            if include_domains:
                payload["include_domains"] = include_domains

            async with httpx_module.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    self.SEARCH_URL,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            sources = [
                ExternalSource(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", "")[:500],
                    score=r.get("score", 0.0),
                )
                for r in data.get("results", [])
            ]

            return ExternalResult(
                query=query,
                source="tavily_search",
                sources=sources,
                answer=data.get("answer", ""),
            )

        except Exception as e:
            logger.error("Tavily Search error : %s", e)
            return ExternalResult(
                query=query,
                source="tavily_search",
                confidence_note=f"Erreur Tavily Search : {type(e).__name__}",
            )

    async def _tavily_extract(
        self, urls: list[str], httpx_module: Any
    ) -> str:
        try:
            async with httpx_module.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    self.EXTRACT_URL,
                    json={
                        "api_key": self._api_key,
                        "urls": urls,
                    },
                )
                response.raise_for_status()
                data = response.json()

            parts = []
            for result in data.get("results", []):
                raw = result.get("raw_content", "")
                if raw:
                    parts.append(raw[:2000])

            extracted = "\n\n---\n\n".join(parts)
            logger.info(
                "Tavily Extract — %d URLs, %d chars extraits",
                len(urls),
                len(extracted),
            )
            return extracted

        except Exception as e:
            logger.error("Tavily Extract error : %s", e)
            return ""


# ─── Mock pour les tests ──────────────────────────────────────


class MockExternalTool:
    async def search(self, query: str, context: str = "") -> ExternalResult:
        return ExternalResult(
            query=query,
            source="mock",
            provider="mock",
            sources=[
                ExternalSource(
                    title="Mock: Crypto Fear & Greed Index",
                    url="https://alternative.me/crypto/fear-and-greed-index/",
                    snippet=f"Résultat simulé pour : {query}",
                    score=0.95,
                )
            ],
            answer=f"Réponse simulée pour : {query}",
            extracted_content="Contenu extrait simulé.",
        )