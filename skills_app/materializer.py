"""Home for the fs-sync-like materializer that pulls skills.aw content into
this workspace's local ``skills/`` tree — Fase 4 of the aw-skills build
(DEPENDS on this app existing, and on Fase 1's ``GET /api/sync/manifest``,
which already ships in fredericowu/aw-skills today).

Deliberately empty of logic and **not called from ``plugin.py``**: this
phase (Fase 2) only has to leave the structure ready to receive that
watchdog, not implement it. When Fase 4 lands, the expected shape — mirroring
``aw-app-knowledgeable/knowledgeable_app/fs_sync.py`` — is:

- a ``sync_tick()`` coroutine that calls ``GET /api/sync/manifest`` through
  ``client.py``'s own base_url/token resolvers (no new HTTP client, no new
  credential),
- diffs the manifest's ``{slug, latest_version, content_hash}`` entries
  against a local cache under ``AW_WORKSPACE_HOME/data/skills/``,
- materializes changed/new skills into this workspace's ``skills/`` tree
  (read-only, mirroring fs-sync's own ``.aw-skill-remote`` marker +
  ``chmod a-w`` convention) and removes ones no longer in the manifest,
- registered from ``plugin.activate`` via
  ``ctx.watchdog.register("skills-materializer", sync_tick, INTERVAL_S)``,
  gated on the same ``watchdog:tasks`` capability this app already
  declares.
"""
from __future__ import annotations
