"""Entrypoint referenced by ``aw-app.json``'s ``runtime.entrypoint``
("skills_app.plugin:SkillsAppPlugin").

This app is a pure HTTP client of skills.aw's REST API (fredericowu/
aw-skills) — no DB driver, no duplicated versioning/tenant logic, just the
4 tools in ``client.py``. Unlike the Knowledgeable Connector, there is no
secret for a human to paste into Settings: this app mints and refreshes its
own per-workspace identity JWT (``identity_token.py``), the same
mint-then-persist shape ``agents_platform_runners_app`` already uses for
its own ``agents_platform_token``.

Also the home for the fs-sync-like materializer (Fase 4 of the aw-skills
build) — see ``materializer.py``'s module docstring; deliberately not
wired in here yet.
"""
from __future__ import annotations

import asyncio
import logging
import os

from . import client, identity_token, materializer, mcp_config
from . import routes as routes_mod

log = logging.getLogger("aw_apps.skills")

#: How often the identity-token watchdog checks for staleness. Short
#: relative to agents-platform-runners' 6h: aw-backend's own identity-token
#: mint (``routes/workspace_identity_token.py``) caps this token at a 24h
#: default TTL (vs. that app's 30-day one), so ``needs_refresh()``'s
#: half-life check trips at ~12h — this just has to tick comfortably more
#: often than that.
IDENTITY_TOKEN_INTERVAL_S = 1.0 * 3600.0


class SkillsAppPlugin:
    def __init__(self) -> None:
        self._live_config: dict = {}

    def _refresh_derived_config(self, ctx) -> dict:
        """Refreshes ``self._live_config`` in place (never rebind — the
        closures handed to ``client.configure`` below capture this one dict
        object, same pattern as ``agents_platform_runners_app/plugin.py``'s
        own ``_live_config``)."""
        config = dict(ctx.config or {})
        self._live_config.clear()
        self._live_config.update(config)
        return self._live_config

    async def activate(self, ctx) -> None:
        self._refresh_derived_config(ctx)

        client.configure(
            base_url_resolver=lambda: self._live_config.get("base_url") or client.DEFAULT_BASE_URL,
            token_resolver=lambda: self._live_config.get("identity_token") or "",
        )

        # Non-fatal at activation — see identity_token.refresh()'s own
        # docstring. A missed mint here is recovered by the watchdog below.
        try:
            refreshed = identity_token.refresh(self._live_config)
            if refreshed:
                self._live_config["identity_token"] = refreshed
        except Exception:  # noqa: BLE001 — must never block activation
            log.warning("identity_token: refresh failed at activation", exc_info=True)

        ctx.routes.register(routes_mod.build_routes())

        port = int(os.environ.get("AW_PORT") or 9030)
        doc = mcp_config.write_mcp_json(ctx.package_dir, port)

        self._register_identity_token_watchdog(ctx)
        self._register_materializer_watchdog(ctx)

        log.info(
            "aw-app-skills activated: mcp server=%s, tools=%s, base_url=%s, token=%s",
            sorted(doc["mcpServers"]), len(client.TOOLS_SCHEMA), client.base_url(),
            "minted" if client.configured() else "NOT SET (watchdog will mint one)",
        )

    def _register_identity_token_watchdog(self, ctx) -> None:
        """Lease-leader gated (``src/apps/watchdog.py``), so exactly one
        worker runs it — a bare background task would run on every
        ``AW_WORKSPACE_WORKERS`` worker and race writing the config, same
        reasoning as ``agents_platform_runners_app``'s identical watchdog."""
        if not ctx.has("watchdog:tasks"):
            log.warning("identity_token: 'watchdog:tasks' capability not granted — "
                        "refresh watchdog not started")
            return

        async def _refresh() -> None:
            try:
                refreshed = await asyncio.to_thread(identity_token.refresh, self._live_config)
            except Exception:  # noqa: BLE001 — a watchdog tick must never raise
                log.warning("identity_token: refresh watchdog tick failed", exc_info=True)
                return
            if refreshed:
                self._live_config["identity_token"] = refreshed
                log.info("identity_token: refreshed identity_token")

        ctx.watchdog.register(
            "identity-token-refresh", _refresh, IDENTITY_TOKEN_INTERVAL_S, run_immediately=True
        )
        log.info("identity_token: watchdog registered (every %.0fs)", IDENTITY_TOKEN_INTERVAL_S)

    def _register_materializer_watchdog(self, ctx) -> None:
        """Fase 4 of the aw-skills build — pulls skills.aw's manifest into
        this app's own read-only cache and, on any change, triggers the
        existing ``agent sync`` fan-out so ``skills/`` and every per-CLI
        mirror pick it up. See ``materializer.py``'s module docstring for
        the full mechanism and why it does not reimplement
        ``materialize()``."""
        if not ctx.has("watchdog:tasks"):
            log.warning("materializer: 'watchdog:tasks' capability not granted — "
                        "skills materializer watchdog not started")
            return

        async def _tick() -> None:
            try:
                result = await materializer.sync_tick()
            except Exception:  # noqa: BLE001 — a watchdog tick must never raise
                log.warning("materializer: sync tick failed", exc_info=True)
                return
            if result.get("changed") or not result.get("ok"):
                log.info("materializer: %s", result)

        ctx.watchdog.register(
            "skills-materializer", _tick, materializer.MATERIALIZER_INTERVAL_S, run_immediately=True
        )
        log.info("materializer: watchdog registered (every %.0fs)",
                  materializer.MATERIALIZER_INTERVAL_S)

    async def list_skill_sources(self, ctx) -> dict:
        """``src/apps/skill_sources.py``'s pull hook. Names the one
        directory the materializer watchdog above keeps in sync with
        skills.aw's manifest; ``materialize()`` re-scans its CONTENTS fresh
        on every sync, so this only has to point at it once, here, at
        activation — it does not need to be re-asserted on every tick."""
        return {"ok": True, "dirs": [str(materializer.materialized_dir())]}

    async def on_config_saved(self, ctx) -> None:
        self._refresh_derived_config(ctx)

    async def deactivate(self) -> None:
        log.info("aw-app-skills deactivated")
