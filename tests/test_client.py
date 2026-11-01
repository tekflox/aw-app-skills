"""Unit tests for skills_app/client.py — the 4 tool bodies' validation and
their 1:1 mapping onto skills.aw's REST surface, with no real network.

Async calls go through ``asyncio.run`` from plain sync test functions —
same convention as aw-app-template's own test suite — rather than adding a
pytest-asyncio dependency just for this.

Run: pytest tests/test_client.py -q
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

from skills_app import client


class _FakeAsyncClient:
    def __init__(self, resp, calls):
        self._resp = resp
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self._calls.append(("GET", url, params, headers))
        return self._resp

    async def post(self, url, json=None, params=None, headers=None):
        self._calls.append(("POST", url, json, headers))
        return self._resp

    async def put(self, url, json=None, params=None, headers=None):
        self._calls.append(("PUT", url, json, headers))
        return self._resp


def _mock_resp(status_code=200, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body if body is not None else {}
    return resp


def _patched_client(resp):
    calls: list = []
    return patch("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(resp, calls)), calls


def setup_function(_fn):
    client.configure(lambda: "http://skills.test", lambda: "test-token")


def teardown_function(_fn):
    client.configure(lambda: client.DEFAULT_BASE_URL, lambda: "")


def test_base_url_falls_back_to_default_when_resolver_returns_empty():
    client.configure(lambda: "", lambda: "")
    assert client.base_url() == client.DEFAULT_BASE_URL


def test_configured_reflects_token_presence():
    client.configure(lambda: "http://skills.test", lambda: "")
    assert client.configured() is False
    client.configure(lambda: "http://skills.test", lambda: "tok")
    assert client.configured() is True


def test_configured_false_when_token_resolver_raises():
    def _raise():
        raise RuntimeError("boom")

    client.configure(lambda: "http://skills.test", _raise)
    assert client.configured() is False


def test_search_skills_requires_query():
    text, is_error = asyncio.run(client.search_skills({}))
    assert is_error is True
    assert "query is required" in text


def test_search_skills_maps_n_results_to_limit_and_sends_bearer():
    patcher, calls = _patched_client(_mock_resp(200, [{"slug": "foo"}]))
    with patcher:
        text, is_error = asyncio.run(client.search_skills({"query": "deploy", "n_results": 3}))
    assert is_error is False
    assert json.loads(text) == [{"slug": "foo"}]
    method, url, params, headers = calls[0]
    assert method == "GET"
    assert url == "http://skills.test/api/skills"
    assert params == {"query": "deploy", "limit": 3}
    assert headers == {"Authorization": "Bearer test-token"}


def test_search_skills_clamps_n_results_into_range():
    patcher, calls = _patched_client(_mock_resp(200, []))
    with patcher:
        asyncio.run(client.search_skills({"query": "x", "n_results": 10_000}))
    assert calls[0][2]["limit"] == 200


def test_search_skills_surfaces_http_error():
    patcher, _ = _patched_client(_mock_resp(500, {"detail": "boom"}))
    with patcher:
        text, is_error = asyncio.run(client.search_skills({"query": "x"}))
    assert is_error is True
    assert "boom" in text


def test_load_skill_requires_name():
    text, is_error = asyncio.run(client.load_skill({}))
    assert is_error is True
    assert "name is required" in text


def test_load_skill_omits_version_param_when_not_given():
    patcher, calls = _patched_client(_mock_resp(200, {"slug": "foo", "version": 3}))
    with patcher:
        asyncio.run(client.load_skill({"name": "foo"}))
    assert calls[0][1] == "http://skills.test/api/skills/foo/content"
    assert calls[0][2] is None


def test_load_skill_passes_explicit_version():
    patcher, calls = _patched_client(_mock_resp(200, {"slug": "foo", "version": 1}))
    with patcher:
        asyncio.run(client.load_skill({"name": "foo", "version": 1}))
    assert calls[0][2] == {"version": 1}


def test_load_skill_rejects_non_integer_version():
    text, is_error = asyncio.run(client.load_skill({"name": "foo", "version": "abc"}))
    assert is_error is True
    assert "integer" in text


def test_create_skill_requires_name_and_content():
    _, is_error = asyncio.run(client.create_skill({"content": "x"}))
    assert is_error is True
    _, is_error = asyncio.run(client.create_skill({"name": "foo"}))
    assert is_error is True


def test_create_skill_rejects_bad_visibility():
    text, is_error = asyncio.run(
        client.create_skill({"name": "foo", "content": "x", "visibility": "secret"})
    )
    assert is_error is True
    assert "visibility" in text


def test_create_skill_defaults_visibility_to_private():
    patcher, calls = _patched_client(_mock_resp(201, {"slug": "foo"}))
    with patch("skills_app.client.workspace_env", return_value="aw"), patcher:
        asyncio.run(client.create_skill({"name": "foo", "description": "d", "content": "c"}))
    method, url, body, headers = calls[0]
    assert method == "POST"
    assert url == "http://skills.test/api/skills"
    assert body == {
        "slug": "foo", "description": "d", "content": "c",
        "visibility": "private", "tenant_slug": "aw",
    }


def test_create_skill_honours_explicit_public_visibility_and_omits_tenant_slug():
    patcher, calls = _patched_client(_mock_resp(201, {"slug": "foo"}))
    with patcher:
        asyncio.run(client.create_skill({"name": "foo", "content": "c", "visibility": "public"}))
    assert calls[0][2]["visibility"] == "public"
    assert "tenant_slug" not in calls[0][2]


def test_create_skill_surfaces_conflict_error():
    patcher, _ = _patched_client(_mock_resp(409, {"detail": "already exists"}))
    with patcher:
        text, is_error = asyncio.run(client.create_skill({"name": "foo", "content": "c"}))
    assert is_error is True
    assert "already exists" in text


def test_update_skill_requires_name_and_content():
    _, is_error = asyncio.run(client.update_skill({"content": "x"}))
    assert is_error is True
    _, is_error = asyncio.run(client.update_skill({"name": "foo"}))
    assert is_error is True


def test_update_skill_puts_content_and_message():
    patcher, calls = _patched_client(_mock_resp(200, {"slug": "foo", "latest_version": 2}))
    with patcher:
        asyncio.run(client.update_skill({"name": "foo", "content": "new", "message": "fix typo"}))
    method, url, body, headers = calls[0]
    assert method == "PUT"
    assert url == "http://skills.test/api/skills/foo"
    assert body == {"content": "new", "message": "fix typo"}


def test_get_surfaces_transport_error():
    import httpx as httpx_mod

    class _RaisingAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *a, **kw):
            raise httpx_mod.ConnectError("down")

    with patch("httpx.AsyncClient", lambda **kw: _RaisingAsyncClient()):
        text, is_error = asyncio.run(client.search_skills({"query": "x"}))
    assert is_error is True
    assert "could not reach skills.aw" in text


def test_describe_error_falls_back_when_body_is_not_json():
    resp = MagicMock()
    resp.status_code = 502
    resp.json.side_effect = ValueError()
    assert client._describe_error(resp) == "HTTP 502"
