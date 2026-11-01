"""Unit tests for skills_app/workspace_env.py.

Run: pytest tests/test_workspace_env.py -q
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from skills_app.workspace_env import workspace_env


def test_reads_from_os_environ_first(monkeypatch):
    monkeypatch.setenv("AW_TEST_VAR", "from-environ")
    assert workspace_env("AW_TEST_VAR") == "from-environ"


def test_falls_back_to_dotenv_file(monkeypatch):
    monkeypatch.delenv("AW_TEST_VAR", raising=False)
    with tempfile.TemporaryDirectory() as home:
        (Path(home) / ".env").write_text("AW_TEST_VAR=from-dotenv\nOTHER=1\n")
        monkeypatch.setenv("AW_WORKSPACE_HOME", home)
        assert workspace_env("AW_TEST_VAR") == "from-dotenv"


def test_returns_empty_string_when_nowhere_to_find_it(monkeypatch):
    monkeypatch.delenv("AW_TEST_VAR_MISSING", raising=False)
    with tempfile.TemporaryDirectory() as home:
        monkeypatch.setenv("AW_WORKSPACE_HOME", home)
        assert workspace_env("AW_TEST_VAR_MISSING") == ""
