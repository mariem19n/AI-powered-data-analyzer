"""
app/semantic/pipeline.py
Pipeline Semantic Layer unifié.

Encapsule les 4 étapes du semantic layer en une seule fonction appelable par l'Orchestrator :

  1. BusinessTermsExtractor — extraction LLM
  2. KGResolver — résolution KG
  3. RulesEnricher           — règles métier implicites
  4. SemanticContextBuilder  — construction JSON final

Usage :
    from app.semantic.pipeline import SemanticPipeline

    pipeline = SemanticPipeline(neo4j_driver)
    context = pipeline.run("prix Bitcoin ce mois")

    # Ou comme fonction injectable dans l'Orchestrator :
    orchestrator = Orchestrator(
        semantic_build_fn=pipeline.run,
        ...
    )
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.semantic.extractor import BusinessTermsExtractor
from app.semantic.resolver import KGResolver
from app.semantic.rules import RulesEnricher
from app.semantic.context_builder import SemanticContextBuilder, SemanticContext

logger = logging.getLogger(__name__)


class SemanticPipeline:
    """
    Pipeline Semantic Layer complet.

    Orchestre les 4 composants Sprint 1 en séquence.
    Chaque composant est initialisé une seule fois au démarrage
    et réutilisé pour chaque question.
    """

    def __init__(self, neo4j_driver: Any):
        """
        Initialise le pipeline avec le driver Neo4j.

        Args:
            neo4j_driver: Instance de Neo4jDriver (app.db.neo4j).
                          Passé à tous les composants qui en ont besoin.
        """
        t0 = time.perf_counter()

        self._extractor = BusinessTermsExtractor(neo4j_driver)
        self._resolver = KGResolver(neo4j_driver)
        self._enricher = RulesEnricher(neo4j_driver)
        self._builder = SemanticContextBuilder()

        init_time = time.perf_counter() - t0

        # Récupérer les stats du vocabulaire pour le log
        vocab = getattr(self._extractor, "_vocab", None)
        if vocab:
            logger.info(
                "SemanticPipeline initialisé en %.1fms — "
                "vocabulaire KG chargé : %d business_terms, %d entities, "
                "%d time_periods, %d metrics, %d synonymes",
                init_time * 1000,
                len(getattr(vocab, "business_terms", [])),
                len(getattr(vocab, "entities", [])),
                len(getattr(vocab, "time_periods", [])),
                len(getattr(vocab, "metrics", [])),
                len(getattr(vocab, "synonyms", [])),
            )
        else:
            logger.info(
                "SemanticPipeline initialisé en %.1fms (vocabulaire non disponible)",
                init_time * 1000,
            )

    def run(self, question: str) -> SemanticContext:
        """
        Exécute le pipeline complet sur une question.

        C'est cette méthode qui est passée comme `semantic_build_fn`
        à l'Orchestrator.

        Args:
            question: Question en langage naturel (déjà normalisée
                      par l'Orchestrator).

        Returns:
            SemanticContext — objet JSON-serializable contenant tout
            ce que le SQL Agent a besoin.

        Raises:
            Exception: propagée à l'Orchestrator qui la gère
            (clarification ou erreur partielle).
        """
        t0 = time.perf_counter()

        # ── Étape 1 : Extraction LLM (SP1-46) ────────────────
        enriched = self._extractor.extract(question)

        if enriched is None:
            logger.warning(
                "Extraction LLM a retourné None pour : '%s'",
                question[:80],
            )
            # Construire un SemanticContext vide avec needs_clarification
            return self._builder.build_empty(
                raw_question=question,
                reason="L'extraction LLM n'a produit aucun résultat.",
            )

        t_extract = time.perf_counter() - t0

        # ── Étape 2 : Résolution KG ─────────────────
        resolved = self._resolver.resolve(enriched)
        t_resolve = time.perf_counter() - t0 - t_extract

        # ── Étape 3 : Règles métier implicites ──────
        with_rules = self._enricher.enrich(resolved)
        t_rules = time.perf_counter() - t0 - t_extract - t_resolve

        # ── Étape 4 : Construction SemanticContext ───
        context = self._builder.build(
            enriched=with_rules,
            raw_question=question,
            corrected_question=getattr(enriched, "corrected_question", question),
            pipeline_confidence=getattr(enriched, "pipeline_confidence", 0.0),
        )
        t_build = time.perf_counter() - t0 - t_extract - t_resolve - t_rules
        total = time.perf_counter() - t0

        logger.info(
            "SemanticPipeline — question='%s' — "
            "extract=%.0fms, resolve=%.0fms, rules=%.0fms, build=%.0fms, "
            "total=%.0fms — %d tables, %d entities, %d metrics, "
            "clarify=%s",
            question[:60],
            t_extract * 1000,
            t_resolve * 1000,
            t_rules * 1000,
            t_build * 1000,
            total * 1000,
            len(context.tables),
            len(context.entity_filters),
            len(context.metrics),
            context.needs_clarification,
        )

        return context

    # ─── Accesseurs utiles ────────────────────────────────────

    @property
    def extractor(self) -> BusinessTermsExtractor:
        """Accès au BusinessTermsExtractor (pour les stats, le vocab, etc.)."""
        return self._extractor

    def reload_vocabulary(self, neo4j_driver: Any) -> None:
        """
        Recharge le vocabulaire KG à chaud.

        Utile si le Feedback Agent a enrichi le KG et qu'on veut
        que les nouvelles entrées soient prises en compte sans
        redémarrer l'application.
        """
        self._extractor._reload_vocabulary(neo4j_driver)
        logger.info("Vocabulaire KG rechargé à chaud")
