import hashlib
import shutil
from pathlib import Path

import pytest
from qa_agentos.core.bootstrap import bootstrap_demo_data
from qa_agentos.models.domain import (
    AgentDefinition,
    AgentVersion,
    ModelCatalog,
    Project,
    ToolDefinition,
    User,
)
from qa_agentos.registry.loader import (
    RegistryImmutabilityError,
    RegistryValidationError,
    find_registry_root,
    load_agents_catalog,
    load_registry_catalog,
    load_tools_catalog,
)
from qa_agentos.registry.sync import sync_registries
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def test_registry_root_discovery():
    """Verify registry root is discovered independently of cwd."""
    root = find_registry_root()
    assert (root / "models.yaml").is_file()
    assert (root / "tools.yaml").is_file()
    assert (root / "agents").is_dir()
    assert (root / "prompts").is_dir()


def test_yaml_parsing_and_schema_validation():
    """Verify loading and schema validation of models, tools, and agents catalogs."""
    root = find_registry_root()
    catalog = load_registry_catalog(root)

    assert len(catalog.models) >= 3
    assert len(catalog.tools) >= 1
    assert len(catalog.agents) >= 1

    # Check test-case-generator
    tcg = next(a for a in catalog.agents if a.slug == "test-case-generator")
    assert tcg.version == "1.0.0"
    assert tcg.status == "active"
    assert tcg.kind == "generic_react"
    assert tcg.parameters.get("temperature") == 0.1
    assert tcg.parameters.get("max_repairs") == 2
    assert tcg.parameters.get("max_steps") == 10
    assert "artifact:write" in tcg.permissions
    assert tcg.allowed_tools == []
    assert "planner" in tcg.model_requirements

    # Check tool util.echo
    echo = next(t for t in catalog.tools if t.name == "util.echo")
    assert echo.version == "1.0.0"
    assert echo.kind == "builtin"
    assert echo.idempotent is True
    assert echo.timeout_ms == 10000


def test_prompt_hash_matches_exact_source_file():
    """Verify SHA-256 prompt hash matches the exact raw file content."""
    root = find_registry_root()
    catalog = load_registry_catalog(root)

    tcg = next(a for a in catalog.agents if a.slug == "test-case-generator")
    raw_prompt = (root / "prompts" / "test-case-generator" / "1.0.0.md").read_text(encoding="utf-8")
    expected_hash = hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest()

    assert tcg.prompt_hash == expected_hash
    assert len(tcg.prompt_hash) == 64


def test_invalid_json_schema_fails_validation(tmp_path: Path):
    """Verify that an invalid input/output JSON schema is caught during validation."""
    root = find_registry_root()

    # Copy files to tmp_path
    shutil.copytree(root / "agents", tmp_path / "agents")
    shutil.copytree(root / "prompts", tmp_path / "prompts")
    shutil.copy(root / "tools.yaml", tmp_path / "tools.yaml")
    shutil.copy(root / "models.yaml", tmp_path / "models.yaml")

    # Corrupt input_schema in agent YAML
    agent_file = tmp_path / "agents" / "test-case-generator.yaml"
    content = agent_file.read_text(encoding="utf-8")
    corrupt_content = content.replace("type: object", "type: invalid_type_123")
    agent_file.write_text(corrupt_content, encoding="utf-8")

    with pytest.raises(RegistryValidationError, match="Invalid JSON Schema"):
        load_registry_catalog(tmp_path)


def test_missing_or_invalid_tool_reference_fails(tmp_path: Path):
    """Verify that referencing an undeclared tool prevents agent activation."""
    root = find_registry_root()

    shutil.copytree(root / "agents", tmp_path / "agents")
    shutil.copytree(root / "prompts", tmp_path / "prompts")
    shutil.copy(root / "tools.yaml", tmp_path / "tools.yaml")
    shutil.copy(root / "models.yaml", tmp_path / "models.yaml")

    agent_file = tmp_path / "agents" / "test-case-generator.yaml"
    content = agent_file.read_text(encoding="utf-8")
    # Add undeclared tool
    modified_content = content.replace("allowed_tools: []", "allowed_tools: ['nonexistent.tool@1.0.0']")
    agent_file.write_text(modified_content, encoding="utf-8")

    tools = load_tools_catalog(tmp_path)
    with pytest.raises(RegistryValidationError, match="references unknown tool"):
        load_agents_catalog(tmp_path, tools)


async def test_initial_sync_and_idempotency_postgres(pg_session: AsyncSession):
    """Verify registry synchronization on PostgreSQL and strict idempotency on repeated runs."""
    root = find_registry_root()

    # 1. Initial sync
    result1 = await sync_registries(pg_session, registry_root=root)
    assert result1.models_created == 3
    assert result1.tools_created == 1
    assert result1.agents_created == 1
    assert result1.versions_created == 1

    # Verify models exist in DB
    models = (await pg_session.execute(select(ModelCatalog))).scalars().all()
    assert len(models) == 3
    model_providers = {m.provider for m in models}
    assert "nvidia_nim" in model_providers
    assert "gemini" in model_providers
    assert "mock" in model_providers

    # Verify tools exist in DB
    tools = (await pg_session.execute(select(ToolDefinition))).scalars().all()
    assert len(tools) == 1
    assert tools[0].name == "util.echo"
    assert tools[0].idempotent is True

    # Verify agent and version in DB
    agents = (await pg_session.execute(select(AgentDefinition))).scalars().all()
    assert len(agents) == 1
    assert agents[0].slug == "test-case-generator"

    versions = (await pg_session.execute(select(AgentVersion))).scalars().all()
    assert len(versions) == 1
    assert versions[0].version == "1.0.0"
    assert versions[0].status == "active"
    assert versions[0].activated_at is not None

    # 2. Second sync (Must be 100% idempotent with 0 mutations)
    result2 = await sync_registries(pg_session, registry_root=root)
    assert result2.total_changes == 0
    assert result2.models_created == 0
    assert result2.models_updated == 0
    assert result2.tools_created == 0
    assert result2.tools_updated == 0
    assert result2.agents_created == 0
    assert result2.agents_updated == 0
    assert result2.versions_created == 0
    assert result2.versions_updated == 0


async def test_active_version_immutability_enforced(pg_session: AsyncSession, tmp_path: Path):
    """Verify that modifying an active version's prompt without version bump fails with an actionable error."""
    root = find_registry_root()

    shutil.copytree(root / "agents", tmp_path / "agents")
    shutil.copytree(root / "prompts", tmp_path / "prompts")
    shutil.copy(root / "tools.yaml", tmp_path / "tools.yaml")
    shutil.copy(root / "models.yaml", tmp_path / "models.yaml")

    # Initial sync to active
    await sync_registries(pg_session, registry_root=tmp_path)

    # Mutate prompt file without bumping version
    prompt_file = tmp_path / "prompts" / "test-case-generator" / "1.0.0.md"
    prompt_file.write_text("Modified prompt content without version bump!", encoding="utf-8")

    with pytest.raises(RegistryImmutabilityError, match="Cannot mutate active agent version"):
        await sync_registries(pg_session, registry_root=tmp_path)


async def test_introducing_new_version_preserves_history(pg_session: AsyncSession, tmp_path: Path):
    """Verify that bumping to a new version preserves older versions and deprecates the prior active version."""
    root = find_registry_root()

    shutil.copytree(root / "agents", tmp_path / "agents")
    shutil.copytree(root / "prompts", tmp_path / "prompts")
    shutil.copy(root / "tools.yaml", tmp_path / "tools.yaml")
    shutil.copy(root / "models.yaml", tmp_path / "models.yaml")

    # Initial sync v1.0.0
    await sync_registries(pg_session, registry_root=tmp_path)

    # Add v1.1.0 prompt
    new_prompt = tmp_path / "prompts" / "test-case-generator" / "1.1.0.md"
    new_prompt.write_text("# Test Case Generator v1.1.0 with advanced heuristics", encoding="utf-8")

    # Update agent YAML to version 1.1.0
    agent_yaml = tmp_path / "agents" / "test-case-generator.yaml"
    content = agent_yaml.read_text(encoding="utf-8")
    content = content.replace("version: 1.0.0", "version: 1.1.0")
    content = content.replace("1.0.0.md", "1.1.0.md")
    agent_yaml.write_text(content, encoding="utf-8")

    # Sync v1.1.0
    result = await sync_registries(pg_session, registry_root=tmp_path)
    assert result.versions_created == 1

    # Verify both versions exist
    stmt = (
        select(AgentVersion)
        .join(AgentDefinition)
        .where(AgentDefinition.slug == "test-case-generator")
        .order_by(AgentVersion.version)
    )
    versions = (await pg_session.execute(stmt)).scalars().all()
    assert len(versions) == 2

    v100 = next(v for v in versions if v.version == "1.0.0")
    v110 = next(v for v in versions if v.version == "1.1.0")

    assert v100.status == "deprecated"
    assert v110.status == "active"
    assert v110.activated_at is not None


async def test_bootstrap_demo_data_integration(pg_session: AsyncSession):
    """Verify demo bootstrap seeds admin, demo project, and synchronizes registries without duplication."""
    # First bootstrap
    await bootstrap_demo_data(pg_session)

    # Query project and user
    user = (await pg_session.execute(select(User).where(User.email == "admin@qa-agentos.local"))).scalar_one_or_none()
    assert user is not None
    assert user.role == "owner"

    proj = (await pg_session.execute(select(Project).where(Project.slug == "demo-project"))).scalar_one_or_none()
    assert proj is not None
    assert proj.is_active is True

    # Registries must have synced
    models = (await pg_session.execute(select(ModelCatalog))).scalars().all()
    assert len(models) >= 3

    # Repeat bootstrap - must succeed idempotently
    await bootstrap_demo_data(pg_session)
