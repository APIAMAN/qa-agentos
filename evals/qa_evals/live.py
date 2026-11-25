"""Run the real Bug Analyzer on the golden dataset, in-process, through the production runtime.

Each case: the fixture is served as a project log artifact, a run is executed by ``RunExecutor``
with the real agent YAML/prompt, the ``log.parse`` tool and the configured model behind LiteLLM.
State lives in a throwaway in-memory SQLite database; nothing touches shared infrastructure except
the model gateway. Limits: an optional shared ``CallBudget`` (calls, estimated spend, pacing, stop on
quota) and the catalog-cost cap (sum of recorded ``model_calls.cost_usd``). ``prompt_override``
replaces the agent's system prompt in this throwaway database only (AC4 weakened-prompt runs).
"""

from __future__ import annotations

import hashlib
import pathlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from qa_agentos.core.config import Settings
from qa_agentos.core.database import Base
from qa_agentos.models.domain import AgentDefinition, AgentVersion, Artifact, ModelCall, Project, Run, RunStep
from qa_agentos.modules.bugs.tools import LOG_PARSE_IMPLEMENTATION, make_log_parse
from qa_agentos.registry.sync import sync_registries
from qa_agentos.runtime import pubsub as pubsub_module
from qa_agentos.runtime import storage as storage_module
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from qa_evals.budget import CallBudget
from qa_evals.dataset import REPO_ROOT, Dataset


class FileStorage:
    """Serves fixture files as artifact objects (key = absolute path)."""

    def get_object_data(self, key: str, max_bytes: Optional[int] = None) -> Tuple[bytes, str]:
        data = pathlib.Path(key).read_bytes()
        return (data if max_bytes is None else data[:max_bytes]), "text/plain"


@dataclass
class LiveResult:
    outputs: Dict[str, Optional[Dict[str, Any]]] = field(default_factory=dict)
    run_errors: Dict[str, str] = field(default_factory=dict)
    not_run: List[str] = field(default_factory=list)
    cost_usd: float = 0.0
    models: List[Dict[str, Any]] = field(default_factory=list)
    agent: Dict[str, Any] = field(default_factory=dict)
    tool_calls: int = 0


async def run_live(
    dataset: Dataset,
    *,
    settings: Settings,
    client: Optional[LiteLLMClient] = None,
    max_cost_usd: float,
    max_cases: Optional[int] = None,
    case_ids: Optional[Sequence[str]] = None,
    prompt_override: Optional[str] = None,
    budget: Optional[CallBudget] = None,
) -> LiveResult:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    previous_storage = storage_module._storage_client
    storage_module._storage_client = FileStorage()  # type: ignore[assignment]
    previous_publish = pubsub_module.publish_run_event

    async def no_live_events(event: Any) -> bool:  # offline eval: no Redis, no UI subscribers
        return False

    pubsub_module.publish_run_event = no_live_events  # type: ignore[assignment]
    gateway = client or LiteLLMClient.from_settings(settings)
    result = LiveResult()
    try:
        async with factory() as db:
            await sync_registries(db, registry_root=REPO_ROOT)
            await db.commit()
            agent = (await db.execute(select(AgentDefinition).where(AgentDefinition.slug == "bug-analyzer"))).scalar_one()
            version = (
                await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.status == "active"))
            ).scalar_one()
            if prompt_override is not None:
                version.prompt_template = prompt_override
                version.prompt_hash = hashlib.sha256(prompt_override.encode("utf-8")).hexdigest()
            result.agent = {
                "slug": agent.slug,
                "version": version.version,
                "prompt_hash": version.prompt_hash,
                "prompt": "override" if prompt_override is not None else "registry",
            }
            project = Project(id=uuid.uuid4(), name="evals", slug=f"evals-{uuid.uuid4().hex[:6]}")
            db.add(project)
            await db.commit()

        resolver = ModelResolver(ProviderPolicy.from_settings(settings))
        cases = [c for c in dataset.cases if not case_ids or c.id in case_ids]
        cases = cases[: max_cases or len(cases)]
        for case in cases:
            if result.cost_usd >= max_cost_usd or (budget is not None and budget.stop_reason):
                result.not_run.append(case.id)
                continue
            async with factory() as db:
                art = Artifact(
                    id=uuid.uuid4(),
                    project_id=project.id,
                    name=case.log_path.name,
                    artifact_type="log",
                    minio_key=str(case.log_path),
                    content_hash="0" * 64,
                    file_size_bytes=case.log_path.stat().st_size,
                )
                run = Run(
                    id=uuid.uuid4(),
                    project_id=project.id,
                    agent_version_id=version.id,
                    status="queued",
                    input_payload={"log_artifact_id": str(art.id), "environment": "eval"},
                    resolved_agent=dict(result.agent),
                )
                db.add_all([art, run])
                await db.commit()
                executor = RunExecutor(
                    db,
                    gateway,
                    model_resolver=resolver,
                    tool_implementations={LOG_PARSE_IMPLEMENTATION: make_log_parse(factory)},
                )
                try:
                    outcome = await executor.execute_run(run)
                except Exception as exc:  # record and continue; a missing output fails the gate
                    result.outputs[case.id] = None
                    result.run_errors[case.id] = f"{type(exc).__name__}: {exc}"
                    continue
                result.outputs[case.id] = outcome.output_payload if outcome.status == "succeeded" else None
                if outcome.status != "succeeded":
                    result.run_errors[case.id] = f"{outcome.status}: {outcome.error_code} {outcome.error_message}"
                result.tool_calls += (
                    await db.execute(
                        select(func.count()).select_from(RunStep).where(RunStep.run_id == run.id, RunStep.step_type == "tool_call")
                    )
                ).scalar_one()
                spent = (await db.execute(select(func.coalesce(func.sum(ModelCall.cost_usd), 0)))).scalar_one()
                result.cost_usd = float(spent)
                if not result.models:
                    result.models = [{"capability": k, **v} for k, v in (run.resolved_models or {}).items()]
    finally:
        storage_module._storage_client = previous_storage
        pubsub_module.publish_run_event = previous_publish  # type: ignore[assignment]
        if client is None:
            await gateway.aclose()
        await engine.dispose()
    return result
