"""The ``skills`` MCP server, over Streamable HTTP (``POST /mcp``).

JSON-RPC 2.0 envelope + dispatch, same shape as
``aw-app-knowledgeable/knowledgeable_app/mcp/http_handler.py`` — the wire
protocol is what aw-mcp-gateway's own ``HttpUpstream`` speaks, and none of it
is specific to skills.aw. The actual tool logic lives in ``client.py``.
"""
from __future__ import annotations

import logging

from .. import client

log = logging.getLogger("aw_apps.skills")

SERVER_NAME = "skills"
SERVER_VERSION = "1.0.0"

TOOLS_SCHEMA = client.TOOLS_SCHEMA

_DISPATCH = {
    "search_skills": client.search_skills,
    "load_skill": client.load_skill,
    "create_skill": client.create_skill,
    "update_skill": client.update_skill,
}

_NO_TOKEN = (
    "This connector has no identity token yet. It mints and refreshes one "
    "automatically against aw-backend (identity_token.py) — if this persists, "
    "check that AW_BACKEND_URL/AW_WORKSPACE/AW_WORKSPACE_HOST_TOKEN are set on "
    "this workspace and look at this app's own log for 'identity_token:'."
)


def _result(req_id, text: str, is_error: bool) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
    }


async def handle_request(request: dict) -> dict | None:
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS_SCHEMA}}
    if method != "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }

    params = request.get("params") or {}
    name = params.get("name", "")
    args = params.get("arguments") or {}

    handler = _DISPATCH.get(name)
    if not handler:
        return _result(req_id, f"Unknown tool: {name}", True)

    if not client.configured():
        return _result(req_id, _NO_TOKEN, True)

    try:
        text, is_error = await handler(args)
    except Exception as exc:  # noqa: BLE001 — last resort, must not 500 the route
        log.exception("skills MCP tool %s failed", name)
        return _result(req_id, f"{name} failed: {exc}", True)

    return _result(req_id, text, is_error)
