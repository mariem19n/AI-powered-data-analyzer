"""
app/agents/analysis/llm/schemas.py
Schémas Pydantic pour la sortie du LLM dans l'Analysis Agent.

Stratégie hybride (anti-hallucination) :
- Insight : structure complète avec text, confidence et supporting_stats.
  supporting_stats force le LLM à se rattacher aux stats brutes calculées.
- Recommendation : structure simple avec text et priority.

Le JSON Schema dérivé est utilisé directement comme response_format dans
l'appel OpenAI : le LLM est CONTRAINT au schéma à la génération, et Pydantic
valide ensuite côté Python (double filet).

`validate_supporting_stats_against_keys()` ajoute une validation sémantique
qui ne peut pas être exprimée en JSON Schema : vérifier que les clés citées
par le LLM existent réellement dans le dict de stats fourni.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


# ─── Constantes paramétrables ─────────────────────────────────────────────


# Limites pour borner la taille de la réponse et éviter les sorties verbeuses
# du LLM. Gardent le rendu lisible côté UI et la latence raisonnable.
MAX_INSIGHTS = 5
MAX_RECOMMENDATIONS = 4
MAX_TEXT_LENGTH = 500
MAX_SUPPORTING_STATS = 5

# Niveaux de priorité ouverts à modification si besoin métier.
PRIORITY_LEVELS = ("low", "medium", "high")


# ─── Modèles Pydantic ─────────────────────────────────────────────────────


class Insight(BaseModel):
    """
    Un insight produit par le LLM à partir des stats descriptives.

    Champs :
    - text : phrase NL complète, autoportante (l'utilisateur peut la lire seule).
    - confidence : confiance du LLM dans CET insight ∈ [0, 1].
    - supporting_stats : noms des clés (chemin pointé) du dict stats qui
        soutiennent l'insight. Ex : ["mean", "trend_direction",
        "quantiles.q50"]. Si vide, l'insight est suspecté d'hallucination
        (warning ajouté côté insight_generator).
    """

    model_config = ConfigDict(extra="forbid")  # rejette les clés inattendues

    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_stats: list[str] = Field(
        default_factory=list,
        max_length=MAX_SUPPORTING_STATS,
    )

    @field_validator("supporting_stats")
    @classmethod
    def _strip_and_dedupe(cls, v: list[str]) -> list[str]:
        """Nettoie les espaces et déduplique en préservant l'ordre."""
        seen: set[str] = set()
        out: list[str] = []
        for raw in v:
            key = (raw or "").strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out


class Recommendation(BaseModel):
    """
    Recommandation actionnable. Structure simple : juste texte + priorité.

    Champs :
    - text : phrase NL actionnable.
    - priority : low | medium | high. Permet à l'UI de trier sans interpréter.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    priority: Literal["low", "medium", "high"] = "medium"


class LLMOutput(BaseModel):
    """
    Sortie complète attendue du LLM pour la génération d'insights.

    Champs :
    - insights : list de Insight, peut être vide si le LLM n'a rien de
                 substantiel à dire (mieux que d'inventer).
    - recommendations : list de Recommendation, peut être vide.
    - overall_confidence : confiance globale ∈ [0, 1]. Doit refléter la
                           moyenne des insight.confidence ; un écart important
                           est un signal de bullshit (warning côté caller).
    """

    model_config = ConfigDict(extra="forbid")

    insights: list[Insight] = Field(default_factory=list, max_length=MAX_INSIGHTS)
    recommendations: list[Recommendation] = Field(
        default_factory=list,
        max_length=MAX_RECOMMENDATIONS,
    )
    overall_confidence: float = Field(..., ge=0.0, le=1.0)


# ─── Validation sémantique post-LLM ───────────────────────────────────────


def _flatten_keys(d: dict[str, Any], prefix: str = "") -> set[str]:
    """
    Aplatit un dict en chemins pointés. Utilisé pour valider supporting_stats.

    {"mean": 1, "quantiles": {"q25": 0, "q50": 1}}
    → {"mean", "quantiles", "quantiles.q25", "quantiles.q50"}

    On garde aussi les clés intermédiaires ("quantiles") parce qu'un insight
    peut légitimement citer la structure entière, pas juste une feuille.
    """
    out: set[str] = set()
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        out.add(path)
        if isinstance(v, dict):
            out.update(_flatten_keys(v, prefix=path))
    return out


def validate_supporting_stats_against_keys(
    output: LLMOutput,
    stats: dict[str, Any],
) -> tuple[LLMOutput, list[str]]:
    """
    Vérifie que les `supporting_stats` cités par le LLM existent vraiment
    dans le dict de stats fourni à l'entrée.

    Filtre les clés inexistantes des supporting_stats (mutation contrôlée),
    et retourne la liste des warnings produits.

    Cette validation ne peut pas être faite côté JSON Schema : elle dépend
    du contenu spécifique passé au LLM, pas de la structure générique.

    Args:
        output: LLMOutput déjà validé Pydantic.
        stats: dict des stats brutes (passé en input au LLM).

    Returns:
        (output_filtré, list_warnings) — l'output peut avoir certains
        supporting_stats vidés. Les insights restent inchangés sinon.
    """
    valid_keys = _flatten_keys(stats)
    warnings: list[str] = []

    cleaned_insights: list[Insight] = []
    for idx, ins in enumerate(output.insights):
        valid_supporting = [k for k in ins.supporting_stats if k in valid_keys]
        invalid = [k for k in ins.supporting_stats if k not in valid_keys]
        if invalid:
            warnings.append(
                f"Insight #{idx + 1} cite supporting_stats inexistants : "
                f"{invalid}. Filtrés."
            )
        if not valid_supporting and ins.supporting_stats:
            warnings.append(
                f"Insight #{idx + 1} a TOUS ses supporting_stats invalides "
                f"— possible hallucination."
            )
        # On reconstruit l'Insight avec les supporting_stats nettoyés.
        cleaned_insights.append(
            ins.model_copy(update={"supporting_stats": valid_supporting})
        )

    cleaned = output.model_copy(update={"insights": cleaned_insights})
    return cleaned, warnings


def detect_confidence_divergence(
    output: LLMOutput,
    threshold: float = 0.25,
) -> str | None:
    """
    Détecte un écart suspect entre overall_confidence et la moyenne des
    insight.confidence. Si trop large, signal possible de bullshit du LLM
    (overall élevé alors que les insights individuels sont faibles, ou
    inversement).

    Returns:
        Message de warning ou None si tout va bien.
    """
    if not output.insights:
        return None
    avg_conf = sum(i.confidence for i in output.insights) / len(output.insights)
    diff = abs(output.overall_confidence - avg_conf)
    if diff > threshold:
        return (
            f"overall_confidence={output.overall_confidence:.2f} diverge de "
            f"la moyenne des insight.confidence ({avg_conf:.2f}, écart "
            f"{diff:.2f} > seuil {threshold:.2f})"
        )
    return None
