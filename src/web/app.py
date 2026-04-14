"""FastAPI web application for workflow management."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..bot.prompts import load_prompt_path
from ..bot.workflow_db import (
    WorkflowEdge,
    WorkflowNode,
    delete_edge,
    delete_node,
    detect_node_hooks,
    get_node,
    list_nodes,
    load_workflow_graph,
    upsert_edge,
    upsert_node,
)

_THIS_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _THIS_DIR / "templates"
_STATIC_DIR = _THIS_DIR / "static"
_REPO_ROOT = _THIS_DIR.parents[1]


def create_app(workflow_db_path: Path) -> FastAPI:
    app = FastAPI(title="personal_agent workflow", docs_url="/docs")
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.middleware("http")
    async def disable_cache(request: Request, call_next):  # type: ignore[unused-ignore]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/workflow")
    async def get_workflow() -> JSONResponse:
        graph = load_workflow_graph(workflow_db_path)
        return JSONResponse(
            {
                "nodes": [_node_to_dict(node) for node in graph.nodes],
                "edges": [
                    {
                        "id": edge.id,
                        "from_node_id": edge.from_node_id,
                        "to_node_id": edge.to_node_id,
                    }
                    for edge in graph.edges
                ],
            }
        )

    @app.get("/api/nodes")
    async def get_nodes() -> JSONResponse:
        return JSONResponse([_node_to_dict(node) for node in list_nodes(workflow_db_path)])

    @app.get("/api/nodes/{node_id}")
    async def get_node_endpoint(node_id: str) -> JSONResponse:
        node = get_node(workflow_db_path, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"node '{node_id}' not found")
        return JSONResponse(_node_to_dict(node))

    @app.put("/api/nodes/{node_id}")
    async def upsert_node_endpoint(node_id: str, body: dict[str, Any]) -> JSONResponse:
        existing = get_node(workflow_db_path, node_id)
        if existing is None:
            existing = WorkflowNode(
                id=node_id,
                name=node_id,
                description="",
                model_name="gpt-5.4",
                node_type="agent",
                pass_index=1,
                start_node=False,
                enabled=True,
                executor_path="",
                pre_hook_path=None,
                post_hook_path=None,
                system_prompt_path=None,
                prompt_template_path=None,
                use_prev_output=True,
                send_response=True,
                timeout_seconds=600,
            )

        try:
            node = WorkflowNode(
                id=node_id,
                name=str(body.get("name", existing.name)),
                description=str(body.get("description", existing.description)),
                model_name=str(body.get("model_name", existing.model_name or "gpt-5.4")),
                node_type=str(body.get("node_type", existing.node_type)),
                pass_index=int(body.get("pass_index", existing.pass_index)),
                start_node=bool(body.get("start_node", existing.start_node)),
                enabled=bool(body.get("enabled", existing.enabled)),
                executor_path=str(body.get("executor_path", existing.executor_path)),
                pre_hook_path=_nullable_str(body.get("pre_hook_path", existing.pre_hook_path)),
                post_hook_path=_nullable_str(body.get("post_hook_path", existing.post_hook_path)),
                system_prompt_path=_nullable_str(body.get("system_prompt_path", existing.system_prompt_path)),
                prompt_template_path=_nullable_str(body.get("prompt_template_path", existing.prompt_template_path)),
                use_prev_output=bool(body.get("use_prev_output", existing.use_prev_output)),
                send_response=bool(body.get("send_response", existing.send_response)),
                timeout_seconds=int(body.get("timeout_seconds", existing.timeout_seconds)),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        upsert_node(workflow_db_path, node)
        return JSONResponse({"status": "ok", "id": node_id})

    @app.delete("/api/nodes/{node_id}")
    async def delete_node_endpoint(node_id: str) -> JSONResponse:
        delete_node(workflow_db_path, node_id)
        return JSONResponse({"status": "ok"})

    @app.post("/api/workflow/edges")
    async def add_edge_endpoint(body: dict[str, Any]) -> JSONResponse:
        try:
            edge = WorkflowEdge(
                id=0,
                from_node_id=str(body["from_node_id"]),
                to_node_id=str(body["to_node_id"]),
            )
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"missing field: {exc}")
        saved = upsert_edge(workflow_db_path, edge)
        return JSONResponse({"status": "ok", "id": saved.id})

    @app.delete("/api/workflow/edges/{edge_id}")
    async def delete_edge_endpoint(edge_id: int) -> JSONResponse:
        delete_edge(workflow_db_path, edge_id)
        return JSONResponse({"status": "ok"})

    @app.post("/api/prompt-preview")
    async def prompt_preview_endpoint(body: dict[str, Any]) -> JSONResponse:
        path_str = _nullable_str(body.get("path"))
        return JSONResponse({
            "path": path_str,
            "content": _safe_prompt_preview(path_str),
        })

    @app.post("/api/node-details-preview")
    async def node_details_preview_endpoint(body: dict[str, Any]) -> JSONResponse:
        node = WorkflowNode(
            id=str(body.get("id", "")),
            name=str(body.get("name", "")),
            description=str(body.get("description", "")),
            model_name=str(body.get("model_name", "gpt-5.4")),
            node_type=str(body.get("node_type", "agent")),
            pass_index=int(body.get("pass_index", 1)),
            start_node=bool(body.get("start_node", False)),
            enabled=bool(body.get("enabled", True)),
            executor_path=str(body.get("executor_path", "")),
            pre_hook_path=_nullable_str(body.get("pre_hook_path")),
            post_hook_path=_nullable_str(body.get("post_hook_path")),
            system_prompt_path=_nullable_str(body.get("system_prompt_path")),
            prompt_template_path=_nullable_str(body.get("prompt_template_path")),
            use_prev_output=bool(body.get("use_prev_output", True)),
            send_response=bool(body.get("send_response", True)),
            timeout_seconds=int(body.get("timeout_seconds", 600)),
        )
        hook_info = detect_node_hooks(node, _REPO_ROOT)
        resolved_tools = _resolve_node_tools(node, workflow_db_path)
        return JSONResponse({
            "system_prompt_path": node.system_prompt_path,
            "system_prompt": _safe_prompt_preview(node.system_prompt_path),
            "preview_prompt": _build_preview_prompt(node),
            "resolved_tools": resolved_tools,
            "execution_code": {
                "pre_hook": _read_code_file(hook_info.get("effective_pre_hook_path")),
                "run": _read_code_file(hook_info.get("effective_executor_path")),
                "post_hook": _read_code_file(hook_info.get("effective_post_hook_path")),
            },
        })

    return app


def _nullable_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _node_to_dict(node: WorkflowNode) -> dict[str, Any]:
    hook_info = detect_node_hooks(node, _REPO_ROOT)
    return {
        "id": node.id,
        "name": node.name,
        "description": node.description,
        "model_name": node.model_name,
        "node_type": node.node_type,
        "pass_index": node.pass_index,
        "start_node": node.start_node,
        "enabled": node.enabled,
        "executor_path": node.executor_path,
        "pre_hook_path": node.pre_hook_path,
        "post_hook_path": node.post_hook_path,
        "system_prompt_path": node.system_prompt_path,
        "prompt_template_path": node.prompt_template_path,
        "system_prompt_preview": _safe_prompt_preview(node.system_prompt_path),
        "prompt_template_preview": _safe_prompt_preview(node.prompt_template_path),
        "use_prev_output": node.use_prev_output,
        "send_response": node.send_response,
        "timeout_seconds": node.timeout_seconds,
        "hooks": hook_info,
    }


def _safe_prompt_preview(path_str: str | None) -> str:
    if not path_str:
        return ""
    try:
        return load_prompt_path(path_str)
    except RuntimeError as exc:
        return f"[prompt load error] {exc}"


def _build_preview_prompt(node: WorkflowNode) -> str:
    system_prompt = _safe_prompt_preview(node.system_prompt_path).strip()
    template = _safe_prompt_preview(node.prompt_template_path).strip() if node.prompt_template_path else ""
    parts: list[str] = []
    if system_prompt:
        parts.append(system_prompt)
    parts.append("----")
    parts.append(f"Model: {node.model_name or 'gpt-5.4'}")
    parts.append(f"Node Name: {node.name or '(empty)'}")
    parts.append(f"Node Description: {node.description or '(empty)'}")
    if node.use_prev_output:
        parts.append("PREVIOUS_INPUT:\n{PREVIOUS_INPUT}")
    if template:
        parts.append("TEMPLATE:\n" + template)
    return "\n\n".join(parts).strip()


def _read_code_file(path_str: Any) -> str:
    if not path_str:
        return "(none)"
    path = _REPO_ROOT / str(path_str)
    if not path.exists():
        return f"(missing) {path_str}"
    return path.read_text(encoding="utf-8")


def _resolve_node_tools(node: WorkflowNode, workflow_db_path: Path) -> list[str]:
    graph = load_workflow_graph(workflow_db_path)
    candidates = graph.candidate_targets(node.id)
    if not any(candidate.enabled for candidate in candidates):
        return []
    resolved: list[str] = []
    for candidate in candidates:
        label = candidate.name or candidate.id
        description = candidate.description or ""
        text = f"{candidate.id}: {label}"
        if description:
            text += f" — {description}"
        resolved.append(text)
    return resolved
