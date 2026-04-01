"""
app/db/neo4j.py
Connexion Neo4j — driver Python officiel.
Rôle : couche bas niveau uniquement.
  - Connexion et healthcheck
  - Validation anti-injection Cypher
  - run_query / run_write
  - CRUD générique
  - init_knowledge_graph() : appelé au démarrage FastAPI
    → crée uniquement les contraintes/index des nœuds agentiques
      (Question, SQLQuery, Insight, etc.) qui ne sont PAS seedés
      par setup_neo4j_schema.py mais créés dynamiquement par les agents.

Les contraintes/index des nœuds sémantiques (Table, Column,
BusinessTerm, etc.) sont gérés par scripts/setup_neo4j_schema.py.
"""

import logging
from typing import Any

from neo4j import Driver, GraphDatabase

from app.config import settings

logger = logging.getLogger(__name__)


# ─── Labels autorisés ─────────────────────────────────────────

ALLOWED_LABELS = frozenset({
    # Schéma technique
    "Table",
    "Column",

    # Vocabulaire métier
    "BusinessTerm",
    "Synonym",
    "Metric",
    "BusinessRule",
    "TimePeriod",

    # Entités métier (cryptos, séries FRED, sources médias)
    "Entity",

    # Pipeline agentique — créés dynamiquement par les agents
    "Question",
    "Intent",
    "SQLQuery",
    "Insight",
    "Anomaly",
    "Correlation",
    "Feedback",
    "Correction",

    # Accès
    "AccessPolicy",
})

# ─── Relations autorisées ─────────────────────────────────────

ALLOWED_RELATIONSHIPS = frozenset({
    # Schéma technique
    "HAS_COLUMN",
    "JOIN",
    "AGGREGATES",
    "DERIVED_FROM",
    "ENRICHES",

    # Sémantique
    "HAS_SYNONYM",
    "RESOLVES_TO",
    "COMPUTED_FROM",
    "APPLIES_TO",
    "REPRESENTS",       # Entity → Column (ex: BTC → fact_crypto_daily.symbol)

    # Pipeline agentique
    "CLASSIFIED_AS",    # Question → Intent
    "GENERATED",        # Question → SQLQuery
    "DETECTED_IN",      # Anomaly → Column
    "CORRELATED",       # Column ↔ Column
    "RATED_BY",         # SQLQuery/Insight → Feedback
    "CORRECTED_BY",     # Feedback → Correction
    "UPDATES",          # Correction → BusinessTerm/BusinessRule
})


# ─── Validation ───────────────────────────────────────────────

def _validate_label(label: str) -> None:
    if label not in ALLOWED_LABELS:
        raise ValueError(
            f"Label '{label}' non autorisé. "
            f"Labels valides : {sorted(ALLOWED_LABELS)}"
        )


def _validate_relationship(rel_type: str) -> None:
    if rel_type not in ALLOWED_RELATIONSHIPS:
        raise ValueError(
            f"Relation '{rel_type}' non autorisée. "
            f"Relations valides : {sorted(ALLOWED_RELATIONSHIPS)}"
        )


def _validate_property_key(key: str) -> None:
    if not key.isidentifier():
        raise ValueError(
            f"Clé '{key}' invalide — seuls les identifiants Python sont acceptés."
        )


# ─── Driver ───────────────────────────────────────────────────

class Neo4jDriver:
    """Wrapper bas niveau autour du driver Neo4j officiel."""

    def __init__(self):
        self._driver: Driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    # ── Connexion ─────────────────────────────────────────────

    def verify_connectivity(self) -> None:
        self._driver.verify_connectivity()

    def is_healthy(self) -> bool:
        try:
            self._driver.verify_connectivity()
            return True
        except Exception:
            return False

    def close(self) -> None:
        self._driver.close()

    @property
    def driver(self) -> Driver:
        return self._driver

    def session(self, **kwargs):
        return self._driver.session(**kwargs)

    # ── Exécution Cypher ──────────────────────────────────────

    def run_query(self, query: str, parameters: dict | None = None) -> list[dict]:
        """Lecture — retourne liste de dicts."""
        with self._driver.session() as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def run_write(self, query: str, parameters: dict | None = None) -> list[dict]:
        """Écriture dans une transaction."""
        with self._driver.session() as session:
            result = session.execute_write(
                lambda tx: list(tx.run(query, parameters or {}))
            )
            return [record.data() for record in result]

    # ── CRUD générique ────────────────────────────────────────

    def create_node(self, label: str, properties: dict) -> dict:
        _validate_label(label)
        records = self.run_query(
            f"CREATE (n:{label} $props) RETURN n",
            {"props": properties}
        )
        return dict(records[0]["n"]) if records else {}

    def merge_node(self, label: str, key: str, properties: dict) -> dict:
        """MERGE — crée si absent, met à jour si existant."""
        _validate_label(label)
        _validate_property_key(key)
        records = self.run_query(
            f"MERGE (n:{label} {{{key}: $key_value}}) "
            f"SET n += $props RETURN n",
            {"key_value": properties.get(key), "props": properties}
        )
        return dict(records[0]["n"]) if records else {}

    def find_node(self, label: str, key: str, value: Any) -> dict | None:
        _validate_label(label)
        _validate_property_key(key)
        records = self.run_query(
            f"MATCH (n:{label} {{{key}: $value}}) RETURN n LIMIT 1",
            {"value": value}
        )
        return dict(records[0]["n"]) if records else None

    def find_nodes(
        self, label: str, filters: dict | None = None, limit: int = 100
    ) -> list[dict]:
        _validate_label(label)
        if filters:
            for key in filters:
                _validate_property_key(key)
            where = " AND ".join([f"n.{k} = ${k}" for k in filters])
            query = f"MATCH (n:{label}) WHERE {where} RETURN n LIMIT {limit}"
        else:
            query = f"MATCH (n:{label}) RETURN n LIMIT {limit}"
        return [dict(r["n"]) for r in self.run_query(query, filters or {})]

    def create_relationship(
        self,
        from_label: str, from_key: str, from_value: Any,
        to_label: str,   to_key: str,   to_value: Any,
        rel_type: str,   properties: dict | None = None,
    ) -> bool:
        _validate_label(from_label)
        _validate_label(to_label)
        _validate_relationship(rel_type)
        _validate_property_key(from_key)
        _validate_property_key(to_key)
        query = (
            f"MATCH (a:{from_label} {{{from_key}: $from_val}}), "
            f"(b:{to_label} {{{to_key}: $to_val}}) "
            f"MERGE (a)-[r:{rel_type}]->(b) "
            + ("SET r += $props " if properties else "")
            + "RETURN r"
        )
        params: dict[str, Any] = {"from_val": from_value, "to_val": to_value}
        if properties:
            params["props"] = properties
        return len(self.run_query(query, params)) > 0

    # ── Init agentique (démarrage FastAPI) ────────────────────
    # Crée uniquement les contraintes/index des nœuds agentiques.
    # Les nœuds sémantiques sont gérés par setup_neo4j_schema.py.

    def init_knowledge_graph(self) -> dict:
        """
        Appelé au démarrage FastAPI (lifespan).
        Idempotent — IF NOT EXISTS partout.
        """
        logger.info("Init contraintes agentiques KG...")
        constraints = self._init_agentic_constraints()
        logger.info("%d contraintes agentiques prêtes.", len(constraints))
        return {"agentic_constraints": constraints}

    def _init_agentic_constraints(self) -> list[str]:
        """
        Contraintes pour les nœuds créés dynamiquement par les agents.
        Ne pas dupliquer les nœuds sémantiques (Table, Column, etc.)
        qui sont gérés par setup_neo4j_schema.py.
        """
        agentic = [
            ("constraint_question_id",     "Question",     "id"),
            ("constraint_intent_name",     "Intent",       "name"),
            ("constraint_sqlquery_id",     "SQLQuery",     "id"),
            ("constraint_insight_id",      "Insight",      "id"),
            ("constraint_anomaly_id",      "Anomaly",      "id"),
            ("constraint_correlation_id",  "Correlation",  "id"),
            ("constraint_feedback_id",     "Feedback",     "id"),
            ("constraint_correction_id",   "Correction",   "id"),
            ("constraint_accesspolicy_id", "AccessPolicy", "id"),
        ]
        created = []
        for name, label, prop in agentic:
            try:
                self.run_write(
                    f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
                )
                created.append(name)
            except Exception as e:
                logger.warning("Contrainte %s : %s", name, e)
        return created

    # ── Statut ────────────────────────────────────────────────

    def get_schema_status(self) -> dict:
        constraints = self.run_query("SHOW CONSTRAINTS")
        indexes     = self.run_query("SHOW INDEXES")
        label_counts = {}
        for label in ALLOWED_LABELS:
            result = self.run_query(f"MATCH (n:{label}) RETURN count(n) AS count")
            label_counts[label] = result[0]["count"] if result else 0
        return {
            "constraints_count": len(constraints),
            "indexes_count":     len(indexes),
            "node_counts":       label_counts,
        }


neo4j_driver = Neo4jDriver()