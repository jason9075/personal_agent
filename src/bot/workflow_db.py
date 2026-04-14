"""SQLite-backed node-first workflow graph storage."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    name: str
    description: str
    node_type: str
    pass_index: int
    start_node: bool
    enabled: bool
    executor_path: str
    pre_hook_path: str | None
    post_hook_path: str | None
    system_prompt_path: str | None
    prompt_template_path: str | None
    use_prev_output: bool
    allowed_tools: list[str]
    send_response: bool
    input_schema: dict | None
    output_schema: dict | None
    timeout_seconds: int
    max_llm_calls: int
    route_label: str | None
    route_description: str | None
    router_mode: str
    router_patterns: list[str]
    metadata: dict


@dataclass(frozen=True)
class WorkflowEdge:
    id: int
    from_node_id: str
    to_node_id: str
    condition_type: str
    condition_value: str


@dataclass(frozen=True)
class WorkflowGraph:
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]

    def enabled_nodes(self) -> list[WorkflowNode]:
        return [node for node in self.nodes if node.enabled]

    def node_by_id(self, node_id: str) -> WorkflowNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def start_node(self) -> WorkflowNode | None:
        return next((node for node in self.nodes if node.start_node and node.enabled), None)

    def outgoing(self, from_node_id: str) -> list[WorkflowEdge]:
        return [edge for edge in self.edges if edge.from_node_id == from_node_id]

    def candidate_targets(self, from_node_id: str) -> list[WorkflowNode]:
        candidate_ids = [edge.to_node_id for edge in self.outgoing(from_node_id)]
        return [
            node
            for node in self.enabled_nodes()
            if node.id in candidate_ids
        ]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_nodes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    node_type TEXT NOT NULL DEFAULT 'agent',
    pass_index INTEGER NOT NULL DEFAULT 1,
    start_node INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    executor_path TEXT NOT NULL DEFAULT '',
    pre_hook_path TEXT NOT NULL DEFAULT '',
    post_hook_path TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    prompt_template TEXT NOT NULL DEFAULT '',
    use_prev_output INTEGER NOT NULL DEFAULT 1,
    allowed_tools TEXT NOT NULL DEFAULT '[]',
    send_response INTEGER NOT NULL DEFAULT 1,
    input_schema TEXT NOT NULL DEFAULT '',
    output_schema TEXT NOT NULL DEFAULT '',
    timeout_seconds INTEGER NOT NULL DEFAULT 600,
    max_llm_calls INTEGER NOT NULL DEFAULT 0,
    route_label TEXT NOT NULL DEFAULT '',
    route_description TEXT NOT NULL DEFAULT '',
    router_mode TEXT NOT NULL DEFAULT 'llm',
    router_patterns TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflow_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    condition_type TEXT NOT NULL DEFAULT 'always',
    condition_value TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (from_node_id) REFERENCES workflow_nodes(id),
    FOREIGN KEY (to_node_id) REFERENCES workflow_nodes(id)
);
"""

_NODE_COLUMNS: dict[str, str] = {
    "name": "TEXT NOT NULL DEFAULT ''",
    "description": "TEXT NOT NULL DEFAULT ''",
    "node_type": "TEXT NOT NULL DEFAULT 'agent'",
    "pass_index": "INTEGER NOT NULL DEFAULT 1",
    "start_node": "INTEGER NOT NULL DEFAULT 0",
    "enabled": "INTEGER NOT NULL DEFAULT 1",
    "executor_path": "TEXT NOT NULL DEFAULT ''",
    "pre_hook_path": "TEXT NOT NULL DEFAULT ''",
    "post_hook_path": "TEXT NOT NULL DEFAULT ''",
    "system_prompt": "TEXT NOT NULL DEFAULT ''",
    "prompt_template": "TEXT NOT NULL DEFAULT ''",
    "system_prompt_path": "TEXT NOT NULL DEFAULT ''",
    "prompt_template_path": "TEXT NOT NULL DEFAULT ''",
    "use_prev_output": "INTEGER NOT NULL DEFAULT 1",
    "allowed_tools": "TEXT NOT NULL DEFAULT '[]'",
    "send_response": "INTEGER NOT NULL DEFAULT 1",
    "input_schema": "TEXT NOT NULL DEFAULT ''",
    "output_schema": "TEXT NOT NULL DEFAULT ''",
    "timeout_seconds": "INTEGER NOT NULL DEFAULT 600",
    "max_llm_calls": "INTEGER NOT NULL DEFAULT 0",
    "route_label": "TEXT NOT NULL DEFAULT ''",
    "route_description": "TEXT NOT NULL DEFAULT ''",
    "router_mode": "TEXT NOT NULL DEFAULT 'llm'",
    "router_patterns": "TEXT NOT NULL DEFAULT '[]'",
    "metadata": "TEXT NOT NULL DEFAULT '{}'",
}


_SEED_NODES: list[WorkflowNode] = [
    WorkflowNode(
        id="route",
        name="Intent Router",
        description="Workflow entrypoint. Routes the incoming message to one of its connected nodes.",
        node_type="router",
        pass_index=1,
        start_node=True,
        enabled=True,
        executor_path="",
        pre_hook_path=None,
        post_hook_path=None,
        system_prompt_path="nodes/route/system.md",
        prompt_template_path=None,
        use_prev_output=False,
        allowed_tools=[],
        send_response=False,
        input_schema=None,
        output_schema={"type": "object"},
        timeout_seconds=60,
        max_llm_calls=1,
        route_label=None,
        route_description=None,
        router_mode="llm",
        router_patterns=[],
        metadata={},
    ),
    WorkflowNode(
        id="finance-report",
        name="Finance Report",
        description="Run the finance RSS pipeline, transcribe audio, and generate a markdown digest.",
        node_type="agent",
        pass_index=2,
        start_node=False,
        enabled=True,
        executor_path="nodes/finance-report/run.py",
        pre_hook_path=None,
        post_hook_path=None,
        system_prompt_path="nodes/finance-report/system.md",
        prompt_template_path=None,
        use_prev_output=True,
        allowed_tools=[],
        send_response=True,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        timeout_seconds=7200,
        max_llm_calls=1,
        route_label="finance-report",
        route_description="當使用者要求財經報告、財經摘要、指定來源節目整理、或指定日期報告時使用。",
        router_mode="direct_regex",
        router_patterns=[],
        metadata={},
    ),
    WorkflowNode(
        id="finance-schedule",
        name="Finance Schedule",
        description="Manage finance report schedules stored in SQLite.",
        node_type="tool",
        pass_index=2,
        start_node=False,
        enabled=True,
        executor_path="nodes/finance-schedule/run.py",
        pre_hook_path=None,
        post_hook_path=None,
        system_prompt_path=None,
        prompt_template_path=None,
        use_prev_output=True,
        allowed_tools=[],
        send_response=True,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        timeout_seconds=60,
        max_llm_calls=0,
        route_label="finance-schedule",
        route_description="當使用者要求新增、修改、刪除、列出或啟停財經報告排程時使用。",
        router_mode="direct_regex",
        router_patterns=[],
        metadata={},
    ),
    WorkflowNode(
        id="general-reply",
        name="General Reply",
        description="Produce a normal LLM reply when no workflow-specific node is more suitable.",
        node_type="agent",
        pass_index=2,
        start_node=False,
        enabled=True,
        executor_path="nodes/general-reply/run.py",
        pre_hook_path=None,
        post_hook_path=None,
        system_prompt_path="nodes/general-reply/system.md",
        prompt_template_path=None,
        use_prev_output=True,
        allowed_tools=[],
        send_response=True,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        timeout_seconds=120,
        max_llm_calls=1,
        route_label="general-reply",
        route_description="當使用者只是一般對話、詢問 bot 能做什麼，或沒有其他明確工作流符合時使用。",
        router_mode="llm",
        router_patterns=[],
        metadata={},
    ),
    WorkflowNode(
        id="echo",
        name="Echo",
        description="Testing node that returns the extracted text directly.",
        node_type="tool",
        pass_index=2,
        start_node=False,
        enabled=True,
        executor_path="nodes/echo/run.py",
        pre_hook_path=None,
        post_hook_path=None,
        system_prompt_path=None,
        prompt_template_path=None,
        use_prev_output=True,
        allowed_tools=[],
        send_response=True,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        timeout_seconds=30,
        max_llm_calls=0,
        route_label="echo",
        route_description="當使用者要求測試 echo 或回傳一段原文時使用。",
        router_mode="direct_regex",
        router_patterns=[r"啟用echo node\s+(?P<text>.+)"],
        metadata={},
    ),
]

_SEED_EDGES: list[tuple[str, str]] = [
    ("route", "finance-report"),
    ("route", "finance-schedule"),
    ("route", "general-reply"),
    ("route", "echo"),
]


def ensure_workflow_db(db_path: Path) -> None:
    """Create tables, migrate columns, and seed base workflow."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        _ensure_node_columns(conn)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS workflow_single_start_node
            ON workflow_nodes(start_node)
            WHERE start_node = 1
            """
        )
        _migrate_legacy_nodes(conn)
        _seed_nodes_and_edges(conn)
        conn.commit()


def _ensure_node_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(workflow_nodes)")}
    for column, ddl in _NODE_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE workflow_nodes ADD COLUMN {column} {ddl}")


def _migrate_legacy_nodes(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_nodes)")}
    has_skill_id = "skill_id" in columns

    conn.execute("UPDATE workflow_nodes SET name = id WHERE name = ''")
    conn.execute("UPDATE workflow_nodes SET route_label = name WHERE route_label = ''")
    conn.execute("UPDATE workflow_nodes SET route_description = description WHERE route_description = ''")

    if has_skill_id:
        conn.execute(
            """
            UPDATE workflow_nodes
            SET executor_path = CASE skill_id
                WHEN 'finance-report' THEN 'nodes/finance-report/run.py'
                WHEN 'finance-schedule' THEN 'nodes/finance-schedule/run.py'
                WHEN 'echo' THEN 'nodes/echo/run.py'
                ELSE executor_path
            END
            WHERE executor_path = ''
            """
        )
        conn.execute(
            """
            UPDATE workflow_nodes
            SET router_mode = CASE skill_id
                WHEN 'finance-report' THEN 'direct_regex'
                WHEN 'finance-schedule' THEN 'direct_regex'
                WHEN 'echo' THEN 'direct_regex'
                ELSE router_mode
            END
            WHERE router_mode = ''
            """
        )

    conn.execute(
        """
        UPDATE workflow_nodes
        SET system_prompt_path = CASE id
            WHEN 'route' THEN 'nodes/route/system.md'
            WHEN 'finance-report' THEN 'nodes/finance-report/system.md'
            WHEN 'general-reply' THEN 'nodes/general-reply/system.md'
            ELSE system_prompt_path
        END
        WHERE system_prompt_path = ''
        """
    )
    conn.execute(
        """
        UPDATE workflow_nodes
        SET system_prompt_path = CASE
            WHEN id LIKE '%:finance-report' THEN 'nodes/finance-report/system.md'
            WHEN id LIKE '%:general-reply' THEN 'nodes/general-reply/system.md'
            WHEN id LIKE '%:route' THEN 'nodes/route/system.md'
            ELSE system_prompt_path
        END
        WHERE system_prompt_path = ''
        """
    )

    start_count = conn.execute(
        "SELECT COUNT(*) FROM workflow_nodes WHERE start_node = 1"
    ).fetchone()[0]
    if start_count == 0 and conn.execute("SELECT COUNT(*) FROM workflow_nodes").fetchone()[0] > 0:
        row = conn.execute(
            "SELECT id FROM workflow_nodes WHERE id = 'route' LIMIT 1"
        ).fetchone()
        if row:
            conn.execute("UPDATE workflow_nodes SET start_node = 1 WHERE id = 'route'")


def _seed_nodes_and_edges(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM workflow_nodes").fetchone()[0]
    if count == 0:
        for node in _SEED_NODES:
            _upsert_node_conn(conn, node)
        for from_id, to_id in _SEED_EDGES:
            _upsert_edge_conn(
                conn,
                WorkflowEdge(
                    id=0,
                    from_node_id=from_id,
                    to_node_id=to_id,
                    condition_type="always",
                    condition_value="",
                ),
            )
        return

    existing_ids = {row[0] for row in conn.execute("SELECT id FROM workflow_nodes")}
    if "route" not in existing_ids:
        _upsert_node_conn(conn, _SEED_NODES[0])
    if "general-reply" not in existing_ids:
        _upsert_node_conn(conn, next(node for node in _SEED_NODES if node.id == "general-reply"))

    existing_ids = {row[0] for row in conn.execute("SELECT id FROM workflow_nodes")}
    for from_id, to_id in _SEED_EDGES:
        resolved_to_id = _resolve_existing_node_id(conn, to_id)
        if from_id in existing_ids and resolved_to_id in existing_ids:
            exists = conn.execute(
                "SELECT 1 FROM workflow_edges WHERE from_node_id = ? AND to_node_id = ? LIMIT 1",
                (from_id, resolved_to_id),
            ).fetchone()
            if not exists:
                _upsert_edge_conn(
                    conn,
                    WorkflowEdge(
                        id=0,
                        from_node_id=from_id,
                        to_node_id=resolved_to_id,
                        condition_type="always",
                        condition_value="",
                    ),
                )


def _resolve_existing_node_id(conn: sqlite3.Connection, wanted_id: str) -> str:
    direct = conn.execute(
        "SELECT id FROM workflow_nodes WHERE id = ? LIMIT 1",
        (wanted_id,),
    ).fetchone()
    if direct:
        return direct[0]

    skill_columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_nodes)")}
    if "skill_id" in skill_columns:
        by_skill = conn.execute(
            "SELECT id FROM workflow_nodes WHERE skill_id = ? ORDER BY pass_index ASC, id ASC LIMIT 1",
            (wanted_id,),
        ).fetchone()
        if by_skill:
            return by_skill[0]

    legacy = conn.execute(
        "SELECT id FROM workflow_nodes WHERE id LIKE ? ORDER BY pass_index ASC, id ASC LIMIT 1",
        (f"%:{wanted_id}",),
    ).fetchone()
    return legacy[0] if legacy else wanted_id


def _json_load(value: str, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _row_to_node(row: sqlite3.Row | tuple) -> WorkflowNode:
    return WorkflowNode(
        id=row[0],
        name=row[1],
        description=row[2],
        node_type=row[3],
        pass_index=int(row[4]),
        start_node=bool(row[5]),
        enabled=bool(row[6]),
        executor_path=row[7],
        pre_hook_path=row[8] or None,
        post_hook_path=row[9] or None,
        system_prompt_path=row[10] or None,
        prompt_template_path=row[11] or None,
        use_prev_output=bool(row[12]),
        allowed_tools=list(_json_load(row[13], [])),
        send_response=bool(row[14]),
        input_schema=_json_load(row[15], None),
        output_schema=_json_load(row[16], None),
        timeout_seconds=int(row[17]),
        max_llm_calls=int(row[18]),
        route_label=row[19] or None,
        route_description=row[20] or None,
        router_mode=row[21] or "llm",
        router_patterns=list(_json_load(row[22], [])),
        metadata=dict(_json_load(row[23], {})),
    )


def load_workflow_graph(db_path: Path) -> WorkflowGraph:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, description, node_type, pass_index, start_node, enabled,
                   executor_path, pre_hook_path, post_hook_path,
                   COALESCE(NULLIF(system_prompt_path, ''), '') AS system_prompt_path,
                   COALESCE(NULLIF(prompt_template_path, ''), '') AS prompt_template_path,
                   use_prev_output, allowed_tools, send_response,
                   input_schema, output_schema, timeout_seconds, max_llm_calls,
                   route_label, route_description, router_mode, router_patterns, metadata
            FROM workflow_nodes
            ORDER BY pass_index ASC, id ASC
            """
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT id, from_node_id, to_node_id, condition_type, condition_value FROM workflow_edges ORDER BY id ASC"
        ).fetchall()

    nodes = [_row_to_node(row) for row in rows]
    edges = [
        WorkflowEdge(
            id=int(row[0]),
            from_node_id=row[1],
            to_node_id=row[2],
            condition_type=row[3],
            condition_value=row[4],
        )
        for row in edge_rows
    ]
    return WorkflowGraph(nodes=nodes, edges=edges)


def list_nodes(db_path: Path) -> list[WorkflowNode]:
    return load_workflow_graph(db_path).nodes


def get_node(db_path: Path, node_id: str) -> WorkflowNode | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, name, description, node_type, pass_index, start_node, enabled,
                   executor_path, pre_hook_path, post_hook_path,
                   COALESCE(NULLIF(system_prompt_path, ''), '') AS system_prompt_path,
                   COALESCE(NULLIF(prompt_template_path, ''), '') AS prompt_template_path,
                   use_prev_output, allowed_tools, send_response,
                   input_schema, output_schema, timeout_seconds, max_llm_calls,
                   route_label, route_description, router_mode, router_patterns, metadata
            FROM workflow_nodes
            WHERE id = ?
            """,
            (node_id,),
        ).fetchone()
    return _row_to_node(row) if row else None


def upsert_node(db_path: Path, node: WorkflowNode) -> None:
    with sqlite3.connect(db_path) as conn:
        _upsert_node_conn(conn, node)
        conn.commit()


def _upsert_node_conn(conn: sqlite3.Connection, node: WorkflowNode) -> None:
    if node.start_node:
        conn.execute("UPDATE workflow_nodes SET start_node = 0 WHERE start_node = 1 AND id != ?", (node.id,))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_nodes)")}
    has_skill_id = "skill_id" in columns

    insert_columns = [
        "id",
        "name",
        "description",
        "node_type",
        "pass_index",
        "start_node",
        "enabled",
        "executor_path",
        "pre_hook_path",
        "post_hook_path",
        "system_prompt_path",
        "prompt_template_path",
        "use_prev_output",
        "allowed_tools",
        "send_response",
        "input_schema",
        "output_schema",
        "timeout_seconds",
        "max_llm_calls",
        "route_label",
        "route_description",
        "router_mode",
        "router_patterns",
        "metadata",
    ]
    values: list[object] = [
        node.id,
        node.name,
        node.description,
        node.node_type,
        node.pass_index,
        1 if node.start_node else 0,
        1 if node.enabled else 0,
        node.executor_path,
        node.pre_hook_path or "",
        node.post_hook_path or "",
        node.system_prompt_path or "",
        node.prompt_template_path or "",
        1 if node.use_prev_output else 0,
        json.dumps(node.allowed_tools, ensure_ascii=False),
        1 if node.send_response else 0,
        json.dumps(node.input_schema, ensure_ascii=False) if node.input_schema is not None else "",
        json.dumps(node.output_schema, ensure_ascii=False) if node.output_schema is not None else "",
        node.timeout_seconds,
        node.max_llm_calls,
        node.route_label or "",
        node.route_description or "",
        node.router_mode,
        json.dumps(node.router_patterns, ensure_ascii=False),
        json.dumps(node.metadata, ensure_ascii=False),
    ]
    if has_skill_id:
        insert_columns.insert(1, "skill_id")
        values.insert(1, node.id)

    update_columns = [column for column in insert_columns if column != "id" and column != "skill_id"]
    conn.execute(
        f"""
        INSERT INTO workflow_nodes ({", ".join(insert_columns)})
        VALUES ({", ".join(["?"] * len(insert_columns))})
        ON CONFLICT(id) DO UPDATE SET
            {", ".join(f"{column} = excluded.{column}" for column in update_columns)}
        """,
        values,
    )


def delete_node(db_path: Path, node_id: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM workflow_edges WHERE from_node_id = ? OR to_node_id = ?", (node_id, node_id))
        conn.execute("DELETE FROM workflow_nodes WHERE id = ?", (node_id,))
        conn.commit()


def upsert_edge(db_path: Path, edge: WorkflowEdge) -> WorkflowEdge:
    with sqlite3.connect(db_path) as conn:
        saved = _upsert_edge_conn(conn, edge)
        conn.commit()
        return saved


def _upsert_edge_conn(conn: sqlite3.Connection, edge: WorkflowEdge) -> WorkflowEdge:
    if edge.id:
        conn.execute(
            """
            UPDATE workflow_edges
            SET from_node_id = ?, to_node_id = ?, condition_type = ?, condition_value = ?
            WHERE id = ?
            """,
            (edge.from_node_id, edge.to_node_id, edge.condition_type, edge.condition_value, edge.id),
        )
        return edge

    cursor = conn.execute(
        """
        INSERT INTO workflow_edges (from_node_id, to_node_id, condition_type, condition_value)
        VALUES (?, ?, ?, ?)
        """,
        (edge.from_node_id, edge.to_node_id, edge.condition_type, edge.condition_value),
    )
    return WorkflowEdge(
        id=int(cursor.lastrowid),
        from_node_id=edge.from_node_id,
        to_node_id=edge.to_node_id,
        condition_type=edge.condition_type,
        condition_value=edge.condition_value,
    )


def delete_edge(db_path: Path, edge_id: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM workflow_edges WHERE id = ?", (edge_id,))
        conn.commit()


def detect_node_hooks(node: WorkflowNode, repo_root: Path) -> dict[str, object]:
    """Return UI-facing lifecycle information by scanning the node directory."""
    base_dir = None
    if node.executor_path:
        base_dir = (repo_root / node.executor_path).resolve().parent
    elif node.pre_hook_path:
        base_dir = (repo_root / node.pre_hook_path).resolve().parent
    elif node.post_hook_path:
        base_dir = (repo_root / node.post_hook_path).resolve().parent

    def _resolve(explicit: str | None, filename: str) -> str | None:
        if explicit:
            return explicit
        if base_dir:
            candidate = base_dir / filename
            if candidate.exists():
                return str(candidate.relative_to(repo_root))
        return None

    effective_pre = _resolve(node.pre_hook_path, "pre_hook.py")
    effective_post = _resolve(node.post_hook_path, "post_hook.py")
    effective_run = node.executor_path or _resolve(None, "run.py")

    return {
        "has_pre_hook": bool(effective_pre and (repo_root / effective_pre).exists()),
        "has_run": bool(effective_run and (repo_root / effective_run).exists()),
        "has_post_hook": bool(effective_post and (repo_root / effective_post).exists()),
        "effective_pre_hook_path": effective_pre,
        "effective_executor_path": effective_run,
        "effective_post_hook_path": effective_post,
    }


def try_pattern_route(node: WorkflowNode, user_msg: str) -> dict | None:
    for pattern in node.router_patterns:
        match = re.search(pattern, user_msg, re.IGNORECASE | re.DOTALL)
        if match:
            return {key: value.strip() for key, value in match.groupdict().items() if value is not None}
    return None
