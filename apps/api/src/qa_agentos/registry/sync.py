from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    ModelCatalog,
    ToolDefinition,
    utc_now,
)
from qa_agentos.registry.loader import (
    RegistryImmutabilityError,
    load_registry_catalog,
)


@dataclass
class SyncResult:
    models_created: int = 0
    models_updated: int = 0
    tools_created: int = 0
    tools_updated: int = 0
    agents_created: int = 0
    agents_updated: int = 0
    versions_created: int = 0
    versions_updated: int = 0

    @property
    def total_changes(self) -> int:
        return (
            self.models_created
            + self.models_updated
            + self.tools_created
            + self.tools_updated
            + self.agents_created
            + self.agents_updated
            + self.versions_created
            + self.versions_updated
        )


async def sync_registries(
    db: AsyncSession,
    registry_root: Optional[Path] = None,
) -> SyncResult:
    """Synchronize file-based registry definitions (models, tools, agents) into the database.

    Guarantees:
    - Idempotency: repeated runs with unchanged files produce 0 mutations.
    - Immutability: active versions cannot have their prompt or config changed without a version bump.
    - Consistency: new active versions automatically deprecate older active versions.
    """
    catalog = load_registry_catalog(registry_root)
    result = SyncResult()

    # 1. Sync Models
    for m in catalog.models:
        stmt_m = select(ModelCatalog).where(
            ModelCatalog.provider == m.provider,
            ModelCatalog.name == m.name,
        )
        db_model = (await db.execute(stmt_m)).scalar_one_or_none()
        if not db_model:
            db_model = ModelCatalog(
                name=m.name,
                provider=m.provider,
                gateway_model_key=m.gateway_model_key,
                capabilities=m.capabilities,
                priority=m.priority,
                structured_output=m.structured_output,
                context_window=m.context_window,
                max_output_tokens=m.max_output_tokens,
                cost_input_per_1m=m.cost_input_per_1m,
                cost_output_per_1m=m.cost_output_per_1m,
                status=m.status,
            )
            db.add(db_model)
            result.models_created += 1
        else:
            # Check for changes
            changed = (
                db_model.gateway_model_key != m.gateway_model_key
                or db_model.capabilities != m.capabilities
                or db_model.priority != m.priority
                or db_model.structured_output != m.structured_output
                or db_model.context_window != m.context_window
                or db_model.max_output_tokens != m.max_output_tokens
                or (db_model.cost_input_per_1m is not None and float(db_model.cost_input_per_1m) != m.cost_input_per_1m)
                or (
                    db_model.cost_output_per_1m is not None
                    and float(db_model.cost_output_per_1m) != m.cost_output_per_1m
                )
                or db_model.status != m.status
            )
            if changed:
                db_model.gateway_model_key = m.gateway_model_key
                db_model.capabilities = m.capabilities
                db_model.priority = m.priority
                db_model.structured_output = m.structured_output
                db_model.context_window = m.context_window
                db_model.max_output_tokens = m.max_output_tokens
                db_model.cost_input_per_1m = m.cost_input_per_1m
                db_model.cost_output_per_1m = m.cost_output_per_1m
                db_model.status = m.status
                result.models_updated += 1

    # 2. Sync Tools
    for t in catalog.tools:
        stmt_t = select(ToolDefinition).where(
            ToolDefinition.name == t.name,
            ToolDefinition.version == t.version,
        )
        db_tool = (await db.execute(stmt_t)).scalar_one_or_none()
        if not db_tool:
            db_tool = ToolDefinition(
                name=t.name,
                version=t.version,
                description=t.description,
                kind=t.kind,
                implementation=t.implementation,
                input_schema=t.input_schema,
                output_schema=t.output_schema,
                permissions=t.permissions,
                timeout_ms=t.timeout_ms,
                retry_policy=t.retry_policy,
                idempotent=t.idempotent,
                is_active=(t.status == "active"),
            )
            db.add(db_tool)
            result.tools_created += 1
        else:
            changed = (
                db_tool.description != t.description
                or db_tool.kind != t.kind
                or db_tool.implementation != t.implementation
                or db_tool.input_schema != t.input_schema
                or db_tool.output_schema != t.output_schema
                or db_tool.permissions != t.permissions
                or db_tool.timeout_ms != t.timeout_ms
                or db_tool.retry_policy != t.retry_policy
                or db_tool.idempotent != t.idempotent
                or db_tool.is_active != (t.status == "active")
            )
            if changed:
                db_tool.description = t.description
                db_tool.kind = t.kind
                db_tool.implementation = t.implementation
                db_tool.input_schema = t.input_schema
                db_tool.output_schema = t.output_schema
                db_tool.permissions = t.permissions
                db_tool.timeout_ms = t.timeout_ms
                db_tool.retry_policy = t.retry_policy
                db_tool.idempotent = t.idempotent
                db_tool.is_active = t.status == "active"
                result.tools_updated += 1

    # 3. Sync Agents & Versions
    for a in catalog.agents:
        stmt_agent = select(AgentDefinition).where(AgentDefinition.slug == a.slug)
        db_agent = (await db.execute(stmt_agent)).scalar_one_or_none()
        if not db_agent:
            db_agent = AgentDefinition(
                slug=a.slug,
                name=a.name,
                description=a.description,
                agent_type=a.kind,
            )
            db.add(db_agent)
            await db.flush()
            result.agents_created += 1
        else:
            if db_agent.name != a.name or db_agent.description != a.description or db_agent.agent_type != a.kind:
                db_agent.name = a.name
                db_agent.description = a.description
                db_agent.agent_type = a.kind
                result.agents_updated += 1

        # Check existing version
        stmt_ver = select(AgentVersion).where(
            AgentVersion.agent_id == db_agent.id,
            AgentVersion.version == a.version,
        )
        db_ver = (await db.execute(stmt_ver)).scalar_one_or_none()

        if db_ver:
            # Enforce Active Version Immutability
            if db_ver.status == "active":
                diffs = []
                if db_ver.prompt_hash != a.prompt_hash:
                    diffs.append("prompt_content")
                if db_ver.parameters != a.parameters:
                    diffs.append("parameters")
                if db_ver.permissions != a.permissions:
                    diffs.append("permissions")
                if db_ver.model_requirements != a.model_requirements:
                    diffs.append("model_requirements")
                if db_ver.allowed_tools != a.allowed_tools:
                    diffs.append("allowed_tools")
                if db_ver.input_schema != a.input_schema:
                    diffs.append("input_schema")
                if db_ver.output_schema != a.output_schema:
                    diffs.append("output_schema")

                if diffs:
                    raise RegistryImmutabilityError(
                        f"Cannot mutate active agent version '{a.slug}@{a.version}'. "
                        f"Modified attributes: {', '.join(diffs)}. "
                        f"Active versions are immutable in QA AgentOS; bump the version number in YAML to apply changes."
                    )
            else:
                # If draft or deprecated, update in-place
                changed = (
                    db_ver.prompt_hash != a.prompt_hash
                    or db_ver.parameters != a.parameters
                    or db_ver.permissions != a.permissions
                    or db_ver.model_requirements != a.model_requirements
                    or db_ver.allowed_tools != a.allowed_tools
                    or db_ver.input_schema != a.input_schema
                    or db_ver.output_schema != a.output_schema
                    or db_ver.status != a.status
                )
                if changed:
                    db_ver.prompt_template = a.prompt_template
                    db_ver.prompt_hash = a.prompt_hash
                    db_ver.parameters = a.parameters
                    db_ver.permissions = a.permissions
                    db_ver.model_requirements = a.model_requirements
                    db_ver.allowed_tools = a.allowed_tools
                    db_ver.input_schema = a.input_schema
                    db_ver.output_schema = a.output_schema

                    if a.status == "active" and db_ver.status != "active":
                        # Deactivate currently active version
                        stmt_active = select(AgentVersion).where(
                            AgentVersion.agent_id == db_agent.id,
                            AgentVersion.status == "active",
                            AgentVersion.id != db_ver.id,
                        )
                        for prev_active in (await db.execute(stmt_active)).scalars().all():
                            prev_active.status = "deprecated"
                        db_ver.status = "active"
                        db_ver.activated_at = utc_now()

                    result.versions_updated += 1
        else:
            # Create new version
            if a.status == "active":
                # Deactivate any currently active version for this agent first
                stmt_active = select(AgentVersion).where(
                    AgentVersion.agent_id == db_agent.id,
                    AgentVersion.status == "active",
                )
                for prev_active in (await db.execute(stmt_active)).scalars().all():
                    prev_active.status = "deprecated"
                activated_at = utc_now()
            else:
                activated_at = None

            new_ver = AgentVersion(
                agent_id=db_agent.id,
                version=a.version,
                status=a.status,
                prompt_hash=a.prompt_hash,
                prompt_template=a.prompt_template,
                parameters=a.parameters,
                permissions=a.permissions,
                input_schema=a.input_schema,
                output_schema=a.output_schema,
                allowed_tools=a.allowed_tools,
                model_requirements=a.model_requirements,
                activated_at=activated_at,
            )
            db.add(new_ver)
            result.versions_created += 1

    await db.commit()
    return result
