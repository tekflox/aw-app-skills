"""This app's backend sub-app, mounted by the runtime at
``/api/apps/skills``.

No ``auth_required: false`` here (unlike the Knowledgeable Connector): this
app has no caller that must reach it without a workspace identity, so every
route stays behind the framework's default ``IdentityGuard`` with no extra
code of its own. ``build_routes()`` therefore takes no ``ctx`` — unlike
aw-knowledgeable's own ``routes.py``, nothing here reads ``ctx.secrets`` or
``ctx.config`` directly; ``plugin.py`` already closed over both when it
called ``client.configure(...)``.
"""
from __future__ import annotations

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import client, materializer


def build_routes() -> FastAPI:
    app = FastAPI(title="skills")

    @app.get("/status")
    async def status() -> dict:
        return {
            "configured": client.configured(),
            "base_url": client.base_url(),
            "tools": [t["name"] for t in client.TOOLS_SCHEMA],
        }

    @app.get("/doctor")
    async def doctor() -> dict:
        """``contributes.doctor`` entry — see
        ``materializer.doctor_report()`` for what counts as drift."""
        return materializer.doctor_report()

    @app.post("/test")
    async def test_connection() -> dict:
        """Prove reachability + auth for real, from the actual owning
        process — a saved/minted token is not a working one until something
        calls out."""
        result, is_error = await client.search_skills({"query": "test", "n_results": 1})
        return {"ok": not is_error, "base_url": client.base_url(), "result": result[:2000]}

    @app.post("/mcp")
    async def mcp_post(request: Request, data: dict | list = Body(...)):
        from .mcp.http_handler import handle_request

        messages = data if isinstance(data, list) else [data]
        responses = []
        for m in messages:
            r = await handle_request(m)
            if r is not None:
                responses.append(r)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses if isinstance(data, list) else responses[0])

    @app.get("/mcp")
    async def mcp_get():
        return Response(status_code=405)

    return app
