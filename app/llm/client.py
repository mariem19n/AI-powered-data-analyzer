"""
app/llm/client.py
Client LLM centralisé pour tout le système.

Fournit une interface unique pour tous les appels LLM :
  - Semantic Layer (extractor)
  - Orchestrator (intent detection)
  - Futurs agents (SQL Agent LLM mode, Analyse Agent insights, ...)

Design :
  - Singleton lazy via get_llm_client()
  - Support du JSON structuré (response_format)
  - Retries automatiques avec backoff
  - Timeout configurable
  - Métriques d'utilisation (tokens, latency)

Usage :
    from app.llm import get_llm_client

    llm = get_llm_client()

    # Appel texte simple
    reply = llm.chat(
        system="Tu es un assistant.",
        user="Bonjour.",
    )

    # Appel JSON structuré
    data = llm.chat_json(
        system="Retourne un JSON valide.",
        user="Donne-moi 3 couleurs.",
    )

    # Appel JSON avec schéma Pydantic
    result = llm.chat_json_schema(
        system="...",
        user="...",
        schema=MySchema,
    )
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Type, TypeVar

from openai import OpenAI
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# ─── Configuration par défaut ─────────────────────────────────

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_S = 30
DEFAULT_MAX_RETRIES = 2
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 1024


T = TypeVar("T", bound=BaseModel)
_request_id_var: ContextVar[str | None] = ContextVar(
    "llm_request_id",
    default=None,
)


# ─── Métriques d'utilisation ──────────────────────────────────


@dataclass
class LLMCallTrace:
    """Trace d'un appel LLM individuel."""

    sequence: int
    request_id: str | None
    purpose: str
    model: str
    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    temperature: float
    max_tokens: int
    response_format: str
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "request_id": self.request_id,
            "purpose": self.purpose,
            "model": self.model,
            "latency_s": round(self.latency_s, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": self.response_format,
            "started_at": self.started_at,
        }


@dataclass
class LLMMetrics:
    """Métriques cumulées des appels LLM."""

    total_calls: int = 0
    total_errors: int = 0
    total_retries: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_latency_s: float = 0.0
    calls_by_purpose: dict[str, int] = field(default_factory=dict)
    call_traces: list[LLMCallTrace] = field(default_factory=list)

    def record_call(
        self,
        purpose: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_s: float,
        temperature: float,
        max_tokens: int,
        response_format: str,
        started_at: str,
        request_id: str | None,
    ) -> None:
        self.total_calls += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_latency_s += latency_s
        self.calls_by_purpose[purpose] = self.calls_by_purpose.get(purpose, 0) + 1
        self.call_traces.append(
            LLMCallTrace(
                sequence=self.total_calls,
                request_id=request_id,
                purpose=purpose,
                model=model,
                latency_s=latency_s,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                started_at=started_at,
            )
        )

    def record_error(self) -> None:
        self.total_errors += 1

    def record_retry(self) -> None:
        self.total_retries += 1

    def snapshot(self) -> dict[str, Any]:
        avg_latency = (
            self.total_latency_s / self.total_calls if self.total_calls else 0.0
        )
        return {
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "total_retries": self.total_retries,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_latency_s": round(self.total_latency_s, 3),
            "avg_latency_s": round(avg_latency, 3),
            "calls_by_purpose": dict(self.calls_by_purpose),
        }

    def traces_since(self, start_index: int) -> list[dict[str, Any]]:
        """Retourne les traces d'appels ajoutees depuis un index donne."""
        return [trace.to_dict() for trace in self.call_traces[start_index:]]

    @property
    def trace(self) -> list[dict[str, Any]]:
        """Alias JSON-compatible pour la trace globale."""
        return [trace.to_dict() for trace in self.call_traces]


# ─── Exceptions ───────────────────────────────────────────────


class LLMError(Exception):
    """Erreur LLM après épuisement des retries."""


class LLMJSONError(LLMError):
    """Le LLM n'a pas retourné un JSON valide."""


class LLMSchemaError(LLMError):
    """Le JSON retourné ne respecte pas le schéma attendu."""


# ─── Client LLM ───────────────────────────────────────────────


class LLMClient:
    """
    Client LLM centralisé réutilisable.

    Attributs configurables via .env :
      - OPENAI_API_KEY (obligatoire)
      - LLM_MODEL (défaut: gpt-4o-mini)
      - LLM_TIMEOUT (défaut: 30)
      - LLM_MAX_RETRIES (défaut: 2)
      - LLM_TEMPERATURE (défaut: 0.0)
      - OPENAI_BASE_URL (optionnel, pour les LLM compatibles OpenAI)
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
    ):
        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError(
                "OPENAI_API_KEY manquant. "
                "Définis-la dans .env ou passe-la en argument."
            )

        url = base_url or os.getenv("OPENAI_BASE_URL") or None

        self._client = OpenAI(api_key=key, base_url=url)
        self._model = model or os.getenv("LLM_MODEL", DEFAULT_MODEL)
        self._timeout = timeout or int(os.getenv("LLM_TIMEOUT", DEFAULT_TIMEOUT_S))
        self._max_retries = max_retries or int(
            os.getenv("LLM_MAX_RETRIES", DEFAULT_MAX_RETRIES)
        )
        self._metrics = _global_metrics

        logger.info(
            "LLMClient initialisé — model=%s, timeout=%ds, max_retries=%d",
            self._model,
            self._timeout,
            self._max_retries,
        )

    # ─── Accès aux métriques ──────────────────────────────────

    @property
    def metrics(self) -> LLMMetrics:
        return self._metrics

    @property
    def model(self) -> str:
        return self._model

    # ─── Appel brut avec retry ────────────────────────────────

    def _call_with_retry(
        self,
        messages: list[dict[str, str]],
        *,
        purpose: str,
        response_format: dict | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> ChatCompletion:
        """
        Appel OpenAI avec retries exponentiels.

        Args:
            messages : liste de messages {role, content}
            purpose : étiquette pour les métriques (ex: "intent_detection")
            response_format : {"type": "json_object"} pour forcer du JSON
            temperature : 0.0 = déterministe
            max_tokens : max tokens de completion

        Returns:
            ChatCompletion — réponse OpenAI brute

        Raises:
            LLMError : après épuisement des retries
        """
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                started_at = datetime.utcnow().isoformat()
                t0 = time.perf_counter()

                kwargs: dict[str, Any] = dict(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=self._timeout,
                )
                if response_format is not None:
                    kwargs["response_format"] = response_format

                response = self._client.chat.completions.create(**kwargs)

                latency = time.perf_counter() - t0
                usage = response.usage
                self._metrics.record_call(
                    purpose=purpose,
                    model=self._model,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                    latency_s=latency,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=(
                        response_format.get("type", "text")
                        if response_format
                        else "text"
                    ),
                    started_at=started_at,
                    request_id=_request_id_var.get(),
                )

                logger.debug(
                    "LLM call [%s] — %dms, %d+%d tokens",
                    purpose,
                    int(latency * 1000),
                    usage.prompt_tokens if usage else 0,
                    usage.completion_tokens if usage else 0,
                )
                return response

            except Exception as e:
                last_error = e
                self._metrics.record_error()

                if attempt < self._max_retries:
                    self._metrics.record_retry()
                    backoff = 2**attempt  # 1s, 2s, 4s...
                    logger.warning(
                        "LLM [%s] tentative %d/%d échouée (%s) — retry dans %ds",
                        purpose,
                        attempt + 1,
                        self._max_retries + 1,
                        type(e).__name__,
                        backoff,
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "LLM [%s] échec final après %d tentatives : %s — %s",
                        purpose,
                        self._max_retries + 1,
                        type(e).__name__,
                        e,
                    )

        raise LLMError(
            f"LLM [{purpose}] a échoué après {self._max_retries + 1} tentatives : "
            f"{type(last_error).__name__} — {last_error}"
        )

    # ─── API publique : chat texte ────────────────────────────

    def chat(
        self,
        system: str,
        user: str,
        *,
        purpose: str = "generic",
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str:
        """
        Appel LLM simple — retourne le texte brut de la réponse.

        Args:
            system : prompt système
            user : message utilisateur
            purpose : étiquette pour les métriques
            temperature : 0.0 = déterministe
            max_tokens : tokens max en sortie

        Returns:
            str : contenu textuel de la réponse
        """
        response = self._call_with_retry(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        return content.strip()

    # ─── API publique : chat JSON libre ───────────────────────

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        purpose: str = "generic_json",
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict[str, Any]:
        """
        Appel LLM forçant une réponse JSON valide.
        Utilise response_format={"type": "json_object"} si supporté.

        Raises:
            LLMJSONError : si le JSON retourné est invalide
        """
        # OpenAI gpt-4o-mini supporte response_format json_object
        response = self._call_with_retry(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            purpose=purpose,
            response_format={"type": "json_object"},
            temperature=temperature,
            max_tokens=max_tokens,
        )
        raw = (response.choices[0].message.content or "").strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(
                "LLM [%s] JSON invalide : %s\nRaw: %s",
                purpose,
                e,
                raw[:500],
            )
            raise LLMJSONError(f"JSON invalide : {e}") from e

    # ─── API publique : chat JSON validé par Pydantic ─────────

    def chat_json_schema(
        self,
        system: str,
        user: str,
        schema: Type[T],
        *,
        purpose: str = "generic_schema",
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> T:
        """
        Appel LLM avec validation Pydantic.
        Si le JSON ne respecte pas le schéma, tente une seconde fois
        en corrigeant le prompt avec l'erreur de validation.

        Args:
            schema : classe Pydantic attendue

        Returns:
            instance du schéma validée

        Raises:
            LLMSchemaError : si la validation échoue définitivement
        """
        # Ajoute le schéma JSON dans le prompt système pour guider le LLM
        schema_json = schema.model_json_schema()
        enriched_system = (
            f"{system}\n\n"
            f"Réponds UNIQUEMENT avec un JSON valide respectant ce schéma :\n"
            f"```json\n{json.dumps(schema_json, indent=2, ensure_ascii=False)}\n```"
        )

        data = self.chat_json(
            system=enriched_system,
            user=user,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        try:
            return schema.model_validate(data)
        except ValidationError as e:
            logger.warning(
                "LLM [%s] réponse non conforme au schéma, seconde tentative : %s",
                purpose,
                e,
            )
            # Seconde tentative avec l'erreur dans le prompt
            corrective_user = (
                f"{user}\n\n"
                f"ATTENTION : ta réponse précédente ne respectait pas le schéma. "
                f"Erreur de validation : {e}\n"
                f"Retourne un JSON strictement conforme."
            )
            data2 = self.chat_json(
                system=enriched_system,
                user=corrective_user,
                purpose=f"{purpose}_retry",
                temperature=temperature,
                max_tokens=max_tokens,
            )
            try:
                return schema.model_validate(data2)
            except ValidationError as e2:
                logger.error(
                    "LLM [%s] schéma invalide après retry : %s",
                    purpose,
                    e2,
                )
                raise LLMSchemaError(
                    f"Schéma {schema.__name__} invalide après retry : {e2}"
                ) from e2


# ─── Singleton ────────────────────────────────────────────────

_global_metrics = LLMMetrics()
_default_client: LLMClient | None = None


def set_llm_request_id(request_id: str):
    """Attache un request_id aux traces LLM du contexte courant."""
    return _request_id_var.set(request_id)


def reset_llm_request_id(token) -> None:
    """Restaure le request_id LLM precedent."""
    _request_id_var.reset(token)


def get_llm_client() -> LLMClient:
    """
    Retourne l'instance partagée du LLMClient.
    Crée l'instance au premier appel (lazy init).
    """
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def reset_llm_client() -> None:
    """Reset le singleton — utile pour les tests."""
    global _default_client
    _default_client = None
