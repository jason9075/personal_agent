"""SQLite-backed node-first workflow graph storage."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkflowNode:
    id: str
    name: str
    description: str
    model_name: str
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
    send_response: bool
    timeout_seconds: int


@dataclass(frozen=True)
class WorkflowEdge:
    id: int
    from_node_id: str
    to_node_id: str


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
        return [node for node in self.enabled_nodes() if node.id in candidate_ids]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_nodes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT 'gpt-5.4',
    node_type TEXT NOT NULL DEFAULT 'agent',
    pass_index INTEGER NOT NULL DEFAULT 1,
    start_node INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    executor_path TEXT NOT NULL DEFAULT '',
    pre_hook_path TEXT NOT NULL DEFAULT '',
    post_hook_path TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    prompt_template TEXT NOT NULL DEFAULT '',
    system_prompt_path TEXT NOT NULL DEFAULT '',
    prompt_template_path TEXT NOT NULL DEFAULT '',
    use_prev_output INTEGER NOT NULL DEFAULT 1,
    send_response INTEGER NOT NULL DEFAULT 1,
    timeout_seconds INTEGER NOT NULL DEFAULT 600
);

CREATE TABLE IF NOT EXISTS workflow_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    FOREIGN KEY (from_node_id) REFERENCES workflow_nodes(id),
    FOREIGN KEY (to_node_id) REFERENCES workflow_nodes(id)
);
"""


_SEED_NODES: list[WorkflowNode] = [
    WorkflowNode(
        id="intent-router",
        name="Intent Router",
        description="Top-level entry node. Either reply directly or delegate to a reachable domain node.",
        model_name="gpt-5.4",
        node_type="router",
        pass_index=1,
        start_node=True,
        enabled=True,
        executor_path="nodes/intent-router/run.py",
        pre_hook_path=None,
        post_hook_path=None,
        system_prompt_path="nodes/intent-router/system.md",
        prompt_template_path="nodes/intent-router/tool_router.md",
        use_prev_output=False,
        send_response=False,
        timeout_seconds=120,
    ),
    WorkflowNode(
        id="finance",
        name="Finance",
        description="Finance domain node. Handles finance questions directly or delegates to finance subflows.",
        model_name="gpt-5.4",
        node_type="router",
        pass_index=2,
        start_node=False,
        enabled=True,
        executor_path="nodes/finance/run.py",
        pre_hook_path=None,
        post_hook_path=None,
        system_prompt_path="nodes/finance/system.md",
        prompt_template_path="nodes/finance/planner.md",
        use_prev_output=True,
        send_response=False,
        timeout_seconds=180,
    ),
    WorkflowNode(
        id="finance-report",
        name="Finance Report",
        description="Download the selected finance RSS episode, transcribe audio, and generate a markdown digest.",
        model_name="gpt-5.4",
        node_type="agent",
        pass_index=3,
        start_node=False,
        enabled=True,
        executor_path="nodes/finance-report/run.py",
        pre_hook_path=None,
        post_hook_path=None,
        system_prompt_path="nodes/finance-report/system.md",
        prompt_template_path=None,
        use_prev_output=True,
        send_response=True,
        timeout_seconds=7200,
    ),
    WorkflowNode(
        id="finance-schedule",
        name="Finance Schedule",
        description="Manage finance report schedules stored in SQLite.",
        model_name="gpt-5.4",
        node_type="tool",
        pass_index=3,
        start_node=False,
        enabled=True,
        executor_path="nodes/finance-schedule/run.py",
        pre_hook_path=None,
        post_hook_path=None,
        system_prompt_path=None,
        prompt_template_path=None,
        use_prev_output=True,
        send_response=True,
        timeout_seconds=60,
    ),
    WorkflowNode(
        id="echo",
        name="Echo",
        description="Testing node that returns the extracted text directly.",
        model_name="gpt-5.4",
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
        send_response=True,
        timeout_seconds=30,
    ),
]

_SEED_EDGES: list[tuple[str, str]] = [
    ("intent-router", "finance"),
    ("intent-router", "echo"),
    ("finance", "finance-report"),
    ("finance", "finance-schedule"),
]


def ensure_workflow_db(db_path: Path) -> None:
    """Create tables and seed the base workflow when the DB is empty."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS workflow_single_start_node
            ON workflow_nodes(start_node)
            WHERE start_node = 1
            """
        )
        _seed_nodes_and_edges(conn)
        conn.commit()


def _seed_nodes_and_edges(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM workflow_nodes").fetchone()[0]
    if count != 0:
        return
    for node in _SEED_NODES:
        _upsert_node_conn(conn, node)
    for from_id, to_id in _SEED_EDGES:
        _upsert_edge_conn(
            conn,
            WorkflowEdge(
                id=0,
                from_node_id=from_id,
                to_node_id=to_id,
            ),
        )


def _row_to_node(row: sqlite3.Row | tuple) -> WorkflowNode:
    return WorkflowNode(
        id=row[0],
        name=row[1],
        description=row[2],
        model_name=row[3] or "gpt-5.4",
        node_type=row[4],
        pass_index=int(row[5]),
        start_node=bool(row[6]),
        enabled=bool(row[7]),
        executor_path=row[8],
        pre_hook_path=row[9] or None,
        post_hook_path=row[10] or None,
        system_prompt_path=row[11] or None,
        prompt_template_path=row[12] or None,
        use_prev_output=bool(row[13]),
        send_response=bool(row[14]),
        timeout_seconds=int(row[15]),
    )


def load_workflow_graph(db_path: Path) -> WorkflowGraph:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, description, model_name, node_type, pass_index, start_node, enabled,
                   executor_path, pre_hook_path, post_hook_path,
                   COALESCE(NULLIF(system_prompt_path, ''), '') AS system_prompt_path,
                   COALESCE(NULLIF(prompt_template_path, ''), '') AS prompt_template_path,
                   use_prev_output, send_response, timeout_seconds
            FROM workflow_nodes
            ORDER BY start_node DESC, id ASC
            """
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT id, from_node_id, to_node_id FROM workflow_edges ORDER BY id ASC"
        ).fetchall()

    nodes = [_row_to_node(row) for row in rows]
    edges = [
        WorkflowEdge(
            id=int(row[0]),
            from_node_id=row[1],
            to_node_id=row[2],
        )
        for row in edge_rows
    ]
    return WorkflowGraph(nodes=nodes, edges=edges)


def list_nodes(db_path: Path) -> list[WorkflowNode]:
    return load_workflow_graph(db_path).nodes


def list_edges(db_path: Path) -> list[WorkflowEdge]:
    return load_workflow_graph(db_path).edges


def get_node(db_path: Path, node_id: str) -> WorkflowNode | None:
    return load_workflow_graph(db_path).node_by_id(node_id)


def upsert_node(db_path: Path, node: WorkflowNode) -> WorkflowNode:
    with sqlite3.connect(db_path) as conn:
        _upsert_node_conn(conn, node)
        conn.commit()
    return node


def _upsert_node_conn(conn: sqlite3.Connection, node: WorkflowNode) -> None:
    if node.start_node:
        conn.execute("UPDATE workflow_nodes SET start_node = 0 WHERE start_node = 1 AND id != ?", (node.id,))
    insert_columns = [
        "id",
        "name",
        "description",
        "model_name",
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
        "send_response",
        "timeout_seconds",
    ]
    values: list[object] = [
        node.id,
        node.name,
        node.description,
        node.model_name,
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
        1 if node.send_response else 0,
        node.timeout_seconds,
    ]
    update_columns = [column for column in insert_columns if column != "id"]
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
    existing = conn.execute(
        """
        SELECT id FROM workflow_edges
        WHERE from_node_id = ? AND to_node_id = ?
        LIMIT 1
        """,
        (edge.from_node_id, edge.to_node_id),
    ).fetchone()
    if existing:
        return WorkflowEdge(
            id=int(existing[0]),
            from_node_id=edge.from_node_id,
            to_node_id=edge.to_node_id,
        )
    cursor = conn.execute(
        """
        INSERT INTO workflow_edges (from_node_id, to_node_id)
        VALUES (?, ?)
        """,
        (edge.from_node_id, edge.to_node_id),
    )
    return WorkflowEdge(
        id=int(cursor.lastrowid),
        from_node_id=edge.from_node_id,
        to_node_id=edge.to_node_id,
    )


def delete_edge(db_path: Path, edge_id: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM workflow_edges WHERE id = ?", (edge_id,))
        conn.commit()


def scan_node_hooks(repo_root: Path, node_executor_path: str) -> tuple[str | None, str | None, str | None]:
    executor = repo_root / node_executor_path
    if not executor.exists():
        return None, None, None
    node_dir = executor.parent
    pre_hook = node_dir / "pre_hook.py"
    run_py = node_dir / "run.py"
    post_hook = node_dir / "post_hook.py"
    return (
        str(pre_hook.relative_to(repo_root)) if pre_hook.exists() else None,
        str(run_py.relative_to(repo_root)) if run_py.exists() else None,
        str(post_hook.relative_to(repo_root)) if post_hook.exists() else None,
    )


def detect_node_hooks(node: WorkflowNode, repo_root: Path) -> dict[str, str | bool | None]:
    scanned_pre, scanned_run, scanned_post = scan_node_hooks(repo_root, node.executor_path)
    effective_pre = node.pre_hook_path or scanned_pre
    effective_run = node.executor_path or scanned_run
    effective_post = node.post_hook_path or scanned_post
    return {
        "has_pre_hook": bool(effective_pre),
        "has_run": bool(effective_run),
        "has_post_hook": bool(effective_post),
        "scanned_pre_hook_path": scanned_pre,
        "scanned_executor_path": scanned_run,
        "scanned_post_hook_path": scanned_post,
        "effective_pre_hook_path": effective_pre,
        "effective_executor_path": effective_run,
        "effective_post_hook_path": effective_post,
    }


def try_pattern_route(node: WorkflowNode, user_msg: str) -> dict | None:
    return None
