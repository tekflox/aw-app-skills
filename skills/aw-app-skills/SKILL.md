---
name: aw-app-skills
description: How to use the 4 MCP tools this app exposes for skills.aw (search_skills, load_skill, create_skill, update_skill), and how they differ from the kb app's same-named pair. Load this before calling any aw__skills__* tool, or before calling aw__kb__search_skills/aw__kb__load_skill if you are unsure which registry you actually want.
---

# aw-app-skills — thin MCP connector over skills.aw

This app is an HTTP client of **skills.aw** (fredericowu/aw-skills, the
standalone skill registry service, `:10016`) — it duplicates none of that
service's own versioning, visibility or tenant logic, it just maps 4 MCP
tools onto 4 of its REST endpoints. Gateway-prefixed `aw__skills__*`.

## The 4 tools

| Tool | REST call | Notes |
|---|---|---|
| `search_skills(query, n_results=10)` | `GET /api/skills?query=&limit=` | Free-text, scoped to every skill this workspace can see (public + this workspace's own private ones). |
| `load_skill(name, version=None)` | `GET /api/skills/{name}/content?version=` | Omit `version` for the latest. |
| `create_skill(name, description, content, visibility='private')` | `POST /api/skills` | **`visibility` defaults to `private`.** Pass `visibility='public'` explicitly to publish — public means the content leaves this workspace. |
| `update_skill(name, content, message)` | `PUT /api/skills/{name}` | Writes a new version; rejected if this workspace does not own the skill. |

Auth is automatic: this app mints and refreshes its own per-workspace
identity token against aw-backend (`identity_token.py`), re-minted hourly
once within half its lifetime of expiring. There is nothing to configure
unless you're pointing `base_url` at a different skills.aw deployment.

## Which registry do I actually want? (read this before guessing)

**There are two unrelated, same-named tool pairs in this workspace right
now**, and calling the wrong one silently reads from/writes to the wrong
place — neither errors, they just answer from a different corpus:

- **`aw__skills__search_skills` / `aw__skills__load_skill`** (this app) —
  read **skills.aw**, the Git-backed, versioned registry. Content here
  survives a workspace rebuild and can be shared across workspaces (public
  skills) or kept private to one workspace.
- **`aw__kb__search_skills` / `aw__kb__load_skill`** (the `kb` app,
  `repos/aw-app-kb/kb_app/mcp_http.py:393,465`) — read this **workspace's
  own local `skills/` tree** (semantic search over whatever is mounted
  under `KB_SKILLS_DIR`). Nothing here is versioned or shared.

This is a **known, temporary duplication** — deprecating the `kb` app's
pair in favor of this one is a later phase of the aw-skills build (not this
one), tracked separately. Until that lands: if you want the durable,
cross-workspace registry, use `aw__skills__*`; if you want "what's in this
workspace's local skills/ folder right now", use `aw__kb__*`. If you are an
agent uncertain which one a task means, prefer `aw__skills__*` — it is the
one backed by a real service with real version history, not a point-in-time
filesystem scan.

## Writing skills: `create_skill`/`update_skill` have real effects

`create_skill` pushes a real Git commit to a repo skills.aw owns (public
content repo, or a per-tenant private repo) — it is not a local scratch
write. Default `visibility='private'` keeps a new skill inside this
workspace; only pass `visibility='public'` when you actually mean to
publish it somewhere every workspace can read.
