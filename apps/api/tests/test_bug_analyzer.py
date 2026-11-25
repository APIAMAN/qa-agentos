"""M3 T4–T6: deterministic log parser, ``log.parse`` tool dispatch, golden dataset, Bug Analyzer agent."""

from __future__ import annotations

import json
import pathlib
import uuid
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import httpx
import jsonschema
import pytest
import yaml
from qa_agentos.models.domain import AgentDefinition, AgentVersion, Artifact, Project, Run, RunStep, ToolDefinition
from qa_agentos.modules.bugs.tools import LOG_PARSE_IMPLEMENTATION, make_log_parse, make_tool_implementations
from qa_agentos.registry.loader import find_registry_root, load_registry_catalog
from qa_agentos.registry.sync import sync_registries
from qa_agentos.runtime.executor import RunExecutor
from qa_agentos.runtime.gateway.client import LiteLLMClient
from qa_agentos.runtime.gateway.resolution import ModelResolver, ProviderPolicy
from qa_agentos.runtime.tools.builtin.logparse import (
    MAX_INLINE_CHARS,
    MAX_INPUT_BYTES,
    MAX_SIGNATURES,
    log_parse,
    parse_log_text,
    redact_secrets,
)
from qa_agentos.runtime.tools.dispatcher import ToolDispatcher, ToolExecutionContext, ToolInvocationRequest
from qa_agentos.runtime.tools.errors import ToolInputInvalidError, ToolNotAllowedError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_report_export import FakeStorage

REPO = find_registry_root()
DATASET_DIR = REPO / "evals" / "datasets" / "bug-analyzer" / "v1"
DATASET = yaml.safe_load((DATASET_DIR / "dataset.yaml").read_text(encoding="utf-8"))
CASES = {c["id"]: c for c in DATASET["cases"]}


def _log(case_id: str) -> str:
    return (DATASET_DIR / CASES[case_id]["log"]).read_text(encoding="utf-8")


def _tool_contract(name: str) -> Any:
    return next(t for t in load_registry_catalog(REPO).tools if t.name == name)


# ------------------------------------------------------------------------------------------
# Parser (pure, deterministic)
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_parser_matches_golden_signatures(case_id: str) -> None:
    """Golden regression: the frozen dataset signatures are what the parser produces."""
    out = parse_log_text(_log(case_id))
    expected = CASES[case_id]["expected"]
    assert out["primary_signature"] == expected["primary_signature"]
    assert [s["signature"] for s in out["signatures"]] == expected["signatures"]
    assert out["error_count"] == expected["error_count"]
    jsonschema.validate(out, _tool_contract("log.parse").output_schema)


def test_parser_is_deterministic_and_redacts() -> None:
    text = _log("09-auth-expired-token-accepted")
    first, second = parse_log_text(text), parse_log_text(text)
    assert first == second
    blob = json.dumps(first)
    assert "SuperSecretPw1" not in blob and "eyJhbGciOiJIUzI1NiJ9" not in blob
    assert first["redactions"] >= 2


@pytest.mark.parametrize(
    "secret",
    [
        "password=hunter2",
        'api_key: "sk-abcdef1234567890"',
        "Authorization: Bearer abcdefghijklmnop123",
        "AKIAABCDEFGHIJKLMNOP",
        "token=ghp_abcdefghijklmnopqrstuvwxyz0123",
        "jane.doe@example.com",
        "4111 1111 1111 1111",
        "postgres://app:s3cretpw@db:5432/x",
        "nvapi-abcdefghijklmnop",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_redaction_covers_secret_kinds(secret: str) -> None:
    sensitive = {
        "password=hunter2": "hunter2",
        'api_key: "sk-abcdef1234567890"': "sk-abcdef1234567890",
        "Authorization: Bearer abcdefghijklmnop123": "abcdefghijklmnop123",
        "AKIAABCDEFGHIJKLMNOP": "AKIAABCDEFGHIJKLMNOP",
        "token=ghp_abcdefghijklmnopqrstuvwxyz0123": "ghp_abcdefghijklmnopqrstuvwxyz0123",
        "jane.doe@example.com": "jane.doe@example.com",
        "4111 1111 1111 1111": "4111 1111 1111 1111",
        "postgres://app:s3cretpw@db:5432/x": "s3cretpw",
        "nvapi-abcdefghijklmnop": "nvapi-abcdefghijklmnop",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----": "MIIB",
    }[secret]
    assert sensitive not in redact_secrets(f"ERROR failed {secret} end")


def test_signature_ignores_ids_timestamps_and_line_numbers() -> None:
    a = parse_log_text(_log("01-checkout-keyerror"))["primary_signature"]
    b = parse_log_text(_log("02-checkout-keyerror-repeat"))["primary_signature"]  # other ids, times, line numbers
    assert a == b


def test_same_message_different_code_path_gets_different_signature() -> None:
    cart = parse_log_text(_log("05-cart-render-typeerror"))
    pricing = parse_log_text(_log("14-pricing-typeerror-jsonl"))
    assert cart["signatures"][0]["message"] == pricing["signatures"][0]["message"]
    assert cart["primary_signature"] != pricing["primary_signature"]


def test_parser_edge_cases_and_limits() -> None:
    empty = parse_log_text("")
    assert empty["format"] == "empty" and empty["primary_signature"] is None and empty["signatures"] == []
    warnings_only = parse_log_text(_log("11-deprecation-warnings"))
    assert warnings_only["error_count"] == 0 and warnings_only["warning_count"] == 2
    window = parse_log_text(_log("08-api-gateway-502"))["time_window"]
    assert window == {"start": "2026-11-07T13:00:01+00:00", "end": "2026-11-07T13:00:09+00:00"}

    many = "\n".join(f"ERROR distinct failure kind-{chr(97 + i % 26)}{chr(97 + i // 26)}" for i in range(40))
    capped = parse_log_text(many)
    assert len(capped["signatures"]) == MAX_SIGNATURES and capped["truncated"] is True
    assert capped["distinct_signatures"] == 40

    huge = parse_log_text("ERROR boom\n" * (MAX_INPUT_BYTES // 10))
    assert huge["truncated"] is True and huge["signatures"][0]["count"] > 1


@pytest.mark.asyncio
async def test_inline_tool_limits() -> None:
    assert (await log_parse({"text": "ERROR x"}))["error_count"] == 1
    with pytest.raises(ToolInputInvalidError):
        await log_parse({"text": "x" * (MAX_INLINE_CHARS + 1)})
    with pytest.raises(ToolInputInvalidError):
        await log_parse({"artifact_id": str(uuid.uuid4())})  # artifact input needs the injected implementation


# ------------------------------------------------------------------------------------------
# Dispatcher integration (allowlist, schemas, project-scoped artifact input)
# ------------------------------------------------------------------------------------------


async def _seed_tool_env(db: AsyncSession, *, allowed: List[str]) -> Tuple[Project, AgentVersion, Run]:
    contract = _tool_contract("log.parse")
    if not (await db.execute(select(ToolDefinition).where(ToolDefinition.name == "log.parse"))).scalar_one_or_none():
        db.add(
            ToolDefinition(
                id=uuid.uuid4(),
                name=contract.name,
                version=contract.version,
                description=contract.description,
                kind=contract.kind,
                implementation=contract.implementation,
                input_schema=contract.input_schema,
                output_schema=contract.output_schema,
                permissions=contract.permissions,
                timeout_ms=contract.timeout_ms,
                retry_policy=contract.retry_policy,
                idempotent=contract.idempotent,
            )
        )
    project = Project(id=uuid.uuid4(), name="bugs", slug=f"bugs-{uuid.uuid4().hex[:6]}")
    agent = AgentDefinition(id=uuid.uuid4(), name="ba", slug=f"ba-{uuid.uuid4().hex[:6]}", agent_type="generic_react")
    db.add_all([project, agent])
    await db.flush()
    version = AgentVersion(
        id=uuid.uuid4(),
        agent_id=agent.id,
        version="1.0.0",
        prompt_hash="p" * 64,
        prompt_template="t",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permissions=["artifact:read", "member"],
        allowed_tools=allowed,
    )
    db.add(version)
    await db.flush()
    run = Run(id=uuid.uuid4(), project_id=project.id, agent_version_id=version.id, status="running", input_payload={})
    db.add(run)
    await db.commit()
    return project, version, run


def _ctx(run: Run, project: Project, version: AgentVersion, step: int = 1) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=run.id, step_number=step, project_id=project.id, agent_version=version, granted_permissions=["artifact:read", "member"]
    )


def _session_factory(db: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db.bind, expire_on_commit=False, class_=AsyncSession)


@pytest.mark.asyncio
async def test_dispatch_inline_text_validates_output(db_session: AsyncSession) -> None:
    project, version, run = await _seed_tool_env(db_session, allowed=["log.parse@1.0.0"])
    dispatcher = ToolDispatcher(db_session)
    result = await dispatcher.dispatch(
        ToolInvocationRequest("log.parse", "1.0.0", {"text": _log("07-payment-go-panic")}), _ctx(run, project, version)
    )
    assert result.status == "succeeded"
    assert result.output_data["primary_signature"] == CASES["07-payment-go-panic"]["expected"]["primary_signature"]


@pytest.mark.asyncio
async def test_dispatch_enforces_allowlist(db_session: AsyncSession) -> None:
    project, version, run = await _seed_tool_env(db_session, allowed=[])
    with pytest.raises(ToolNotAllowedError):
        await ToolDispatcher(db_session).dispatch(
            ToolInvocationRequest("log.parse", "1.0.0", {"text": "ERROR x"}), _ctx(run, project, version)
        )


@pytest.mark.asyncio
async def test_dispatch_artifact_input_is_project_scoped(db_session: AsyncSession) -> None:
    project, version, run = await _seed_tool_env(db_session, allowed=["log.parse"])
    other = Project(id=uuid.uuid4(), name="other", slug=f"other-{uuid.uuid4().hex[:6]}")
    db_session.add(other)
    await db_session.flush()
    storage = FakeStorage()
    arts = {}
    for owner, name, kind in [(project, "app.log", "log"), (other, "theirs.log", "log"), (project, "shot.png", "screenshot")]:
        key = f"{owner.id}/{name}"
        storage.objects[key] = (_log("09-auth-expired-token-accepted").encode(), "text/plain")
        art = Artifact(
            id=uuid.uuid4(),
            project_id=owner.id,
            run_id=None,
            name=name,
            artifact_type=kind,
            minio_key=key,
            content_hash="h" * 64,
            file_size_bytes=100,
        )
        arts[name] = art
        db_session.add(art)
    await db_session.commit()

    impls = make_tool_implementations(_session_factory(db_session))
    dispatcher = ToolDispatcher(db_session, custom_implementations=impls)
    with patch("qa_agentos.modules.bugs.tools.get_storage_client", return_value=storage):
        ok = await dispatcher.dispatch(
            ToolInvocationRequest("log.parse", "1.0.0", {"artifact_id": str(arts["app.log"].id)}),
            _ctx(run, project, version, step=1),
        )
        assert ok.output_data["source"]["name"] == "app.log"
        assert ok.output_data["primary_signature"] == CASES["09-auth-expired-token-accepted"]["expected"]["primary_signature"]
        assert "SuperSecretPw1" not in json.dumps(ok.output_data)

        for step, (name, msg) in enumerate([("theirs.log", "not found"), ("shot.png", "cannot be parsed")], start=2):
            with pytest.raises(ToolInputInvalidError, match=msg):
                await dispatcher.dispatch(
                    ToolInvocationRequest("log.parse", "1.0.0", {"artifact_id": str(arts[name].id)}),
                    _ctx(run, project, version, step=step),
                )


@pytest.mark.asyncio
async def test_artifact_parse_reads_bounded_range(db_session: AsyncSession) -> None:
    project, version, run = await _seed_tool_env(db_session, allowed=["log.parse"])
    calls: List[Any] = []

    class RangeStorage(FakeStorage):
        def get_object_data(self, key: str, max_bytes: Any = None) -> Tuple[bytes, str]:
            calls.append(max_bytes)
            return b"ERROR big\n" * 10, "text/plain"

    art = Artifact(
        id=uuid.uuid4(),
        project_id=project.id,
        name="big.log",
        artifact_type="log",
        minio_key="k",
        content_hash="h" * 64,
        file_size_bytes=MAX_INPUT_BYTES * 10,
    )
    db_session.add(art)
    await db_session.commit()
    with patch("qa_agentos.modules.bugs.tools.get_storage_client", return_value=RangeStorage()):
        out = await make_log_parse(_session_factory(db_session))({"artifact_id": str(art.id)}, _ctx(run, project, version))
    assert calls == [MAX_INPUT_BYTES + 1]  # never reads the whole 20 MB object
    assert out["truncated"] is True


# ------------------------------------------------------------------------------------------
# Bug Analyzer agent end to end (real YAML agent + prompt + tool contract, mock model)
# ------------------------------------------------------------------------------------------


def _scripted_gateway(responses: List[Dict[str, Any]], seen: List[str]) -> LiteLLMClient:
    idx = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal idx
        seen.append(request.content.decode("utf-8"))
        body = responses[min(idx, len(responses) - 1)]
        idx += 1
        return httpx.Response(
            200,
            json={
                "id": f"c-{idx}",
                "object": "chat.completion",
                "created": 1,
                "model": "mock",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(body)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    return LiteLLMClient(base_url="http://mock-gateway", api_key="sk-test", transport=httpx.MockTransport(handler))


async def seed_bug_analyzer_run(db: AsyncSession, storage: FakeStorage, case_id: str) -> Tuple[Run, Artifact]:
    """Sync the real registry, upload a fixture as a log artifact and queue a Bug Analyzer run."""
    await sync_registries(db, registry_root=REPO)
    agent = (await db.execute(select(AgentDefinition).where(AgentDefinition.slug == "bug-analyzer"))).scalar_one()
    version = (
        await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.status == "active"))
    ).scalar_one()
    project = Project(id=uuid.uuid4(), name="ba-e2e", slug=f"ba-e2e-{uuid.uuid4().hex[:6]}")
    db.add(project)
    await db.flush()
    key = f"{project.id}/logs/{case_id}.log"
    storage.objects[key] = (_log(case_id).encode("utf-8"), "text/plain")
    art = Artifact(
        id=uuid.uuid4(),
        project_id=project.id,
        name=f"{case_id}.log",
        artifact_type="log",
        minio_key=key,
        content_hash="h" * 64,
        file_size_bytes=len(_log(case_id).encode()),
    )
    db.add(art)
    run = Run(
        id=uuid.uuid4(),
        project_id=project.id,
        agent_version_id=version.id,
        status="queued",
        input_payload={"log_artifact_id": str(art.id), "environment": "staging"},
        resolved_agent={"slug": "bug-analyzer", "version": version.version, "prompt_hash": version.prompt_hash},
    )
    db.add(run)
    await db.flush()
    return run, art


def bug_report_for(case_id: str, **overrides: Any) -> Dict[str, Any]:
    exp = CASES[case_id]["expected"]
    report: Dict[str, Any] = {
        "title": f"Analysis of {case_id}",
        "severity": exp["severity"],
        "category": exp["category"],
        "summary": CASES[case_id]["note"],
        "root_cause": "See evidence.",
        "primary_signature": exp["primary_signature"],
        "evidence": [{"signature": s, "detail": "observed in logs"} for s in exp["signatures"][:2]],
        "repro_steps": [],
        "affected_component": None,
        "confidence": 0.8,
    }
    report.update(overrides)
    return report


@pytest.mark.asyncio
async def test_bug_analyzer_agent_end_to_end(pg_session: AsyncSession) -> None:
    storage = FakeStorage()
    case_id = "09-auth-expired-token-accepted"
    run, art = await seed_bug_analyzer_run(pg_session, storage, case_id)
    seen: List[str] = []
    client = _scripted_gateway(
        [
            {"tool_calls": [{"tool": "log.parse", "version": "1.0.0", "input_data": {"artifact_id": str(art.id)}}]},
            bug_report_for(case_id, title="Expired tokens accepted on admin routes"),
        ],
        seen,
    )
    resolver = ModelResolver(ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test"))
    executor = RunExecutor(
        pg_session,
        client,
        model_resolver=resolver,
        tool_implementations={LOG_PARSE_IMPLEMENTATION: make_log_parse(_session_factory(pg_session))},
    )
    with patch("qa_agentos.modules.bugs.tools.get_storage_client", return_value=storage):
        result = await executor.execute_run(run)

    assert result.status == "succeeded", result
    assert result.output_payload["severity"] == "critical"
    assert result.output_payload["primary_signature"] == CASES[case_id]["expected"]["primary_signature"]

    steps = (await pg_session.execute(select(RunStep).where(RunStep.run_id == run.id))).scalars().all()
    tool_steps = [s for s in steps if s.step_type == "tool_call" or s.node == "execute_tools"]
    assert tool_steps and tool_steps[0].status in ("succeeded", "completed")

    # Security: the model saw the sanitized parse result, never the raw log or its secrets.
    assert len(seen) == 2
    assert all("SuperSecretPw1" not in body and "eyJhbGciOiJIUzI1NiJ9" not in body for body in seen)
    assert CASES[case_id]["expected"]["primary_signature"] in seen[1]
    prompt = (REPO / "prompts" / "bug-analyzer" / "1.0.0.md").read_text(encoding="utf-8").splitlines()[0]
    assert prompt in seen[0]


@pytest.mark.asyncio
async def test_bug_analyzer_rejects_invalid_severity(pg_session: AsyncSession) -> None:
    """The agent's output schema is enforced: an unknown severity is repaired or fails, never stored."""
    storage = FakeStorage()
    case_id = "11-deprecation-warnings"
    run, art = await seed_bug_analyzer_run(pg_session, storage, case_id)
    bad = bug_report_for(case_id, severity="catastrophic")
    client = _scripted_gateway(
        [{"tool_calls": [{"tool": "log.parse", "version": "1.0.0", "input_data": {"artifact_id": str(art.id)}}]}, bad],
        [],
    )
    resolver = ModelResolver(ProviderPolicy(allowed_providers=frozenset({"mock"}), environment="test"))
    executor = RunExecutor(
        pg_session,
        client,
        model_resolver=resolver,
        tool_implementations={LOG_PARSE_IMPLEMENTATION: make_log_parse(_session_factory(pg_session))},
    )
    with patch("qa_agentos.modules.bugs.tools.get_storage_client", return_value=storage):
        result = await executor.execute_run(run)
    assert result.status == "failed"
    assert result.error_code == "OUTPUT_SCHEMA_INVALID"


def test_dataset_is_well_formed() -> None:
    assert DATASET["parser_version"] == "1.0.0"
    severities = [c["expected"]["severity"] for c in DATASET["cases"]]
    assert len(severities) >= 10 and {"critical", "high", "medium", "low"} <= set(severities)
    for dup in DATASET["duplicates"]:
        assert dup["case"] in CASES and dup["of"] in CASES
    assert pathlib.Path(DATASET_DIR / "logs").is_dir()
