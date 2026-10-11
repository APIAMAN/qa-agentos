"""PostgreSQL checkpointer integration for LangGraph.

Manages AsyncPostgresSaver instances with schema isolation in the dedicated
`langgraph` PostgreSQL schema, away from core application tables.
"""

from __future__ import annotations

import asyncio
import re
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool


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


@asynccontextmanager
async def postgres_checkpointer(
    database_url: str,
    *,
    schema: str = "langgraph",
    min_size: int = 1,
    max_size: int = 5,
) -> AsyncIterator[AsyncPostgresSaver]:
    """Provide an AsyncPostgresSaver operating inside an isolated PostgreSQL schema."""
    if sys.platform == "win32":
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass

    normalized_url = _normalize_psycopg_url(database_url)

    # 1. Ensure schema exists using a bootstrap connection
    async with await AsyncConnection.connect(normalized_url, autocommit=True) as setup_conn:
        await setup_conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')

    from psycopg.rows import dict_row

    # 2. Open connection pool with search_path restricted to schema
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
            saver = AsyncPostgresSaver(conn)  # type: ignore[arg-type]
            await saver.setup()
            yield saver
