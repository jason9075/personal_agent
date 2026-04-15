"""FastAPI web application for workflow management."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..bot.engine import execute_workflow
from ..bot.prompts import build_runtime_context, compose_prompt, load_engine_system_prompt, load_prompt_path
from ..bot.scheduler import FinanceScheduler
from ..bot.schedule_db import (
    ScheduledJob,
    create_job,
    delete_job,
    get_job,
    list_jobs,
    update_job,
)
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
_DEBUG_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="debug_chat")
_TEMPLATES_DIR = _THIS_DIR / "templates"
_STATIC_DIR = _THIS_DIR / "static"
_REPO_ROOT = _THIS_DIR.parents[1]


def create_app(
    workflow_db_path: Path,
    schedule_db_path: Path,
    scheduler: FinanceScheduler | None = None,
) -> FastAPI:
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
                model_name=None,
                start_node=False,
                enabled=True,
                executor_path="",
                pre_hook_path=None,
                post_hook_path=None,
                node_prompt_path=None,
                use_prev_output=True,
                timeout_seconds=600,
            )

        try:
            node = WorkflowNode(
                id=node_id,
                name=str(body.get("name", existing.name)),
                description=str(body.get("description", existing.description)),
                model_name=_nullable_str(body.get("model_name", existing.model_name)),
                start_node=bool(body.get("start_node", existing.start_node)),
                enabled=bool(body.get("enabled", existing.enabled)),
                executor_path=str(body.get("executor_path", existing.executor_path)),
                pre_hook_path=_nullable_str(body.get("pre_hook_path", existing.pre_hook_path)),
                post_hook_path=_nullable_str(body.get("post_hook_path", existing.post_hook_path)),
                node_prompt_path=_nullable_str(body.get("node_prompt_path", existing.node_prompt_path)),
                use_prev_output=bool(body.get("use_prev_output", existing.use_prev_output)),
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

    @app.get("/api/schedule/jobs")
    async def list_jobs_endpoint() -> JSONResponse:
        return JSONResponse([_job_to_dict(job) for job in list_jobs(schedule_db_path)])

    @app.post("/api/schedule/jobs")
    async def create_job_endpoint(body: dict[str, Any]) -> JSONResponse:
        try:
            job = create_job(
                schedule_db_path,
                name=str(body["name"]),
                cron_expr=str(body["cron_expr"]),
                job_type=str(body.get("job_type", "finance-report")),
                task_message=str(body.get("task_message", "")),
                source_id=str(body.get("source_id", "")),
                workers=int(body.get("workers", 4)),
                channel_id=str(body.get("channel_id", "")),
                run_once=bool(body.get("run_once", False)),
            )
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"missing field: {exc}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse(_job_to_dict(job), status_code=201)

    @app.put("/api/schedule/jobs/{job_id}")
    async def update_job_endpoint(job_id: int, body: dict[str, Any]) -> JSONResponse:
        try:
            job = update_job(
                schedule_db_path,
                job_id,
                name=str(body["name"]) if "name" in body else None,
                cron_expr=str(body["cron_expr"]) if "cron_expr" in body else None,
                job_type=str(body["job_type"]) if "job_type" in body else None,
                task_message=str(body["task_message"]) if "task_message" in body else None,
                source_id=str(body["source_id"]) if "source_id" in body else None,
                workers=int(body["workers"]) if "workers" in body else None,
                channel_id=str(body["channel_id"]) if "channel_id" in body else None,
                enabled=bool(body["enabled"]) if "enabled" in body else None,
                run_once=bool(body["run_once"]) if "run_once" in body else None,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return JSONResponse(_job_to_dict(job))

    @app.delete("/api/schedule/jobs/{job_id}")
    async def delete_job_endpoint(job_id: int) -> JSONResponse:
        try:
            delete_job(schedule_db_path, job_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return JSONResponse({"status": "ok"})

    @app.post("/api/schedule/jobs/{job_id}/run")
    async def run_job_endpoint(job_id: int) -> JSONResponse:
        if scheduler is None:
            raise HTTPException(status_code=503, detail="scheduler is not available")
        try:
            await scheduler.run_job_now(job_id)
            job = get_job(schedule_db_path, job_id)
        except RuntimeError as exc:
            detail = str(exc)
            status_code = 409 if "already running" in detail else 404
            raise HTTPException(status_code=status_code, detail=detail)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return JSONResponse(_job_to_dict(job))

    @app.post("/api/prompt-preview")
    async def prompt_preview_endpoint(body: dict[str, Any]) -> JSONResponse:
        path_str = _nullable_str(body.get("path"))
        return JSONResponse({
            "path": path_str,
            "content": _safe_prompt_preview(path_str),
        })

    @app.get("/api/engine-prompt")
    async def engine_prompt_endpoint() -> JSONResponse:
        return JSONResponse({
            "path": "src/bot/engine_system_prompt.md",
            "content": _safe_engine_prompt(),
        })

    @app.post("/api/debug/chat")
    async def debug_chat_endpoint(body: dict[str, Any]) -> JSONResponse:
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=422, detail="message is required")
        loop = asyncio.get_event_loop()
        node_trace: list[str] = []
        try:
            reply = await loop.run_in_executor(
                _DEBUG_EXECUTOR,
                lambda: execute_workflow(message, workflow_db_path, _REPO_ROOT, node_trace=node_trace),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return JSONResponse({"reply": reply, "node_trace": node_trace})

    @app.post("/api/node-details-preview")
    async def node_details_preview_endpoint(body: dict[str, Any]) -> JSONResponse:
        node = WorkflowNode(
            id=str(body.get("id", "")),
            name=str(body.get("name", "")),
            description=str(body.get("description", "")),
            model_name=_nullable_str(body.get("model_name")),
            start_node=bool(body.get("start_node", False)),
            enabled=bool(body.get("enabled", True)),
            executor_path=str(body.get("executor_path", "")),
            pre_hook_path=_nullable_str(body.get("pre_hook_path")),
            post_hook_path=_nullable_str(body.get("post_hook_path")),
            node_prompt_path=_nullable_str(body.get("node_prompt_path")),
            use_prev_output=bool(body.get("use_prev_output", True)),
            timeout_seconds=int(body.get("timeout_seconds", 600)),
        )
        hook_info = detect_node_hooks(node, _REPO_ROOT)
        resolved_tools = _resolve_node_tools(node, workflow_db_path)
        reachable_nodes_json = _build_reachable_nodes_preview(node, workflow_db_path)
        return JSONResponse({
            "engine_prompt_path": "src/bot/engine_system_prompt.md",
            "engine_prompt": _safe_engine_prompt(),
            "node_prompt_path": node.node_prompt_path,
            "node_prompt": _safe_prompt_preview(node.node_prompt_path),
            "run_output_preview": _build_run_output_preview(node),
            "preview_prompt": _build_preview_prompt(node, reachable_nodes_json),
            "resolved_tools": resolved_tools,
            "execution_code": {
                "pre_hook": _read_code_file(hook_info.get("effective_pre_hook_path")),
                "run": _read_code_file(hook_info.get("effective_executor_path")),
                "post_hook": _read_code_file(hook_info.get("effective_post_hook_path")),
            },
        })

    return app


def _job_to_dict(job: ScheduledJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "name": job.name,
        "cron_expr": job.cron_expr,
        "job_type": job.job_type,
        "task_message": job.task_message,
        "source_id": job.source_id,
        "workers": job.workers,
        "channel_id": job.channel_id,
        "enabled": job.enabled,
        "run_once": job.run_once,
        "last_run_at": job.last_run_at,
        "last_status": job.last_status,
        "last_message": job.last_message,
    }


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
        "start_node": node.start_node,
        "enabled": node.enabled,
        "executor_path": node.executor_path,
        "pre_hook_path": node.pre_hook_path,
        "post_hook_path": node.post_hook_path,
        "node_prompt_path": node.node_prompt_path,
        "node_prompt_preview": _safe_prompt_preview(node.node_prompt_path),
        "use_prev_output": node.use_prev_output,
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


def _safe_engine_prompt() -> str:
    try:
        return load_engine_system_prompt()
    except RuntimeError as exc:
        return f"[engine prompt load error] {exc}"


def _build_preview_prompt(node: WorkflowNode, reachable_nodes_json: str) -> str:
    engine_prompt = _safe_engine_prompt().strip()
    node_prompt = _safe_prompt_preview(node.node_prompt_path).strip()
    if not _node_uses_llm(node):
        return "(this node does not call LLM; stdout is returned directly unless another node consumes it)"
    next_nodes = json.loads(reachable_nodes_json) if reachable_nodes_json.strip() else []
    task_prompt = _safe_finance_report_task_prompt() if (node.executor_path or "").strip() == "nodes/finance-report/run.py" else ""
    runtime_context = build_runtime_context(
        previous_input="{PREVIOUS_INPUT}" if node.use_prev_output else "",
        run_output="{RUN_OUTPUT}",
        next_nodes=next_nodes,
        recent_context="{recent_context}" if (node.executor_path or "").strip() == "nodes/intent-router/run.py" else "",
        user_message="{user_message}",
        task_prompt=task_prompt,
    )
    return compose_prompt(engine_prompt, node_prompt, runtime_context)


def _build_run_output_preview(node: WorkflowNode) -> str:
    return "{RUN_OUTPUT}"


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


def _build_reachable_nodes_preview(node: WorkflowNode, workflow_db_path: Path) -> str:
    graph = load_workflow_graph(workflow_db_path)
    candidates = [
        {
            "id": candidate.id,
            "name": candidate.name,
            "description": candidate.description or candidate.name,
        }
        for candidate in graph.candidate_targets(node.id)
        if candidate.enabled
    ]
    if not candidates:
        return "[]"
    return json_dumps_pretty(candidates)


def json_dumps_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _node_uses_llm(node: WorkflowNode) -> bool:
    return bool(node.model_name)


def _safe_finance_report_task_prompt() -> str:
    path = "nodes/finance-report/impl/prompt/finance_report_analysis.md"
    raw = _safe_prompt_preview(path).strip()
    if not raw or raw.startswith("[prompt load error]"):
        return raw
    try:
        return raw.format(
            transcript_path="{transcript_path}",
            note_date="{note_date}",
            note_path="{note_path}",
            source_title="{source_title}",
            source_author="{source_author}",
        )
    except (KeyError, ValueError):
        return raw
