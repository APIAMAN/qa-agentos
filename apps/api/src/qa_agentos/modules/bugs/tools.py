"""Storage-aware tool implementations, injected into the runtime's ToolDispatcher by the worker.

``runtime/`` must not import ``modules/`` (ADR-001), so the parts of a tool that need the database
or object storage live here and are passed in through ``RunExecutor(tool_implementations=...)``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Mapping

from qa_agentos.models.domain import Artifact
from qa_agentos.runtime.storage import StorageError, get_storage_client
from qa_agentos.runtime.tools.builtin.logparse import (
    MAX_INPUT_BYTES,
    log_parse,
    parse_log_text,
)
from qa_agentos.runtime.tools.dispatcher import ToolCallable, ToolExecutionContext
from qa_agentos.runtime.tools.errors import ToolInputInvalidError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

LOG_PARSE_IMPLEMENTATION = "qa_agentos.tools.builtin.logparse:log_parse"
#: Artifact types that may be parsed as logs.
LOG_ARTIFACT_TYPES = {"log", "text", "json"}


def make_log_parse(session_factory: async_sessionmaker[AsyncSession]) -> ToolCallable:
    """``log.parse`` that also accepts ``artifact_id`` (read from this run's project only)."""

    async def log_parse_with_artifacts(input_data: Mapping[str, Any], context: Any = None) -> Dict[str, Any]:
        artifact_id = input_data.get("artifact_id")
        if not artifact_id:
            return await log_parse(input_data, context)
        if not isinstance(context, ToolExecutionContext):
            raise ToolInputInvalidError("Artifact input requires a run context.")
        try:
            art_uuid = uuid.UUID(str(artifact_id))
        except ValueError:
            raise ToolInputInvalidError("artifact_id must be a UUID.") from None

        async with session_factory() as session:
            artifact = (
                await session.execute(
                    select(Artifact).where(Artifact.id == art_uuid, Artifact.project_id == context.project_id)
                )
            ).scalar_one_or_none()
        # Same error for "missing" and "other project": artifact ids are not probeable.
        if artifact is None:
            raise ToolInputInvalidError(f"Artifact {art_uuid} not found in this project.")
        if artifact.artifact_type not in LOG_ARTIFACT_TYPES:
            raise ToolInputInvalidError(
                f"Artifact type '{artifact.artifact_type}' cannot be parsed as a log.",
                details={"allowed_types": sorted(LOG_ARTIFACT_TYPES)},
            )

        storage = get_storage_client()
        try:
            # Read one byte past the limit so the parser can report truncation.
            data, _ = await asyncio.to_thread(storage.get_object_data, artifact.minio_key, MAX_INPUT_BYTES + 1)
        except StorageError as exc:
            raise RuntimeError(f"Log artifact could not be read: {exc}") from exc  # transient: retried
        result = parse_log_text(data.decode("utf-8", errors="replace"))
        result["source"] = {"artifact_id": str(art_uuid), "name": artifact.name, "size_bytes": artifact.file_size_bytes}
        if artifact.file_size_bytes > MAX_INPUT_BYTES:
            result["truncated"] = True
        return result

    return log_parse_with_artifacts


def make_tool_implementations(session_factory: async_sessionmaker[AsyncSession]) -> Dict[str, ToolCallable]:
    """Implementations the worker passes to ``RunExecutor(tool_implementations=...)``."""
    return {LOG_PARSE_IMPLEMENTATION: make_log_parse(session_factory)}
