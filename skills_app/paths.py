"""Where this app keeps state that must survive a container recreation.

Same location and reasoning as ``aw-app-plaud``/``aw-app-google-workspace-mcp``'s
own ``paths.py``: ``$AW_WORKSPACE_HOME/data/skills/`` is durable across an app
update (an update is uninstall+install, which takes ``ctx.package_dir`` with
it) and across a container recreate.

Nothing here imports the host runtime (``src.apps.paths``): a Tier-1 app only
touches the host through its ``ctx`` facades and plain env vars, and this
repo has no ``src/`` tree of its own to import from in CI (see
``workspace_env.py``'s own docstring for the same rule).
"""
from __future__ import annotations

import os
from pathlib import Path

APP_ID = "skills"
DEFAULT_WORKSPACE_HOME = "/opt/aw-workspace/.aw-workspace"


def workspace_home() -> Path:
    return Path(os.environ.get("AW_WORKSPACE_HOME") or DEFAULT_WORKSPACE_HOME)


def data_dir() -> Path:
    path = workspace_home() / "data" / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path
