"""Mint + persist a per-workspace identity JWT for this connector's own use.

Mirrors ``agents_platform_runners_app/identity_token.py``'s design — same
two-hop mint-then-persist shape, same "credentials are re-asserted, content
is seeded once" rule. This app needs its own copy rather than a shared
field: aw-skills' own auth model (DESIGN.md §6.3,
``backend/app/core/identity.py`` in fredericowu/aw-skills) is a
per-workspace *membership* JWT, not a shared secret like
aw-knowledgeable's ``X-Internal-Secret`` — so unlike that connector, there
is no secret for a human to paste into Settings at all. This module is the
entire credential story for this app.

Two hops, deliberately not one:

1. **Mint** — ``POST {AW_BACKEND_URL}/api/workspaces/{AW_WORKSPACE}/identity-token``
   with ``Authorization: Bearer {AW_WORKSPACE_HOST_TOKEN}``. No
   ``expiry_seconds`` sent — aw-backend's own default (24h,
   ``routes/workspace_identity_token.py``) is deliberately short for a
   machine-held credential, and the minted token's ``memberships`` claim
   names exactly this one workspace, which is what lets aw-skills scope
   reads/writes to it without this connector ever sending a tenant header.
2. **Persist** — ``POST {AW_WORKSPACE_API_URL}/api/apps/skills/config``
   with ``X-Api-Key: {AW_WORKSPACE_API_KEY}``. This is the config-save
   path, not an in-memory mutation: it is what makes a refreshed token
   survive this process restarting, and ``plugin.py`` reads it back out of
   ``ctx.config`` on the next activation.

Both hops, and the decode below, never raise past :func:`refresh` — called
from ``activate()`` (non-fatal by design) and from a watchdog tick, where an
exception is a stack trace nobody reads.
"""
from __future__ import annotations

import base64
import json
import logging
import time

import httpx

from .workspace_env import workspace_env

log = logging.getLogger("aw_apps.skills.identity_token")

CONFIG_SLUG = "skills"
CONFIG_FIELD = "identity_token"
TIMEOUT_S = 20.0

#: Refresh once the token has used up this fraction of its lifetime — not a
#: fixed days-left threshold, same reasoning as the runners app's own
#: identical constant. Trips at ~12h on aw-backend's 24h default TTL (vs.
#: that app's ~15 days of headroom on a 30-day token); the watchdog interval
#: (``plugin.py``'s ``IDENTITY_TOKEN_INTERVAL_S``) just needs to tick
#: comfortably more often than that.
HALF_LIFE_FRACTION = 0.5


def _decode_claims(token: str) -> dict | None:
    """``{"iat": ..., "exp": ...}`` decoded from the JWT payload WITHOUT
    verifying its signature — this module is deciding whether to refresh,
    not authenticating anyone, and holds no public key to check against
    anyway. Returns None on anything that isn't a 3-part JWT with numeric
    iat/exp claims."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        return {"iat": float(claims["iat"]), "exp": float(claims["exp"])}
    except Exception:
        return None


def needs_refresh(token: str | None, *, now: float | None = None) -> bool:
    """Missing, unparseable, expired, or past half its lifetime."""
    if not token:
        return True
    claims = _decode_claims(token)
    if claims is None:
        return True
    now = time.time() if now is None else now
    iat, exp = claims["iat"], claims["exp"]
    if now >= exp or exp <= iat:
        return True
    return (now - iat) / (exp - iat) >= HALF_LIFE_FRACTION


def _mint(base_url: str, workspace: str, host_token: str) -> str | None:
    url = f"{base_url.rstrip('/')}/api/workspaces/{workspace}/identity-token"
    try:
        with httpx.Client(timeout=TIMEOUT_S) as http_client:
            resp = http_client.post(url, headers={"Authorization": f"Bearer {host_token}"})
    except httpx.HTTPError as exc:
        log.warning("identity_token: could not reach aw-backend at %s: %s", url, exc)
        return None
    if resp.status_code >= 400:
        log.warning("identity_token: aw-backend refused the mint (%s): %s",
                    resp.status_code, resp.text[:300])
        return None
    try:
        token = resp.json().get("token")
    except ValueError:
        log.warning("identity_token: aw-backend returned a non-JSON body")
        return None
    if not token:
        log.warning("identity_token: aw-backend's response carried no 'token' field")
        return None
    return token


def _persist(api_url: str, api_key: str, token: str) -> bool:
    url = f"{api_url.rstrip('/')}/api/apps/{CONFIG_SLUG}/config"
    try:
        with httpx.Client(timeout=TIMEOUT_S) as http_client:
            resp = http_client.post(url, json={"config": {CONFIG_FIELD: token}},
                                     headers={"X-Api-Key": api_key})
    except httpx.HTTPError as exc:
        log.warning("identity_token: could not persist the refreshed token at %s: %s", url, exc)
        return False
    if resp.status_code >= 400:
        log.warning("identity_token: this workspace refused the config save (%s): %s",
                    resp.status_code, resp.text[:300])
        return False
    return True


def refresh(config: dict) -> str | None:
    """Mint + persist a fresh token if the one in ``config`` needs it.

    Returns the new token on success — already persisted; the caller only
    needs this to also update its own in-memory copy before the next tool
    call reads through to it. Returns None, and leaves ``config`` untouched,
    when no refresh was needed or any step failed. Never raises.
    """
    current = (config or {}).get(CONFIG_FIELD)
    if not needs_refresh(current):
        return None

    backend_url = workspace_env("AW_BACKEND_URL")
    workspace = workspace_env("AW_WORKSPACE")
    host_token = workspace_env("AW_WORKSPACE_HOST_TOKEN")
    if not backend_url or not workspace or not host_token:
        log.info("identity_token: AW_BACKEND_URL/AW_WORKSPACE/AW_WORKSPACE_HOST_TOKEN "
                 "not fully set — skipping auto-mint")
        return None

    token = _mint(backend_url, workspace, host_token)
    if not token:
        return None

    api_url = workspace_env("AW_WORKSPACE_API_URL")
    api_key = workspace_env("AW_WORKSPACE_API_KEY")
    if not api_url or not api_key:
        log.warning("identity_token: minted a fresh token but AW_WORKSPACE_API_URL/"
                    "AW_WORKSPACE_API_KEY are not set — could not persist it")
        return None

    if not _persist(api_url, api_key, token):
        return None

    log.info("identity_token: minted and persisted a fresh identity_token")
    return token
