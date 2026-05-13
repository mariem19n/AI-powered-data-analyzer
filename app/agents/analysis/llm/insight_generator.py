"""
app/agents/analysis/llm/insight_generator.py
Génération d'insights et de recommandations via LLM.

C'est le SEUL appel LLM de l'Analysis Agent.

Utilise le client LLM partagé (app/llm/client.py) déjà en place dans le
projet : LLMClient.chat_json_schema() gère le JSON, la validation Pydantic
et le retry sur erreur de schéma. Ce générateur s'occupe de :
  1. construire le prompt user via le PromptTemplate enregistré
  2. appeler chat_json_schema avec le schéma LLMOutput
  3. validation sémantique : supporting_stats existent dans le dict stats
  4. détection de divergence de confidence (signal anti-bullshit)
  5. fallback stat-based dégradé si tout échoue

La classe est instanciable avec injection du LLMClient → testable avec mocks
et permet d'utiliser soit le singleton partagé (get_llm_client()) soit un
client dédié (ANALYSIS_LLM_MODEL).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.agents.analysis.llm.prompts import get_prompt
from app.agents.analysis.llm.schemas import (
    Insight,
    LLMOutput,
    Recommendation,
    detect_confidence_divergence,
    validate_supporting_stats_against_keys,
)
from app.llm import LLMClient
from app.llm.client import LLMError, LLMJSONError, LLMSchemaError

logger = logging.getLogger(__name__)


# ─── Résultat retourné par le générateur ──────────────────────────────────


@dataclass
class GeneratedInsights:
    """
    Résultat structuré de la génération.

    Les tasks consomment ce dataclass et le mappent dans leur TaskResult :
    - insights / recommendations → TaskResult.insights / .recommendations
      (extraction du .text par la task)
    - warnings → TaskResult.warnings (concat avec ceux de la task)
    - overall_confidence → TaskResult.metadata["confidence"]
    - llm_metadata → TaskResult.metadata (model, fallback flag, ...)
    """

    insights: list[Insight] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    overall_confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    llm_metadata: dict[str, Any] = field(default_factory=dict)
    used_fallback: bool = False


# ─── Générateur ───────────────────────────────────────────────────────────


@dataclass
class InsightGenerator:
    """
    Génère insights et recommandations via le LLMClient partagé.

    Le client est INJECTÉ dans le constructeur (DI). Deux usages typiques :

        # 1. Modèle global du projet (variable LLM_MODEL)
        from app.llm import get_llm_client
        generator = InsightGenerator(client=get_llm_client())

        # 2. Modèle dédié à l'Analysis Agent (ANALYSIS_LLM_MODEL)
        import os
        from app.llm import LLMClient
        generator = InsightGenerator(
            client=LLMClient(model=os.getenv("ANALYSIS_LLM_MODEL")),
        )

    Args:
        client : LLMClient injecté (singleton partagé ou instance dédiée).
        temperature : par défaut 0.0 pour reproductibilité analytique.
        max_tokens : limite tokens en sortie.
        purpose_prefix : préfixe pour les métriques par-task. Ex : pour
            task='descriptive', purpose passé au client = 'analysis_descriptive'.
            Permet aux métriques LLM de distinguer les tasks de l'Analysis Agent
            des autres appels (extractor, intent, ...).
    """

    client: LLMClient
    temperature: float = 0.2
    max_tokens: int = 1500
    purpose_prefix: str = "analysis_"

    def generate(
        self,
        *,
        task_name: str,
        stats: dict[str, Any],
        prompt_kwargs: dict[str, Any] | None = None,
    ) -> GeneratedInsights:
        """
        Génère les insights pour une task donnée.

        Args:
            task_name : clé du PromptTemplate (descriptive, anomaly, ...).
            stats : dict de stats brutes — passé au prompt ET utilisé pour la
                validation sémantique des supporting_stats.
            prompt_kwargs : kwargs supplémentaires passés au build_user du
                prompt (shape, subtype, warnings, semantic_hints, ...).

        Returns:
            GeneratedInsights — toujours un résultat exploitable. En cas
            d'échec LLM, used_fallback=True et un insight stat-based
            dégradé est produit.
        """
        warnings_out: list[str] = []
        prompt_kwargs = prompt_kwargs or {}
        purpose = f"{self.purpose_prefix}{task_name}"

        # 1. Récupération du prompt template.
        try:
            template = get_prompt(task_name)
        except KeyError as e:
            warnings_out.append(
                f"Prompt introuvable pour task='{task_name}'. Fallback."
            )
            logger.error("Prompt lookup failed: %s", e)
            return self._build_fallback(stats, warnings_out)

        # 2. Construction du prompt user.
        try:
            user_prompt = template.build_user(stats=stats, **prompt_kwargs)
        except Exception as e:  # noqa: BLE001 — un builder peut tout lever
            warnings_out.append(
                f"Échec de construction du prompt user : {e}. Fallback."
            )
            logger.exception("Prompt user build failed for task=%s", task_name)
            return self._build_fallback(stats, warnings_out)

        # 3. Appel LLM via chat_json_schema (JSON + Pydantic + retry sur schéma
        #    sont gérés en interne par le client).
        try:
            llm_output: LLMOutput = self.client.chat_json_schema(
                system=template.system,
                user=user_prompt,
                schema=LLMOutput,
                purpose=purpose,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except LLMSchemaError as e:
            warnings_out.append(
                f"Sortie LLM non conforme au schéma après retry : {e}. "
                f"Insight dégradé fourni."
            )
            logger.error("LLM schema error for task=%s: %s", task_name, e)
            return self._build_fallback(stats, warnings_out)
        except LLMJSONError as e:
            warnings_out.append(
                f"Sortie LLM JSON invalide : {e}. Insight dégradé fourni."
            )
            logger.error("LLM JSON error for task=%s: %s", task_name, e)
            return self._build_fallback(stats, warnings_out)
        except LLMError as e:
            warnings_out.append(
                f"LLM en échec après retries : {e}. Insight dégradé fourni."
            )
            logger.error("LLM API failure for task=%s: %s", task_name, e)
            return self._build_fallback(stats, warnings_out)

        # 4. Validation sémantique : supporting_stats existent dans stats.
        cleaned_output, sem_warnings = validate_supporting_stats_against_keys(
            llm_output, stats
        )
        warnings_out.extend(sem_warnings)

        # 5. Détection de divergence de confidence (signal bullshit).
        divergence_warning = detect_confidence_divergence(cleaned_output)
        if divergence_warning:
            warnings_out.append(divergence_warning)

        # 6. Construction du résultat final.
        return GeneratedInsights(
            insights=cleaned_output.insights,
            recommendations=cleaned_output.recommendations,
            overall_confidence=cleaned_output.overall_confidence,
            warnings=warnings_out,
            llm_metadata={
                "model": self.client.model,
                "purpose": purpose,
            },
            used_fallback=False,
        )

    # ─── Fallback stat-based (NL dégradé mais TaskResult valide) ──────────

    def _build_fallback(
        self,
        stats: dict[str, Any],
        warnings: list[str],
    ) -> GeneratedInsights:
        """
        Construit un GeneratedInsights dégradé sans LLM.

        L'objectif : ne JAMAIS casser le pipeline. L'utilisateur reçoit
        une description factuelle minimale construite mécaniquement à partir
        des stats les plus saillantes. La confidence est volontairement
        basse pour signaler la dégradation côté UI.

        On ne hardcode aucune clé spécifique au domaine. On consomme les
        clés génériques produites par stats/descriptive.summarize_*.
        """
        text_parts: list[str] = []
        supporting: list[str] = []

        n = stats.get("n")
        if isinstance(n, int) and n > 0:
            text_parts.append(f"{n} observations analysées.")
            supporting.append("n")

        # Pour timeseries : direction, variation totale, premiers/derniers points.
        trend = stats.get("trend_direction")
        if isinstance(trend, str):
            text_parts.append(f"Tendance : {trend}.")
            supporting.append("trend_direction")

        pct = stats.get("pct_change_total")
        if isinstance(pct, (int, float)):
            text_parts.append(f"Variation totale : {pct * 100:.2f}%.")
            supporting.append("pct_change_total")

        first = stats.get("first")
        last = stats.get("last")
        if isinstance(first, (int, float)) and isinstance(last, (int, float)):
            text_parts.append(f"Première valeur : {first}, dernière : {last}.")
            supporting.extend(["first", "last"])

        # Pour groupby : nombre de groupes.
        n_groups = stats.get("n_groups")
        if isinstance(n_groups, int) and n_groups > 0:
            text_parts.append(f"{n_groups} groupes analysés.")
            supporting.append("n_groups")

        if not text_parts:
            text_parts.append(
                "Analyse statistique disponible (résultat textuel non généré "
                "automatiquement)."
            )

        fallback_insight = Insight(
            text=" ".join(text_parts),
            confidence=0.3,
            supporting_stats=supporting,
        )

        warnings.append(
            "Insight généré en mode dégradé (sans LLM) suite à un échec "
            "API ou validation. Confidence forcée à 0.3."
        )

        return GeneratedInsights(
            insights=[fallback_insight],
            recommendations=[],
            overall_confidence=0.3,
            warnings=warnings,
            llm_metadata={"fallback": True, "model": self.client.model},
            used_fallback=True,
        )
