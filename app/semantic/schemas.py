"""
app/semantic/schemas.py
Modèles Pydantic pour la sortie du Semantic Layer.

V3 — Validation sémantique :
  - ClassifiedTerm avec resolution_status (pas binaire existe/absent)
  - Statuts : resolved, resolved_by_synonym, resolved_by_fuzzy,
              plausible_but_new, ambiguous, invalid
  - Support d'enrichissement futur du KG (candidate terms)
"""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


# ─── Catégories (extensible — correspond aux labels Neo4j) ───

class TermCategory(str, Enum):
    """Catégories de termes — correspond aux labels Neo4j."""
    BUSINESS_TERM = "BusinessTerm"
    ENTITY        = "Entity"
    TIME_PERIOD   = "TimePeriod"
    METRIC        = "Metric"
    UNKNOWN       = "Unknown"

    @classmethod
    def from_kg_label(cls, label: str) -> TermCategory:
        for member in cls:
            if member.value == label:
                return member
        return cls.UNKNOWN


# ─── Statuts de résolution ────────────────────────────────────

class ResolutionStatus(str, Enum):
    """
    Statut de résolution d'un terme — pas binaire existe/absent.
    Reflète la cohérence et la plausibilité du terme.
    """
    RESOLVED            = "resolved"             # Match exact dans le KG
    RESOLVED_BY_SYNONYM = "resolved_by_synonym"  # Résolu via un synonyme connu
    RESOLVED_BY_FUZZY   = "resolved_by_fuzzy"    # Résolu via fuzzy matching
    PLAUSIBLE_BUT_NEW   = "plausible_but_new"    # Absent du KG mais plausible dans le domaine
    AMBIGUOUS           = "ambiguous"            # Plusieurs correspondances possibles
    INVALID             = "invalid"              # Incohérent avec la question / le domaine


class MatchMethod(str, Enum):
    """Comment le terme a été mis en correspondance avec le KG."""
    EXACT   = "exact"
    SYNONYM = "synonym"
    FUZZY   = "fuzzy"
    NONE    = "none"   # Pas de match KG


# ─── Correction (pré-traitement) ─────────────────────────────

class CorrectionType(str, Enum):
    SPELLING      = "spelling"
    SYNONYM       = "synonym"
    NORMALIZATION = "normalization"
    ALIAS         = "alias"


class AppliedCorrection(BaseModel):
    """Une correction appliquée pendant le pré-traitement."""
    original: str
    corrected: str
    correction_type: CorrectionType
    confidence: float = Field(ge=0.0, le=1.0)
    matched_term: str | None = None


class PreprocessResult(BaseModel):
    """Sortie du pré-traitement."""
    original_question: str
    corrected_question: str
    corrections: list[AppliedCorrection] = Field(default_factory=list)
    is_corrected: bool = False


# ─── Terme classifié (sortie du validateur) ──────────────────

class ClassifiedTerm(BaseModel):
    """
    Un terme extrait, classifié et évalué.
    Le resolution_status indique si le terme est résolu, plausible,
    ambigu ou invalide — pas juste "existe / n'existe pas".
    """
    text: str = Field(description="Texte du terme (forme canonique si résolu)")
    category: TermCategory = Field(description="Catégorie (depuis KG ou inférée)")
    confidence: float = Field(ge=0.0, le=1.0, description="Score de confiance")

    # Résolution sémantique
    resolution_status: ResolutionStatus = Field(
        default=ResolutionStatus.RESOLVED,
        description="Statut de résolution — pas binaire existe/absent"
    )
    matched_by: MatchMethod = Field(
        default=MatchMethod.EXACT,
        description="Méthode de correspondance avec le KG"
    )
    matched_kg_node: str | None = Field(
        default=None,
        description="Nom du nœud KG correspondant (si résolu)"
    )

    # Traçabilité
    original_text: str | None = Field(
        default=None,
        description="Texte original avant correction/canonicalisation"
    )
    was_corrected: bool = Field(
        default=False,
        description="True si corrigé par le pré-traitement"
    )

    def is_resolved(self) -> bool:
        """True si le terme est résolu dans le KG (exact, synonym ou fuzzy)."""
        return self.resolution_status in (
            ResolutionStatus.RESOLVED,
            ResolutionStatus.RESOLVED_BY_SYNONYM,
            ResolutionStatus.RESOLVED_BY_FUZZY,
        )

    def is_candidate_for_kg(self) -> bool:
        """True si le terme mérite d'être ajouté au KG à terme."""
        return self.resolution_status == ResolutionStatus.PLAUSIBLE_BUT_NEW


# ─── ExtractedTerms (rétro-compatible avec le LLM) ──────────

class ExtractedTerms(BaseModel):
    """
    Sortie brute de l'extraction LLM.
    Structure intermédiaire entre l'extracteur et le validateur.
    """
    raw_question: str
    business_terms: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    time_periods: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    unresolved_terms: list[str] = Field(default_factory=list)
    needs_clarification: bool = False

    def is_empty(self) -> bool:
        return (
            not self.business_terms
            and not self.entities
            and not self.time_periods
            and not self.metrics
        )

    def all_terms(self) -> list[str]:
        return (
            self.business_terms
            + self.entities
            + self.time_periods
            + self.metrics
        )


# ─── Changement de validation ────────────────────────────────

class ValidationChange(BaseModel):
    """Un changement appliqué par le validateur."""
    term: str
    action: str  # recategorized | kept | marked_plausible | marked_ambiguous | marked_invalid
    from_category: str | None = None
    to_category: str | None = None
    resolution_status: str | None = None
    reason: str = ""


# ─── EnrichedTerms (sortie finale du pipeline) ──────────────

class EnrichedTerms(BaseModel):
    """
    Sortie finale du pipeline complet.
    Contient les termes classifiés avec statuts de résolution,
    pas une validation binaire existe/absent.
    """
    raw_question: str
    corrected_question: str
    terms: list[ClassifiedTerm] = Field(default_factory=list)
    unresolved_terms: list[str] = Field(default_factory=list)
    needs_clarification: bool = False

    # Audit trail
    preprocessing: PreprocessResult | None = None
    validation_changes: list[ValidationChange] = Field(default_factory=list)
    pipeline_confidence: float = 0.0

    # ── Accesseurs par catégorie ──────────────────────────────

    def business_terms(self) -> list[str]:
        return [t.text for t in self.terms if t.category == TermCategory.BUSINESS_TERM]

    def entities(self) -> list[str]:
        return [t.text for t in self.terms if t.category == TermCategory.ENTITY]

    def time_periods(self) -> list[str]:
        return [t.text for t in self.terms if t.category == TermCategory.TIME_PERIOD]

    def metrics(self) -> list[str]:
        return [t.text for t in self.terms if t.category == TermCategory.METRIC]

    # ── Accesseurs par statut ─────────────────────────────────

    def resolved_terms(self) -> list[ClassifiedTerm]:
        """Termes résolus dans le KG (exact, synonym, fuzzy)."""
        return [t for t in self.terms if t.is_resolved()]

    def candidate_terms(self) -> list[ClassifiedTerm]:
        """Termes plausibles mais pas encore dans le KG — candidats pour enrichissement."""
        return [t for t in self.terms if t.is_candidate_for_kg()]

    def ambiguous_terms(self) -> list[ClassifiedTerm]:
        """Termes ambigus nécessitant clarification."""
        return [t for t in self.terms if t.resolution_status == ResolutionStatus.AMBIGUOUS]

    def invalid_terms(self) -> list[ClassifiedTerm]:
        """Termes invalides / hallucinés."""
        return [t for t in self.terms if t.resolution_status == ResolutionStatus.INVALID]

    # ── Utilitaires ───────────────────────────────────────────

    def terms_by_category(self, category: TermCategory) -> list[ClassifiedTerm]:
        return [t for t in self.terms if t.category == category]

    def is_empty(self) -> bool:
        return len(self.terms) == 0

    def all_term_texts(self) -> list[str]:
        return [t.text for t in self.terms]

    def high_confidence_terms(self, threshold: float = 0.7) -> list[ClassifiedTerm]:
        return [t for t in self.terms if t.confidence >= threshold]