"""
app/agents/analysis/kg_writer.py
Écriture des Insights et Anomalies produits par l'Analysis Agent dans Neo4j.

Conception :
- Module pur side-effect : il consomme un `kg_payload` (list[dict]) déjà
  construit par une task et écrit dans Neo4j. Il ne calcule rien, ne raisonne
  pas, ne fait pas de logique métier.
- Idempotent : chaque entrée a un `node_id` déterministe (hash stable) → si
  la même question est rejouée, MERGE met à jour au lieu de dupliquer.
- Non-bloquant : un échec d'écriture (Neo4j down, contrainte violée, ...)
  ne lève jamais. Le writer retourne une liste de warnings que la task
  ajoute à son TaskResult. Le pipeline reste exploitable même KG cassé.

Format attendu pour chaque entrée de kg_payload :
    {
        "node_type": "Insight" | "Anomaly" | ...,
        "properties": {
            "text": "...",
            "confidence": 0.85,
            "supporting_stats": [...],
            ...
        },
        # Optionnels :
        "relationships": [
            {
                "type": "DERIVED_FROM",     # nom de relation Cypher
                "target_type": "Question",
                "target_match": {"question_id": "..."},  # propriétés pour MATCH
                "direction": "outgoing",     # 'outgoing' (n)-[]->(t) ou 'incoming'
            },
            ...
        ],
    }

Le `node_id` (hash stable) est ajouté automatiquement par
`prepare_kg_payload()` ou peut être pré-fourni dans `properties["node_id"]`.

Aucun nom de label, type de relation ou propriété n'est hardcodé ici. Les
tasks contrôlent entièrement le contenu du payload. Le writer fait juste
le pont vers Neo4j.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ─── Constantes ────────────────────────────────────────────────────────────


# Labels Neo4j supportés actuellement par le writer. La liste est extensible :
# ajouter un label ici suffit, aucune autre modification n'est nécessaire.
# C'est juste un filet de sécurité contre les fautes de frappe ("Insigth").
SUPPORTED_NODE_TYPES: frozenset[str] = frozenset({
    "Insight",
    "Anomaly",
    "Correlation",
    "Forecast",
})

# Direction par défaut des relations (du nœud créé vers la cible).
DEFAULT_RELATIONSHIP_DIRECTION = "outgoing"


# ─── Protocol pour le driver Neo4j ────────────────────────────────────────


class Neo4jDriverLike(Protocol):
    """
    Interface minimale attendue du driver Neo4j (DI pour testabilité).

    L'implémentation réelle est `app/kg/neo4j.py` (Sprint 1). On ne dépend
    pas de cette implémentation directement pour permettre les mocks en test.
    """

    def execute_write(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> Any: ...


# ─── Résultat ──────────────────────────────────────────────────────────────


@dataclass
class KGWriteResult:
    """
    Résultat d'une écriture KG.

    - `written_count` : nombre d'entrées écrites avec succès (créées ou mises à jour)
    - `failed_count` : nombre d'entrées qui ont échoué
    - `warnings` : liste de messages destinés à TaskResult.warnings
    - `node_ids` : ids des nœuds écrits (utile pour log et tests)
    """

    written_count: int = 0
    failed_count: int = 0
    warnings: list[str] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)


# ─── Calcul du node_id déterministe ───────────────────────────────────────


def compute_node_id(node_type: str, properties: dict[str, Any]) -> str:
    """
    Calcule un identifiant stable et déterministe pour un nœud.

    L'idée : deux écritures successives produisant le même contenu doivent
    avoir le même `node_id` → MERGE met à jour au lieu de dupliquer.

    On hash :
    - le type de nœud
    - les propriétés "stables" (text, supporting_stats, task_name)
      en excluant les propriétés volatiles (created_at, confidence)
      pour qu'un re-run avec une confidence légèrement différente reste
      le même insight logique.

    L'algorithme :
    1. Sérialise les clés stables triées en JSON canonique
    2. SHA256 → tronqué à 16 caractères hex (collision improbable à l'échelle
       d'un projet, lisible dans les logs)

    Args:
        node_type : label Neo4j (Insight, Anomaly, ...)
        properties : dict de propriétés du nœud

    Returns:
        Hash hex de 16 caractères, préfixé du type pour debug visuel.
        Ex: "Insight:a3f2b1c4d5e6f789"
    """
    # Clés volatiles ignorées dans le hash — elles peuvent varier entre runs
    # sans changer l'identité logique du nœud.
    volatile_keys = {"created_at", "updated_at", "confidence", "overall_confidence"}

    stable = {k: v for k, v in properties.items() if k not in volatile_keys}

    # Sérialisation canonique (clés triées, séparateurs sans espace).
    canonical = json.dumps(stable, sort_keys=True, ensure_ascii=False, default=str)

    digest = hashlib.sha256(
        f"{node_type}|{canonical}".encode("utf-8")
    ).hexdigest()[:16]

    return f"{node_type}:{digest}"


# ─── Préparation du payload (annotation + validation) ─────────────────────


def prepare_kg_payload(
    payload: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Valide et enrichit un kg_payload avant écriture.

    Pour chaque entrée :
    - vérifie node_type et properties (présents et bien typés)
    - rejette les entrées avec node_type non supporté
    - calcule node_id si absent (hash déterministe sur les propriétés stables)
    - ajoute created_at en UTC ISO si absent

    Args:
        payload : list[dict] tel que produit par une task.

    Returns:
        (payload_enrichi, warnings) — les entrées invalides sont écartées
        et un warning est ajouté pour chacune.
    """
    enriched: list[dict[str, Any]] = []
    warnings: list[str] = []

    for idx, entry in enumerate(payload):
        if not isinstance(entry, dict):
            warnings.append(
                f"kg_payload[{idx}] ignoré : type {type(entry).__name__} "
                f"(dict attendu)"
            )
            continue

        node_type = entry.get("node_type")
        if not isinstance(node_type, str) or not node_type:
            warnings.append(
                f"kg_payload[{idx}] ignoré : node_type manquant ou invalide"
            )
            continue

        if node_type not in SUPPORTED_NODE_TYPES:
            warnings.append(
                f"kg_payload[{idx}] ignoré : node_type='{node_type}' non supporté. "
                f"Valeurs acceptées : {sorted(SUPPORTED_NODE_TYPES)}"
            )
            continue

        properties = entry.get("properties")
        if not isinstance(properties, dict):
            warnings.append(
                f"kg_payload[{idx}] ignoré : properties manquant ou non-dict"
            )
            continue

        # Copie pour ne pas muter l'entrée originale.
        new_props = dict(properties)
        new_props.setdefault(
            "created_at",
            datetime.now(timezone.utc).isoformat(),
        )
        new_props.setdefault("node_id", compute_node_id(node_type, new_props))

        enriched.append(
            {
                "node_type": node_type,
                "properties": new_props,
                "relationships": entry.get("relationships") or [],
            }
        )

    return enriched, warnings


# ─── Construction des requêtes Cypher ─────────────────────────────────────


def _build_node_merge_query(node_type: str) -> str:
    """
    Construit une requête Cypher MERGE pour un nœud du type donné.

    MERGE sur node_id (clé d'identité stable) → idempotent.
    Toutes les autres propriétés sont écrites via SET (overwrite).

    Le paramètre $properties est un dict — c'est Neo4j qui itère dessus
    avec `+= $properties` (syntaxe pour fusion de map).

    Note: on injecte `node_type` directement dans la string parce que les
    labels Neo4j ne peuvent PAS être paramétrés (limitation Cypher).
    On a déjà validé `node_type ∈ SUPPORTED_NODE_TYPES` en amont, donc pas
    de risque d'injection — c'est une whitelist stricte.
    """
    return (
        f"MERGE (n:{node_type} {{node_id: $node_id}}) "
        f"SET n += $properties "
        f"RETURN n.node_id AS node_id"
    )


def _build_relationship_query(
    source_type: str,
    rel_type: str,
    target_type: str,
    direction: str,
) -> str:
    """
    Construit une requête Cypher pour créer une relation entre un nœud
    source (déjà créé) et un nœud cible (matché par propriétés).

    MATCH les deux nœuds, puis MERGE la relation pour idempotence.

    Direction :
    - 'outgoing' : (source)-[r:REL]->(target)
    - 'incoming' : (source)<-[r:REL]-(target)

    Limitation Cypher identique à _build_node_merge_query : labels et type
    de relation ne peuvent pas être paramétrés. On valide leur format
    (alphanumeric + underscore) avant injection pour bloquer toute injection.
    """
    if direction == "outgoing":
        arrow_open, arrow_close = "-", "->"
    else:
        arrow_open, arrow_close = "<-", "-"

    return (
        f"MATCH (s:{source_type} {{node_id: $source_id}}) "
        f"MATCH (t:{target_type}) WHERE t += $target_match "
        # Note : la ligne ci-dessus n'est pas valide Cypher en l'état, on
        # construit le WHERE dynamiquement plus bas. Cette fonction n'est
        # utilisée que via _execute_relationship qui assemble correctement.
        f"MERGE (s){arrow_open}[r:{rel_type}]{arrow_close}(t) "
        f"RETURN id(r) AS rel_id"
    )


def _validate_cypher_identifier(value: str, name: str) -> None:
    """
    Vérifie qu'une string ne contient que des caractères alphanumériques et
    underscores — sécurité contre l'injection Cypher pour les labels et les
    types de relation (qui ne peuvent pas être paramétrés).

    Lève ValueError si invalide.
    """
    if not value or not all(c.isalnum() or c == "_" for c in value):
        raise ValueError(
            f"Cypher identifier invalide pour {name}='{value}' "
            f"(attendu : alphanumérique + underscore uniquement)"
        )


def _execute_relationship(
    driver: Neo4jDriverLike,
    source_type: str,
    source_id: str,
    rel_spec: dict[str, Any],
) -> tuple[bool, str | None]:
    """
    Exécute la création d'une relation. Retourne (success, error_msg).

    Construit le WHERE dynamiquement à partir de target_match — une clé
    par propriété cible, paramètres nommés pour bloquer toute injection.
    """
    rel_type = rel_spec.get("type")
    target_type = rel_spec.get("target_type")
    target_match = rel_spec.get("target_match") or {}
    direction = rel_spec.get("direction", DEFAULT_RELATIONSHIP_DIRECTION)

    if not isinstance(rel_type, str) or not rel_type:
        return False, "rel.type manquant"
    if not isinstance(target_type, str) or not target_type:
        return False, "rel.target_type manquant"
    if not isinstance(target_match, dict) or not target_match:
        return False, "rel.target_match manquant ou vide"
    if direction not in ("outgoing", "incoming"):
        return False, f"rel.direction='{direction}' invalide"

    try:
        _validate_cypher_identifier(rel_type, "rel.type")
        _validate_cypher_identifier(target_type, "rel.target_type")
        _validate_cypher_identifier(source_type, "source_type")
    except ValueError as e:
        return False, str(e)

    # Construction du WHERE paramétré.
    where_clauses = []
    params: dict[str, Any] = {"source_id": source_id}
    for i, (key, value) in enumerate(target_match.items()):
        try:
            _validate_cypher_identifier(key, f"target_match.key[{i}]")
        except ValueError as e:
            return False, str(e)
        param_name = f"tm_{i}"
        where_clauses.append(f"t.{key} = ${param_name}")
        params[param_name] = value

    where_str = " AND ".join(where_clauses)

    if direction == "outgoing":
        rel_pattern = f"(s)-[r:{rel_type}]->(t)"
    else:
        rel_pattern = f"(s)<-[r:{rel_type}]-(t)"

    query = (
        f"MATCH (s:{source_type} {{node_id: $source_id}}) "
        f"MATCH (t:{target_type}) WHERE {where_str} "
        f"MERGE {rel_pattern} "
        f"RETURN id(r) AS rel_id"
    )

    try:
        driver.execute_write(query, params)
        return True, None
    except Exception as e:  # noqa: BLE001 — on remonte toute erreur en warning
        return False, f"{type(e).__name__}: {e}"


# ─── Writer principal ─────────────────────────────────────────────────────


@dataclass
class KGWriter:
    """
    Écrit un kg_payload (produit par une task) dans Neo4j.

    Args:
        driver : driver Neo4j injecté (DI pour testabilité).

    Usage typique depuis le runner :
        writer = KGWriter(driver=neo4j_driver)
        result = writer.write(task_result.kg_payload)
        task_result.warnings.extend(result.warnings)
    """

    driver: Neo4jDriverLike

    def write(self, payload: list[dict[str, Any]] | None) -> KGWriteResult:
        """
        Écrit chaque entrée du payload dans Neo4j.

        Non-bloquant : un échec sur une entrée n'arrête pas le traitement
        des suivantes. Tout est remonté dans KGWriteResult.warnings.

        Returns:
            KGWriteResult avec compteurs et warnings.
        """
        result = KGWriteResult()

        if not payload:
            return result

        prepared, prep_warnings = prepare_kg_payload(payload)
        result.warnings.extend(prep_warnings)

        for entry in prepared:
            success, node_id, error_msgs = self._write_entry(entry)
            if success:
                result.written_count += 1
                if node_id is not None:
                    result.node_ids.append(node_id)
            else:
                result.failed_count += 1
            result.warnings.extend(error_msgs)

        if result.written_count > 0:
            logger.info(
                "KG write : %d nœud(s) écrit(s), %d échec(s)",
                result.written_count,
                result.failed_count,
            )
        if result.failed_count > 0:
            logger.warning(
                "KG write : %d entrée(s) ont échoué — voir warnings",
                result.failed_count,
            )

        return result

    # ─── Implémentation par entrée ────────────────────────────────────────

    def _write_entry(
        self, entry: dict[str, Any]
    ) -> tuple[bool, str | None, list[str]]:
        """
        Écrit une seule entrée (nœud + relations).

        Returns:
            (success, node_id, error_msgs)
        """
        node_type = entry["node_type"]
        properties = entry["properties"]
        relationships = entry.get("relationships") or []
        errors: list[str] = []

        # Validation finale du label avant injection en Cypher.
        try:
            _validate_cypher_identifier(node_type, "node_type")
        except ValueError as e:
            return False, None, [str(e)]

        node_id = properties.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            return False, None, [
                f"node_id manquant pour {node_type} après prepare — bug interne"
            ]

        # 1. Création / mise à jour du nœud.
        try:
            query = _build_node_merge_query(node_type)
            self.driver.execute_write(
                query,
                {"node_id": node_id, "properties": properties},
            )
        except Exception as e:  # noqa: BLE001 — on log et on continue
            return False, None, [
                f"Échec MERGE {node_type}({node_id}) : {type(e).__name__}: {e}"
            ]

        # 2. Création des relations.
        for rel_idx, rel_spec in enumerate(relationships):
            ok, err = _execute_relationship(
                driver=self.driver,
                source_type=node_type,
                source_id=node_id,
                rel_spec=rel_spec,
            )
            if not ok:
                errors.append(
                    f"Relation #{rel_idx} de {node_type}({node_id}) : {err}"
                )

        return True, node_id, errors
