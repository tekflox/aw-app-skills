"""The fs-sync-like materializer that pulls skills.aw content into this
workspace's local ``skills/`` tree — Fase 4 of the aw-skills build.

Mechanism, end to end:

1. ``sync_tick()`` (run by a watchdog registered in ``plugin.py``, every
   ``MATERIALIZER_INTERVAL_S``) calls ``GET /api/sync/manifest`` through
   ``client.py``'s own base_url/token resolvers — no new HTTP client, no new
   credential. Only downloads content whose ``(version, content_hash)``
   changed since the last tick's ack (``manifest_ack.json`` under this app's
   own ``paths.data_dir()`` — survives a restart, same pattern as the
   runners app's ``skills_sync_ack.json``).
2. Downloaded content is verified against the manifest's own
   ``content_hash`` (:func:`_hash_files`, byte-identical to aw-skills'
   ``hash_skill_content`` / aw-workspace's own ``SkillsRegistry._hash_dir``)
   before being cached — a partial/corrupted download must never get
   written into the cache the rest of this pipeline trusts.
3. Materialization into the actual ``skills/`` tree (and from there into
   ``.claude/skills``, ``.cursor/skills``, ``.gemini/skills``) is NOT
   reimplemented here. ``src/apps/skill_sources.py``'s existing "pull" hook
   — built for exactly this shape of problem ("a skill that only exists
   after install") — already does it: ``plugin.py``'s
   ``list_skill_sources()`` names :func:`materialized_dir` once, at
   activation, and ``src/libs/skills_sync.py``'s ``_materialize_app_sources``
   then re-scans that directory's CONTENTS fresh on every
   ``materialize()`` call, with exact-mirror add/update/delete semantics,
   ``.aw-app-id``/``.aw-skill-source`` ownership markers, and collision
   detection already built and tested. ``shutil.copy2`` (which that pass
   uses) preserves the read-only mode this module stamps onto every
   materialized file, so "read-only in ``skills/``" falls out of the
   existing pull mechanism for free — no new marker, no parallel delete
   pass.
4. ``materialize()`` itself only runs at boot, on an explicit
   ``POST /api/agent/sync`` (the "Sync now" action), or on native skill
   CRUD — there is no periodic core watchdog driving it. So every tick that
   actually changed the cache also calls :func:`trigger_agent_sync`, which
   POSTs to that same existing endpoint using the ``X-Api-Key`` workspace
   credential — the same credential/pattern ``identity_token.py`` already
   uses to persist a refreshed token. This keeps the Tier-1 boundary intact
   (no importing core's ``src`` tree, which this app's own repo/CI does not
   have) while still reaching the ~3min autonomous cadence the design calls
   for.

**Catastrophic-manifest guard**: a manifest that comes back with zero
skills right after a tick that saw N>0 is treated as an explicit error
(logged, nothing cached or deleted), never as "every skill was deleted" —
the exact lesson the Knowledgeable Connector's fs-sync valve already paid
for. aw-skills being unreachable is handled the same way: the last known
cache keeps serving, stale rather than empty.
"""
from __future__ import annotations

import filecmp
import json
import logging
import os
import shutil
from pathlib import Path

from . import client
from .paths import data_dir
from .workspace_env import workspace_env

log = logging.getLogger("aw_apps.skills.materializer")

#: ``plugin.py``'s watchdog tick interval — ~3 min, per the Fase 4 card.
MATERIALIZER_INTERVAL_S = 3.0 * 60.0

CACHE_DIRNAME = "materialized"
ACK_FILENAME = "manifest_ack.json"
AGENT_SYNC_TIMEOUT_S = 60.0

#: Markers ``_materialize_app_sources`` (src/libs/skills_sync.py) stamps
#: onto every entry it pulls from this app's cache — never part of the
#: content a skill's own ``content_hash`` covers, so :func:`doctor_report`
#: must ignore them when comparing what's actually in ``skills/`` to what
#: this app cached.
_OWNER_MARKER = ".aw-app-id"
_SOURCE_MARKER = ".aw-skill-source"
_IGNORED_MARKERS = (_OWNER_MARKER, _SOURCE_MARKER)


def materialized_dir() -> Path:
    """The one directory this app registers via ``list_skill_sources()`` —
    one subdirectory per skill, each a plain ``SKILL.md`` (+ any assets)."""
    d = data_dir() / CACHE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ack_path() -> Path:
    return data_dir() / ACK_FILENAME


def _load_ack() -> dict:
    try:
        with open(_ack_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("skills"), dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"state_hash": None, "skills": {}}


def _save_ack(state_hash: str, skills: dict) -> None:
    path = _ack_path()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"state_hash": state_hash, "skills": skills}, f)
    os.replace(tmp, path)  # atomic


def _hash_files(files: dict[str, bytes]) -> str:
    """Sorted relpath + bytes, sha256 — byte-identical to aw-skills' own
    ``hash_skill_content`` (``backend/app/core/hashing.py``), so a hash
    computed here from a download can be checked against the manifest's
    ``content_hash`` without ever trusting the transport."""
    import hashlib

    hasher = hashlib.sha256()
    for relpath in sorted(files):
        hasher.update(relpath.encode())
        hasher.update(files[relpath])
    return hasher.hexdigest()


def _write_skill(dest: Path, content: str, assets: dict | None) -> None:
    """Writes one skill's materialized copy, then chmods every file
    read-only. A full rewrite (not a diff) — this only runs when the
    manifest's ``(version, content_hash)`` actually changed, so there is no
    stale-asset case to reconcile: whatever was here before this call is
    exactly what the previous version looked like, and this version may
    have dropped files the old one had."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    files = {"SKILL.md": content}
    files.update(assets or {})
    for relpath, text in files.items():
        path = dest / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for path in dest.rglob("*"):
        if path.is_file():
            os.chmod(path, 0o444)


def trigger_agent_sync() -> bool:
    """``POST /api/agent/sync`` — the existing fan-out
    (``materialize()`` + the per-CLI mirror copy) this app has no business
    reimplementing. Same ``X-Api-Key`` workspace credential
    ``identity_token.py`` already uses to persist a refreshed token: a
    Tier-1 app reaches the host through its own HTTP API, never by
    importing core's ``src`` tree."""
    import httpx

    api_url = workspace_env("AW_WORKSPACE_API_URL")
    api_key = workspace_env("AW_WORKSPACE_API_KEY")
    if not api_url or not api_key:
        log.warning("materializer: AW_WORKSPACE_API_URL/AW_WORKSPACE_API_KEY not set — "
                    "cannot trigger agent sync; skills/ stays stale until the next one runs")
        return False
    try:
        with httpx.Client(timeout=AGENT_SYNC_TIMEOUT_S) as http_client:
            resp = http_client.post(f"{api_url.rstrip('/')}/api/agent/sync",
                                     headers={"X-Api-Key": api_key})
        resp.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("materializer: POST /api/agent/sync failed: %s", exc)
        return False


async def sync_tick() -> dict:
    """One bounded tick. Never raises — called from a watchdog tick with
    nothing to catch it."""
    manifest, err = await client.get_manifest()
    if err:
        log.warning("materializer: GET /api/sync/manifest failed: %s — "
                    "serving the last known cache, not wiping it", err)
        return {"ok": False, "reason": err}

    entries = (manifest or {}).get("skills") or []
    state_hash = (manifest or {}).get("state_hash")
    ack = _load_ack()
    prev_skills = ack.get("skills") or {}

    if state_hash == ack.get("state_hash"):
        return {"ok": True, "changed": False, "skill_count": len(prev_skills)}

    if not entries and prev_skills:
        log.error(
            "materializer: manifest went from %d skill(s) to 0 in one tick — "
            "treating this as an erroring/transient response, NOT a real mass "
            "deletion; the existing cache and skills/ are left untouched",
            len(prev_skills),
        )
        return {"ok": False, "reason": "empty manifest after a non-empty previous state"}

    cache = materialized_dir()
    new_skills: dict[str, dict] = {}
    added: list[str] = []
    updated: list[str] = []
    failed: list[str] = []

    for entry in entries:
        slug = entry.get("slug")
        version = entry.get("latest_version")
        content_hash = entry.get("content_hash")
        if not slug or version is None or not content_hash:
            continue

        prior = prev_skills.get(slug)
        if prior and prior.get("version") == version and prior.get("content_hash") == content_hash:
            new_skills[slug] = prior
            continue

        data, cerr = await client.get_content(slug, version)
        if cerr or data is None:
            log.warning("materializer: could not fetch %s v%s: %s", slug, version, cerr)
            failed.append(slug)
            if prior:
                new_skills[slug] = prior  # keep serving the last good copy
            continue

        content = data.get("content") or ""
        assets = {
            k: (v if isinstance(v, str) else str(v))
            for k, v in (data.get("assets") or {}).items()
        }
        files = {"SKILL.md": content.encode(), **{k: v.encode() for k, v in assets.items()}}
        computed_hash = _hash_files(files)
        if computed_hash != content_hash:
            log.warning(
                "materializer: %s v%s content_hash mismatch (got %s, manifest says %s) — "
                "not caching a possibly-corrupted download", slug, version, computed_hash, content_hash,
            )
            failed.append(slug)
            if prior:
                new_skills[slug] = prior
            continue

        _write_skill(cache / slug, content, assets)
        new_skills[slug] = {"version": version, "content_hash": content_hash}
        (added if prior is None else updated).append(slug)

    removed = sorted(set(prev_skills) - set(new_skills))
    for slug in removed:
        shutil.rmtree(cache / slug, ignore_errors=True)

    _save_ack(state_hash, new_skills)

    changed = bool(added or updated or removed)
    if changed:
        trigger_agent_sync()

    return {
        "ok": not failed, "changed": changed, "skill_count": len(new_skills),
        "added": added, "updated": updated, "removed": removed, "failed": failed,
    }


def _workspace_skills_dir() -> Path:
    """Same resolution as aw-workspace core's own
    ``src/apps/paths.py::skills_dir()``, reimplemented rather than imported
    — this app has no ``src/`` tree of its own to import from (see
    ``workspace_env.py``'s own docstring for the same rule)."""
    root = os.path.realpath(workspace_env("AW_WORKSPACE_CONTAINER_DIR") or "/opt/aw-workspace")
    return Path(root) / "skills"


def _relative_files(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and p.name not in _IGNORED_MARKERS
    }


def doctor_report() -> dict:
    """``contributes.doctor`` entry: flags a skill whose copy in the real
    ``skills/<slug>/`` tree no longer matches what this app last cached from
    aw-skills — a local edit that will be silently overwritten the next
    time ``materialize()`` runs (the ``.aw-skill-source``-owned exact-mirror
    semantics ``src/libs/skills_sync.py`` already documents). Distinct from
    a skill simply not materialized yet (``AW_WORKSPACE`` restarted mid-tick,
    or the next ``agent sync`` hasn't run) — that is reported separately,
    not conflated with drift."""
    cache = materialized_dir()
    ws_root = _workspace_skills_dir()
    drifted: list[str] = []
    not_yet_synced: list[str] = []

    for slug_dir in sorted(p for p in cache.iterdir() if p.is_dir()):
        slug = slug_dir.name
        dest = ws_root / slug
        if not dest.is_dir():
            not_yet_synced.append(slug)
            continue
        for relpath in _relative_files(slug_dir):
            src_file, dest_file = slug_dir / relpath, dest / relpath
            if not dest_file.is_file() or not filecmp.cmp(src_file, dest_file, shallow=False):
                drifted.append(slug)
                break

    return {
        "ok": not drifted,
        "detail": {
            "cached_skills": sum(1 for p in cache.iterdir() if p.is_dir()),
            "not_yet_synced": not_yet_synced,
            "drifted": [
                f"{slug} differs from the aw-skills cache — any local edit will be "
                "overwritten on the next sync" for slug in drifted
            ],
        },
    }
