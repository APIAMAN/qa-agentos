
"""Tests ensuring architecture import boundaries are preserved.

Runtime and agent modules must never import provider-specific SDKs
(e.g., openai, anthropic, google.genai, boto3) directly.
All LLM gateway communication must route exclusively through the LiteLLM
OpenAI-compatible HTTP gateway using httpx.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_ROOT_MODULES = frozenset(
    {
        "openai",
        "anthropic",
        "google.generativeai",
        
        "google.genai",
        "google.cloud",
        "boto3",
        "botocore",
        "cohere",
        "mistralai",
        "groq",
        "together",
        "replicate",
        "litellm",  # HTTP proxy endpoint only; no in-process LiteLLM Python SDK imports
    }
)


def _get_imports_from_file(file_path: Path) -> list[tuple[int, str]]:
    """Parse a python source file and extract all imported module names."""
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    imports: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append((node.lineno, node.module))

    return imports


def test_no_provider_sdk_imports_in_runtime():
    """Verify that no forbidden vendor SDKs are imported anywhere under qa_agentos/runtime/."""
    # Find api root directory
    test_dir = Path(__file__).resolve().parent
    api_src = test_dir.parent / "src" / "qa_agentos"
    runtime_dir = api_src / "runtime"

    assert runtime_dir.is_dir(), f"Expected runtime directory at {runtime_dir}"

    runtime_py_files = list(runtime_dir.rglob("*.py"))
    assert len(runtime_py_files) > 0, "Expected python files in runtime directory"

    violations: list[str] = []
    for file_path in runtime_py_files:
        for lineno, module_name in _get_imports_from_file(file_path):
            top_level = module_name.split(".")[0]
            # Check both full module path and root module
            if (
                top_level in FORBIDDEN_ROOT_MODULES
                or module_name in FORBIDDEN_ROOT_MODULES
                or any(module_name.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_ROOT_MODULES)
            ):
                rel_path = file_path.relative_to(api_src)
                violations.append(f"{rel_path}:{lineno} imports '{module_name}'")

    assert not violations, "Forbidden provider SDK imports found in runtime:\n" + "\n".join(violations)


def test_gateway_client_uses_httpx_only():
    """Verify that LiteLLM client specifically imports httpx and no direct SDKs."""
    test_dir = Path(__file__).resolve().parent
    client_file = test_dir.parent / "src" / "qa_agentos" / "runtime" / "gateway" / "client.py"
    assert client_file.is_file(), f"Expected client file at {client_file}"

    imports = _get_imports_from_file(client_file)
    imported_modules = {mod.split(".")[0] for _, mod in imports}

    assert "httpx" in imported_modules, "LiteLLMClient must import and use httpx"
    for forbidden in FORBIDDEN_ROOT_MODULES:
        assert forbidden not in imported_modules, f"client.py must not import {forbidden}"
