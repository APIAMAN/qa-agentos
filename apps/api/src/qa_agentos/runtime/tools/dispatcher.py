"""Generic Tool Registry Dispatcher for QA AgentOS.

Executes the pipeline:
Resolve tool -> check agent allowlist -> validate input -> enforce permissions ->
enforce timeout/retry policy -> execute approved implementation -> validate output ->
persist execution step.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

import jsonschema
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from qa_agentos.models.domain import AgentVersion, RunStep, ToolDefinition
from qa_agentos.runtime.tools.builtin.util import echo
from qa_agentos.runtime.tools.errors import (
    ToolError,
    ToolExecutionFailedError,
    ToolImplementationUnsupportedError,
    ToolInputInvalidError,
    ToolNotAllowedError,
    ToolNotFoundError,
    ToolOutputInvalidError,
    ToolPermissionDeniedError,
    ToolStepConflictError,
    ToolTimeoutError,
    ToolVersionUnavailableError,
)

logger = get_logger(__name__)

ToolCallable = Callable[[Mapping[str, Any], Any], Awaitable[dict[str, Any]]]

# Static, explicit mapping from approved implementation identifiers to trusted Python callables.
APPROVED_BUILTIN_TOOLS: dict[str, ToolCallable] = {
    "qa_agentos.tools.builtin.util:echo": echo,
    "util.echo": echo,
}


@dataclass(frozen=True)
class ToolInvocationRequest:
    """Request to invoke a registered tool."""

    tool_name: str
    tool_version: str
    input_data: Mapping[str, Any]


@dataclass(frozen=True)
class ToolExecutionContext:
    """Context under which a tool is invoked."""

    run_id: uuid.UUID
    step_number: int
    project_id: uuid.UUID
    agent_version: AgentVersion
    granted_permissions: Optional[Sequence[str]] = None
    #: Run attempt (fencing token) executing this call; each attempt records its own step row.
    attempt: int = 1

    @property
    def idempotency_key(self) -> str:
        """Stable across attempts of the same logical call: pass it to external APIs that accept
        idempotency keys so a call repeated after a crash is de-duplicated downstream."""
        return f"qa-agentos:{self.run_id}:{self.step_number}"


@dataclass(frozen=True)
class ToolExecutionResult:
    """Auditable result of a successful tool execution."""

    run_id: uuid.UUID
    step_number: int
    tool_name: str
    tool_version: str
    output_data: dict[str, Any]
    status: str
    attempt_count: int
    latency_ms: int
    from_cache: bool = False


class ToolDispatcher:
    """Generic tool dispatcher responsible for security, validation, retries, and persistence."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        custom_implementations: Optional[dict[str, ToolCallable]] = None,
        backoff_multiplier: float = 0.05,
        commit_on_record: bool = False,
    ) -> None:
        self._session = session
        # When True, each recorded step is committed immediately so a crash after a tool's
        # side effect cannot lose the record that prevents re-executing it on recovery.
        self._commit_on_record = commit_on_record
        self._implementations = dict(APPROVED_BUILTIN_TOOLS)
        if custom_implementations:
            self._implementations.update(custom_implementations)
        self._backoff_multiplier = backoff_multiplier

    async def resolve_tool(self, tool_name: str, tool_version: str) -> ToolDefinition:
        """Resolve a tool from the database-backed catalog by exact name and version."""
        # 1. Check if tool exists by name
        stmt_any = select(ToolDefinition).where(ToolDefinition.name == tool_name)
        res_any = await self._session.execute(stmt_any)
        any_tools = res_any.scalars().all()
        if not any_tools:
            raise ToolNotFoundError(
                f"Tool '{tool_name}' not found in registry.",
                details={"tool_name": tool_name},
            )

        # 2. Check exact version match
        stmt_exact = select(ToolDefinition).where(
            ToolDefinition.name == tool_name,
            ToolDefinition.version == tool_version,
        )
        res_exact = await self._session.execute(stmt_exact)
        tool = res_exact.scalar_one_or_none()
        if tool is None or not tool.is_active:
            raise ToolVersionUnavailableError(
                f"Tool '{tool_name}' version '{tool_version}' is unavailable or inactive.",
                details={
                    "tool_name": tool_name,
                    "requested_version": tool_version,
                    "available_versions": [t.version for t in any_tools if t.is_active],
                },
            )

        # 3. Check kind
        if tool.kind != "builtin":
            raise ToolImplementationUnsupportedError(
                f"Tool kind '{tool.kind}' is not supported in this phase.",
                details={"kind": tool.kind},
            )

        # 4. Check static implementation mapping
        if tool.implementation not in self._implementations:
            raise ToolImplementationUnsupportedError(
                f"Implementation '{tool.implementation}' is not approved or supported.",
                details={"implementation": tool.implementation},
            )

        return tool

    def check_agent_allowlist(self, agent_version: AgentVersion, tool_name: str, tool_version: str) -> None:
        """Ensure the agent version explicitly permits invoking this tool."""
        allowed_list = agent_version.allowed_tools or []
        # Allow exact matching by "name", "name@version", or "name:version"
        allowed_set = set(allowed_list)
        is_allowed = (
            tool_name in allowed_set
            or f"{tool_name}@{tool_version}" in allowed_set
            or f"{tool_name}:{tool_version}" in allowed_set
        )
        if not is_allowed:
            raise ToolNotAllowedError(
                f"Tool '{tool_name}' is not permitted by agent version {agent_version.version}.",
                details={
                    "tool_name": tool_name,
                    "tool_version": tool_version,
                    "agent_id": str(agent_version.agent_id),
                    "allowed_tools": allowed_list,
                },
            )

    def validate_input(self, tool: ToolDefinition, input_data: Mapping[str, Any]) -> None:
        """Validate invocation input using Draft 2020-12 schema."""
        validator = jsonschema.Draft202012Validator(tool.input_schema)
        errors = sorted(validator.iter_errors(input_data), key=lambda e: list(e.path))
        if errors:
            err_details = [
                {
                    "path": "/" + "/".join(str(p) for p in e.path),
                    "message": e.message,
                }
                for e in errors
            ]
            raise ToolInputInvalidError(
                f"Input validation failed for tool '{tool.name}': {errors[0].message}",
                details={"errors": err_details},
            )

    def enforce_permissions(self, tool: ToolDefinition, context: ToolExecutionContext) -> None:
        """Enforce permission intersection between context grant and agent ceiling against tool requirements."""
        tool_required = set(tool.permissions or [])
        if tool.required_permission and tool.required_permission.strip():
            tool_required.add(tool.required_permission.strip())

        agent_ceiling = set(context.agent_version.permissions or [])
        if context.granted_permissions is not None:
            context_granted = set(context.granted_permissions)
        else:
            context_granted = agent_ceiling

        effective_permissions = context_granted & agent_ceiling
        missing = tool_required - effective_permissions
        if missing:
            raise ToolPermissionDeniedError(
                f"Missing required permissions to execute tool '{tool.name}': {sorted(missing)}",
                details={
                    "missing_permissions": sorted(missing),
                    "required_permissions": sorted(tool_required),
                    "effective_permissions": sorted(effective_permissions),
                },
            )

    def validate_output(self, tool: ToolDefinition, output_data: Any) -> dict[str, Any]:
        """Validate output data is JSON-serializable and matches output JSON schema."""
        try:
            # Ensure serialization round-trip succeeds
            json.dumps(output_data)
        except (TypeError, ValueError) as exc:
            raise ToolOutputInvalidError(
                f"Tool '{tool.name}' produced non-JSON-serializable output: {exc}",
                details={"serialization_error": str(exc)},
            ) from exc

        if not isinstance(output_data, dict):
            raise ToolOutputInvalidError(
                f"Tool '{tool.name}' output must be a JSON object, got {type(output_data).__name__}",
                details={"output_type": type(output_data).__name__},
            )

        validator = jsonschema.Draft202012Validator(tool.output_schema)
        errors = sorted(validator.iter_errors(output_data), key=lambda e: list(e.path))
        if errors:
            err_details = [
                {
                    "path": "/" + "/".join(str(p) for p in e.path),
                    "message": e.message,
                }
                for e in errors
            ]
            raise ToolOutputInvalidError(
                f"Output validation failed for tool '{tool.name}': {errors[0].message}",
                details={"errors": err_details},
            )

        return output_data

    async def _check_existing_step(
        self,
        context: ToolExecutionContext,
        request: ToolInvocationRequest,
    ) -> Optional[ToolExecutionResult]:
        """Enforce idempotency across attempts and detect conflicting replays.

        - A matching step that already succeeded in *any* attempt is returned from cache, so
          recovery never repeats a completed tool side effect.
        - Within the same attempt, a matching failed step re-raises its failure and a
          different tool/input at the same step number is a conflict.
        - A step that failed in an earlier attempt is executed again (that is the retry).
        """
        stmt = (
            select(RunStep)
            .where(
                RunStep.run_id == context.run_id,
                RunStep.step_number == context.step_number,
            )
            .order_by(RunStep.attempt.asc())
        )
        rows = list((await self._session.execute(stmt)).scalars().all())
        if not rows:
            return None

        expected_node = f"tool:{request.tool_name}@{request.tool_version}"
        canonical_input = json.dumps(dict(request.input_data), sort_keys=True)

        def matches(step: RunStep) -> bool:
            return step.node == expected_node and json.dumps(step.input_data, sort_keys=True) == canonical_input

        for existing in rows:
            if matches(existing) and existing.status == "succeeded":
                return ToolExecutionResult(
                    run_id=existing.run_id,
                    step_number=existing.step_number,
                    tool_name=request.tool_name,
                    tool_version=request.tool_version,
                    output_data=existing.output_data or {},
                    status="succeeded",
                    attempt_count=existing.attempt,
                    latency_ms=existing.latency_ms or 0,
                    from_cache=True,
                )

        same_attempt = [r for r in rows if r.attempt == context.attempt]
        if not same_attempt:
            return None
        existing = same_attempt[0]
        if matches(existing) and existing.status == "failed":
            err = existing.error or {}
            raise ToolExecutionFailedError(
                f"Prior execution of step {context.step_number} failed: {err.get('message', 'Unknown')}",
                details=err,
            )

        raise ToolStepConflictError(
            f"Step {context.step_number} already exists for run {context.run_id} with conflicting input or tool.",
            details={
                "run_id": str(context.run_id),
                "step_number": context.step_number,
                "attempt": context.attempt,
                "existing_node": existing.node,
                "requested_node": expected_node,
            },
        )

    async def _record_step(
        self,
        context: ToolExecutionContext,
        tool_name: str,
        tool_version: str,
        input_data: Mapping[str, Any],
        output_data: Optional[dict[str, Any]],
        status_val: str,
        attempt: int,
        latency_ms: int,
        error_val: Optional[dict[str, Any]] = None,
    ) -> RunStep:
        """Persist an auditable RunStep row in the database."""
        node = f"tool:{tool_name}@{tool_version}"
        step = RunStep(
            run_id=context.run_id,
            step_number=context.step_number,
            step_type="tool_call",
            node=node,
            attempt=context.attempt,
            input_data=dict(input_data),
            output_data=output_data,
            status=status_val,
            latency_ms=latency_ms,
            error=error_val,
            finished_at=datetime.now(timezone.utc),
        )
        self._session.add(step)
        await self._session.flush()
        if self._commit_on_record:
            await self._session.commit()
        return step

    async def dispatch(
        self,
        request: ToolInvocationRequest,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """Execute the full tool dispatch pipeline."""
        # 1. Idempotency / replay check
        cached_result = await self._check_existing_step(context, request)
        if cached_result is not None:
            return cached_result

        tool: Optional[ToolDefinition] = None
        attempt = 1
        start_time = time.monotonic()

        try:
            # 2. Tool resolution
            tool = await self.resolve_tool(request.tool_name, request.tool_version)

            # 3. Agent allowlist check
            self.check_agent_allowlist(context.agent_version, tool.name, tool.version)

            # 4. Input schema validation
            self.validate_input(tool, request.input_data)

            # 5. Permission enforcement
            self.enforce_permissions(tool, context)

            # 6. Timeout and retry policy resolution
            retry_policy = tool.retry_policy if isinstance(tool.retry_policy, dict) else {}
            raw_max_attempts = int(retry_policy.get("max_attempts", 1))
            # Non-idempotent tools are never retried
            max_attempts = raw_max_attempts if tool.idempotent else 1
            max_attempts = max(1, max_attempts)

            timeout_seconds = float(tool.timeout_ms) / 1000.0 if tool.timeout_ms > 0 else 30.0

            impl_callable = self._implementations[tool.implementation]

            # 7. Execute approved implementation with bounded retries
            raw_output: Any = None
            last_exc: Optional[Exception] = None

            for current_attempt in range(1, max_attempts + 1):
                attempt = current_attempt
                try:
                    raw_output = await asyncio.wait_for(
                        impl_callable(dict(request.input_data), context),
                        timeout=timeout_seconds,
                    )
                    last_exc = None
                    break
                except asyncio.TimeoutError as exc:
                    last_exc = exc
                    if current_attempt < max_attempts and tool.idempotent:
                        backoff = min(self._backoff_multiplier * (2 ** (current_attempt - 1)), 2.0)
                        await asyncio.sleep(backoff)
                        continue
                    raise ToolTimeoutError(
                        f"Tool '{tool.name}' timed out after {tool.timeout_ms}ms on attempt {current_attempt}/{max_attempts}.",
                        details={"timeout_ms": tool.timeout_ms, "attempts": current_attempt},
                    ) from exc
                except ToolError:
                    # Never retry domain/dispatcher errors
                    raise
                except Exception as exc:
                    last_exc = exc
                    # Transient error: retry only if idempotent and attempts remain
                    if current_attempt < max_attempts and tool.idempotent:
                        backoff = min(self._backoff_multiplier * (2 ** (current_attempt - 1)), 2.0)
                        await asyncio.sleep(backoff)
                        continue
                    raise ToolExecutionFailedError(
                        f"Tool '{tool.name}' execution failed: {type(exc).__name__}: {exc}",
                        details={"error_type": type(exc).__name__, "message": str(exc)},
                    ) from exc

            if last_exc is not None and raw_output is None:
                raise ToolExecutionFailedError(f"Tool execution failed: {last_exc}")

            # 8. Output validation
            validated_output = self.validate_output(tool, raw_output)
            latency_ms = int((time.monotonic() - start_time) * 1000)

            # 9. Persist successful RunStep
            await self._record_step(
                context=context,
                tool_name=tool.name,
                tool_version=tool.version,
                input_data=request.input_data,
                output_data=validated_output,
                status_val="succeeded",
                attempt=attempt,
                latency_ms=latency_ms,
            )

            return ToolExecutionResult(
                run_id=context.run_id,
                step_number=context.step_number,
                tool_name=tool.name,
                tool_version=tool.version,
                output_data=validated_output,
                status="succeeded",
                attempt_count=attempt,
                latency_ms=latency_ms,
                from_cache=False,
            )

        except ToolError as exc:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            t_name = tool.name if tool else request.tool_name
            t_ver = tool.version if tool else request.tool_version
            err_dict = {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "tool_attempts": attempt,
            }
            # Record failed step in database
            try:
                await self._record_step(
                    context=context,
                    tool_name=t_name,
                    tool_version=t_ver,
                    input_data=request.input_data,
                    output_data=None,
                    status_val="failed",
                    attempt=attempt,
                    latency_ms=latency_ms,
                    error_val=err_dict,
                )
            except Exception as record_exc:
                # Do not let step recording failure mask the original domain exception, but
                # never hide it either: the session must be usable for the run's own writes.
                logger.error(
                    "Failed to record failed tool step",
                    run_id=str(context.run_id),
                    step_number=context.step_number,
                    error=str(record_exc),
                )
                await self._session.rollback()
            raise
