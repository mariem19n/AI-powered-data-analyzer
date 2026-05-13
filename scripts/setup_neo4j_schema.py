"""
scripts/setup_neo4j_schema.py
SP1-45 + SP1-51 — Modélisation et seed complet du Knowledge Graph Neo4j.

Couvre :
  SP1-45 : contraintes, index, nœuds Table/Column, relations entre tables
  SP1-51 : Entity, BusinessTerm, Synonym, Metric, BusinessRule, TimePeriod

Idempotent — MERGE partout, relançable sans risque.

Usage :
    python scripts/setup_neo4j_schema.py

Prérequis :
    docker-compose up -d   (Neo4j doit tourner)
    pip install neo4j python-dotenv
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.kg.schema_definitions import (
    PG_TABLES,
    TABLE_JOINS,
    ENTITIES,
    BUSINESS_TERMS,
    METRICS,
    BUSINESS_RULES,
    TIME_PERIODS,
)

NEO4J_URI = os.getenv("NEO4J_URI_LOCAL", os.getenv("NEO4J_URI", "bolt://localhost:7687"))
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "neo4j_password_123")


# ─── SP1-45 : Contraintes et index ───────────────────────────

def create_constraints_and_indexes(session):
    print("\n📐 Contraintes d'unicité (SP1-45)...")

    # Nœuds sémantiques — gérés ici
    semantic_constraints = [
        ("constraint_table_name",        "Table",        "name"),
        ("constraint_column_id",         "Column",       "id"),
        ("constraint_entity_id",         "Entity",       "id"),
        ("constraint_businessterm_name", "BusinessTerm", "name"),
        ("constraint_synonym_text",      "Synonym",      "text"),
        ("constraint_metric_name",       "Metric",       "name"),
        ("constraint_businessrule_id",   "BusinessRule", "id"),
        ("constraint_timeperiod_name",   "TimePeriod",   "name"),
    ]
    # Nœuds agentiques — aussi créés ici pour le seed complet
    # (neo4j.py les recrée au démarrage FastAPI via IF NOT EXISTS, pas de conflit)
    agentic_constraints = [
        ("constraint_question_id",       "Question",     "id"),
        ("constraint_intent_name",       "Intent",       "name"),
        ("constraint_sqlquery_id",       "SQLQuery",     "id"),
        ("constraint_insight_id",        "Insight",      "id"),
        ("constraint_anomaly_id",        "Anomaly",      "id"),
        ("constraint_correlation_id",    "Correlation",  "id"),
        ("constraint_feedback_id",       "Feedback",     "id"),
        ("constraint_correction_id",     "Correction",   "id"),
        ("constraint_accesspolicy_id",   "AccessPolicy", "id"),
    ]

    for name, label, prop in semantic_constraints + agentic_constraints:
        try:
            session.run(
                f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
            )
            print(f"  ✅ {label}.{prop}")
        except Exception as e:
            print(f"  ⚠ {label}.{prop} — {e}")

    print("\n📇 Index de performance (SP1-45)...")
    indexes = [
        ("idx_column_table_name",    "Column",       "table_name"),
        ("idx_column_data_type",     "Column",       "data_type"),
        ("idx_column_is_nullable",   "Column",       "is_nullable"),
        ("idx_entity_type",          "Entity",       "entity_type"),
        ("idx_entity_domain",        "Entity",       "domain"),
        ("idx_businessterm_domain",  "BusinessTerm", "domain"),
        ("idx_metric_domain",        "Metric",       "domain"),
        ("idx_table_category",       "Table",        "category"),
        ("idx_sqlquery_score",       "SQLQuery",     "score"),
        ("idx_insight_created_at",   "Insight",      "created_at"),
        ("idx_anomaly_detected_at",  "Anomaly",      "detected_at"),
        ("idx_feedback_rating",      "Feedback",     "rating"),
        ("idx_question_intent",      "Question",     "intent"),
    ]
    for name, label, prop in indexes:
        try:
            session.run(
                f"CREATE INDEX {name} IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.{prop})"
            )
            print(f"  ✅ {label}.{prop}")
        except Exception as e:
            print(f"  ⚠ {label}.{prop} — {e}")


# ─── SP1-45 : Tables et colonnes ─────────────────────────────

def create_table_nodes(session):
    print("\n🗄  Tables et colonnes (SP1-45)...")
    for table_name, category, description, columns in PG_TABLES:
        session.run("""
            MERGE (t:Table {name: $name})
            SET t.category    = $category,
                t.description = $description,
                t.updated_at  = datetime()
        """, name=table_name, category=category, description=description)

        for col_name, data_type, is_key, is_nullable, col_desc in columns:
            col_id = f"{table_name}.{col_name}"
            session.run("""
                MERGE (c:Column {id: $id})
                SET c.name        = $col_name,
                    c.table_name  = $table_name,
                    c.data_type   = $data_type,
                    c.is_key      = $is_key,
                    c.is_nullable = $is_nullable,
                    c.description = $col_desc,
                    c.updated_at  = datetime()
                WITH c
                MATCH (t:Table {name: $table_name})
                MERGE (t)-[:HAS_COLUMN]->(c)
            """, id=col_id, col_name=col_name, table_name=table_name,
                 data_type=data_type, is_key=is_key,
                 is_nullable=is_nullable, col_desc=col_desc)

        print(f"  ✅ {table_name} ({len(columns)} colonnes)")


# ─── SP1-45 : Relations entre tables ─────────────────────────

def create_table_relationships(session):
    print("\n🔗 Relations entre tables (SP1-45)...")
    for from_table, from_col, to_table, to_col, rel_type, desc in TABLE_JOINS:
        rel_id = f"{from_table}.{from_col}->{to_table}.{to_col}"
        session.run(f"""
            MATCH (a:Table {{name: $from_table}})
            MATCH (b:Table {{name: $to_table}})
            MERGE (a)-[r:{rel_type} {{id: $rel_id}}]->(b)
            SET r.from_column = $from_col,
                r.to_column   = $to_col,
                r.description = $desc,
                r.updated_at  = datetime()
        """, from_table=from_table, to_table=to_table,
             rel_id=rel_id, from_col=from_col, to_col=to_col, desc=desc)
        print(f"  ✅ ({from_table})-[{rel_type}]->({to_table})")


# ─── SP1-51 : Entités métier ──────────────────────────────────

def create_entities(session):
    print("\n🏷  Entités métier (SP1-51)...")
    for entity_id, entity_type, label, description, table, column, filter_val in ENTITIES:
        col_id = f"{table}.{column}"
        session.run("""
            MERGE (e:Entity {id: $id})
            SET e.entity_type   = $entity_type,
                e.label         = $label,
                e.description   = $description,
                e.filter_value  = $filter_val,
                e.updated_at    = datetime()
            WITH e
            MATCH (c:Column {id: $col_id})
            MERGE (e)-[:REPRESENTS]->(c)
        """, id=entity_id, entity_type=entity_type, label=label,
             description=description, filter_val=filter_val, col_id=col_id)
        print(f"  ✅ {label} ({entity_type}) → {table}.{column} = '{filter_val}'")


# ─── SP1-51 : Termes métier ───────────────────────────────────

def create_business_terms(session):
    print("\n💼 Termes métier et synonymes (SP1-51)...")
    for term, domain, description, synonyms, table, column in BUSINESS_TERMS:
        session.run("""
            MERGE (bt:BusinessTerm {name: $name})
            SET bt.domain      = $domain,
                bt.description = $description,
                bt.updated_at  = datetime()
        """, name=term, domain=domain, description=description)

        for syn in synonyms:
            session.run("""
                MERGE (s:Synonym {text: $text})
                SET s.updated_at = datetime()
                WITH s
                MATCH (bt:BusinessTerm {name: $term})
                MERGE (bt)-[:HAS_SYNONYM]->(s)
            """, text=syn, term=term)

        col_id = f"{table}.{column}"
        session.run("""
            MATCH (bt:BusinessTerm {name: $term})
            MATCH (c:Column {id: $col_id})
            MERGE (bt)-[:RESOLVES_TO]->(c)
        """, term=term, col_id=col_id)

        print(f"  ✅ '{term}' ({len(synonyms)} synonymes) → {table}.{column}")


# ─── SP1-51 : Métriques ───────────────────────────────────────

def create_metrics(session):
    """
    Crée les Metric avec leur formule, leur table source et leurs
    `requires_terms` (sémantique OR : la métrique s'active si au moins un
    de ces termes est extrait de la question).
    """
    print("\n📊 Métriques calculées (SP1-51)...")
    for name, domain, description, formula, table, requires_terms in METRICS:
        session.run("""
            MERGE (m:Metric {name: $name})
            SET m.domain         = $domain,
                m.description    = $description,
                m.formula        = $formula,
                m.requires_terms = $requires_terms,
                m.updated_at     = datetime()
            WITH m
            MATCH (t:Table {name: $table})
            MERGE (m)-[:COMPUTED_FROM]->(t)
        """, name=name, domain=domain, description=description,
             formula=formula, table=table,
             requires_terms=requires_terms or [])
        req_label = (
            f" [requires={requires_terms}]" if requires_terms else ""
        )
        print(f"  ✅ {name} ({domain}){req_label}")


# ─── SP1-51 : Règles métier ───────────────────────────────────

def create_business_rules(session):
    print("\n📋 Règles métier implicites...")
    for rule_id, table, description, condition, rule_type in BUSINESS_RULES:
        session.run("""
            MERGE (br:BusinessRule {id: $id})
            SET br.description = $description,
                br.condition   = $condition,
                br.rule_type   = $rule_type,
                br.updated_at  = datetime()
            WITH br
            MATCH (t:Table {name: $table})
            MERGE (br)-[:APPLIES_TO]->(t)
        """, id=rule_id, description=description,
             condition=condition, rule_type=rule_type, table=table)
        print(f"  ✅ {rule_id} ({rule_type})")

# ─── SP1-51 : Périodes temporelles ───────────────────────────

def create_time_periods(session):
    print("\n📅 Périodes temporelles (SP1-51)...")
    for name, sql_expr, filter_expr in TIME_PERIODS:
        session.run("""
            MERGE (tp:TimePeriod {name: $name})
            SET tp.sql_expression    = $sql_expr,
                tp.filter_expression = $filter_expr,
                tp.updated_at        = datetime()
        """, name=name, sql_expr=sql_expr, filter_expr=filter_expr)
        print(f"  ✅ {name}")


# ─── Vérification finale ──────────────────────────────────────

def verify(session):
    print("\n🔍 Vérification finale...")
    rows = session.run("""
        MATCH (n)
        RETURN labels(n)[0] AS label, COUNT(n) AS count
        ORDER BY count DESC
    """).data()

    print(f"\n  {'Label':<20} {'Nœuds':>8}")
    print("  " + "─" * 30)
    total = 0
    for r in rows:
        print(f"  {r['label']:<20} {r['count']:>8}")
        total += r["count"]
    print("  " + "─" * 30)
    print(f"  {'TOTAL':<20} {total:>8}")

    rel = session.run("MATCH ()-[r]->() RETURN COUNT(r) AS c").single()["c"]
    print(f"\n  Relations : {rel}")

    # Vérification des relations Entity → Column
    entity_rels = session.run(
        "MATCH (e:Entity)-[:REPRESENTS]->(c:Column) RETURN COUNT(e) AS c"
    ).single()["c"]
    print(f"  Entity→Column : {entity_rels}/{len(ENTITIES)}")

    # Vérification des relations BusinessTerm → Column
    bt_rels = session.run(
        "MATCH (bt:BusinessTerm)-[:RESOLVES_TO]->(c:Column) RETURN COUNT(bt) AS c"
    ).single()["c"]
    print(f"  BusinessTerm→Column : {bt_rels}/{len(BUSINESS_TERMS)}")

    # Affichage des métriques avec requires_terms (pour vérifier la contrainte
    # d'activation correlation_prix_sentiment).
    constrained_metrics = session.run("""
        MATCH (m:Metric)
        WHERE m.requires_terms IS NOT NULL AND size(m.requires_terms) > 0
        RETURN m.name AS name, m.requires_terms AS requires
        ORDER BY m.name
    """).data()
    if constrained_metrics:
        print(f"\n  Métriques avec requires_terms :")
        for r in constrained_metrics:
            print(f"    • {r['name']} ← {r['requires']}")


# ─── Main ─────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  SP1-45 + SP1-51 — Seed complet du Knowledge Graph Neo4j")
    print("  Schéma basé sur le PostgreSQL réel du projet")
    print("=" * 65)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        driver.verify_connectivity()
        print("✅ Connecté à Neo4j")
    except Exception as e:
        print(f"❌ Connexion impossible : {e}")
        print(f"   URI : {NEO4J_URI}")
        print(f"   Vérifier : docker-compose ps")
        return

    with driver.session() as session:
        # SP1-45
        create_constraints_and_indexes(session)
        create_table_nodes(session)
        create_table_relationships(session)

        # SP1-51
        create_entities(session)
        create_business_terms(session)
        create_metrics(session)
        create_business_rules(session)
        create_time_periods(session)

        # Vérification
        verify(session)

    driver.close()

    print(f"\n{'═'*65}")
    print("  ✅ SP1-45 + SP1-51 TERMINÉS")
    print(f"{'═'*65}")


if __name__ == "__main__":
    main()