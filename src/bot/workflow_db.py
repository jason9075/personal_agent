"""SQLite-backed skills and workflow graph storage.

Skills replace SKILL.md files — metadata managed via DB (and future web UI).
WorkflowGraph defines which skills are available at each pass and how passes
connect to each other via conditional edges.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .skills import SkillActionResult


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillDef:
    id: str
    display_name: str
    description: str
    router_mode: str            # 'direct_regex' | 'llm' | 'always'
    router_patterns: list[str]  # named-group regex strings
    script_path: str            # relative to repo root, e.g. "skills/echo/run.py"
    system_prompt: str
    pass2_mode: str             # 'never' | 'always' | 'optional'
    enabled: bool


@dataclass(frozen=True)
class WorkflowNode:
    id: str           # e.g. "p1:finance-report"
    pass_index: int
    skill_id: str
    enabled: bool


@dataclass(frozen=True)
class WorkflowEdge:
    id: int
    from_node_id: str
    to_node_id: str
    condition_type: str   # 'always' | 'returncode_eq' | 'output_contains'
    condition_value: str


@dataclass(frozen=True)
class WorkflowGraph:
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    skills: dict[str, SkillDef]   # skill_id -> SkillDef

    def nodes_at_pass(self, pass_index: int) -> list[WorkflowNode]:
        return [n for n in self.nodes if n.pass_index == pass_index and n.enabled]

    def skill_for_node(self, node: WorkflowNode) -> SkillDef | None:
        return self.skills.get(node.skill_id)

    def successors(self, from_node_id: str, result: SkillActionResult) -> list[WorkflowNode]:
        """Return enabled successor nodes whose edge conditions are satisfied."""
        reachable_ids = {
            e.to_node_id
            for e in self.edges
            if e.from_node_id == from_node_id and _edge_condition_met(e, result)
        }
        return [n for n in self.nodes if n.id in reachable_ids and n.enabled]

    @property
    def max_passes(self) -> int:
        if not self.nodes:
            return 1
        return max(n.pass_index for n in self.nodes)


def _edge_condition_met(edge: WorkflowEdge, result: SkillActionResult) -> bool:
    if edge.condition_type == "always":
        return True
    if edge.condition_type == "returncode_eq":
        try:
            return result.returncode == int(edge.condition_value)
        except (ValueError, TypeError):
            return False
    if edge.condition_type == "output_contains":
        return edge.condition_value in (result.stdout or "")
    return False


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    router_mode TEXT NOT NULL DEFAULT 'llm',
    router_patterns TEXT NOT NULL DEFAULT '[]',
    script_path TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    pass2_mode TEXT NOT NULL DEFAULT 'always',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workflow_nodes (
    id TEXT PRIMARY KEY,
    pass_index INTEGER NOT NULL,
    skill_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (skill_id) REFERENCES skills(id)
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

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

_SEED_SKILLS: list[dict] = [
    {
        "id": "finance-report",
        "display_name": "Finance Report",
        "description": (
            "Generate finance report from RSS feed sources. "
            "Triggered by keywords: finance, report, 財經, 報告, 來源, source."
        ),
        "router_mode": "direct_regex",
        "router_patterns": json.dumps([]),
        "script_path": "skills/finance-report/run.py",
        "system_prompt": "",
        "pass2_mode": "optional",
    },
    {
        "id": "finance-schedule",
        "display_name": "Finance Schedule",
        "description": (
            "Manage scheduled finance report jobs (list/add/update/delete/enable/disable). "
            "Triggered by: schedule, cron, 排程 combined with finance/財經."
        ),
        "router_mode": "direct_regex",
        "router_patterns": json.dumps([]),
        "script_path": "skills/finance-schedule/run.py",
        "system_prompt": "",
        "pass2_mode": "never",
    },
    {
        "id": "echo",
        "display_name": "Echo",
        "description": "Echo skill for testing Pass 1 routing and direct reply. Triggered by: '啟用echo skill <text>'.",
        "router_mode": "direct_regex",
        "router_patterns": json.dumps([r"啟用echo skill\s+(?P<text>.+)"]),
        "script_path": "skills/echo/run.py",
        "system_prompt": "",
        "pass2_mode": "never",
    },
]

_SEED_NODES: list[dict] = [
    {"id": "p1:finance-report",   "pass_index": 1, "skill_id": "finance-report"},
    {"id": "p1:finance-schedule", "pass_index": 1, "skill_id": "finance-schedule"},
    {"id": "p1:echo",             "pass_index": 1, "skill_id": "echo"},
]

# No edges for now — pass2_mode on each SkillDef controls synthesis.
# Future: add "p1:finance-report → p2:synthesis" edges here.
_SEED_EDGES: list[dict] = []


# ---------------------------------------------------------------------------
# DB lifecycle
# ---------------------------------------------------------------------------


def ensure_workflow_db(db_path: Path) -> None:
    """Create tables and seed initial data if the DB is empty."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
        # Seed only if empty
        count = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
        if count == 0:
            _seed(conn)


def _seed(conn: sqlite3.Connection) -> None:
    for s in _SEED_SKILLS:
        conn.execute(
            """
            INSERT OR IGNORE INTO skills
              (id, display_name, description, router_mode, router_patterns,
               script_path, system_prompt, pass2_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                s["id"], s["display_name"], s["description"], s["router_mode"],
                s["router_patterns"], s["script_path"], s["system_prompt"], s["pass2_mode"],
            ),
        )
    for n in _SEED_NODES:
        conn.execute(
            "INSERT OR IGNORE INTO workflow_nodes (id, pass_index, skill_id) VALUES (?, ?, ?)",
            (n["id"], n["pass_index"], n["skill_id"]),
        )
    for e in _SEED_EDGES:
        conn.execute(
            """
            INSERT OR IGNORE INTO workflow_edges
              (from_node_id, to_node_id, condition_type, condition_value)
            VALUES (?, ?, ?, ?)
            """,
            (e["from"], e["to"], e.get("condition_type", "always"), e.get("condition_value", "")),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def load_workflow_graph(db_path: Path) -> WorkflowGraph:
    """Load the full workflow graph (nodes + edges + skills) from DB."""
    with sqlite3.connect(db_path) as conn:
        skill_rows = conn.execute(
            "SELECT id, display_name, description, router_mode, router_patterns, "
            "script_path, system_prompt, pass2_mode, enabled FROM skills"
        ).fetchall()
        node_rows = conn.execute(
            "SELECT id, pass_index, skill_id, enabled FROM workflow_nodes"
        ).fetchall()
        edge_rows = conn.execute(
            "SELECT id, from_node_id, to_node_id, condition_type, condition_value FROM workflow_edges"
        ).fetchall()

    skills = {
        row[0]: SkillDef(
            id=row[0],
            display_name=row[1],
            description=row[2],
            router_mode=row[3],
            router_patterns=json.loads(row[4] or "[]"),
            script_path=row[5],
            system_prompt=row[6],
            pass2_mode=row[7],
            enabled=bool(row[8]),
        )
        for row in skill_rows
    }
    nodes = [WorkflowNode(id=r[0], pass_index=r[1], skill_id=r[2], enabled=bool(r[3])) for r in node_rows]
    edges = [WorkflowEdge(id=r[0], from_node_id=r[1], to_node_id=r[2], condition_type=r[3], condition_value=r[4]) for r in edge_rows]
    return WorkflowGraph(nodes=nodes, edges=edges, skills=skills)


def get_skill(db_path: Path, skill_id: str) -> SkillDef | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, display_name, description, router_mode, router_patterns, "
            "script_path, system_prompt, pass2_mode, enabled FROM skills WHERE id = ?",
            (skill_id,),
        ).fetchone()
    if row is None:
        return None
    return SkillDef(
        id=row[0], display_name=row[1], description=row[2], router_mode=row[3],
        router_patterns=json.loads(row[4] or "[]"), script_path=row[5],
        system_prompt=row[6], pass2_mode=row[7], enabled=bool(row[8]),
    )


def list_skills(db_path: Path) -> list[SkillDef]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, display_name, description, router_mode, router_patterns, "
            "script_path, system_prompt, pass2_mode, enabled FROM skills ORDER BY id"
        ).fetchall()
    return [
        SkillDef(
            id=r[0], display_name=r[1], description=r[2], router_mode=r[3],
            router_patterns=json.loads(r[4] or "[]"), script_path=r[5],
            system_prompt=r[6], pass2_mode=r[7], enabled=bool(r[8]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Write (used by web UI)
# ---------------------------------------------------------------------------


def upsert_skill(db_path: Path, skill: SkillDef) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO skills
              (id, display_name, description, router_mode, router_patterns,
               script_path, system_prompt, pass2_mode, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
              display_name = excluded.display_name,
              description = excluded.description,
              router_mode = excluded.router_mode,
              router_patterns = excluded.router_patterns,
              script_path = excluded.script_path,
              system_prompt = excluded.system_prompt,
              pass2_mode = excluded.pass2_mode,
              enabled = excluded.enabled,
              updated_at = excluded.updated_at
            """,
            (
                skill.id, skill.display_name, skill.description, skill.router_mode,
                json.dumps(skill.router_patterns), skill.script_path,
                skill.system_prompt, skill.pass2_mode, 1 if skill.enabled else 0,
            ),
        )
        conn.commit()


def upsert_node(db_path: Path, node: WorkflowNode) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO workflow_nodes (id, pass_index, skill_id, enabled)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              pass_index = excluded.pass_index,
              skill_id = excluded.skill_id,
              enabled = excluded.enabled
            """,
            (node.id, node.pass_index, node.skill_id, 1 if node.enabled else 0),
        )
        conn.commit()


def delete_node(db_path: Path, node_id: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM workflow_edges WHERE from_node_id = ? OR to_node_id = ?", (node_id, node_id))
        conn.execute("DELETE FROM workflow_nodes WHERE id = ?", (node_id,))
        conn.commit()


def upsert_edge(db_path: Path, edge: WorkflowEdge) -> WorkflowEdge:
    with sqlite3.connect(db_path) as conn:
        if edge.id:
            conn.execute(
                "UPDATE workflow_edges SET from_node_id=?, to_node_id=?, condition_type=?, condition_value=? WHERE id=?",
                (edge.from_node_id, edge.to_node_id, edge.condition_type, edge.condition_value, edge.id),
            )
            conn.commit()
            return edge
        cursor = conn.execute(
            "INSERT INTO workflow_edges (from_node_id, to_node_id, condition_type, condition_value) VALUES (?, ?, ?, ?)",
            (edge.from_node_id, edge.to_node_id, edge.condition_type, edge.condition_value),
        )
        conn.commit()
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


# ---------------------------------------------------------------------------
# Pattern matching helper (used by engine)
# ---------------------------------------------------------------------------


def try_pattern_route(skill_def: SkillDef, user_msg: str) -> dict | None:
    """Try named-group regex patterns from skill's router_patterns. Returns groupdict or None."""
    for pattern in skill_def.router_patterns:
        m = re.search(pattern, user_msg, re.IGNORECASE | re.DOTALL)
        if m:
            return {k: v.strip() for k, v in m.groupdict().items() if v is not None}
    return None
