"""Unit tests for skills_app/mcp_config.py and skills_app/mcp/self_register.py.

Run: pytest tests/test_mcp_config.py -q
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from skills_app import mcp_config
from skills_app.mcp import self_register


def test_build_self_entry_uses_hostname_and_port():
    entry = self_register.build_self_entry(port=1234)
    assert entry["url"].endswith(":1234/api/apps/skills/mcp")
    assert entry["type"] == "http"
    assert entry["enabled"] is True


def test_build_self_entry_includes_api_key_header_when_set(monkeypatch):
    monkeypatch.setenv("AW_WORKSPACE_API_KEY", "secret-key")
    entry = self_register.build_self_entry(port=1234)
    assert entry["headers"] == {"X-Api-Key": "secret-key"}


def test_build_self_entry_omits_headers_when_no_api_key(monkeypatch):
    monkeypatch.delenv("AW_WORKSPACE_API_KEY", raising=False)
    entry = self_register.build_self_entry(port=1234)
    assert "headers" not in entry


def test_build_mcp_servers_keyed_by_skills():
    doc = mcp_config.build_mcp_servers(port=9999)
    assert set(doc.keys()) == {"skills"}


def test_write_mcp_json_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        doc = mcp_config.write_mcp_json(tmp, port=9999)
        path = Path(tmp) / "mcp.json"
        assert path.is_file()
        assert json.loads(path.read_text()) == doc


def test_write_mcp_json_is_idempotent_skip_on_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        mcp_config.write_mcp_json(tmp, port=9999)
        path = Path(tmp) / "mcp.json"
        first_mtime = path.stat().st_mtime_ns
        mcp_config.write_mcp_json(tmp, port=9999)
        assert path.stat().st_mtime_ns == first_mtime
