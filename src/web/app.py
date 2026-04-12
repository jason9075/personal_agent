"""FastAPI web application for workflow management.

Runs in the same asyncio event loop as the Discord bot (via asyncio.gather).
Serves a vis-network graph editor for managing skills and workflow nodes/edges.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

_THIS_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _THIS_DIR / "templates"
_STATIC_DIR = _THIS_DIR / "static"


def create_app(workflow_db_path: Path) -> FastAPI:
    app = FastAPI(title="personal_agent workflow", docs_url="/docs")

    # Static assets (app.css, app.js)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    # ── Pages ────────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))

    # ── Skills API ───────────────────────────────────────────────────────────

    @app.get("/api/skills")
    async def get_skills() -> JSONResponse:
        from ..bot.workflow_db import list_skills
        skills = list_skills(workflow_db_path)
        return JSONResponse([_skill_to_dict(s) for s in skills])

    @app.put("/api/skills/{skill_id}")
    async def update_skill(skill_id: str, body: dict[str, Any]) -> JSONResponse:
        from ..bot.workflow_db import SkillDef, get_skill, upsert_skill
        existing = get_skill(workflow_db_path, skill_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"skill '{skill_id}' not found")
        updated = SkillDef(
            id=skill_id,
            display_name=str(body.get("display_name", existing.display_name)),
            description=str(body.get("description", existing.description)),
            router_mode=str(body.get("router_mode", existing.router_mode)),
            router_patterns=list(body.get("router_patterns", existing.router_patterns)),
            script_path=str(body.get("script_path", existing.script_path)),
            system_prompt=str(body.get("system_prompt", existing.system_prompt)),
            pass2_mode=str(body.get("pass2_mode", existing.pass2_mode)),
            enabled=bool(body.get("enabled", existing.enabled)),
        )
        upsert_skill(workflow_db_path, updated)
        return JSONResponse({"status": "ok", "id": skill_id})

    # ── Workflow graph API ────────────────────────────────────────────────────

    @app.get("/api/workflow")
    async def get_workflow() -> JSONResponse:
        from ..bot.workflow_db import load_workflow_graph
        graph = load_workflow_graph(workflow_db_path)
        return JSONResponse({
            "nodes": [
                {"id": n.id, "pass_index": n.pass_index, "skill_id": n.skill_id, "enabled": n.enabled}
                for n in graph.nodes
            ],
            "edges": [
                {
                    "id": e.id,
                    "from_node_id": e.from_node_id,
                    "to_node_id": e.to_node_id,
                    "condition_type": e.condition_type,
                    "condition_value": e.condition_value,
                }
                for e in graph.edges
            ],
            "skills": {sid: _skill_to_dict(s) for sid, s in graph.skills.items()},
        })

    @app.put("/api/workflow/nodes/{node_id}")
    async def upsert_node_endpoint(node_id: str, body: dict[str, Any]) -> JSONResponse:
        from ..bot.workflow_db import WorkflowNode, upsert_node
        try:
            node = WorkflowNode(
                id=node_id,
                pass_index=int(body["pass_index"]),
                skill_id=str(body["skill_id"]),
                enabled=bool(body.get("enabled", True)),
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        upsert_node(workflow_db_path, node)
        return JSONResponse({"status": "ok", "id": node_id})

    @app.delete("/api/workflow/nodes/{node_id}")
    async def delete_node_endpoint(node_id: str) -> JSONResponse:
        from ..bot.workflow_db import delete_node
        delete_node(workflow_db_path, node_id)
        return JSONResponse({"status": "ok"})

    @app.post("/api/workflow/edges")
    async def add_edge_endpoint(body: dict[str, Any]) -> JSONResponse:
        from ..bot.workflow_db import WorkflowEdge, upsert_edge
        try:
            edge = WorkflowEdge(
                id=0,
                from_node_id=str(body["from_node_id"]),
                to_node_id=str(body["to_node_id"]),
                condition_type=str(body.get("condition_type", "always")),
                condition_value=str(body.get("condition_value", "")),
            )
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"missing field: {exc}")
        saved = upsert_edge(workflow_db_path, edge)
        return JSONResponse({"status": "ok", "id": saved.id})

    @app.delete("/api/workflow/edges/{edge_id}")
    async def delete_edge_endpoint(edge_id: int) -> JSONResponse:
        from ..bot.workflow_db import delete_edge
        delete_edge(workflow_db_path, edge_id)
        return JSONResponse({"status": "ok"})

    return app


# ── Helpers ───────────────────────────────────────────────────────────────────

def _skill_to_dict(s: Any) -> dict:
    return {
        "id": s.id,
        "display_name": s.display_name,
        "description": s.description,
        "router_mode": s.router_mode,
        "router_patterns": s.router_patterns,
        "script_path": s.script_path,
        "system_prompt": s.system_prompt,
        "pass2_mode": s.pass2_mode,
        "enabled": s.enabled,
    }
