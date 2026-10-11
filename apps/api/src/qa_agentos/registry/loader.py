import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema
import yaml


class RegistryError(Exception):
    """Base exception for registry operations."""


class RegistryValidationError(RegistryError):
    """Raised when registry files or references fail validation."""


class RegistryImmutabilityError(RegistryError):
    """Raised when attempting to modify an active agent version without bumping the version."""


@dataclass
class AgentRegistryItem:
    slug: str
    name: str
    description: str
    kind: str
    version: str
    status: str
    prompt_file: str
    prompt_template: str
    prompt_hash: str
    parameters: Dict[str, Any]
    permissions: List[str]
    allowed_tools: List[str]
    model_requirements: Dict[str, Any]
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]


@dataclass
class ToolRegistryItem:
    name: str
    version: str
    description: str
    kind: str
    implementation: str
    permissions: List[str]
    timeout_ms: int
    retry_policy: Dict[str, Any]
    idempotent: bool
    status: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]


@dataclass
class ModelRegistryItem:
    name: str
    provider: str
    gateway_model_key: str
    capabilities: List[str]
    priority: int
    structured_output: bool
    context_window: int
    status: str
    max_output_tokens: Optional[int] = None
    cost_input_per_1m: Optional[float] = None
    cost_output_per_1m: Optional[float] = None


@dataclass
class RegistryCatalog:
    root_path: Path
    agents: List[AgentRegistryItem] = field(default_factory=list)
    tools: List[ToolRegistryItem] = field(default_factory=list)
    models: List[ModelRegistryItem] = field(default_factory=list)


def find_registry_root(explicit_root: Optional[Path] = None) -> Path:
    """Resolve the project registry root directory independently of the current working directory."""
    if explicit_root:
        candidate = Path(explicit_root).resolve()
        if (candidate / "models.yaml").is_file() and (candidate / "agents").is_dir():
            return candidate

    env_root = os.getenv("QA_AGENTOS_REGISTRY_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / "models.yaml").is_file() and (candidate / "agents").is_dir():
            return candidate

    # Search upwards from this file
    curr = Path(__file__).resolve().parent
    for _ in range(8):
        if (curr / "models.yaml").is_file() and (curr / "agents").is_dir():
            return curr
        if curr.parent == curr:
            break
        curr = curr.parent

    # Check cwd as fallback
    cwd = Path.cwd().resolve()
    if (cwd / "models.yaml").is_file() and (cwd / "agents").is_dir():
        return cwd

    raise RegistryError(
        "Could not determine QA AgentOS registry root directory containing 'agents/' and 'models.yaml'."
    )


def validate_json_schema(schema: Dict[str, Any], context: str) -> None:
    """Validate that a schema dict is a valid JSON Schema (Draft 2020-12 compatible)."""
    if not isinstance(schema, dict):
        raise RegistryValidationError(f"{context} schema must be a JSON object, got {type(schema).__name__}")
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.exceptions.SchemaError as exc:
        raise RegistryValidationError(f"Invalid JSON Schema in {context}: {exc.message}") from exc


def load_tools_catalog(root_path: Path) -> List[ToolRegistryItem]:
    tools_file = root_path / "tools.yaml"
    if not tools_file.is_file():
        raise RegistryValidationError(f"Tools catalog not found at {tools_file}")

    try:
        data = yaml.safe_load(tools_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RegistryValidationError(f"Failed to parse tools.yaml: {exc}") from exc

    if not isinstance(data, dict) or "tools" not in data or not isinstance(data["tools"], list):
        raise RegistryValidationError("tools.yaml must contain a top-level 'tools' list")

    items: List[ToolRegistryItem] = []
    seen = set()

    for idx, t in enumerate(data["tools"]):
        name = t.get("name")
        version = str(t.get("version", "1.0.0"))
        if not name:
            raise RegistryValidationError(f"Tool at index {idx} missing required 'name'")
        key = (name, version)
        if key in seen:
            raise RegistryValidationError(f"Duplicate tool definition: {name}@{version}")
        seen.add(key)

        input_schema = t.get("input_schema")
        output_schema = t.get("output_schema")
        if not input_schema or not output_schema:
            raise RegistryValidationError(f"Tool '{name}' must define input_schema and output_schema")

        validate_json_schema(input_schema, f"tool '{name}' input_schema")
        validate_json_schema(output_schema, f"tool '{name}' output_schema")

        items.append(
            ToolRegistryItem(
                name=name,
                version=version,
                description=t.get("description", ""),
                kind=t.get("kind", "builtin"),
                implementation=t.get("implementation", ""),
                permissions=list(t.get("permissions", [])),
                timeout_ms=int(t.get("timeout_ms", 30000)),
                retry_policy=dict(t.get("retry_policy", {"max_attempts": 2, "backoff": "exponential"})),
                idempotent=bool(t.get("idempotent", True)),
                status=t.get("status", "active"),
                input_schema=input_schema,
                output_schema=output_schema,
            )
        )

    return items


def load_models_catalog(root_path: Path) -> List[ModelRegistryItem]:
    models_file = root_path / "models.yaml"
    if not models_file.is_file():
        raise RegistryValidationError(f"Models catalog not found at {models_file}")

    try:
        data = yaml.safe_load(models_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RegistryValidationError(f"Failed to parse models.yaml: {exc}") from exc

    if not isinstance(data, dict) or "models" not in data or not isinstance(data["models"], list):
        raise RegistryValidationError("models.yaml must contain a top-level 'models' list")

    items: List[ModelRegistryItem] = []
    seen = set()

    for idx, m in enumerate(data["models"]):
        name = m.get("name")
        provider = m.get("provider")
        gateway_model_key = m.get("gateway_model_key")
        if not name or not provider or not gateway_model_key:
            raise RegistryValidationError(
                f"Model at index {idx} must define 'name', 'provider', and 'gateway_model_key'"
            )
        key = (provider, name)
        if key in seen:
            raise RegistryValidationError(f"Duplicate model definition: {provider}/{name}")
        seen.add(key)

        items.append(
            ModelRegistryItem(
                name=name,
                provider=provider,
                gateway_model_key=gateway_model_key,
                capabilities=list(m.get("capabilities", [])),
                priority=int(m.get("priority", 0)),
                structured_output=bool(m.get("structured_output", False)),
                context_window=int(m.get("context_window", 128000)),
                status=m.get("status", "available"),
                max_output_tokens=m.get("max_output_tokens"),
                cost_input_per_1m=float(m["cost_input_per_1m"]) if m.get("cost_input_per_1m") is not None else None,
                cost_output_per_1m=float(m["cost_output_per_1m"]) if m.get("cost_output_per_1m") is not None else None,
            )
        )

    return items


def load_agents_catalog(root_path: Path, available_tools: List[ToolRegistryItem]) -> List[AgentRegistryItem]:
    agents_dir = root_path / "agents"
    if not agents_dir.is_dir():
        raise RegistryValidationError(f"Agents directory not found at {agents_dir}")

    available_tool_names = {t.name for t in available_tools}
    items: List[AgentRegistryItem] = []

    for yaml_path in sorted(agents_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RegistryValidationError(f"Failed to parse agent file {yaml_path.name}: {exc}") from exc

        if not isinstance(data, dict):
            raise RegistryValidationError(f"Agent file {yaml_path.name} must contain a YAML mapping")

        slug = data.get("slug") or yaml_path.stem
        version = str(data.get("version", "1.0.0"))
        status = data.get("status", "draft")
        if status not in ("draft", "active", "deprecated"):
            raise RegistryValidationError(f"Agent '{slug}' has invalid status '{status}'")

        # Resolve prompt content and exact SHA-256 hash
        prompt_file_rel = data.get("prompt_file")
        if prompt_file_rel:
            prompt_file_path = root_path / prompt_file_rel
            if not prompt_file_path.is_file():
                raise RegistryValidationError(
                    f"Agent '{slug}' references prompt file '{prompt_file_rel}' which does not exist"
                )
            prompt_content = prompt_file_path.read_text(encoding="utf-8")
        elif "system_prompt" in data:
            prompt_file_rel = f"inline:{slug}"
            prompt_content = str(data["system_prompt"])
        else:
            raise RegistryValidationError(f"Agent '{slug}' must define either 'prompt_file' or 'system_prompt'")

        prompt_hash = hashlib.sha256(prompt_content.encode("utf-8")).hexdigest()

        # Validate input/output schemas
        input_schema = data.get("input_schema")
        output_schema = data.get("output_schema")
        if not input_schema or not output_schema:
            raise RegistryValidationError(f"Agent '{slug}' must define input_schema and output_schema")

        validate_json_schema(input_schema, f"agent '{slug}' input_schema")
        validate_json_schema(output_schema, f"agent '{slug}' output_schema")

        # Validate tool references against available tools
        allowed_tools = list(data.get("allowed_tools", []))
        for tool_ref in allowed_tools:
            tool_name = tool_ref.split("@")[0] if "@" in tool_ref else tool_ref
            if tool_name not in available_tool_names:
                raise RegistryValidationError(
                    f"Agent '{slug}' references unknown tool '{tool_ref}'. Tool not found in tools.yaml"
                )

        items.append(
            AgentRegistryItem(
                slug=slug,
                name=str(data.get("name") or slug),
                description=str(data.get("description") or ""),
                kind=str(data.get("kind") or "generic_react"),
                version=version,
                status=status,
                prompt_file=prompt_file_rel,
                prompt_template=prompt_content,
                prompt_hash=prompt_hash,
                parameters=dict(data.get("parameters", {})),
                permissions=list(data.get("permissions", [])),
                allowed_tools=allowed_tools,
                model_requirements=dict(data.get("model_requirements", {})),
                input_schema=input_schema,
                output_schema=output_schema,
            )
        )

    return items


def load_registry_catalog(explicit_root: Optional[Path] = None) -> RegistryCatalog:
    """Load and validate all registry definitions (models, tools, agents) from disk."""
    root_path = find_registry_root(explicit_root)
    tools = load_tools_catalog(root_path)
    models = load_models_catalog(root_path)
    agents = load_agents_catalog(root_path, tools)
    return RegistryCatalog(
        root_path=root_path,
        agents=agents,
        tools=tools,
        models=models,
    )
