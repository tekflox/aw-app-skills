"""Unit tests for skills_app/plugin.py's SkillsAppPlugin — activate() wiring,
the identity-token watchdog registration, and on_config_saved()/deactivate().

``ctx`` is a lightweight double (MagicMock), not the real F4 runtime.

Run: pytest tests/test_plugin.py -q
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from skills_app import client
from skills_app.plugin import SkillsAppPlugin


def _make_ctx(config=None, has_watchdog=True):
    ctx = MagicMock()
    ctx.package_dir = tempfile.mkdtemp()
    ctx.config = config or {}
    ctx.has.return_value = has_watchdog
    return ctx


def test_activate_registers_routes_and_writes_mcp_json():
    ctx = _make_ctx()

    with patch("skills_app.identity_token.refresh", return_value=None):
        asyncio.run(SkillsAppPlugin().activate(ctx))

    ctx.routes.register.assert_called_once()
    assert (Path(ctx.package_dir) / "mcp.json").is_file()


def test_activate_configures_client_from_ctx_config():
    ctx = _make_ctx(config={"base_url": "http://custom.test", "identity_token": "tok"})

    with patch("skills_app.identity_token.refresh", return_value=None):
        asyncio.run(SkillsAppPlugin().activate(ctx))

    assert client.base_url() == "http://custom.test"
    assert client.configured() is True


def test_activate_absorbs_a_minted_token_into_live_config():
    ctx = _make_ctx(config={})
    plugin = SkillsAppPlugin()

    with patch("skills_app.identity_token.refresh", return_value="freshly-minted"):
        asyncio.run(plugin.activate(ctx))

    assert plugin._live_config["identity_token"] == "freshly-minted"
    assert client.configured() is True


def test_activate_survives_identity_token_refresh_raising():
    ctx = _make_ctx()

    with patch("skills_app.identity_token.refresh", side_effect=RuntimeError("down")):
        # must not raise
        asyncio.run(SkillsAppPlugin().activate(ctx))

    ctx.routes.register.assert_called_once()


def test_watchdog_registered_when_capability_granted():
    ctx = _make_ctx(has_watchdog=True)

    with patch("skills_app.identity_token.refresh", return_value=None):
        asyncio.run(SkillsAppPlugin().activate(ctx))

    ctx.watchdog.register.assert_called_once()
    name, fn, interval = ctx.watchdog.register.call_args.args
    assert name == "identity-token-refresh"
    assert ctx.watchdog.register.call_args.kwargs["run_immediately"] is True


def test_watchdog_not_registered_without_capability():
    ctx = _make_ctx(has_watchdog=False)

    with patch("skills_app.identity_token.refresh", return_value=None):
        asyncio.run(SkillsAppPlugin().activate(ctx))

    ctx.watchdog.register.assert_not_called()


def test_watchdog_tick_updates_live_config_on_refresh():
    ctx = _make_ctx(has_watchdog=True)
    plugin = SkillsAppPlugin()

    with patch("skills_app.identity_token.refresh", return_value=None):
        asyncio.run(plugin.activate(ctx))

    _, tick_fn, _ = ctx.watchdog.register.call_args.args
    with patch("skills_app.identity_token.refresh", return_value="rotated"):
        asyncio.run(tick_fn())
    assert plugin._live_config["identity_token"] == "rotated"


def test_watchdog_tick_survives_refresh_raising():
    ctx = _make_ctx(has_watchdog=True)
    plugin = SkillsAppPlugin()

    with patch("skills_app.identity_token.refresh", return_value=None):
        asyncio.run(plugin.activate(ctx))

    _, tick_fn, _ = ctx.watchdog.register.call_args.args
    with patch("skills_app.identity_token.refresh", side_effect=RuntimeError("boom")):
        asyncio.run(tick_fn())  # must not raise


def test_on_config_saved_refreshes_live_config():
    ctx = _make_ctx(config={"base_url": "http://a.test"})
    plugin = SkillsAppPlugin()

    with patch("skills_app.identity_token.refresh", return_value=None):
        asyncio.run(plugin.activate(ctx))

    ctx.config = {"base_url": "http://b.test"}
    asyncio.run(plugin.on_config_saved(ctx))
    assert plugin._live_config["base_url"] == "http://b.test"


def test_deactivate_completes_without_touching_ctx():
    assert asyncio.run(SkillsAppPlugin().deactivate()) is None
