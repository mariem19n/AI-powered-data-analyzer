"""
app/orchestrator/executor.py
Executor — exécute un ExecutionPlan en appelant les agents.

Caractéristiques :
  - Topological sort pour respecter les dépendances
  - Parallélisme quand plusieurs étapes n'ont pas de dépendances entre elles
  - Retries avec backoff exponentiel par étape
  - Erreurs partielles non-bloquantes : une étape échouée ne stoppe pas
    les étapes indépendantes

Les agents sont injectés via l'interface AgentRunner, ce qui permet
de les mocker en tests et de changer d'implémentation sans toucher
à l'executor.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Protocol

from app.orchestrator.schemas import (
    AgentType,
    ExecutionPlan,
    ExecutionStep,
    StepResult,
    StepStatus,
)

logger = logging.getLogger(__name__)


# ─── Interface d'agent ────────────────────────────────────────


class AgentRunner(Protocol):
    """
    Interface qu'un agent doit exposer à l'Executor.

    Les agents réels (SQL Agent, Analyse Agent) implémenteront
    cette méthode. Les mocks de test aussi.
    """

    async def run(
        self,
        instruction: dict[str, Any],
        upstream_results: dict[str, Any],
    ) -> Any:
        """
        Exécute une étape.

        Args:
            instruction : payload de l'ExecutionStep.instruction
            upstream_results : {step_id: data} des étapes dont
                               l'étape courante dépend

        Returns:
            Any : données produites par l'agent (DataFrame records,
                  insights, etc.)

        Raises:
            Exception : l'executor s'occupe des retries
        """
        ...


# ─── Executor ─────────────────────────────────────────────────


class PlanExecutor:
    """
    Exécute un ExecutionPlan avec parallélisme et retries.

    Usage :
        executor = PlanExecutor(
            agents={
                AgentType.SQL_AGENT: sql_agent,
                AgentType.ANALYSIS_AGENT: analyse_agent,
            },
            max_retries_per_step=1,
        )
        results = await executor.execute(plan)
    """

    def __init__(
        self,
        agents: dict[AgentType, AgentRunner],
        max_retries_per_step: int = 1,
        step_timeout_s: float = 60.0,
    ):
        self._agents = agents
        self._max_retries = max_retries_per_step
        self._step_timeout = step_timeout_s

    async def execute(self, plan: ExecutionPlan) -> dict[str, StepResult]:
        """
        Exécute le plan complet.

        Returns:
            dict {step_id: StepResult}
        """
        results: dict[str, StepResult] = {}
        remaining = {s.step_id: s for s in plan.steps}

        while remaining:
            # Identifier les étapes prêtes à s'exécuter (toutes leurs
            # dépendances sont terminées, en succès ou en échec).
            ready: list[ExecutionStep] = []
            for step in list(remaining.values()):
                deps_done = all(
                    dep in results for dep in step.depends_on
                )
                if deps_done:
                    ready.append(step)

            if not ready:
                # Deadlock : des étapes ont des dépendances non résolvables.
                # Marquer tout le reste comme SKIPPED.
                logger.error(
                    "Deadlock détecté dans le plan %s : %d étapes non résolvables",
                    plan.plan_id,
                    len(remaining),
                )
                for step_id, step in remaining.items():
                    results[step_id] = StepResult(
                        step_id=step_id,
                        status=StepStatus.SKIPPED,
                        error="Dépendances non résolvables (upstream failed)",
                    )
                break

            # Séparer les étapes parallélisables des séquentielles.
            # Une étape `parallelizable=True` peut s'exécuter en parallèle
            # des autres étapes ready parallélisables.
            parallel = [s for s in ready if s.parallelizable]
            sequential = [s for s in ready if not s.parallelizable]

            # Exécuter les parallèles en concurrent
            if parallel:
                tasks = [
                    self._run_step_with_retry(step, results)
                    for step in parallel
                ]
                step_results = await asyncio.gather(*tasks, return_exceptions=False)
                for step, result in zip(parallel, step_results):
                    results[step.step_id] = result
                    del remaining[step.step_id]

            # Exécuter les séquentielles une par une (ordre dans la liste)
            for step in sequential:
                result = await self._run_step_with_retry(step, results)
                results[step.step_id] = result
                del remaining[step.step_id]

            # Si une étape échoue, ses enfants doivent être marqués SKIPPED
            self._propagate_failures(plan, results, remaining)

        return results

    # ─── Helpers ──────────────────────────────────────────────

    async def _run_step_with_retry(
        self,
        step: ExecutionStep,
        results: dict[str, StepResult],
    ) -> StepResult:
        """Exécute une étape avec retries."""
        agent = self._agents.get(step.agent)
        if agent is None:
            logger.error("Agent %s non enregistré", step.agent.value)
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.FAILED,
                error=f"Agent {step.agent.value} non enregistré",
            )

        upstream = {
            dep: results[dep].data
            for dep in step.depends_on
            if dep in results and results[dep].status == StepStatus.SUCCESS
        }

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            t0 = time.perf_counter()
            try:
                data = await asyncio.wait_for(
                    agent.run(step.instruction, upstream),
                    timeout=self._step_timeout,
                )
                return StepResult(
                    step_id=step.step_id,
                    status=StepStatus.SUCCESS,
                    data=data,
                    duration_s=time.perf_counter() - t0,
                    retries=attempt,
                )
            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(
                    "Step %s timeout (%.1fs) — tentative %d/%d",
                    step.step_id,
                    self._step_timeout,
                    attempt + 1,
                    self._max_retries + 1,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "Step %s échouée (%s: %s) — tentative %d/%d",
                    step.step_id,
                    type(e).__name__,
                    e,
                    attempt + 1,
                    self._max_retries + 1,
                )

            if attempt < self._max_retries:
                backoff = 2**attempt
                await asyncio.sleep(backoff)

        return StepResult(
            step_id=step.step_id,
            status=StepStatus.FAILED,
            error=f"{type(last_error).__name__}: {last_error}",
            retries=self._max_retries,
        )

    @staticmethod
    def _propagate_failures(
        plan: ExecutionPlan,
        results: dict[str, StepResult],
        remaining: dict[str, ExecutionStep],
    ) -> None:
        """
        Marque comme SKIPPED toute étape dont une dépendance a échoué.
        Fait une passe jusqu'à stabilité.
        """
        changed = True
        while changed:
            changed = False
            for step_id, step in list(remaining.items()):
                for dep in step.depends_on:
                    if dep in results and results[dep].status in (
                        StepStatus.FAILED,
                        StepStatus.SKIPPED,
                    ):
                        results[step_id] = StepResult(
                            step_id=step_id,
                            status=StepStatus.SKIPPED,
                            error=f"Dépendance {dep} a échoué",
                        )
                        del remaining[step_id]
                        changed = True
                        break
