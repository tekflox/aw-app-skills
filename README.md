# aw-app-skills

Tier-1 MCP connector for **skills.aw** (fredericowu/aw-skills), the
standalone, Git-backed skill registry service — Fase 2 of the aw-skills
build (Fase 1, the service itself, is already live: container healthy at
`:10016`, REST complete, `master@832f32e`).

Modeled on the Knowledgeable Connector (`repos/aw-app-knowledgeable`): a
thin HTTP client, zero duplicated business logic, registered on the MCP
gateway as `aw__skills__*`.

## What this app does

1. **Exposes 4 MCP tools** as a thin layer over skills.aw's REST API —
   `search_skills`, `load_skill`, `create_skill`, `update_skill`. See
   `skills/aw-app-skills/SKILL.md` for the full tool reference.
2. **Hosts the future fs-sync-like materializer** (`skills_app/
   materializer.py`) — this phase only leaves the structure ready to
   receive that watchdog. The materializer's own logic is Fase 4 of the
   aw-skills build, which depends on this app existing and on Fase 1's
   `GET /api/sync/manifest` endpoint (already shipped).

## Known, temporary duplication

The `kb` app already exposes `search_skills`/`load_skill`
(`aw__kb__*`, `repos/aw-app-kb/kb_app/mcp_http.py:393,465`) — but those read
this workspace's own local `skills/` tree, a different, filesystem-backed
index from the registry this app talks to. There is no *technical*
conflict (the gateway prefixes the two pairs differently:
`aw__skills__*` vs `aw__kb__*`), but the two same-named pairs can confuse
an agent deciding which one to call. Resolving that — deprecating the `kb`
app's pair — is Fase 5 of the aw-skills build, not this one. Until then,
see the "Which registry do I actually want?" section in
`skills/aw-app-skills/SKILL.md`.

## Auth: no secret to configure

Unlike the Knowledgeable Connector's shared `X-Internal-Secret`, skills.aw's
own auth model is a **per-workspace identity JWT** — this app mints and
refreshes its own (`skills_app/identity_token.py`, same two-hop
mint-then-persist shape `agents_platform_runners_app` already uses for its
`agents_platform_token`), on an hourly watchdog tick. There is nothing to
paste into Settings; the only user-facing config field is `base_url`
(defaults to `http://aw-skills:10016` — a container **name** on
`aw-stack-net`, same pattern as the Knowledgeable Connector's own
`base_url`, not the public hostname).

## Out of scope for this phase

Materializer logic (Fase 4), marketplace CI ingest job on the aw-skills
side (Fase 3), AP-MT's own provider + name-index decommission (Fase 5), the
real skills.aw UI (Fase 6).

## Local dev

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q
```
