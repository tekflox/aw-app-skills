"""Unit tests for skills_app/mcp/http_handler.py's JSON-RPC dispatch —
initialize/tools-list/tools-call, the no-token guard, and exception safety.

Run: pytest tests/test_http_handler.py -q
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from skills_app import client
from skills_app.mcp import http_handler


def _run(coro):
    return asyncio.run(coro)


def setup_function(_fn):
    client.configure(lambda: "http://skills.test", lambda: "test-token")


def teardown_function(_fn):
    client.configure(lambda: client.DEFAULT_BASE_URL, lambda: "")


def test_initialize():
    resp = _run(http_handler.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
    assert resp["result"]["serverInfo"]["name"] == "skills"
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_notifications_initialized_returns_none():
    resp = _run(http_handler.handle_request({"method": "notifications/initialized"}))
    assert resp is None


def test_tools_list_returns_all_4_tools():
    resp = _run(http_handler.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"search_skills", "load_skill", "create_skill", "update_skill"}


def test_unknown_method_returns_json_rpc_error():
    resp = _run(http_handler.handle_request({"jsonrpc": "2.0", "id": 3, "method": "bogus"}))
    assert resp["error"]["code"] == -32601


def test_tools_call_unknown_tool():
    resp = _run(http_handler.handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    }))
    assert resp["result"]["isError"] is True
    assert "Unknown tool" in resp["result"]["content"][0]["text"]


def test_tools_call_without_token_returns_no_token_message():
    client.configure(lambda: "http://skills.test", lambda: "")
    resp = _run(http_handler.handle_request({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "search_skills", "arguments": {"query": "x"}},
    }))
    assert resp["result"]["isError"] is True
    assert "identity token" in resp["result"]["content"][0]["text"]


def test_tools_call_dispatches_to_client_and_returns_its_result():
    async def _fake_search_skills(args):
        assert args == {"query": "x"}
        return "ok result", False

    with patch.object(http_handler, "_DISPATCH", {"search_skills": _fake_search_skills}):
        resp = _run(http_handler.handle_request({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "search_skills", "arguments": {"query": "x"}},
        }))
    assert resp["result"]["isError"] is False
    assert resp["result"]["content"][0]["text"] == "ok result"


def test_tools_call_swallows_handler_exception():
    async def _boom(args):
        raise RuntimeError("kaboom")

    with patch.object(http_handler, "_DISPATCH", {"search_skills": _boom}):
        resp = _run(http_handler.handle_request({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "search_skills", "arguments": {}},
        }))
    assert resp["result"]["isError"] is True
    assert "kaboom" in resp["result"]["content"][0]["text"]
