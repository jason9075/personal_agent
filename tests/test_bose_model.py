from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.bot import engine, llm
from src.bot.config import BOSE_CONTROL_CHANNEL_ID
from src.bot.nodes import NodeActionResult
from src.bot.workflow_db import ensure_workflow_db, load_workflow_graph


class BoseModelTest(unittest.TestCase):
    def test_bose_uses_one_luna_max_call_and_other_channels_keep_their_model(self) -> None:
        root = Path(__file__).resolve().parents[1]
        original = engine._execute_executor

        def executor(node, payload, previous, repo):
            if node.id not in {"bose-player", "echo"}:
                return original(node, payload, previous, repo)
            return NodeActionResult(node.id, payload, '{"kind":"reply","reply":"volume result"}', "", 0)

        for channel, disabled, target, model, effort in (
            (BOSE_CONTROL_CHANNEL_ID, False, "bose-player", "gpt-6-luna", "max"),
            ("123", False, "echo", "gpt-6.1-sol", ""),
            (BOSE_CONTROL_CHANNEL_ID, True, None, "gpt-6.1-sol", ""),
        ):
            with self.subTest(channel=channel, disabled=disabled), tempfile.TemporaryDirectory() as directory:
                db = Path(directory) / "workflow.db"
                ensure_workflow_db(db)
                if disabled:
                    with sqlite3.connect(db) as conn:
                        conn.execute("UPDATE workflow_nodes SET enabled=0 WHERE id='bose-router'")
                decision = {
                          "decision": "use_next_node", "next_node_id": "bose-player",
                          "args": {"action": "get_volume"},
                } if target else {"decision": "reply", "reply": "Bose 控制目前停用"}
                if target:
                    decision["next_node_id"] = target
                trace: list[str] = []
                with (patch.object(engine, "_execute_executor", side_effect=executor),
                      patch.object(engine, "run_codex_request", return_value=json.dumps(decision)) as infer):
                    result = engine.execute_workflow(
                        engine.WorkflowRequest(message="volume", channel_id=channel), db, root,
                        trace_db_path=Path(directory) / "trace.db", node_trace=trace,
                    )
                    self.assertEqual(result, "volume result" if target else "Bose 控制目前停用")
                    if target == "echo":
                        self.assertEqual(trace, ["intent-router", "echo"])
                    infer.assert_called_once()
                    request = infer.call_args.args[0]
                    self.assertEqual((request.model_name, request.reasoning_effort), (model, effort))

    def test_reasoning_effort_is_explicit_in_codex_command_only_when_requested(self) -> None:
        for effort in ("max", ""):
            with (self.subTest(effort=effort),
                  patch.object(llm.subprocess, "run", return_value=SimpleNamespace(
                      returncode=0, stdout="ok", stderr="",
                  )) as run, patch.object(llm, "_log_request")):
                llm.run_codex_request(llm.LlmRequest(
                    node_id="bose-router", model_name="gpt-6-luna", node_prompt_path=None,
                    reasoning_effort=effort,
                ), Path.cwd())
                argv = run.call_args.args[0]
                self.assertEqual(argv[argv.index("-m") + 1], "gpt-6-luna")
                if effort:
                    self.assertEqual(argv[argv.index("-c") + 1], 'model_reasoning_effort="max"')
                else:
                    self.assertNotIn("-c", argv)

    def test_migration_preserves_existing_models_and_adds_router_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "workflow.db"
            ensure_workflow_db(db)
            with sqlite3.connect(db) as conn:
                for target in ("finance", "echo", "schedule"):
                    conn.execute("INSERT INTO workflow_edges (from_node_id,to_node_id) VALUES ('bose-router',?)", (target,))
                conn.execute("INSERT INTO workflow_edges (from_node_id,to_node_id) VALUES ('intent-router','bose-player')")
                conn.execute("UPDATE workflow_nodes SET node_prompt_path='nodes/intent-router/node.md' WHERE id='bose-router'")
            ensure_workflow_db(db)
            ensure_workflow_db(db)
            graph = load_workflow_graph(db)
            router = graph.node_by_id("bose-router")
            entry = graph.start_node()
            assert router and entry
            self.assertEqual(router.model_name, "gpt-6-luna")
            self.assertEqual(entry.id, "intent-router")
            self.assertEqual(entry.model_name, "gpt-6.1-sol")
            self.assertEqual(len(graph.edges), len({(e.from_node_id, e.to_node_id) for e in graph.edges}))
            self.assertEqual({n.id for n in graph.candidate_targets(router.id)}, {"bose-player"})
            self.assertEqual(router.node_prompt_path, "nodes/bose-router/node.md")
            root_targets = {n.id for n in graph.candidate_targets(entry.id)}
            self.assertTrue({"finance", "echo", "schedule", "bose-router"} <= root_targets)
            self.assertNotIn("bose-player", root_targets)

    def test_bose_cannot_delegate_to_finance_even_if_model_requests_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "workflow.db"
            ensure_workflow_db(db)
            with patch.object(engine, "run_codex_request", return_value=json.dumps({
                "decision": "use_next_node", "next_node_id": "finance", "args": {},
            })):
                with self.assertRaisesRegex(RuntimeError, "unreachable next node 'finance'"):
                    engine.execute_workflow(engine.WorkflowRequest(
                        message="finance", start_node_id="bose-router", channel_id=BOSE_CONTROL_CHANNEL_ID,
                    ), db, Path(__file__).resolve().parents[1], trace_db_path=Path(directory) / "trace.db")


if __name__ == "__main__":
    unittest.main()
