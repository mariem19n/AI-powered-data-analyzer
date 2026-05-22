"""
app/orchestrator/schemas.py
Schémas Pydantic de l'Orchestrator.

  - Intent : résultat de la détection d'intent (LLM)
  - ExecutionPlan : séquence d'étapes à exécuter par les agents
  - ExecutionStep : une étape (agent cible + instruction)
  - OrchestratorResponse : réponse finale assemblée
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from app.orchestrator.provenance import ProvenanceTrace



# ─── Énumérations ─────────────────────────────────────────────


class IntentType(str, Enum):
    """Les 6 intents supportés par le système."""

    AGGREGATION = "aggregation"
    COMPARISON = "comparison"
    CORRELATION = "correlation"
    ANOMALY_DETECTION = "anomaly_detection"
    FORECASTING = "forecasting"
    DIAGNOSIS = "diagnosis"
    EXTERNAL_KNOWLEDGE = "external_knowledge" 
    UNKNOWN = "unknown"  # fallback si le LLM ne sait pas classifier


class AgentType(str, Enum):
    """Agents que l'Orchestrator peut invoquer."""

    SQL_AGENT = "sql_agent"
    ANALYSIS_AGENT = "analysis_agent"
    EXTERNAL_TOOL = "external_tool"


class ResponseMode(str, Enum):
    """
    Mode de réponse — indique la source des données.

    L'utilisateur doit TOUJOURS savoir d'où vient la réponse.
    C'est critique pour la traçabilité et la confiance.

    Définitions strictes :
      INTERNAL          — 100% données PostgreSQL / KG. Aucune source externe.
      EXTERNAL          — le terme est reconnu comme concept valide mais
                          absent de la base interne. La réponse vient
                          de sources web de confiance (whitelist).
      HYBRID            — données internes ET enrichissement externe dans
                          la MÊME réponse. Les deux sources sont étiquetées.
      EXTERNAL_FALLBACK — le plan SQL a été tenté mais a échoué (0 résultats,
                          erreur), donc le système a basculé sur le web.
                          Différent de EXTERNAL (qui ne tente pas le SQL).
    """

    INTERNAL = "internal"
    EXTERNAL = "external"
    HYBRID = "hybrid"
    EXTERNAL_FALLBACK = "external_fallback"


class StepStatus(str, Enum):
    """État d'exécution d'une étape."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ─── Intent ───────────────────────────────────────────────────


class Intent(BaseModel):
    """
    Résultat de la détection d'intent.

    Produit par un appel LLM unique au début du pipeline.
    L'Orchestrator est le seul à interpréter l'intent.
    """

    primary: IntentType = Field(
        description="Intent principal de la question"
    )
    secondary: list[IntentType] = Field(
        default_factory=list,
        description=(
            "Intents secondaires (ex: diagnosis = anomaly + correlation). "
            "Vide si l'intent est simple."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Score de confiance du LLM sur la classification (0-1)",
    )
    reasoning: str = Field(
        default="",
        description="Justification courte du LLM (pour debug et audit)",
    )
    needs_clarification: bool = Field(
        default=False,
        description=(
            "True si la question est trop ambiguë pour être classifiée "
            "avec confiance (below threshold)"
        ),
    )
    suggested_questions: list[str] = Field(
        default_factory=list,
        description=(
            "2-3 reformulations concrètes proposées par le LLM quand "
            "needs_clarification=true. Doivent être des questions "
            "directement posables au système, pas des conseils génériques."
        ),
    )

    def is_composite(self) -> bool:
        """True si l'intent combine plusieurs types."""
        return bool(self.secondary)


# ─── Execution Plan ───────────────────────────────────────────


class ExecutionStep(BaseModel):
    """
    Une étape d'exécution du plan.

    Chaque étape cible un agent spécifique et contient
    l'instruction + les dépendances vers d'autres étapes.
    """

    step_id: str = Field(description="Identifiant unique de l'étape (ex: 'sql_1')")
    agent: AgentType = Field(description="Agent cible")
    description: str = Field(description="Description courte de l'étape (audit)")
    instruction: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Payload envoyé à l'agent. Structure libre — dépend du plan. "
            "Ex SQL: {'task': 'extract', 'entity_filter': 'BTC', 'time_filter': '...'} "
            "Ex Analyse: {'task': 'correlation', 'input_steps': ['sql_1','sql_2']}"
        ),
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="step_id des étapes qui doivent terminer avant celle-ci",
    )
    parallelizable: bool = Field(
        default=False,
        description="Si True, peut être exécutée en parallèle d'autres étapes parallèles",
    )


class ExecutionPlan(BaseModel):
    """
    Plan d'exécution complet.

    Séquence ordonnée d'étapes. Les dépendances explicites
    permettent à l'executor d'identifier les étapes parallélisables.
    """

    plan_id: str = Field(description="UUID du plan")
    intent: IntentType = Field(description="Intent principal qui a généré ce plan")
    signature: str = Field(
        description=(
            "Signature composite (intent + forme du SemanticContext) "
            "pour matcher des plans passés dans le KG"
        )
    )
    steps: list[ExecutionStep] = Field(description="Étapes ordonnées")
    reused_from_kg: bool = Field(
        default=False,
        description="True si le plan provient du KG (réutilisation)",
    )
    source_plan_id: str | None = Field(
        default=None,
        description="Si reused_from_kg=True, l'id du plan source dans le KG",
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def step_by_id(self, step_id: str) -> ExecutionStep | None:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def roots(self) -> list[ExecutionStep]:
        """Étapes sans dépendances (racines du DAG)."""
        return [s for s in self.steps if not s.depends_on]


# ─── Résultats d'exécution ────────────────────────────────────


class StepResult(BaseModel):
    """Résultat d'une étape exécutée."""

    step_id: str
    status: StepStatus
    data: Any = None
    error: str | None = None
    duration_s: float = 0.0
    retries: int = 0


# ─── Réponse finale ───────────────────────────────────────────


class ClarificationRequest(BaseModel):
    """Demande de clarification à l'utilisateur."""

    reason: str = Field(description="Raison courte de la clarification")
    unknown_terms: list[str] = Field(
        default_factory=list,
        description="Termes que le système n'a pas compris",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Suggestions de reformulation",
    )


class OrchestratorResponse(BaseModel):
    """
    Réponse finale assemblée par l'Orchestrator.

    Ce qui est retourné à l'API Gateway pour le streaming
    vers le frontend.
    """

    session_id: str
    question: str
    intent: Intent | None = None
    plan: ExecutionPlan | None = None

    # Résultats
    data: list[dict[str, Any]] = Field(
        default_factory=list,
        description="DataFrames sérialisés (records) par étape",
    )
    insights: list[str] = Field(
        default_factory=list,
        description="Insights textuels générés par l'Analyse Agent",
    )
    visualizations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="JSON Plotly des visualisations",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommandations actionnables",
    )

    analysis_stats: dict[str, Any] = Field(
        default_factory=dict,
        description="Stats brutes par step_id (forecast evaluation, diagnostics, etc.)",
    )

    # Statut
    needs_clarification: bool = False
    clarification: ClarificationRequest | None = None
    partial: bool = Field(
        default=False,
        description="True si certaines étapes ont échoué mais d'autres ont réussi",
    )
    failed_steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Warnings non-bloquants produits pendant l'exécution. "
            "Inclut : viz manquante, conversion datetime ratée, KG indisponible, "
            "supporting_stats invalides, fallback LLM utilisé, etc."
        ),
    )

    # Traçabilité des sources — L'UTILISATEUR DOIT TOUJOURS SAVOIR
    response_mode: ResponseMode = Field(
        default=ResponseMode.INTERNAL,
        description=(
            "Mode de réponse : 'internal' (données PostgreSQL), "
            "'hybrid' (interne + externe), 'external' (web/LLM uniquement)"
        ),
    )
    external_data: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Données provenant de sources externes (web search, LLM knowledge). "
            "Contient toujours : source, provider, confidence_note. "
            "None si response_mode='internal'."
        ),
    )
    source_disclaimer: str = Field(
        default="",
        description=(
            "Message affiché à l'utilisateur quand la réponse contient "
            "des données externes. Vide pour les réponses 100% internes."
        ),
    )

    provenance: ProvenanceTrace | None = Field(
        default=None,
        description="Trace de provenance pour le modal 'Sources et méthode'",
    )

    # Méta
    cache_hit: bool = False
    total_duration_s: float = 0.0
    llm_calls: int = 0
    llm_trace: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Trace des appels LLM effectues pendant cette requete",
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
