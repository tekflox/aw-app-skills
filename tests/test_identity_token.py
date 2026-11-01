"""Unit tests for skills_app/identity_token.py — the mint-then-persist
refresh policy, and the two HTTP hops, with no real network.

Run: pytest tests/test_identity_token.py -q
"""
from __future__ import annotations

import base64
import json
import time
from unittest.mock import MagicMock, patch

from skills_app import identity_token


def _fake_jwt(iat: float, exp: float) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"iat": iat, "exp": exp}).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.sig"


def test_needs_refresh_true_for_missing_token():
    assert identity_token.needs_refresh(None) is True
    assert identity_token.needs_refresh("") is True


def test_needs_refresh_true_for_unparseable_token():
    assert identity_token.needs_refresh("not-a-jwt") is True
    assert identity_token.needs_refresh("a.b") is True


def test_needs_refresh_false_for_a_fresh_token():
    now = time.time()
    token = _fake_jwt(now, now + 86400)
    assert identity_token.needs_refresh(token, now=now) is False


def test_needs_refresh_true_past_half_life():
    now = time.time()
    token = _fake_jwt(now - 50000, now - 50000 + 86400)  # ~58% through a 24h token
    assert identity_token.needs_refresh(token, now=now) is True


def test_needs_refresh_true_for_expired_token():
    now = time.time()
    token = _fake_jwt(now - 90000, now - 1000)
    assert identity_token.needs_refresh(token, now=now) is True


def test_refresh_skips_when_token_not_due():
    now = time.time()
    fresh = _fake_jwt(now, now + 86400)
    with patch.object(identity_token, "needs_refresh", return_value=False) as m:
        result = identity_token.refresh({"identity_token": fresh})
    m.assert_called_once()
    assert result is None


def test_refresh_returns_none_when_env_not_set():
    with patch.object(identity_token, "workspace_env", return_value=""):
        assert identity_token.refresh({}) is None


def test_refresh_mints_and_persists_on_success():
    env = {
        "AW_BACKEND_URL": "http://backend.local",
        "AW_WORKSPACE": "aw",
        "AW_WORKSPACE_HOST_TOKEN": "host-token",
        "AW_WORKSPACE_API_URL": "http://workspace.local",
        "AW_WORKSPACE_API_KEY": "api-key",
    }
    with patch.object(identity_token, "workspace_env", side_effect=lambda n: env.get(n, "")), \
         patch.object(identity_token, "_mint", return_value="new-token") as mint, \
         patch.object(identity_token, "_persist", return_value=True) as persist:
        result = identity_token.refresh({})
    mint.assert_called_once_with("http://backend.local", "aw", "host-token")
    persist.assert_called_once_with("http://workspace.local", "api-key", "new-token")
    assert result == "new-token"


def test_refresh_returns_none_when_mint_fails():
    env = {
        "AW_BACKEND_URL": "http://backend.local",
        "AW_WORKSPACE": "aw",
        "AW_WORKSPACE_HOST_TOKEN": "host-token",
    }
    with patch.object(identity_token, "workspace_env", side_effect=lambda n: env.get(n, "")), \
         patch.object(identity_token, "_mint", return_value=None):
        assert identity_token.refresh({}) is None


def test_refresh_returns_none_when_persist_target_missing():
    env = {
        "AW_BACKEND_URL": "http://backend.local",
        "AW_WORKSPACE": "aw",
        "AW_WORKSPACE_HOST_TOKEN": "host-token",
    }
    with patch.object(identity_token, "workspace_env", side_effect=lambda n: env.get(n, "")), \
         patch.object(identity_token, "_mint", return_value="new-token"), \
         patch.object(identity_token, "_persist") as persist:
        result = identity_token.refresh({})
    persist.assert_not_called()
    assert result is None


def test_refresh_returns_none_when_persist_fails():
    env = {
        "AW_BACKEND_URL": "http://backend.local",
        "AW_WORKSPACE": "aw",
        "AW_WORKSPACE_HOST_TOKEN": "host-token",
        "AW_WORKSPACE_API_URL": "http://workspace.local",
        "AW_WORKSPACE_API_KEY": "api-key",
    }
    with patch.object(identity_token, "workspace_env", side_effect=lambda n: env.get(n, "")), \
         patch.object(identity_token, "_mint", return_value="new-token"), \
         patch.object(identity_token, "_persist", return_value=False):
        assert identity_token.refresh({}) is None


def _mock_response(status_code=200, json_body=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = text
    return resp


def test_mint_posts_bearer_host_token_and_returns_token():
    resp = _mock_response(200, {"token": "abc"})
    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.post.return_value = resp
        token = identity_token._mint("http://backend.local", "aw", "host-token")
    assert token == "abc"
    call = mock_client_cls.return_value.__enter__.return_value.post.call_args
    assert call.args[0] == "http://backend.local/api/workspaces/aw/identity-token"
    assert call.kwargs["headers"] == {"Authorization": "Bearer host-token"}


def test_mint_returns_none_on_http_error_status():
    resp = _mock_response(401, text="nope")
    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.post.return_value = resp
        assert identity_token._mint("http://backend.local", "aw", "host-token") is None


def test_mint_returns_none_on_transport_error():
    import httpx as httpx_mod

    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.post.side_effect = httpx_mod.ConnectError("boom")
        assert identity_token._mint("http://backend.local", "aw", "host-token") is None


def test_mint_returns_none_when_no_token_field():
    resp = _mock_response(200, {"nope": "field"})
    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.post.return_value = resp
        assert identity_token._mint("http://backend.local", "aw", "host-token") is None


def test_persist_posts_api_key_and_returns_true():
    resp = _mock_response(200, {"ok": True})
    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.post.return_value = resp
        assert identity_token._persist("http://workspace.local", "api-key", "tok") is True
    call = mock_client_cls.return_value.__enter__.return_value.post.call_args
    assert call.args[0] == "http://workspace.local/api/apps/skills/config"
    assert call.kwargs["json"] == {"config": {"identity_token": "tok"}}
    assert call.kwargs["headers"] == {"X-Api-Key": "api-key"}


def test_persist_returns_false_on_error_status():
    resp = _mock_response(403, text="forbidden")
    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.post.return_value = resp
        assert identity_token._persist("http://workspace.local", "api-key", "tok") is False


def test_persist_returns_false_on_transport_error():
    import httpx as httpx_mod

    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.return_value.__enter__.return_value.post.side_effect = httpx_mod.ConnectError("boom")
        assert identity_token._persist("http://workspace.local", "api-key", "tok") is False
