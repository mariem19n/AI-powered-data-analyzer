"""
app/semantic/schemas.py
Modèles Pydantic pour la sortie du Semantic Layer.

ExtractedTerms : résultat de SP1-46 (extraction LLM)
SemanticContext : résultat final de SP1-50 (après KG lookup) — défini ici
                 pour avoir tous les schémas au même endroit.
"""

from pydantic import BaseModel, Field


class ExtractedTerms(BaseModel):
    """
    Sortie de SP1-46 — BusinessTermsExtractor.
    Résultat brut de l'extraction LLM avant résolution dans le KG.
    """

    raw_question: str = Field(
        description="Question originale posée par l'utilisateur"
    )

    business_terms: list[str] = Field(
        default_factory=list,
        description="Termes métier reconnus — correspondent aux BusinessTerm du KG. "
                    "Ex: ['prix Bitcoin', 'sentiment crypto', 'volatilité']",
    )

    entities: list[str] = Field(
        default_factory=list,
        description="Entités métier reconnues — correspondent aux Entity du KG. "
                    "Ex: ['Bitcoin', 'Ethereum', 'FEDFUNDS', 'Reuters']",
    )

    time_periods: list[str] = Field(
        default_factory=list,
        description="Expressions temporelles reconnues — correspondent aux TimePeriod du KG. "
                    "Ex: ['30 derniers jours', 'ce mois', 'bull market 2024']",
    )

    metrics: list[str] = Field(
        default_factory=list,
        description="Métriques calculées reconnues — correspondent aux Metric du KG. "
                    "Ex: ['moyenne_mobile_7j', 'volatilite_30j']",
    )

    unresolved_terms: list[str] = Field(
        default_factory=list,
        description="Termes extraits par le LLM mais absents du KG. "
                    "Transmis à l'Orchestrateur pour demander une clarification.",
    )

    needs_clarification: bool = Field(
        default=False,
        description="True si des termes sont non résolus ou si la question est ambiguë.",
    )

    def is_empty(self) -> bool:
        """Retourne True si aucun terme n'a été extrait."""
        return (
            not self.business_terms
            and not self.entities
            and not self.time_periods
            and not self.metrics
        )

    def all_terms(self) -> list[str]:
        """Retourne tous les termes extraits dans une liste unique."""
        return (
            self.business_terms
            + self.entities
            + self.time_periods
            + self.metrics
        )
