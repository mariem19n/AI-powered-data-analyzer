"""
app/orchestrator/conversation_context.py
Résolution du contexte conversationnel.

Nouveau nœud du graphe LangGraph, inséré entre normalize et cache_check.
Permet de comprendre les questions de suivi (follow-ups)

Architecture :
  1. Load   — charge les N derniers tours depuis Redis
  2. Detect — détecte si la question est un follow-up (règles d'abord, LLM si ambigu)
  3. Rewrite — fusionne le contexte précédent avec la nouvelle question

Principes :
  - Règles déterministes en priorité
  - LLM seulement si les règles ne suffisent pas à décider
  - Stockage léger : on ne garde que les infos utiles par tour, pas le chat complet
  - TTL 30 min avec sliding expiration
  - Séparé du session store existant (PREFIX_SESSION) pour ne pas polluer l'historique chat
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =====================================================================
# Schemas — Contexte structuré par tour
# =====================================================================


class TurnContext(BaseModel):
    """
    Contexte structuré d'un tour de conversation.

    On ne stocke pas le chat brut — seulement les infos utiles
    pour résoudre les follow-ups. Cela réduit la taille Redis
    et accélère la détection.
    """

    turn_number: int = Field(description="Numéro du tour (1-indexed)")
    timestamp: float = Field(description="Epoch du tour")
    original_question: str = Field(description="Question telle que posée par l'utilisateur")
    resolved_question: str = Field(
        description="Question autonome après résolution (= original si pas un follow-up)"
    )
    intent: str = Field(default="", description="Intent détecté (ex: 'forecasting')")
    entities: list[dict[str, str]] = Field(
        default_factory=list,
        description="Entités extraites — [{'name': 'Solana', 'value': 'SOL'}]",
    )
    metric: str = Field(default="", description="Métrique principale (ex: 'close_usd')")
    time_context: str = Field(default="", description="Expression temporelle (ex: '7 prochains jours')")
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Champs libres pour des paramètres spécifiques (horizon_days, etc.)",
    )


class ConversationState(BaseModel):
    """
    État complet de la conversation stocké dans Redis.

    Clé Redis : session:{session_id}:conv_context
    TTL : 30 min (sliding)
    """

    session_id: str
    turns: list[TurnContext] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def last_turn(self) -> TurnContext | None:
        """Retourne le dernier tour, ou None si aucun historique."""
        return self.turns[-1] if self.turns else None

    def last_n_turns(self, n: int = 3) -> list[TurnContext]:
        """Retourne les N derniers tours (plus récent en dernier)."""
        return self.turns[-n:]


class FollowUpDetection(BaseModel):
    """Résultat de la détection de follow-up."""

    is_follow_up: bool = Field(default=False)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    method: str = Field(
        default="none",
        description="Méthode utilisée : 'rule', 'llm', 'none'",
    )
    rule_matched: str = Field(
        default="",
        description="Nom de la règle déterministe qui a matché (si method='rule')",
    )
    resolved_question: str = Field(
        default="",
        description="Question réécrite autonome (vide si pas un follow-up)",
    )


# =====================================================================
# Patterns — Règles déterministes de détection de follow-up
# =====================================================================

# Pronoms anaphoriques et formules de continuation (FR + EN)
_ANAPHORIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    # "Et pour X ?" / "Et X ?" — substitution d'entité
    ("entity_swap_et_pour", re.compile(
        r"^et\s+pour\s+(.+?)\s*\??$", re.IGNORECASE
    )),
    ("entity_swap_et", re.compile(
        r"^et\s+(?!si\b)(.+?)\s*\??$", re.IGNORECASE
    )),
    # "Pareil pour X" / "La même chose pour X" / "Idem pour X"
    ("same_for", re.compile(
        r"^(?:pareil|la\s+même\s+chose|idem|même\s+chose)\s+(?:pour|avec|sur)\s+(.+?)\s*\.?$",
        re.IGNORECASE,
    )),
    # "What about X?" / "How about X?" / "And X?"
    ("what_about", re.compile(
        r"^(?:what|how|and)\s+about\s+(.+?)\s*\??$", re.IGNORECASE
    )),
    # "And for X?" (EN)
    ("and_for_en", re.compile(
        r"^and\s+for\s+(.+?)\s*\??$", re.IGNORECASE
    )),
]

# Questions très courtes sans verbe = probablement un follow-up
_SHORT_QUESTION_MAX_WORDS = 4

# Mots qui indiquent une question autonome (pas un follow-up)
_AUTONOMOUS_MARKERS = re.compile(
    r"\b(?:montre|affiche|donne|calcule|compare|analyse|prévois|prédis|"
    r"show|display|give|calculate|compare|analyze|forecast|predict|"
    r"quel(?:le)?s?\s+(?:est|sont)|what\s+(?:is|are))\b",
    re.IGNORECASE,
)

# Mots de liaison temporelle qui modifient le contexte précédent
_TEMPORAL_MODIFIERS = re.compile(
    r"^(?:mais\s+)?(?:pour|sur|pendant|durant|over|for|during)\s+"
    r"(?:les?\s+)?(\d+\s+(?:derniers?\s+)?(?:jours?|semaines?|mois|ans?|"
    r"days?|weeks?|months?|years?))",
    re.IGNORECASE,
)


# =====================================================================
# ConversationContextResolver
# =====================================================================


class ConversationContextResolver:
    """
    Résout le contexte conversationnel pour les follow-ups.

    Usage dans le graphe LangGraph :
        state = await resolver.resolve(state)

    Injecté dans l'Orchestrator via le constructeur, comme semantic_build_fn.

    Paramètres :
        redis_client : RedisClient existant (app.redis_client)
        llm_client   : LLMClient partagé (app.llm.client) — utilisé seulement si les règles
                        ne suffisent pas à détecter un follow-up
        max_turns    : nombre de tours à garder (défaut 3)
        ttl_seconds  : TTL Redis du contexte conversationnel (défaut 1800 = 30 min)
    """

    REDIS_PREFIX = "session:{session_id}:conv_context"
    DEFAULT_MAX_TURNS = 3
    DEFAULT_TTL = 1800  # 30 minutes

    def __init__(
        self,
        redis_client: Any,
        llm_client: Any | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        ttl_seconds: int = DEFAULT_TTL,
    ):
        self._redis = redis_client
        self._llm = llm_client
        self._max_turns = max_turns
        self._ttl = ttl_seconds

    # ─── Interface publique (appelée par le nœud LangGraph) ──

    async def resolve(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Point d'entrée principal — nœud du graphe.

        Lit le state, détecte les follow-ups, réécrit la question si
        nécessaire, et met à jour le state avec les nouveaux champs.

        Champs ajoutés au state :
          - conversation_context : ConversationState complète
          - is_follow_up         : bool
          - resolved_question    : str (question autonome)

        La question normalisée du state est remplacée par la version
        résolue pour que le reste du pipeline (cache_check, intent, etc.)
        travaille avec une question complète.
        """
        session_id = state.get("session_id", "")
        question = state.get("normalized_question") or state.get("raw_question", "")

        if not session_id or not question:
            logger.warning("ConversationContextResolver: session_id ou question manquant")
            state["is_follow_up"] = False
            state["conversation_context"] = None
            return state

        # 1. Charger le contexte depuis Redis
        conv_state = await self._load_context(session_id)

        # 2. Détecter si c'est un follow-up
        detection = self._detect_follow_up(question, conv_state)

        # 3. Si les règles sont incertaines, tenter le LLM
        if not detection.is_follow_up and detection.confidence < 0.5 and conv_state.last_turn():
            detection = await self._llm_detect_follow_up(question, conv_state)

        # 4. Réécrire si follow-up détecté
        if detection.is_follow_up and detection.resolved_question:
            resolved = detection.resolved_question
            logger.info(
                "Follow-up détecté [%s] : '%s' → '%s'",
                detection.method,
                question,
                resolved,
            )
        else:
            resolved = question

        # 5. Mettre à jour le state
        state["is_follow_up"] = detection.is_follow_up
        state["follow_up_detection"] = detection.model_dump()
        state["conversation_context"] = conv_state.model_dump()

        # Remplacer la question normalisée pour le reste du pipeline
        if detection.is_follow_up:
            state["normalized_question"] = resolved
            # Recalculer le hash car la question a changé
            import hashlib
            state["question_hash"] = hashlib.sha256(
                resolved.lower().strip().encode()
            ).hexdigest()[:16]

        return state

    async def save_turn(
        self,
        session_id: str,
        original_question: str,
        resolved_question: str,
        intent: str = "",
        entities: list[dict[str, str]] | None = None,
        metric: str = "",
        time_context: str = "",
        **extra: Any,
    ) -> None:
        """
        Sauvegarde le tour courant dans Redis après l'exécution complète.

        Appelé par le nœud cache_store (ou aggregate) du graphe,
        une fois que l'intent et les entités sont connus.
        """
        conv_state = await self._load_context(session_id)
        turn_number = (conv_state.last_turn().turn_number + 1) if conv_state.last_turn() else 1

        new_turn = TurnContext(
            turn_number=turn_number,
            timestamp=time.time(),
            original_question=original_question,
            resolved_question=resolved_question,
            intent=intent,
            entities=entities or [],
            metric=metric,
            time_context=time_context,
            extra=extra if extra else {},
        )

        conv_state.turns.append(new_turn)

        # Garder seulement les N derniers tours
        if len(conv_state.turns) > self._max_turns:
            conv_state.turns = conv_state.turns[-self._max_turns:]

        conv_state.updated_at = time.time()

        await self._save_context(session_id, conv_state)
        logger.debug(
            "Tour %d sauvegardé pour session %s (total: %d tours)",
            turn_number,
            session_id,
            len(conv_state.turns),
        )

    # ─── Redis I/O ───────────────────────────────────────────

    def _redis_key(self, session_id: str) -> str:
        return self.REDIS_PREFIX.format(session_id=session_id)

    async def _load_context(self, session_id: str) -> ConversationState:
        """Charge le contexte depuis Redis. Retourne un state vide si absent."""
        key = self._redis_key(session_id)
        try:
            raw = await self._redis.client.get(key)
            if raw is None:
                return ConversationState(session_id=session_id)

            # Sliding expiration — renouveler le TTL à chaque accès
            await self._redis.client.expire(key, self._ttl)
            return ConversationState.model_validate_json(raw)

        except Exception as e:
            logger.error("Erreur chargement contexte Redis : %s", e)
            return ConversationState(session_id=session_id)

    async def _save_context(self, session_id: str, conv_state: ConversationState) -> None:
        """Sauvegarde le contexte dans Redis avec TTL."""
        key = self._redis_key(session_id)
        try:
            await self._redis.client.setex(
                key,
                self._ttl,
                conv_state.model_dump_json(),
            )
        except Exception as e:
            logger.error("Erreur sauvegarde contexte Redis : %s", e)

    # ─── Détection par règles déterministes ──────────────────

    def _detect_follow_up(
        self,
        question: str,
        conv_state: ConversationState,
    ) -> FollowUpDetection:
        """
        Détection rules-first.

        Retourne un FollowUpDetection. Si is_follow_up=False et
        confidence < 0.5, le caller doit tenter le LLM.
        """
        last = conv_state.last_turn()

        # Pas d'historique → impossible d'être un follow-up
        if last is None:
            return FollowUpDetection(
                is_follow_up=False,
                confidence=1.0,  # Certain que ce n'est PAS un follow-up
                method="rule",
                rule_matched="no_history",
            )

        stripped = question.strip()

        # ── Règle 1 : patterns anaphoriques explicites ───────
        for rule_name, pattern in _ANAPHORIC_PATTERNS:
            match = pattern.match(stripped)
            if match:
                new_entity = match.group(1).strip()
                resolved = self._substitute_entity(last, new_entity)
                return FollowUpDetection(
                    is_follow_up=True,
                    confidence=0.95,
                    method="rule",
                    rule_matched=rule_name,
                    resolved_question=resolved,
                )

        # ── Règle 2 : modification temporelle ────────────────
        temporal_match = _TEMPORAL_MODIFIERS.match(stripped)
        if temporal_match:
            new_time = temporal_match.group(1).strip()
            resolved = self._substitute_time(last, new_time)
            return FollowUpDetection(
                is_follow_up=True,
                confidence=0.90,
                method="rule",
                rule_matched="temporal_modifier",
                resolved_question=resolved,
            )

        # ── Règle 3 : question très courte sans verbe d'action ─
        words = stripped.split()
        if (
            len(words) <= _SHORT_QUESTION_MAX_WORDS
            and not _AUTONOMOUS_MARKERS.search(stripped)
            and last is not None
        ):
            # Probablement un follow-up, mais incertain → confidence faible
            # pour déclencher le LLM
            return FollowUpDetection(
                is_follow_up=False,
                confidence=0.3,
                method="rule",
                rule_matched="short_no_verb_uncertain",
            )

        # ── Règle 4 : question longue avec verbe d'action → autonome ─
        if _AUTONOMOUS_MARKERS.search(stripped):
            return FollowUpDetection(
                is_follow_up=False,
                confidence=0.9,
                method="rule",
                rule_matched="has_autonomous_verb",
            )

        # ── Aucune règle ne matche → incertain ──────────────
        return FollowUpDetection(
            is_follow_up=False,
            confidence=0.5,
            method="rule",
            rule_matched="no_match",
        )

    # ─── Substitution d'entité dans la question précédente ───

    @staticmethod
    def _substitute_entity(last_turn: TurnContext, new_entity: str) -> str:
        """
        Remplace l'entité du tour précédent par la nouvelle.

        Ex : "Prévois le prix de Solana pour les 7 prochains jours"
             + new_entity="Ethereum"
             → "Prévois le prix de Ethereum pour les 7 prochains jours"
        """
        resolved = last_turn.resolved_question

        if last_turn.entities:
            # Remplacer la première entité trouvée dans la question résolue
            for ent in last_turn.entities:
                old_name = ent.get("name", "")
                if old_name and old_name.lower() in resolved.lower():
                    # Remplacement case-insensitive mais en gardant la casse de new_entity
                    pattern = re.compile(re.escape(old_name), re.IGNORECASE)
                    resolved = pattern.sub(new_entity, resolved, count=1)
                    return resolved

        # Fallback : si on n'a pas trouvé l'entité dans le texte,
        # on reconstruit en utilisant l'intent et le time_context
        if last_turn.intent and last_turn.time_context:
            intent_verbs = {
                "forecasting": "Prévois le prix de",
                "aggregation": "Montre le prix de",
                "comparison": "Compare",
                "correlation": "Corrélation de",
                "anomaly_detection": "Détecte les anomalies pour",
            }
            verb = intent_verbs.get(last_turn.intent, "Analyse")
            metric_label = last_turn.metric or "prix"
            return f"{verb} {new_entity} ({metric_label}) pour {last_turn.time_context}"

        # Dernier recours : concaténation simple
        return f"{resolved} — appliqué à {new_entity}"

    @staticmethod
    def _substitute_time(last_turn: TurnContext, new_time: str) -> str:
        """
        Remplace l'expression temporelle du tour précédent.

        Ex : "Prévois le prix de Solana pour les 7 prochains jours"
             + new_time="30 derniers jours"
             → "Prévois le prix de Solana pour les 30 derniers jours"
        """
        resolved = last_turn.resolved_question
        old_time = last_turn.time_context

        if old_time and old_time.lower() in resolved.lower():
            pattern = re.compile(re.escape(old_time), re.IGNORECASE)
            return pattern.sub(new_time, resolved, count=1)

        # Fallback : remplacer la portion après "pour" / "sur" / "pendant"
        time_prefix = re.compile(
            r"(pour\s+(?:les?\s+)?|sur\s+(?:les?\s+)?|pendant\s+(?:les?\s+)?)"
            r"(.+?)(\s*\.?\s*)$",
            re.IGNORECASE,
        )
        match = time_prefix.search(resolved)
        if match:
            return resolved[: match.start(2)] + new_time + match.group(3)

        # Dernier recours
        return f"{resolved} sur {new_time}"

    # ─── Détection par LLM (fallback) ────────────────────────

    async def _llm_detect_follow_up(
        self,
        question: str,
        conv_state: ConversationState,
    ) -> FollowUpDetection:
        """
        Appel LLM pour détecter si la question est un follow-up.

        Appelé UNIQUEMENT quand les règles déterministes ne sont pas
        assez confiantes (confidence < 0.5) ET qu'un historique existe.
        """
        if self._llm is None:
            logger.debug("Pas de LLM configuré — skip détection LLM")
            return FollowUpDetection(
                is_follow_up=False,
                confidence=0.5,
                method="none",
            )

        last_turns = conv_state.last_n_turns(self._max_turns)
        history_text = "\n".join(
            f"Tour {t.turn_number} : {t.resolved_question}" for t in last_turns
        )

        system_prompt = (
            "Tu es un détecteur de questions de suivi (follow-up) dans un système "
            "d'analyse de données crypto et macroéconomique.\n\n"
            "Tu reçois l'historique des derniers tours et la nouvelle question.\n"
            "Tu dois déterminer si la nouvelle question est un follow-up "
            "(elle dépend du contexte précédent pour être comprise) "
            "ou une question autonome (elle se suffit à elle-même).\n\n"
            "Si c'est un follow-up, tu dois réécrire la question en une "
            "version autonome et complète.\n\n"
            "Réponds UNIQUEMENT en JSON :\n"
            '{"is_follow_up": true/false, "resolved_question": "...", "reasoning": "..."}\n\n'
            "Si ce n'est PAS un follow-up, resolved_question = la question originale."
        )

        user_prompt = (
            f"Historique récent :\n{history_text}\n\n"
            f"Nouvelle question : {question}\n\n"
            "Est-ce un follow-up ? Si oui, réécris la question complète."
        )

        try:
            response = await self._llm.acomplete(
                system=system_prompt,
                user=user_prompt,
                temperature=0.0,
                max_tokens=300,
            )

            # Parser le JSON
            text = response.strip()
            # Nettoyer les fences markdown si présentes
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*$", "", text)
            parsed = json.loads(text)

            is_follow = bool(parsed.get("is_follow_up", False))
            resolved = parsed.get("resolved_question", question)

            return FollowUpDetection(
                is_follow_up=is_follow,
                confidence=0.85 if is_follow else 0.80,
                method="llm",
                resolved_question=resolved if is_follow else "",
            )

        except json.JSONDecodeError as e:
            logger.warning("LLM follow-up detection: JSON invalide — %s", e)
            return FollowUpDetection(is_follow_up=False, confidence=0.4, method="llm")
        except Exception as e:
            logger.error("LLM follow-up detection error: %s", e)
            return FollowUpDetection(is_follow_up=False, confidence=0.4, method="llm")

    # ─── Utilitaires ─────────────────────────────────────────

    async def clear_context(self, session_id: str) -> bool:
        """Supprime le contexte conversationnel d'une session."""
        key = self._redis_key(session_id)
        try:
            return await self._redis.client.delete(key) > 0
        except Exception as e:
            logger.error("Erreur suppression contexte : %s", e)
            return False


# =====================================================================
# Helper — Extraction des entités depuis le SemanticContext
# =====================================================================


def extract_turn_metadata(
    intent: Any,
    semantic_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Extrait les métadonnées utiles pour le TurnContext depuis
    l'intent et le SemanticContext du tour courant.

    Appelé dans le nœud cache_store ou aggregate pour alimenter
    save_turn() avec les bonnes valeurs.

    Returns:
        dict avec les clés : intent, entities, metric, time_context, extra
    """
    metadata: dict[str, Any] = {
        "intent": "",
        "entities": [],
        "metric": "",
        "time_context": "",
    }

    # Intent
    if intent is not None:
        primary = getattr(intent, "primary", None)
        if primary is not None:
            metadata["intent"] = primary.value if hasattr(primary, "value") else str(primary)

    if semantic_context is None:
        return metadata

    # Entités — extraire depuis entity_filters du SemanticContext
    entity_filters = semantic_context.get("entity_filters", [])
    for ef in entity_filters:
        name = ef.get("entity_name", "") or ef.get("name", "")
        value = ef.get("filter_value", "") or ef.get("value", "")
        if name:
            metadata["entities"].append({"name": name, "value": value})

    # Métrique — d'abord chercher dans metrics (formules calculées),
    # puis fallback sur columns (cas forecasting où le prix est dans
    # columns: [{"name": "prix", "column": "close_usd"}] et pas dans metrics).
    # Ceci est important pour les follow-ups comme "Et pour le volume ?"
    # où on doit savoir que le tour précédent ciblait close_usd.
    metrics = semantic_context.get("metrics", [])
    if metrics:
        first = metrics[0]
        metadata["metric"] = first.get("formula", "") or first.get("name", "")
    else:
        # Fallback : colonnes résolues (ex: close_usd, volume_24h)
        columns = semantic_context.get("columns", [])
        if columns:
            first_col = columns[0]
            metadata["metric"] = (
                first_col.get("column", "")
                or first_col.get("name", "")
            )

    # Contexte temporel — le format réel des time_filters est :
    # {"raw_text": "7 prochains jours", "expression": "...",
    #  "filter_clause": "...", "is_resolved": true}
    time_filters = semantic_context.get("time_filters", [])
    if time_filters:
        first_time = time_filters[0]
        metadata["time_context"] = (
            first_time.get("raw_text", "")
            or first_time.get("expression", "")
            or first_time.get("filter_clause", "")
        )

    return metadata
