"""
app/agents/feedback/kg_updater.py

KGUpdater — Persistence du feedback dans le Knowledge Graph Neo4j.

Responsabilités MVP :
  1. Créer un nœud :Feedback avec le score composite et les métadonnées
  2. Relier Feedback → Question / Intent / SQLQuery / Insight / Anomaly
  3. Marquer le statut (reinforced, deprecated, neutral) sur le nœud cible
  4. Mettre à jour le score de confiance du nœud cible

Ce que le MVP ne fait PAS (tickets futurs) :
  - Modifier les synonymes / BusinessTerm
  - Modifier les prompts des autres agents
  - Supprimer le cache Redis
  - Changer les plans d'exécution

Utilise le driver Neo4j via session() — cohérent avec app/db/neo4j.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.agents.feedback.models import (
    CompositeScore,
    FeedbackInput,
    FeedbackStatus,
    FeedbackType,
    TargetType,
)

logger = logging.getLogger(__name__)


# ── Mapping target_type → label Neo4j ──────────────────────────────────────

_TARGET_TYPE_TO_LABEL: dict[TargetType, str] = {
    TargetType.ANSWER: "Response",
    TargetType.INSIGHT: "Insight",
    TargetType.SQL: "SQLQuery",
    TargetType.ANOMALY: "Anomaly",
    TargetType.FORECAST: "Forecast",
    TargetType.CORRELATION: "Correlation",
}


class KGUpdater:
    """Écrit le feedback et ses effets dans le Knowledge Graph.

    Parameters
    ----------
    neo4j_driver : Any
        Instance de Neo4jDriver (app/db/neo4j.py) exposant .session().
    """

    def __init__(self, neo4j_driver: Any) -> None:
        self._driver = neo4j_driver

    def persist(
        self,
        feedback: FeedbackInput,
        score: CompositeScore,
        feedback_id: str,
    ) -> tuple[list[str], list[str]]:
        """Persiste le feedback dans le KG.

        Returns
        -------
        tuple[list[str], list[str]]
            (opérations effectuées, warnings)
        """
        operations: list[str] = []
        warnings: list[str] = []

        # ── 1. Créer le nœud Feedback ──────────────────────────
        try:
            self._create_feedback_node(feedback, score, feedback_id)
            operations.append(
                f"CREATE (:Feedback {{id: '{feedback_id}'}})"
            )
        except Exception as exc:
            warnings.append(
                f"Échec création nœud Feedback({feedback_id}) : {exc}"
            )
            logger.error("KG: échec CREATE Feedback %s", exc, exc_info=True)
            # On continue — les liens et mises à jour sont best-effort
            return operations, warnings

        # ── 2. Lier Feedback → Question (via question text) ────
        try:
            linked = self._link_to_question(feedback, feedback_id)
            if linked:
                operations.append(
                    f"LINK Feedback({feedback_id}) -[:RATES]-> Question"
                )
        except Exception as exc:
            warnings.append(f"Échec lien Feedback→Question : {exc}")

        # ── 3. Lier Feedback → Intent (si disponible) ─────────
        if feedback.intent:
            try:
                self._link_to_intent(feedback, feedback_id)
                operations.append(
                    f"LINK Feedback({feedback_id}) -[:EVALUATES_INTENT]-> "
                    f"Intent({feedback.intent})"
                )
            except Exception as exc:
                warnings.append(f"Échec lien Feedback→Intent : {exc}")

        # ── 4. Lier Feedback → Target et mettre à jour statut ─
        if feedback.target_id:
            try:
                updated = self._update_target(feedback, score, feedback_id)
                if updated:
                    operations.append(
                        f"UPDATE {feedback.target_type.value}("
                        f"{feedback.target_id}) → "
                        f"status={score.status.value}, "
                        f"confidence_score={score.composite}"
                    )
            except Exception as exc:
                warnings.append(
                    f"Échec mise à jour target "
                    f"{feedback.target_type.value}({feedback.target_id}) : "
                    f"{exc}"
                )

        # ── 5. Enregistrer la correction si présente ───────────
        if feedback.feedback_type == FeedbackType.CORRECTION_SQL and feedback.corrected_sql:
            try:
                self._store_correction(
                    feedback_id=feedback_id,
                    correction_type="sql",
                    original_target_id=feedback.target_id,
                    corrected_value=feedback.corrected_sql,
                )
                operations.append(
                    f"CREATE (:Correction {{type: 'sql'}}) "
                    f"-[:CORRECTS]-> target({feedback.target_id})"
                )
            except Exception as exc:
                warnings.append(f"Échec enregistrement correction SQL : {exc}")

        if feedback.feedback_type == FeedbackType.CORRECTION_INSIGHT and feedback.corrected_text:
            try:
                self._store_correction(
                    feedback_id=feedback_id,
                    correction_type="insight",
                    original_target_id=feedback.target_id,
                    corrected_value=feedback.corrected_text,
                )
                operations.append(
                    f"CREATE (:Correction {{type: 'insight'}}) "
                    f"-[:CORRECTS]-> target({feedback.target_id})"
                )
            except Exception as exc:
                warnings.append(
                    f"Échec enregistrement correction insight : {exc}"
                )

        # ── 6. Marquer false_positive sur anomalie ─────────────
        if (
            feedback.feedback_type == FeedbackType.FALSE_POSITIVE
            and feedback.target_id
        ):
            try:
                self._mark_false_positive(feedback.target_id)
                operations.append(
                    f"UPDATE Anomaly({feedback.target_id}) → "
                    f"false_positive=true"
                )
            except Exception as exc:
                warnings.append(
                    f"Échec marquage false_positive({feedback.target_id}) : "
                    f"{exc}"
                )

        logger.info(
            "KGUpdater terminé : %d opérations, %d warnings",
            len(operations),
            len(warnings),
        )
        return operations, warnings

    # ── Queries Neo4j ──────────────────────────────────────────

    def _run_write(self, query: str, parameters: dict[str, Any]) -> Any:
        """Exécute une query d'écriture via le driver Neo4j.

        Utilise session() pour rester cohérent avec le driver existant
        dans app/db/neo4j.py (Neo4jDriver expose .session()).
        """
        with self._driver.session() as session:
            return session.run(query, parameters).consume()

    def _run_read(self, query: str, parameters: dict[str, Any]) -> list[dict]:
        """Exécute une query de lecture."""
        with self._driver.session() as session:
            result = session.run(query, parameters)
            return [record.data() for record in result]

    def _create_feedback_node(
        self,
        feedback: FeedbackInput,
        score: CompositeScore,
        feedback_id: str,
    ) -> None:
        """Crée le nœud :Feedback dans le KG."""
        now = datetime.now(timezone.utc).isoformat()

        query = """
        CREATE (f:Feedback {
            id: $feedback_id,
            response_id: $response_id,
            session_id: $session_id,
            question: $question,
            intent: $intent,
            rating: $rating,
            feedback_type: $feedback_type,
            target_type: $target_type,
            target_id: $target_id,
            comment: $comment,
            score_human: $score_human,
            score_implicit: $score_implicit,
            score_expert: $score_expert,
            composite_score: $composite,
            status: $status,
            created_at: $created_at
        })
        """
        self._run_write(query, {
            "feedback_id": feedback_id,
            "response_id": feedback.response_id,
            "session_id": feedback.session_id,
            "question": feedback.question,
            "intent": feedback.intent or "",
            "rating": feedback.rating,
            "feedback_type": feedback.feedback_type.value,
            "target_type": feedback.target_type.value,
            "target_id": feedback.target_id or "",
            "comment": feedback.comment or "",
            "score_human": score.score_human,
            "score_implicit": score.score_implicit,
            "score_expert": score.score_expert,
            "composite": score.composite,
            "status": score.status.value,
            "created_at": now,
        })

    def _link_to_question(
        self, feedback: FeedbackInput, feedback_id: str
    ) -> bool:
        """Lie le Feedback à un nœud Question existant (match par texte)."""
        query = """
        MATCH (q:Question)
        WHERE q.text = $question OR q.normalized = $question
        WITH q LIMIT 1
        MATCH (f:Feedback {id: $feedback_id})
        MERGE (f)-[:RATES]->(q)
        RETURN q.text AS matched
        """
        records = self._run_read(query, {
            "question": feedback.question,
            "feedback_id": feedback_id,
        })
        return len(records) > 0

    def _link_to_intent(
        self, feedback: FeedbackInput, feedback_id: str
    ) -> None:
        """Lie le Feedback à un nœud Intent."""
        query = """
        MERGE (i:Intent {name: $intent})
        WITH i
        MATCH (f:Feedback {id: $feedback_id})
        MERGE (f)-[:EVALUATES_INTENT]->(i)
        """
        self._run_write(query, {
            "intent": feedback.intent,
            "feedback_id": feedback_id,
        })

    def _update_target(
        self,
        feedback: FeedbackInput,
        score: CompositeScore,
        feedback_id: str,
    ) -> bool:
        """Met à jour le nœud cible avec le statut et score de confiance.

        Lie Feedback → Target via :RATES_TARGET.
        Met à jour les propriétés feedback_status et confidence_score.
        """
        label = _TARGET_TYPE_TO_LABEL.get(feedback.target_type)
        if not label:
            logger.warning(
                "target_type %s non mappé à un label Neo4j",
                feedback.target_type,
            )
            return False

        # On utilise un paramètre pour le label car Cypher ne supporte pas
        # les labels dynamiques — on passe par APOC si dispo, sinon on
        # fait un match par propriété id + un CASE sur les labels connus.
        #
        # Pattern safe sans APOC : match par id property.
        query = f"""
        MATCH (t:{label} {{id: $target_id}})
        SET t.feedback_status = $status,
            t.confidence_score = $composite,
            t.last_feedback_at = $now
        WITH t
        MATCH (f:Feedback {{id: $feedback_id}})
        MERGE (f)-[:RATES_TARGET]->(t)
        RETURN t.id AS updated
        """
        now = datetime.now(timezone.utc).isoformat()
        records = self._run_read(query, {
            "target_id": feedback.target_id,
            "status": score.status.value,
            "composite": score.composite,
            "now": now,
            "feedback_id": feedback_id,
        })
        return len(records) > 0

    def _store_correction(
        self,
        feedback_id: str,
        correction_type: str,
        original_target_id: str | None,
        corrected_value: str,
    ) -> None:
        """Crée un nœud :Correction lié au Feedback et à la cible."""
        now = datetime.now(timezone.utc).isoformat()

        query = """
        MATCH (f:Feedback {id: $feedback_id})
        CREATE (c:Correction {
            type: $correction_type,
            corrected_value: $corrected_value,
            original_target_id: $original_target_id,
            created_at: $now
        })
        MERGE (f)-[:PRODUCED]->(c)
        """
        params: dict[str, Any] = {
            "feedback_id": feedback_id,
            "correction_type": correction_type,
            "corrected_value": corrected_value,
            "original_target_id": original_target_id or "",
            "now": now,
        }
        self._run_write(query, params)

        # Lier la correction à la cible si elle existe
        if original_target_id:
            link_query = """
            MATCH (c:Correction {original_target_id: $target_id, created_at: $now})
            WITH c LIMIT 1
            OPTIONAL MATCH (t {id: $target_id})
            WHERE t IS NOT NULL
            MERGE (c)-[:CORRECTS]->(t)
            """
            self._run_write(link_query, {
                "target_id": original_target_id,
                "now": now,
            })

    def _mark_false_positive(self, anomaly_id: str) -> None:
        """Marque une anomalie comme faux positif."""
        query = """
        MATCH (a:Anomaly {id: $anomaly_id})
        SET a.false_positive = true,
            a.feedback_status = 'deprecated',
            a.false_positive_at = $now
        """
        self._run_write(query, {
            "anomaly_id": anomaly_id,
            "now": datetime.now(timezone.utc).isoformat(),
        })
