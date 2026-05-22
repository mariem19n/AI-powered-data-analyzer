"""
app/agents/feedback/score.py

ScoreCalculator — Calcul du score composite pondéré.

Le score final est sur l'échelle 1–5 (cohérent avec le rating utilisateur).

Redistribution automatique : si une composante est absente (ex: pas
d'expert), sa pondération est redistribuée proportionnellement.

Exemple sans expert :
  humain = 0.50 / (0.50 + 0.30) = 0.625  (62.5%)
  implicite = 0.30 / (0.50 + 0.30) = 0.375  (37.5%)
  expert = 0.0

Ne fait aucun appel LLM, aucune écriture KG, aucun accès Redis.
"""

from __future__ import annotations

import logging

from app.agents.feedback.config import FeedbackConfig, feedback_config
from app.agents.feedback.models import (
    CompositeScore,
    FeedbackInput,
    FeedbackStatus,
    ImplicitSignals,
)

logger = logging.getLogger(__name__)


class ScoreCalculator:
    """Calcule le score composite à partir du feedback humain et implicite."""

    def __init__(self, config: FeedbackConfig | None = None) -> None:
        self.cfg = config or feedback_config

    def compute(
        self,
        feedback: FeedbackInput,
        implicit: ImplicitSignals,
        expert_score: float | None = None,
    ) -> CompositeScore:
        """Calcule le score composite pondéré.

        Parameters
        ----------
        feedback : FeedbackInput
            Contient le rating humain (1–5).
        implicit : ImplicitSignals
            Signaux d'exécution automatiques.
        expert_score : float | None
            Score de validation expert (1–5). None si pas disponible.

        Returns
        -------
        CompositeScore
        """
        # ── Scores individuels (1–5) ──────────────────────────
        score_human: float | None = float(feedback.rating)
        score_implicit: float | None = self._compute_implicit_score(implicit)
        score_expert: float | None = expert_score

        # ── Redistribution des pondérations ────────────────────
        components: list[tuple[float | None, float]] = [
            (score_human, self.cfg.FEEDBACK_WEIGHT_HUMAN),
            (score_implicit, self.cfg.FEEDBACK_WEIGHT_IMPLICIT),
            (score_expert, self.cfg.FEEDBACK_WEIGHT_EXPERT),
        ]

        # Filtrer les composantes présentes (score non-None)
        present = [
            (score, weight)
            for score, weight in components
            if score is not None
        ]

        if not present:
            # Aucun signal — score neutre par défaut
            composite = 3.0
            w_human = w_implicit = w_expert = 0.0
        else:
            total_weight = sum(w for _, w in present)
            # Pondérations effectives redistribuées
            effective: dict[int, float] = {}
            for idx, (score, weight) in enumerate(components):
                if score is not None:
                    effective[idx] = weight / total_weight
                else:
                    effective[idx] = 0.0

            w_human = effective[0]
            w_implicit = effective[1]
            w_expert = effective[2]

            composite = sum(
                score * effective[idx]
                for idx, (score, weight) in enumerate(components)
                if score is not None
            )

        # Clamp sur 1–5
        composite = max(1.0, min(5.0, round(composite, 2)))

        # ── Statut ─────────────────────────────────────────────
        status = self._derive_status(composite)

        logger.info(
            "Score composite calculé : %.2f (%s) — "
            "humain=%.1f (w=%.3f) implicite=%s (w=%.3f) expert=%s (w=%.3f)",
            composite,
            status.value,
            score_human or 0.0,
            w_human,
            f"{score_implicit:.1f}" if score_implicit is not None else "N/A",
            w_implicit,
            f"{score_expert:.1f}" if score_expert is not None else "N/A",
            w_expert,
        )

        return CompositeScore(
            score_human=score_human,
            score_implicit=score_implicit,
            score_expert=score_expert,
            weight_human=round(w_human, 4),
            weight_implicit=round(w_implicit, 4),
            weight_expert=round(w_expert, 4),
            composite=composite,
            status=status,
        )

    # ── Score implicite (1–5) ──────────────────────────────────

    def _compute_implicit_score(
        self, signals: ImplicitSignals
    ) -> float | None:
        """Combine les signaux d'exécution en un score 1–5.

        Chaque signal produit un sous-score 1–5 pondéré par son poids.
        Si aucun signal exploitable n'est disponible, retourne None
        (la composante sera absente → redistribution).
        """
        sub_scores: list[tuple[float, float]] = []  # (score, weight)

        # 1. Succès d'exécution : binaire → 5 ou 1
        sub_scores.append((
            5.0 if signals.execution_success else 1.0,
            self.cfg.IMPLICIT_WEIGHT_EXECUTION_SUCCESS,
        ))

        # 2. Temps de réponse : interpolation linéaire
        if signals.response_time_seconds is not None:
            score_rt = self._linear_interpolate(
                value=signals.response_time_seconds,
                good=self.cfg.IMPLICIT_RESPONSE_TIME_FAST,
                bad=self.cfg.IMPLICIT_RESPONSE_TIME_SLOW,
                invert=True,  # plus rapide = mieux
            )
            sub_scores.append((
                score_rt,
                self.cfg.IMPLICIT_WEIGHT_RESPONSE_TIME,
            ))

        # 3. Taux de nulls : interpolation linéaire
        if signals.null_rate is not None:
            score_null = self._linear_interpolate(
                value=signals.null_rate,
                good=self.cfg.IMPLICIT_NULL_RATE_GOOD,
                bad=self.cfg.IMPLICIT_NULL_RATE_BAD,
                invert=True,  # moins de nulls = mieux
            )
            sub_scores.append((
                score_null,
                self.cfg.IMPLICIT_WEIGHT_NULL_RATE,
            ))

        # 4. Confiance LLM : mapping direct 0.0–1.0 → 1–5
        if signals.llm_confidence is not None:
            score_llm = 1.0 + 4.0 * max(0.0, min(1.0, signals.llm_confidence))
            sub_scores.append((
                score_llm,
                self.cfg.IMPLICIT_WEIGHT_LLM_CONFIDENCE,
            ))

        if not sub_scores:
            return None

        # Pondération avec redistribution (même logique que le composite)
        total_w = sum(w for _, w in sub_scores)
        if total_w == 0:
            return None

        result = sum(s * (w / total_w) for s, w in sub_scores)
        return max(1.0, min(5.0, round(result, 2)))

    # ── Utilitaires ────────────────────────────────────────────

    @staticmethod
    def _linear_interpolate(
        value: float,
        good: float,
        bad: float,
        invert: bool = False,
    ) -> float:
        """Interpolation linéaire entre good (→5) et bad (→1).

        Si invert=True, les valeurs basses sont bonnes (temps de réponse,
        taux de nulls).
        """
        if good == bad:
            return 3.0

        # Normaliser entre 0 et 1 (0 = good, 1 = bad)
        if invert:
            # value faible = bon
            ratio = (value - good) / (bad - good)
        else:
            # value élevée = bon
            ratio = (bad - value) / (bad - good)

        ratio = max(0.0, min(1.0, ratio))
        return 1.0 + 4.0 * (1.0 - ratio) if invert else 1.0 + 4.0 * ratio

    def _derive_status(self, composite: float) -> FeedbackStatus:
        """Déduit le statut depuis le score composite."""
        if composite >= self.cfg.FEEDBACK_REINFORCE_THRESHOLD:
            return FeedbackStatus.REINFORCED
        if composite <= self.cfg.FEEDBACK_DEPRECIATE_THRESHOLD:
            return FeedbackStatus.DEPRECATED
        return FeedbackStatus.NEUTRAL
