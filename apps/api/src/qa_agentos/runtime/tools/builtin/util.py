"""Builtin utility tools.

Currently implements `util.echo` for diagnostic, connectivity, and dispatcher testing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


async def echo(input_data: Mapping[str, Any], context: Any = None) -> dict[str, Any]:
    """Echoes back the input message, UTC ISO timestamp, and optional metadata.

    Conforms to the `util.echo` contract registered in tools.yaml.
    """
    message = str(input_data["message"])
    payload: dict[str, Any] = {
        "echoed_message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if "metadata" in input_data and input_data["metadata"] is not None:
        payload["metadata"] = dict(input_data["metadata"])
    return payload
