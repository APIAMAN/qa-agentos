"""Inline task dispatcher for QA AgentOS M1 Phase 5.

Executes runs asynchronously in-process via RunExecutor, managing in-flight tasks,
exception handling, clean shutdown, and cancellation without blocking HTTP intake.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Optional

from qa_agentos.core.config import Settings
from qa_agentos.core.config import settings as global_settings
from qa_agentos.core.database import AsyncSessionLocal
from qa_agentos.core.logging import logger
from qa_agentos.models.domain import Run
from qa_agentos.runtime.events import TERMINAL_STATUSES, RunStateManager
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class InlineDispatcher:
    """Interim Phase 5 in-process execution dispatcher."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        litellm_client: LiteLLMClient,
        *,
        model_resolver: Optional[ModelResolver] = None,
    ) -> None:
        self.session_factory = session_factory
        self.litellm_client = litellm_client
        self.model_resolver = model_resolver
        self._in_flight_tasks: Dict[uuid.UUID, asyncio.Task[Any]] = {}

    @classmethod
    def from_settings(cls, s: Optional[Settings] = None) -> "InlineDispatcher":
        app_settings = s or global_settings
        session_factory = AsyncSessionLocal
        litellm_client = LiteLLMClient.from_settings(app_settings)
        policy = ProviderPolicy.from_settings(app_settings)
        resolver = ModelResolver(policy)
        return cls(
            session_factory=session_factory,
            litellm_client=litellm_client,
            model_resolver=resolver,
        )

    def is_in_flight(self, run_id: uuid.UUID) -> bool:
        task = self._in_flight_tasks.get(run_id)
        return task is not None and not task.done()

    def dispatch(self, run_id: uuid.UUID) -> None:
        """Schedule asynchronous execution of a persisted run."""
        if self.is_in_flight(run_id):
            logger.warning("Run already in flight, skipping duplicate dispatch", run_id=str(run_id))
            return

        task = asyncio.create_task(self._execute_run_worker(run_id), name=f"run-worker-{run_id}")
        self._in_flight_tasks[run_id] = task
        task.add_done_callback(lambda _: self._in_flight_tasks.pop(run_id, None))

    def cancel(self, run_id: uuid.UUID) -> None:
        """Cancel an in-flight execution task if active."""
        task = self._in_flight_tasks.get(run_id)
        if task and not task.done():
            task.cancel()

    async def _execute_run_worker(self, run_id: uuid.UUID) -> None:
        """Worker task executing RunExecutor with full exception handling."""
        try:
            async with self.session_factory() as session:
                run = await session.get(Run, run_id)
                if not run:
                    logger.error("Run not found during dispatch execution", run_id=str(run_id))
                    return

                if run.status in TERMINAL_STATUSES:
                    logger.info("Run already terminal before execution", run_id=str(run_id), status=run.status)
                    return

                executor = RunExecutor(
                    session,
                    self.litellm_client,
                    model_resolver=self.model_resolver,
                )
                await executor.execute_run(run)
                await session.commit()
        except asyncio.CancelledError:
            logger.info("Run execution worker task cancelled", run_id=str(run_id))
        except Exception as exc:
            logger.exception("Unexpected exception in run execution worker", run_id=str(run_id), error=str(exc))
            # Safely mark run as failed so it is not left stuck in queued/running
            try:
                async with self.session_factory() as session:
                    run = await session.get(Run, run_id)
                    if run and run.status not in TERMINAL_STATUSES:
                        state_mgr = RunStateManager(session, run)
                        await state_mgr.transition_to(
                            "failed",
                            payload={
                                "error_code": "INTERNAL_EXECUTION_ERROR",
                                "error_message": str(exc),
                            },
                        )
                        await session.commit()
            except Exception:
                logger.exception("Failed to update run status after worker crash", run_id=str(run_id))

    async def wait_for_run(self, run_id: uuid.UUID, timeout: float = 30.0) -> None:
        """Helper for test suites to await in-flight task completion."""
        task = self._in_flight_tasks.get(run_id)
        if task and not task.done():
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Gracefully cancel and await all in-flight execution tasks."""
        tasks = [t for t in self._in_flight_tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._in_flight_tasks.clear()


_global_dispatcher: Optional[InlineDispatcher] = None


def get_dispatcher() -> InlineDispatcher:
    """Dependency provider for the application-wide InlineDispatcher."""
    global _global_dispatcher
    if _global_dispatcher is None:
        _global_dispatcher = InlineDispatcher.from_settings()
    return _global_dispatcher


def set_dispatcher(dispatcher: Optional[InlineDispatcher]) -> None:
    """Explicitly set or reset the global dispatcher (for tests / lifespan)."""
    global _global_dispatcher
    _global_dispatcher = dispatcher
