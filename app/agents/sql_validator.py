"""
app/agents/sql_validator.py
Output Validator — vérifie la qualité des résultats SQL.

S'exécute APRÈS l'exécution de la requête. Vérifie :
  - Le résultat n'est pas vide
  - Les types de données sont cohérents
  - Pas de NaN inattendus dans les colonnes critiques
  - Le nombre de lignes est raisonnable
  - Les colonnes retournées correspondent à ce qui a été demandé

Ne bloque pas l'exécution — retourne des warnings et un score
de confiance que l'Orchestrator peut utiliser pour décider de
retenter ou non.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Résultat de la validation d'un résultat SQL."""

    is_valid: bool = True
    confidence: float = 1.0  # 0.0 à 1.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    row_count: int = 0
    column_count: int = 0

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)
        self.confidence = max(0.0, self.confidence - 0.1)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.is_valid = False
        self.confidence = max(0.0, self.confidence - 0.3)


# Colonnes qui ne devraient jamais avoir de NaN
CRITICAL_COLUMNS: set[str] = {
    "date", "symbol", "fred_code", "keyword",
}

# Seuils
MAX_ROWS_WARNING = 50_000
MAX_ROWS_ERROR = 500_000
MIN_ROWS_WARNING = 0


class OutputValidator:
    """
    Valide les résultats d'une exécution SQL.

    Usage :
        validator = OutputValidator()
        result = validator.validate(records, columns, ctx)
    """

    def validate(
        self,
        records: list[dict[str, Any]],
        columns: list[str],
        semantic_context: dict[str, Any] | None = None,
    ) -> ValidationResult:
        """
        Valide un résultat SQL.

        Args:
            records: liste de dicts (DataFrame.to_dict('records'))
            columns: noms des colonnes retournées
            semantic_context: pour vérifier la cohérence avec la demande

        Returns:
            ValidationResult avec warnings, erreurs et score de confiance
        """
        result = ValidationResult(
            row_count=len(records),
            column_count=len(columns),
        )

        # 1. Vérifier que le résultat n'est pas vide
        self._check_empty(records, result)

        # 2. Vérifier le nombre de lignes
        self._check_row_count(records, result)

        # 3. Vérifier les NaN/None dans les colonnes critiques
        self._check_null_values(records, columns, result)

        # 4. Vérifier la cohérence des types
        self._check_type_consistency(records, columns, result)

        # 5. Vérifier la correspondance avec le SemanticContext
        if semantic_context:
            self._check_context_match(columns, semantic_context, result)

        # Log du résultat
        if result.errors:
            logger.warning(
                "Validation FAILED — %d errors, %d warnings, confidence=%.2f : %s",
                len(result.errors),
                len(result.warnings),
                result.confidence,
                "; ".join(result.errors),
            )
        elif result.warnings:
            logger.info(
                "Validation OK with %d warnings, confidence=%.2f : %s",
                len(result.warnings),
                result.confidence,
                "; ".join(result.warnings),
            )
        else:
            logger.info(
                "Validation OK — %d rows, %d cols, confidence=%.2f",
                result.row_count,
                result.column_count,
                result.confidence,
            )

        return result

    # ─── Checks individuels ───────────────────────────────────

    @staticmethod
    def _check_empty(
        records: list[dict], result: ValidationResult
    ) -> None:
        if not records:
            result.add_warning(
                "Résultat vide — aucune ligne retournée. "
                "Le filtre est peut-être trop restrictif."
            )

    @staticmethod
    def _check_row_count(
        records: list[dict], result: ValidationResult
    ) -> None:
        count = len(records)
        if count > MAX_ROWS_ERROR:
            result.add_error(
                f"Trop de lignes ({count:,}) — la requête est probablement "
                f"trop large. Limite recommandée : {MAX_ROWS_WARNING:,}."
            )
        elif count > MAX_ROWS_WARNING:
            result.add_warning(
                f"Beaucoup de lignes ({count:,}) — considérer un filtre "
                f"temporel plus restrictif ou un GROUP BY."
            )

    @staticmethod
    def _check_null_values(
        records: list[dict],
        columns: list[str],
        result: ValidationResult,
    ) -> None:
        if not records:
            return

        for col in columns:
            if col not in CRITICAL_COLUMNS:
                continue

            null_count = sum(
                1 for row in records
                if row.get(col) is None
            )
            if null_count > 0:
                pct = null_count / len(records) * 100
                if pct > 50:
                    result.add_error(
                        f"Colonne critique '{col}' a {pct:.0f}% de valeurs NULL"
                    )
                elif pct > 10:
                    result.add_warning(
                        f"Colonne critique '{col}' a {pct:.0f}% de valeurs NULL"
                    )

    @staticmethod
    def _check_type_consistency(
        records: list[dict],
        columns: list[str],
        result: ValidationResult,
    ) -> None:
        """
        Vérifie que chaque colonne a des types cohérents.
        Tolère les None — vérifie les valeurs non-null.
        """
        if len(records) < 2:
            return

        sample = records[:100]  # vérifier sur un échantillon

        for col in columns:
            types_seen: set[str] = set()
            for row in sample:
                val = row.get(col)
                if val is not None:
                    types_seen.add(type(val).__name__)

            # Si plus de 2 types différents dans la même colonne → warning
            if len(types_seen) > 2:
                result.add_warning(
                    f"Colonne '{col}' a des types mixtes : {types_seen}"
                )

    @staticmethod
    def _check_context_match(
        columns: list[str],
        ctx: dict[str, Any],
        result: ValidationResult,
    ) -> None:
        """
        Vérifie que les colonnes retournées contiennent ce qui était demandé.
        """
        requested_columns: set[str] = set()
        for col_spec in ctx.get("columns", []):
            requested_columns.add(col_spec.get("column", ""))
        for ef in ctx.get("entity_filters", []):
            requested_columns.add(ef.get("column", ""))

        # Supprimer les vides
        requested_columns.discard("")

        if not requested_columns:
            return

        columns_set = set(columns)
        missing = requested_columns - columns_set
        if missing:
            result.add_warning(
                f"Colonnes demandées mais absentes du résultat : {missing}"
            )
