"""PostgreSQL checkpointer integration for LangGraph.

Manages AsyncPostgresSaver instances with schema isolation in the dedicated
`langgraph` PostgreSQL schema, away from core application tables.

Setup (schema + checkpoint tables) runs under a PostgreSQL advisory lock so several
worker processes starting at once cannot race on ``CREATE TABLE IF NOT EXISTS``, and
runs at most once per process per database.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Sequence, Set, Tuple, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_SETUP_ADVISORY_LOCK_KEY = 0x5141_4C47  # "QALG": stable key for checkpoint schema setup
_setup_done: Set[str] = set()


def _normalize_psycopg_url(url: str) -> str:
    """Normalize SQLAlchemy / asyncpg URLs to standard PostgreSQL URL for psycopg."""
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgres+asyncpg://"):
        return "postgresql://" + url[len("postgres+asyncpg://") :]
    return url


def _mask_url(url: str) -> str:
    """Mask credentials for secure logging."""
    return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:******@", url)


async def _ensure_setup(normalized_url: str, schema: str) -> None:
    key = f"{normalized_url}#{schema}"
    if key in _setup_done:
        return
    async with await AsyncConnection.connect(
        normalized_url,
        autocommit=True,
        row_factory=dict_row,
        options=f"-c search_path={schema}",
    ) as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (_SETUP_ADVISORY_LOCK_KEY,))
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
            await AsyncPostgresSaver(conn).setup()
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (_SETUP_ADVISORY_LOCK_KEY,))
    _setup_done.add(key)


CheckpointFence = Callable[[], Awaitable[None]]
_fenced_classes: Dict[type, type] = {}


def fenced_checkpointer(inner: BaseCheckpointSaver[Any], fence: CheckpointFence) -> BaseCheckpointSaver[Any]:
    """Return a view of ``inner`` that runs ``fence`` before every checkpoint write.

    Hatchet can start a retry before the previous attempt has processed its cancellation
    (observed live on task timeout), so two attempts of one run may briefly overlap. The
    stale attempt is already fenced from application writes; this fences its LangGraph
    writes too: ``fence`` raises (e.g. ``RunSupersededError``) once a newer attempt owns the
    run, so the stale attempt cannot append checkpoints to the run's thread.

    The view is a dynamic subclass of the concrete saver that overrides only ``aput`` and
    ``aput_writes``; it shares the saver's connection, lock and storage (shallow copy of its
    attributes), so every other checkpoint behaviour is exactly the saver's own. The
    original saver object is not modified (it may be shared, e.g. an in-memory saver).
    """
    base_cls = type(inner)
    sub = _fenced_classes.get(base_cls)
    if sub is None:

        async def aput(
            self: Any, config: RunnableConfig, checkpoint: Any, metadata: Any, new_versions: Any
        ) -> RunnableConfig:
            await self._qa_checkpoint_fence()
            return await base_cls.aput(self, config, checkpoint, metadata, new_versions)

        async def aput_writes(
            self: Any, config: RunnableConfig, writes: Sequence[Tuple[str, Any]], task_id: str, task_path: str = ""
        ) -> None:
            await self._qa_checkpoint_fence()
            await base_cls.aput_writes(self, config, writes, task_id, task_path)

        sub = type(f"Fenced{base_cls.__name__}", (base_cls,), {"aput": aput, "aput_writes": aput_writes})
        _fenced_classes[base_cls] = sub

    view: Any = object.__new__(sub)
    view.__dict__.update(inner.__dict__)
    view._qa_checkpoint_fence = fence
    return cast(BaseCheckpointSaver[Any], view)


@asynccontextmanager
async def postgres_checkpointer(
    database_url: str,
    *,
    schema: str = "langgraph",
    min_size: int = 1,
    max_size: int = 5,
) -> AsyncIterator[AsyncPostgresSaver]:
    """Provide an AsyncPostgresSaver operating inside an isolated PostgreSQL schema."""
    normalized_url = _normalize_psycopg_url(database_url)
    await _ensure_setup(normalized_url, schema)

    conn_options = f"-c search_path={schema}"
    async with AsyncConnectionPool(
        conninfo=normalized_url,
        min_size=min_size,
        max_size=max_size,
        kwargs={"options": conn_options, "autocommit": True, "row_factory": dict_row},
        open=False,
    ) as pool:
        await pool.open()
        # Acquire a dedicated connection for AsyncPostgresSaver
        async with pool.connection() as conn:
            yield AsyncPostgresSaver(conn)  # type: ignore[arg-type]
