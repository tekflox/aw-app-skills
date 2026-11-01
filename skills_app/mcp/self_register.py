"""Entry describing this app's own ``/mcp`` endpoint, for aw-mcp-gateway's
app-scan (``scan_app_mcp_servers()``, which reads ``<app dir>/mcp.json``).

``MCP_SERVER_NAME`` is deliberately ``"skills"``, not ``"aw-skills"`` or
``"aw-app-skills"``: the gateway derives each tool's prefix from the server
name (dashes to underscores), and this card's briefing names the target
prefix explicitly as ``aw__skills__*`` — distinct from the ``kb`` app's
``aw__kb__*`` pair that reads a different, filesystem-backed tree (see
README.md).

Tier-1 (in-process): this *is* the aw-workspace process, so
``socket.gethostname()`` is exactly the value ContainerSupervisor injects into
sibling containers as ``AW_WORKSPACE_HOST``, and ``AW_WORKSPACE_API_KEY`` is
already in this process's environment — nothing has to be provisioned. The
header is required because Tier-1 routes sit behind IdentityGuard.
"""
from __future__ import annotations

import os
import socket

MCP_SERVER_NAME = "skills"
ROUTE_PATH = "/api/apps/skills/mcp"


def build_self_entry(port: int | None = None) -> dict:
    host = socket.gethostname()
    port = port or int(os.environ.get("AW_PORT") or 9030)
    entry: dict = {
        "type": "http",
        "url": f"http://{host}:{port}{ROUTE_PATH}",
        "enabled": True,
    }
    api_key = os.environ.get("AW_WORKSPACE_API_KEY")
    if api_key:
        entry["headers"] = {"X-Api-Key": api_key}
    return entry
