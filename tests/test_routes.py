"""TestClient coverage for skills_app/routes.py's build_routes().

Run: pytest tests/test_routes.py -q
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from skills_app import client
from skills_app.routes import build_routes


def setup_function(_fn):
    client.configure(lambda: "http://skills.test", lambda: "test-token")


def teardown_function(_fn):
    client.configure(lambda: client.DEFAULT_BASE_URL, lambda: "")


def test_status_reports_configured_and_tools():
    test_client = TestClient(build_routes())
    resp = test_client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["base_url"] == "http://skills.test"
    assert set(body["tools"]) == {"search_skills", "load_skill", "create_skill", "update_skill"}


def test_test_endpoint_reports_ok_on_success():
    async def _fake_search_skills(args):
        return '{"ok": true}', False

    with patch.object(client, "search_skills", _fake_search_skills):
        test_client = TestClient(build_routes())
        resp = test_client.post("/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_test_endpoint_reports_failure():
    async def _fake_search_skills(args):
        return "boom", True

    with patch.object(client, "search_skills", _fake_search_skills):
        test_client = TestClient(build_routes())
        resp = test_client.post("/test")
    assert resp.json()["ok"] is False


def test_mcp_get_is_405():
    test_client = TestClient(build_routes())
    assert test_client.get("/mcp").status_code == 405


def test_mcp_post_single_message_returns_single_object():
    test_client = TestClient(build_routes())
    resp = test_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp.status_code == 200
    body = resp.json()
    assert "tools" in body["result"]


def test_mcp_post_batch_returns_list():
    test_client = TestClient(build_routes())
    resp = test_client.post("/mcp", json=[
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "initialize"},
    ])
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list) and len(body) == 2


def test_mcp_post_notification_only_returns_202():
    test_client = TestClient(build_routes())
    resp = test_client.post("/mcp", json={"method": "notifications/initialized"})
    assert resp.status_code == 202
