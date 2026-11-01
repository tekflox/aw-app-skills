"""Unit tests for skills_app/materializer.py — Fase 4 of the aw-skills
build: the manifest pull, the cache/ack bookkeeping, the catastrophic-empty-
manifest guard, and the doctor drift check.

Run: pytest tests/test_materializer.py -q
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from skills_app import materializer


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch):
    """Every test gets its own throwaway ``data_dir()`` — never touch the
    real ``.aw-workspace/data/skills/``."""
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(materializer, "data_dir", lambda: tmp)
    yield tmp


def _hash_for(content: str, assets: dict | None = None) -> str:
    files = {"SKILL.md": content.encode()}
    files.update({k: v.encode() for k, v in (assets or {}).items()})
    return materializer._hash_files(files)


def _manifest(entries):
    skills = [{"slug": s, "latest_version": v, "content_hash": h} for s, v, h in entries]
    state_src = "|".join(f"{s}:{v}:{h}" for s, v, h in entries)
    import hashlib

    return {"skills": skills, "state_hash": hashlib.sha256(state_src.encode()).hexdigest()}


def test_materialized_dir_is_created():
    d = materializer.materialized_dir()
    assert d.is_dir()
    assert d.name == materializer.CACHE_DIRNAME


def test_sync_tick_reports_error_and_keeps_cache_on_manifest_fetch_failure():
    with patch.object(materializer.client, "get_manifest", return_value=(None, "down")):
        result = asyncio.run(materializer.sync_tick())
    assert result == {"ok": False, "reason": "down"}


def test_sync_tick_is_a_noop_when_state_hash_unchanged():
    manifest = _manifest([("foo", 1, _hash_for("content"))])
    materializer._save_ack(manifest["state_hash"], {"foo": {"version": 1, "content_hash": manifest["skills"][0]["content_hash"]}})

    with patch.object(materializer.client, "get_manifest", return_value=(manifest, None)), \
         patch.object(materializer.client, "get_content") as get_content, \
         patch.object(materializer, "trigger_agent_sync") as trigger:
        result = asyncio.run(materializer.sync_tick())

    assert result["changed"] is False
    get_content.assert_not_called()
    trigger.assert_not_called()


def test_sync_tick_downloads_and_caches_a_new_skill_read_only():
    content_hash = _hash_for("# foo\nbody")
    manifest = _manifest([("foo", 1, content_hash)])

    with patch.object(materializer.client, "get_manifest", return_value=(manifest, None)), \
         patch.object(materializer.client, "get_content",
                       return_value=({"content": "# foo\nbody", "assets": {}}, None)), \
         patch.object(materializer, "trigger_agent_sync") as trigger:
        result = asyncio.run(materializer.sync_tick())

    assert result["added"] == ["foo"]
    assert result["ok"] is True
    skill_md = materializer.materialized_dir() / "foo" / "SKILL.md"
    assert skill_md.read_text(encoding="utf-8") == "# foo\nbody"
    assert (skill_md.stat().st_mode & 0o222) == 0  # chmod a-w
    trigger.assert_called_once()

    ack = materializer._load_ack()
    assert ack["skills"]["foo"] == {"version": 1, "content_hash": content_hash}


def test_sync_tick_writes_assets_alongside_skill_md():
    assets = {"reference.md": "extra detail"}
    content_hash = _hash_for("# foo", assets)
    manifest = _manifest([("foo", 1, content_hash)])

    with patch.object(materializer.client, "get_manifest", return_value=(manifest, None)), \
         patch.object(materializer.client, "get_content",
                       return_value=({"content": "# foo", "assets": assets}, None)), \
         patch.object(materializer, "trigger_agent_sync"):
        asyncio.run(materializer.sync_tick())

    asset_path = materializer.materialized_dir() / "foo" / "reference.md"
    assert asset_path.read_text(encoding="utf-8") == "extra detail"


def test_sync_tick_skips_caching_on_content_hash_mismatch():
    manifest = _manifest([("foo", 1, "deadbeef" * 8)])  # a hash that can't match

    with patch.object(materializer.client, "get_manifest", return_value=(manifest, None)), \
         patch.object(materializer.client, "get_content",
                       return_value=({"content": "# foo", "assets": {}}, None)), \
         patch.object(materializer, "trigger_agent_sync") as trigger:
        result = asyncio.run(materializer.sync_tick())

    assert result["failed"] == ["foo"]
    assert not (materializer.materialized_dir() / "foo").exists()
    trigger.assert_not_called()  # nothing actually changed


def test_sync_tick_keeps_last_good_copy_when_download_errors():
    content_hash = _hash_for("# foo v1")
    materializer._write_skill(materializer.materialized_dir() / "foo", "# foo v1", {})
    materializer._save_ack("old-hash", {"foo": {"version": 1, "content_hash": content_hash}})

    new_manifest = _manifest([("foo", 2, "irrelevant-since-fetch-fails")])
    with patch.object(materializer.client, "get_manifest", return_value=(new_manifest, None)), \
         patch.object(materializer.client, "get_content", return_value=(None, "500")), \
         patch.object(materializer, "trigger_agent_sync"):
        result = asyncio.run(materializer.sync_tick())

    assert result["failed"] == ["foo"]
    assert (materializer.materialized_dir() / "foo" / "SKILL.md").read_text(encoding="utf-8") == "# foo v1"
    # the kept copy still carries its OLD version/hash — a failed fetch must
    # never advance the ack to the version it couldn't actually get.
    ack = materializer._load_ack()
    assert ack["skills"]["foo"]["version"] == 1


def test_sync_tick_removes_a_skill_dropped_from_the_manifest():
    # An empty manifest with a non-empty previous state is the catastrophic
    # guard's own case (tested separately below) — this uses a surviving
    # skill alongside the removed one so it exercises plain removal instead.
    materializer._write_skill(materializer.materialized_dir() / "gone", "# gone", {})
    survivor_hash = _hash_for("# stays")
    materializer._save_ack("old-hash", {
        "gone": {"version": 1, "content_hash": "x"},
        "stays": {"version": 1, "content_hash": survivor_hash},
    })
    materializer._write_skill(materializer.materialized_dir() / "stays", "# stays", {})
    manifest = _manifest([("stays", 1, survivor_hash)])

    with patch.object(materializer.client, "get_manifest", return_value=(manifest, None)), \
         patch.object(materializer, "trigger_agent_sync") as trigger:
        result = asyncio.run(materializer.sync_tick())

    assert result["removed"] == ["gone"]
    assert not (materializer.materialized_dir() / "gone").exists()
    assert (materializer.materialized_dir() / "stays").is_dir()
    trigger.assert_called_once()


def test_sync_tick_guards_against_empty_manifest_after_nonempty_previous():
    materializer._write_skill(materializer.materialized_dir() / "foo", "# foo", {})
    materializer._save_ack("old-hash", {"foo": {"version": 1, "content_hash": "x"}})

    with patch.object(materializer.client, "get_manifest", return_value=(_manifest([]), None)), \
         patch.object(materializer, "trigger_agent_sync") as trigger:
        result = asyncio.run(materializer.sync_tick())

    assert result["ok"] is False
    assert "empty manifest" in result["reason"]
    # nothing was wiped — neither the cache nor the ack record.
    assert (materializer.materialized_dir() / "foo").is_dir()
    assert materializer._load_ack()["skills"]["foo"]["version"] == 1
    trigger.assert_not_called()


def test_sync_tick_accepts_a_genuinely_empty_manifest_with_no_prior_state():
    with patch.object(materializer.client, "get_manifest", return_value=(_manifest([]), None)), \
         patch.object(materializer, "trigger_agent_sync") as trigger:
        result = asyncio.run(materializer.sync_tick())

    assert result["ok"] is True
    assert result["skill_count"] == 0
    trigger.assert_not_called()


def test_trigger_agent_sync_posts_with_api_key(monkeypatch):
    monkeypatch.setattr(materializer, "workspace_env",
                         lambda name: {"AW_WORKSPACE_API_URL": "http://core.test",
                                       "AW_WORKSPACE_API_KEY": "secret"}.get(name, ""))

    calls = []

    class _FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None):
            calls.append((url, headers))

            class _Resp:
                def raise_for_status(self):
                    return None
            return _Resp()

    with patch("httpx.Client", _FakeClient):
        assert materializer.trigger_agent_sync() is True

    assert calls == [("http://core.test/api/agent/sync", {"X-Api-Key": "secret"})]


def test_trigger_agent_sync_returns_false_without_credentials(monkeypatch):
    monkeypatch.setattr(materializer, "workspace_env", lambda name: "")
    assert materializer.trigger_agent_sync() is False


def test_doctor_report_ok_when_workspace_copy_matches_cache(monkeypatch, tmp_path):
    ws_root = tmp_path / "skills"
    monkeypatch.setattr(materializer, "_workspace_skills_dir", lambda: ws_root)

    materializer._write_skill(materializer.materialized_dir() / "foo", "# foo", {})
    (ws_root / "foo").mkdir(parents=True)
    (ws_root / "foo" / "SKILL.md").write_text("# foo", encoding="utf-8")
    (ws_root / "foo" / ".aw-app-id").write_text("skills", encoding="utf-8")  # ignored marker

    report = materializer.doctor_report()
    assert report["ok"] is True
    assert report["detail"]["drifted"] == []


def test_doctor_report_flags_a_locally_edited_skill(monkeypatch, tmp_path):
    ws_root = tmp_path / "skills"
    monkeypatch.setattr(materializer, "_workspace_skills_dir", lambda: ws_root)

    materializer._write_skill(materializer.materialized_dir() / "foo", "# foo", {})
    (ws_root / "foo").mkdir(parents=True)
    (ws_root / "foo" / "SKILL.md").write_text("# foo EDITED BY HAND", encoding="utf-8")

    report = materializer.doctor_report()
    assert report["ok"] is False
    assert any("foo" in line for line in report["detail"]["drifted"])


def test_doctor_report_distinguishes_not_yet_synced_from_drift(monkeypatch, tmp_path):
    ws_root = tmp_path / "skills"  # deliberately never created
    monkeypatch.setattr(materializer, "_workspace_skills_dir", lambda: ws_root)

    materializer._write_skill(materializer.materialized_dir() / "foo", "# foo", {})

    report = materializer.doctor_report()
    assert report["ok"] is True  # not-yet-synced is not drift
    assert report["detail"]["not_yet_synced"] == ["foo"]
    assert report["detail"]["drifted"] == []
