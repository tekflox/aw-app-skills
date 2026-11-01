"""HTTP client for skills.aw's REST API (fredericowu/aw-skills) — this
connector's 4 tool bodies.

Style ported from ``aw-app-knowledgeable/knowledgeable_app/mcp/client.py``:
a plain client of a remote API, a 1:1 mapping onto its REST surface with no
duplicated business logic (versioning, tenant resolution, visibility rules
all stay server-side) and credentials read **fresh on every call** — never
cached at import time — so a refreshed identity token (see
``identity_token.py``) takes effect on the very next tool call with no
restart.

Unlike that connector, auth here is a per-workspace identity JWT, not a
shared secret: aw-skills' own ``core/identity.py`` resolves which
workspace(s) a caller may read/write from the JWT's ``memberships`` claim,
never from a header this client sets. Holding a valid token for this
workspace is the whole access-control story on this side; `create_skill`
with no explicit tenant defaults to this workspace because the minted
token carries exactly one membership (``backend/app/api/skills.py``'s
``_resolve_write_tenant``).
"""
from __future__ import annotations

import json
from typing import Callable

import httpx

DEFAULT_BASE_URL = "http://aw-skills:10016"

_base_url_resolver: Callable[[], str] = lambda: DEFAULT_BASE_URL
_token_resolver: Callable[[], str] = lambda: ""


def configure(base_url_resolver: Callable[[], str], token_resolver: Callable[[], str]) -> None:
    """Installed once from ``plugin.activate``; every function below reads
    through these two callables on every call — see module docstring."""
    global _base_url_resolver, _token_resolver
    _base_url_resolver = base_url_resolver
    _token_resolver = token_resolver


def base_url() -> str:
    return (_base_url_resolver() or DEFAULT_BASE_URL).rstrip("/")


def _token() -> str:
    try:
        return (_token_resolver() or "").strip()
    except Exception:
        return ""


def configured() -> bool:
    return bool(_token())


def _headers() -> dict:
    token = _token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _as_text(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _describe_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    detail = data.get("detail") if isinstance(data, dict) else None
    return f"HTTP {resp.status_code}: {detail}" if detail else f"HTTP {resp.status_code}"


async def _get(path: str, params: dict | None = None) -> tuple[dict | None, str | None]:
    async with httpx.AsyncClient(timeout=30) as http_client:
        try:
            resp = await http_client.get(f"{base_url()}{path}", params=params, headers=_headers())
        except httpx.HTTPError as exc:
            return None, f"could not reach skills.aw at {base_url()}: {exc}"
    if resp.status_code >= 400:
        return None, _describe_error(resp)
    return resp.json(), None


async def _post_json(path: str, body: dict) -> tuple[dict | None, str | None]:
    async with httpx.AsyncClient(timeout=30) as http_client:
        try:
            resp = await http_client.post(f"{base_url()}{path}", json=body, headers=_headers())
        except httpx.HTTPError as exc:
            return None, f"could not reach skills.aw at {base_url()}: {exc}"
    if resp.status_code >= 400:
        return None, _describe_error(resp)
    return resp.json(), None


async def _put_json(path: str, body: dict) -> tuple[dict | None, str | None]:
    async with httpx.AsyncClient(timeout=30) as http_client:
        try:
            resp = await http_client.put(f"{base_url()}{path}", json=body, headers=_headers())
        except httpx.HTTPError as exc:
            return None, f"could not reach skills.aw at {base_url()}: {exc}"
    if resp.status_code >= 400:
        return None, _describe_error(resp)
    return resp.json(), None


async def search_skills(args: dict) -> tuple[str, bool]:
    """GET /api/skills?query=&limit= — ``n_results`` maps onto the route's
    own ``limit`` (server caps it at 200)."""
    query = (args.get("query") or "").strip()
    if not query:
        return "query is required", True
    try:
        n_results = int(args.get("n_results") or 10)
    except (TypeError, ValueError):
        n_results = 10
    n_results = max(1, min(n_results, 200))
    data, err = await _get("/api/skills", {"query": query, "limit": n_results})
    if err:
        return f"search_skills failed: {err}", True
    return _as_text(data), False


async def load_skill(args: dict) -> tuple[str, bool]:
    """GET /api/skills/{slug}/content?version= — omitted version loads the
    skill's own latest (the route's default, not something this client
    computes)."""
    name = (args.get("name") or "").strip()
    if not name:
        return "name is required", True
    params: dict = {}
    version = args.get("version")
    if version is not None:
        try:
            params["version"] = int(version)
        except (TypeError, ValueError):
            return "version must be an integer", True
    data, err = await _get(f"/api/skills/{name}/content", params or None)
    if err:
        return f"load_skill failed: {err}", True
    return _as_text(data), False


async def create_skill(args: dict) -> tuple[str, bool]:
    """POST /api/skills — ``visibility`` defaults to ``private``: making a
    skill public is an explicit act, because public means its content
    leaves this workspace (DESIGN.md, this card's own briefing)."""
    name = (args.get("name") or "").strip()
    content = args.get("content")
    if not name:
        return "name is required", True
    if not content:
        return "content is required", True
    visibility = (args.get("visibility") or "private").strip().lower()
    if visibility not in ("public", "private"):
        return "visibility must be 'public' or 'private'", True
    body = {
        "slug": name,
        "description": args.get("description") or None,
        "content": content,
        "visibility": visibility,
    }
    data, err = await _post_json("/api/skills", body)
    if err:
        return f"create_skill failed: {err}", True
    return _as_text(data), False


async def update_skill(args: dict) -> tuple[str, bool]:
    """PUT /api/skills/{slug} — writes a new version; the server rejects
    this if the caller's workspace does not own the skill."""
    name = (args.get("name") or "").strip()
    content = args.get("content")
    if not name:
        return "name is required", True
    if not content:
        return "content is required", True
    body = {"content": content, "message": args.get("message") or None}
    data, err = await _put_json(f"/api/skills/{name}", body)
    if err:
        return f"update_skill failed: {err}", True
    return _as_text(data), False


TOOLS_SCHEMA = [
    {
        "name": "search_skills",
        "description": (
            "Search skills.aw (the standalone skill registry) by free-text query. "
            "Scoped to every skill this connector's workspace can see — public skills "
            "plus this workspace's own private ones."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search query."},
                "n_results": {
                    "type": "integer",
                    "description": "Max results to return (default 10, server caps at 200).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "load_skill",
        "description": (
            "Load one skill's content from skills.aw by slug, optionally a specific "
            "version — omitted, this loads that skill's latest version."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The skill's slug."},
                "version": {
                    "type": "integer",
                    "description": "Specific version number. Omit for the latest.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_skill",
        "description": (
            "Create a new skill on skills.aw. Defaults to 'private' (visible only to "
            "this workspace) — pass visibility='public' explicitly to publish it, "
            "since public means the content leaves this workspace."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The new skill's slug."},
                "description": {"type": "string", "description": "One-line description."},
                "content": {"type": "string", "description": "The skill's SKILL.md body."},
                "visibility": {
                    "type": "string",
                    "enum": ["private", "public"],
                    "description": "Defaults to 'private'.",
                },
            },
            "required": ["name", "description", "content"],
        },
    },
    {
        "name": "update_skill",
        "description": "Write a new version of an existing skill on skills.aw.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The skill's slug."},
                "content": {"type": "string", "description": "The new SKILL.md body."},
                "message": {"type": "string", "description": "Commit message for this version."},
            },
            "required": ["name", "content"],
        },
    },
]
