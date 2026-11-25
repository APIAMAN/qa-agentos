"""Run dispatchers for QA AgentOS.

Supports both InlineDispatcher (for tests/local dev) and HatchetDispatcher (for durable execution).
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Set

from qa_agentos.core.config import Settings
from qa_agentos.core.config import settings as global_settings
from qa_agentos.core.database import AsyncSessionLocal
from qa_agentos.core.errors import APIError
from qa_agentos.models.domain import Run
from qa_agentos.runtime.events import (
    TERMINAL_STATUSES,
    RunStateManager,
    RunTerminalStateError,
    commit_and_publish,
)
from qa_agentos.runtime.executor import OutputSink, RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

logger = get_logger(__name__)


class RunDispatcher(ABC):
    """Abstract interface for run execution dispatchers."""

    @abstractmethod
    async def enqueue(self, run_id: uuid.UUID) -> Optional[str]:
        """Asynchronously enqueue a run, returning its task identifier if available."""
        pass

    @abstractmethod
    def dispatch(self, run_id: uuid.UUID) -> None:
        """Schedule execution without blocking the caller."""
        pass

    @abstractmethod
    def cancel(self, run_id: uuid.UUID) -> None:
        """Request cancellation of an in-flight execution."""
        pass

    @abstractmethod
    async def shutdown(self, timeout: float = 5.0) -> None:
        """Gracefully release resources and wait for in-flight tasks."""
        pass


class InlineDispatcher(RunDispatcher):
    """In-process execution dispatcher for tests and local single-process development."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        litellm_client: LiteLLMClient,
        *,
        model_resolver: Optional[ModelResolver] = None,
        checkpointer: Optional[Any] = None,
        output_sink: Optional[OutputSink] = None,
        tool_implementations: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.session_factory = session_factory
        self.output_sink = output_sink
        if tool_implementations is None:
            from qa_agentos.modules.bugs.tools import make_tool_implementations

            tool_implementations = make_tool_implementations(session_factory)
        self.tool_implementations = tool_implementations
        self.litellm_client = litellm_client
        self.model_resolver = model_resolver
        from langgraph.checkpoint.memory import MemorySaver
        self.checkpointer = checkpointer or MemorySaver()
        self._in_flight_tasks: Dict[uuid.UUID, asyncio.Task[Any]] = {}

    @classmethod
    def from_settings(cls, s: Optional[Settings] = None) -> "InlineDispatcher":
        app_settings = s or global_settings
        session_factory = AsyncSessionLocal
        litellm_client = LiteLLMClient.from_settings(app_settings)
        policy = ProviderPolicy.from_settings(app_settings)
        resolver = ModelResolver(policy)
        from qa_agentos.modules.runs.output import run_output_sink

        return cls(
            session_factory=session_factory,
            litellm_client=litellm_client,
            model_resolver=resolver,
            output_sink=run_output_sink,
        )

    def is_in_flight(self, run_id: uuid.UUID) -> bool:
        task = self._in_flight_tasks.get(run_id)
        return task is not None and not task.done()

    async def enqueue(self, run_id: uuid.UUID) -> Optional[str]:
        self.dispatch(run_id)
        return f"inline-{run_id}"

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

                # Inline execution does not use Hatchet claims; consume any approval resume marker
                # so stale-run recovery does not dispatch the resume a second time.
                if run.resume_requested_at is not None:
                    run.resume_requested_at = None
                    await session.commit()

                executor = RunExecutor(
                    session,
                    self.litellm_client,
                    model_resolver=self.model_resolver,
                    checkpointer=self.checkpointer,
                    output_sink=self.output_sink,
                    tool_implementations=self.tool_implementations,
                )
                await executor.execute_run(run)
                await commit_and_publish(session)
        except RunTerminalStateError:
            logger.info("Run execution worker found run already terminal", run_id=str(run_id))
        except asyncio.CancelledError:
            # Cancellation by the user leaves the run already 'cancelled'. Any other cancellation
            # (process shutdown) interrupts an in-memory execution that cannot resume: fail it
            # explicitly instead of leaving it 'running' forever.
            logger.info("Run execution worker task cancelled", run_id=str(run_id))
            await self._fail_if_active(run_id, "EXECUTION_INTERRUPTED", "Execution was interrupted (process shutdown).")
        except Exception as exc:
            logger.exception("Unexpected exception in run execution worker", run_id=str(run_id), error=str(exc))
            code = exc.code if isinstance(exc, APIError) else "INTERNAL_EXECUTION_ERROR"
            await self._fail_if_active(run_id, code, str(exc))

    async def _fail_if_active(self, run_id: uuid.UUID, code: str, message: str) -> None:
        try:
            async with self.session_factory() as session:
                run = await session.get(Run, run_id)
                if run and run.status not in TERMINAL_STATUSES:
                    state_mgr = RunStateManager(session, run)
                    await state_mgr.transition_to(
                        "failed",
                        payload={"error_code": code, "error_message": message},
                    )
                    await commit_and_publish(session)
        except Exception:
            logger.exception("Failed to update run status after worker failure", run_id=str(run_id))

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


class HatchetDispatcher(RunDispatcher):
    """Durable execution dispatcher backed by Hatchet task engine."""

    def __init__(
        self,
        hatchet_client: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.hatchet = hatchet_client
        self.session_factory = session_factory
        # Strong references keep fire-and-forget tasks alive until they finish.
        self._background_tasks: Set[asyncio.Task[Any]] = set()

    @classmethod
    def from_settings(cls, s: Optional[Settings] = None) -> "HatchetDispatcher":
        app_settings = s or global_settings
        if not app_settings.HATCHET_CLIENT_TOKEN:
            raise ValueError("Cannot initialize HatchetDispatcher: HATCHET_CLIENT_TOKEN is not configured.")

        from hatchet_sdk import ClientConfig, Hatchet

        token_str = app_settings.HATCHET_CLIENT_TOKEN.get_secret_value()
        config = ClientConfig(
            token=token_str,
            server_url=app_settings.HATCHET_CLIENT_SERVER_URL or "http://localhost:8080",
            host_port=app_settings.HATCHET_CLIENT_HOST_PORT or "localhost:7077",
            tenant_id=app_settings.HATCHET_CLIENT_TENANT_ID or "707d0855-80ab-4e1f-a156-f1c4546cbf52",
        )
        hatchet_client = Hatchet(config=config)
        return cls(hatchet_client=hatchet_client, session_factory=AsyncSessionLocal)

    async def enqueue(self, run_id: uuid.UUID) -> Optional[str]:
        """Enqueue run to Hatchet task queue and persist hatchet_run_id."""
        logger.info("Attempting Hatchet task enqueue", run_id=str(run_id))
        try:
            # Dynamically reference agent_run task from worker module
            from qa_agentos.workers.main import AgentRunInput, agent_run_task

            task_ref = await agent_run_task.aio_run_no_wait(AgentRunInput(run_id=str(run_id)))
            hatchet_run_id = getattr(getattr(task_ref, "workflow_run_ref", None), "workflow_run_id", None)
            if not hatchet_run_id and hasattr(task_ref, "workflow_run_id"):
                hatchet_run_id = task_ref.workflow_run_id
            elif not hatchet_run_id:
                hatchet_run_id = str(uuid.uuid4())

            logger.info("Successfully enqueued to Hatchet", run_id=str(run_id), hatchet_run_id=hatchet_run_id)

            # Persist hatchet_run_id safely
            try:
                async with self.session_factory() as session:
                    # Only while unclaimed: once a worker claimed the run, it owns hatchet_run_id.
                    stmt = (
                        update(Run)
                        .where(Run.id == run_id, Run.status == "queued")
                        .values(hatchet_run_id=str(hatchet_run_id))
                    )
                    await session.execute(stmt)
                    await session.commit()
            except Exception as db_exc:
                logger.warning(
                    "Enqueued to Hatchet but failed to persist hatchet_run_id locally; "
                    "worker execution claim will reconcile",
                    run_id=str(run_id),
                    hatchet_run_id=str(hatchet_run_id),
                    error=str(db_exc),
                )

            return str(hatchet_run_id)
        except Exception as exc:
            logger.error(
                "Failed to enqueue run to Hatchet; leaving in queued state for outbox sweeper",
                run_id=str(run_id),
                error=str(exc),
            )
            return None

    def _spawn(self, coro: Any, name: str) -> None:
        task: asyncio.Task[Any] = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_done)

    def _on_background_done(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Background Hatchet task failed", task=task.get_name(), error=str(task.exception()))

    def dispatch(self, run_id: uuid.UUID) -> None:
        """Schedule non-blocking enqueue via a tracked background task.

        A lost enqueue is recovered by the outbox sweeper (run stays queued, unassigned).
        """
        self._spawn(self.enqueue(run_id), f"hatchet-dispatch-{run_id}")

    def cancel(self, run_id: uuid.UUID) -> None:
        """Send cancellation to Hatchet engine if run is associated with a Hatchet task."""
        self._spawn(self._async_cancel(run_id), f"hatchet-cancel-{run_id}")

    async def _async_cancel(self, run_id: uuid.UUID) -> None:
        try:
            async with self.session_factory() as session:
                run = await session.get(Run, run_id)
                if not run or not run.hatchet_run_id:
                    return
                hatchet_id = run.hatchet_run_id

            if hasattr(self.hatchet, "runs") and hasattr(self.hatchet.runs, "aio_cancel"):
                await self.hatchet.runs.aio_cancel(hatchet_id)
                logger.info("Cancelled Hatchet workflow run", run_id=str(run_id), hatchet_run_id=hatchet_id)
        except Exception as exc:
            logger.warning("Failed to cancel Hatchet workflow run", run_id=str(run_id), error=str(exc))

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Let in-flight enqueue/cancel calls finish (bounded by ``timeout``)."""
        pending = [t for t in self._background_tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)


_global_dispatcher: Optional[RunDispatcher] = None


def get_dispatcher() -> RunDispatcher:
    """Dependency provider for the application-wide RunDispatcher."""
    global _global_dispatcher
    if _global_dispatcher is None:
        if global_settings.RUN_DISPATCHER.lower() == "hatchet":
            _global_dispatcher = HatchetDispatcher.from_settings(global_settings)
        else:
            _global_dispatcher = InlineDispatcher.from_settings(global_settings)
    return _global_dispatcher


def set_dispatcher(dispatcher: Optional[RunDispatcher]) -> None:
    """Explicitly set or reset the global dispatcher (for tests / lifespan)."""
    global _global_dispatcher
    _global_dispatcher = dispatcher
