from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from src.bot.engine import WorkflowRequest, execute_workflow
from src.bot.nodes import NodeExecutionError
from src.bot.workflow_db import ensure_workflow_db, load_workflow_graph


spec = importlib.util.spec_from_file_location(
    "bose_player", Path(__file__).resolve().parents[1] / "nodes/bose-player/run.py",
)
assert spec and spec.loader
player = importlib.util.module_from_spec(spec)
spec.loader.exec_module(player)


class YouTubeTargetTest(unittest.TestCase):
    def test_video_urls_are_normalized_without_playlists(self) -> None:
        for url in (
            "https://youtu.be/jNQXAC9IVRw?t=3",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw&list=anything",
            "https://m.youtube.com/shorts/jNQXAC9IVRw",
            "<https://www.youtube.com/watch?v=jNQXAC9IVRw>",
        ):
            with self.subTest(url=url):
                self.assertEqual(player.youtube_target(url), "https://www.youtube.com/watch?v=jNQXAC9IVRw")

    def test_search_terms_are_data(self) -> None:
        self.assertEqual(player.youtube_target("周杰倫 稻香"), "ytsearch1:周杰倫 稻香")
        self.assertEqual(player.youtube_target("$(touch /tmp/nope)"), "ytsearch1:$(touch /tmp/nope)")

    def test_non_youtube_sources_and_playlists_are_rejected(self) -> None:
        for value in (
            "https://example.com/test", "https://youtube.com.evil.test/watch?v=jNQXAC9IVRw",
            "file:///etc/passwd", "/etc/passwd", "--script=test", "",
            "https://youtube.com/playlist?list=test",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                player.youtube_target(value)


class BoseMigrationTest(unittest.TestCase):
    def test_existing_workflow_is_extended_once_and_preserves_node_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "workflow.sqlite3"
            ensure_workflow_db(db)
            with sqlite3.connect(db) as conn:
                conn.execute("DELETE FROM workflow_edges WHERE to_node_id = 'bose-player'")
                conn.execute("DELETE FROM workflow_nodes WHERE id = 'bose-player'")
            ensure_workflow_db(db)
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE workflow_nodes SET enabled = 0 WHERE id = 'bose-player'")
            ensure_workflow_db(db)
            graph = load_workflow_graph(db)
            node = graph.node_by_id("bose-player")
            self.assertIsNotNone(node)
            assert node
            self.assertFalse(node.enabled)
            self.assertIsNone(node.model_name)
            self.assertEqual(node.executor_path, "nodes/bose-player/run.py")
            self.assertEqual(len([edge for edge in graph.edges
                                  if edge.to_node_id == node.id and edge.from_node_id == "bose-router"]), 1)


class PlaybackRegressionTest(unittest.TestCase):
    def test_resolver_checks_streams_before_returning_a_format(self) -> None:
        info = {"id": "bk_Ob2aVXEM", "url": "https://example.test/manifest.m3u8"}
        with patch.object(player, "command", return_value=json.dumps(info)) as run:
            with patch.object(player.shutil, "which", return_value="/usr/bin/deno"):
                self.assertEqual(player.resolve_video("佳佳老師說故事"), info)
        argv = run.call_args.args[0]
        self.assertIn("--check-formats", argv)
        self.assertEqual(argv[argv.index("--format") + 1], "bestaudio/best[height<=360]/best")
        self.assertEqual(argv[-1], "ytsearch1:佳佳老師說故事")

    def test_headers_are_set_before_loading_the_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (patch.object(player, "STATE_DIR", Path(temp_dir)),
                  patch.object(player.shutil, "which", return_value="/usr/bin/mpv"),
                  patch.object(player, "stop"), patch.object(player, "command") as run,
                  patch.object(player, "ipc", side_effect=[None, None, 0.1, "pipewire"]) as ipc):
                player.start_player({"url": "https://example.test/audio", "http_headers": {
                    "User-Agent": "YouTube client", "Accept": "*/*,text/plain",
                }}, "bluez_output.test")
                self.assertEqual(ipc.call_args_list[:2], [
                    call(["set_property", "http-header-fields", [
                        "User-Agent: YouTube client", "Accept: */*,text/plain",
                    ]]),
                    call(["loadfile", "https://example.test/audio"]),
                ])
                self.assertIn("--audio-device=pipewire/bluez_output.test", run.call_args.args[0])
                self.assertNotIn("https://example.test/audio", run.call_args.args[0])

    def test_early_403_reports_stream_failure_without_waiting_twenty_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (patch.object(player, "STATE_DIR", Path(temp_dir)),
                  patch.object(player.shutil, "which", return_value="/usr/bin/mpv"),
                  patch.object(player, "stop"),
                  patch.object(player, "command", side_effect=["", "failed"]),
                  patch.object(player, "ipc", side_effect=OSError),
                  patch.object(player, "player_failure", return_value="YouTube HTTP 403"),
                  patch.object(player.time, "sleep") as sleep):
                with self.assertRaisesRegex(RuntimeError, "YouTube HTTP 403"):
                    player.start_player({"url": "https://example.test/audio"}, "test")
                sleep.assert_not_called()

    def test_failed_executor_marks_run_and_node_as_error_and_stops_chaining(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(__file__).resolve().parents[1]
            db, trace = Path(temp_dir) / "workflow.db", Path(temp_dir) / "trace.db"
            ensure_workflow_db(db)
            with sqlite3.connect(db) as conn:
                conn.execute("INSERT INTO workflow_edges (from_node_id,to_node_id) VALUES ('bose-player','echo')")
            with self.assertRaisesRegex(NodeExecutionError, "只支援 YouTube"):
                execute_workflow(WorkflowRequest(start_node_id="bose-player", args={
                    "action": "play", "url": "https://example.test/video",
                }), db, root, trace_db_path=trace)
            with sqlite3.connect(trace) as conn:
                self.assertEqual(conn.execute("SELECT status FROM workflow_runs").fetchone()[0], "error")
                self.assertEqual(conn.execute("SELECT node_id,status FROM workflow_node_logs").fetchall(), [
                    ("bose-player", "error"),
                ])


class VolumeQueryTest(unittest.TestCase):
    def test_query_reports_both_volumes_without_setting_them(self) -> None:
        objects = [{"id": 61, "info": {"props": {
            "media.class": "Audio/Sink", "api.bluez5.address": "78:2B:64:78:CA:A9",
        }}}]
        with (patch.object(player, "bose_address", return_value="78:2B:64:78:CA:A9"),
              patch.object(player, "command", side_effect=[json.dumps(objects), "Volume: 0.43 [MUTED]"]) as run,
              patch.object(player, "ipc", side_effect=[40.0, False]) as ipc):
            reply = player.get_volume()
            self.assertIn("Bose 喇叭音量 43%（已靜音）", reply)
            self.assertIn("播放器音量 40%", reply)
            self.assertEqual(run.call_args_list, [call(["pw-dump"]), call(["wpctl", "get-volume", "61"])])
            self.assertTrue(all(c.args[0][0] == "get_property" for c in ipc.call_args_list))

    def test_speaker_volume_is_available_without_a_player(self) -> None:
        with (patch.object(player, "bose_address", return_value="AA:BB:CC:DD:EE:FF"),
              patch.object(player, "command", return_value="[]"),
              patch.object(player, "ipc", side_effect=OSError)):
            reply = player.get_volume()
            self.assertIn("未連線", reply)
            self.assertIn("沒有正在播放", reply)

    def test_volume_action_without_a_value_is_a_read_only_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(player, "STATE_DIR", Path(directory)),
                  patch.object(player, "get_volume", return_value="音量 43%") as query):
                self.assertEqual(player.execute({"action": "volume"}), "音量 43%")
                query.assert_called_once()


class AutoplayTest(unittest.TestCase):
    def test_only_jiajia_channel_defaults_to_autoplay_and_single_episode_can_opt_out(self) -> None:
        cases = [(player.JIAJIA_CHANNEL_ID, {}, True),
                 (player.JIAJIA_CHANNEL_ID, {"autoplay": False}, False),
                 ("another-channel", {}, False)]
        with tempfile.TemporaryDirectory() as directory:
            for channel, extra, expected in cases:
                info = {"id": "bk_Ob2aVXEM", "channel_id": channel, "title": "Story"}
                with (self.subTest(channel=channel, extra=extra),
                      patch.object(player, "STATE_DIR", Path(directory)),
                      patch.object(player, "bose_sink", return_value="speaker"),
                      patch.object(player, "resolve_video", return_value=info),
                      patch.object(player, "start_player") as start,
                      patch.object(player, "start_autoplay") as autoplay):
                    player.execute({"action": "play", "query": "佳佳老師說故事", **extra})
                    start.assert_called_once_with(info, "speaker", continuous=expected)
                    autoplay.assert_called_once_with(info, continuous=expected)

    def test_finished_episode_advances_to_unseen_episode_without_resetting_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(player, "STATE_DIR", Path(directory)),
                  patch.object(player, "resolve_video", return_value={
                      "id": "e8gwisBH38k", "channel_id": player.JIAJIA_CHANNEL_ID,
                      "title": "Next story", "url": "https://example.test/next",
                  }) as resolve,
                  patch.object(player, "ipc", return_value=True) as ipc):
                player.write_session({"token": "session", "enabled": True, "seen": ["bk_Ob2aVXEM"]})
                self.assertTrue(player.advance_autoplay("session", ["bk_Ob2aVXEM", "e8gwisBH38k"]))
                resolve.assert_called_once_with("https://www.youtube.com/watch?v=e8gwisBH38k")
                self.assertEqual(player.read_session()["current_id"], "e8gwisBH38k")
                self.assertIn(call(["loadfile", "https://example.test/next"]), ipc.call_args_list)
                self.assertFalse(any(c.args[0][:2] == ["set_property", "volume"] for c in ipc.call_args_list))

    def test_stop_or_new_session_during_lookup_cannot_restart_old_playback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(player, "STATE_DIR", Path(directory)):
                player.write_session({"token": "old", "enabled": True, "seen": []})
                def replace_session(value):
                    player.write_session({"token": "new", "enabled": True, "seen": []})
                    return {"id": "e8gwisBH38k", "channel_id": player.JIAJIA_CHANNEL_ID}
                with (patch.object(player, "resolve_video", side_effect=replace_session),
                      patch.object(player, "ipc") as ipc):
                    self.assertFalse(player.advance_autoplay("old", ["e8gwisBH38k"]))
                    ipc.assert_not_called()

    def test_stop_cancels_supervisor_before_player_and_clears_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(player, "STATE_DIR", Path(directory)),
                  patch.object(player, "SOCKET_PATH", Path(directory) / "player.sock"),
                  patch.object(player, "stop_unit") as stop_unit):
                player.write_session({"enabled": True})
                player.stop()
                self.assertEqual(stop_unit.call_args_list, [call(player.AUTOPLAY_UNIT), call(player.UNIT)])
                self.assertFalse(player.read_session())

    def test_three_failed_candidates_stop_autoplay_and_report_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(player, "STATE_DIR", Path(directory)),
                  patch.object(player, "resolve_video", side_effect=RuntimeError("HTTP 403")) as resolve,
                  patch.object(player, "ipc") as ipc):
                player.write_session({"token": "session", "enabled": True, "seen": []})
                self.assertFalse(player.advance_autoplay("session", ["a", "b", "c", "d"]))
                self.assertEqual(resolve.call_count, 3)
                self.assertFalse(player.read_session()["enabled"])
                self.assertEqual(player.read_session()["error"], "HTTP 403")
                ipc.assert_called_once_with(["quit"])


class StorySearchFallbackTest(unittest.TestCase):
    def test_empty_search_falls_back_to_original_channel_title_and_resolves_video(self) -> None:
        episode = {"id": "H01rHePErSQ", "title": "【佳佳老師說故事】EP242《 驢子和馬 》"}
        video = {"id": episode["id"], "url": "https://example.test/audio", "channel_id": player.JIAJIA_CHANNEL_ID}
        responses = [json.dumps({"entries": []}), json.dumps({"entries": [episode]}), json.dumps(video)]
        with patch.object(player, "command", side_effect=responses) as run:
            self.assertEqual(player.resolve_video("佳佳老師 驢子和馬"), video)
        self.assertEqual(run.call_args_list[0].args[0][-1], "ytsearch1:佳佳老師 驢子和馬")
        self.assertEqual(run.call_args_list[1].args[0][-1], f"https://www.youtube.com/channel/{player.JIAJIA_CHANNEL_ID}/videos")
        self.assertEqual(run.call_args_list[2].args[0][-1], "https://www.youtube.com/watch?v=H01rHePErSQ")

    def test_matching_handles_spacing_and_punctuation(self) -> None:
        episode = {"id": "H01rHePErSQ", "title": "EP242《 驢子和馬 》"}
        with patch.object(player, "episode_entries", return_value=[episode]):
            self.assertEqual(player.find_jiajia_episode("佳佳老師說故事 《 驢子 和 馬 》"),
                             "https://www.youtube.com/watch?v=H01rHePErSQ")

    def test_ambiguous_and_missing_titles_do_not_select_an_unrelated_episode(self) -> None:
        entries = [{"id": "H01rHePErSQ", "title": "EP242《驢子和馬》"},
                   {"id": "y0L_CHKkIJM", "title": "EP167《驢子過河》"}]
        with patch.object(player, "episode_entries", return_value=entries):
            self.assertIsNone(player.find_jiajia_episode("佳佳老師 不存在的故事"))
            with self.assertRaisesRegex(RuntimeError, "多個相近集名"):
                player.find_jiajia_episode("佳佳老師 驢子")

    def test_unrelated_searches_and_direct_links_never_use_jiajia_fallback(self) -> None:
        for value in ("周杰倫 稻香", "https://youtu.be/H01rHePErSQ"):
            with (self.subTest(value=value),
                  patch.object(player, "command", return_value=json.dumps({"entries": []})),
                  patch.object(player, "find_jiajia_episode") as fallback):
                with self.assertRaisesRegex(RuntimeError, "找不到可播放"):
                    player.resolve_video(value)
                fallback.assert_not_called()

    def test_resolved_fallback_must_belong_to_the_original_channel(self) -> None:
        responses = [json.dumps({"entries": []}),
                     json.dumps({"url": "https://example.test/audio", "channel_id": "wrong-channel"})]
        with (patch.object(player, "command", side_effect=responses),
              patch.object(player, "find_jiajia_episode", return_value="https://youtu.be/H01rHePErSQ")):
            with self.assertRaisesRegex(RuntimeError, "不屬於佳佳老師原頻道"):
                player.resolve_video("佳佳老師 驢子和馬")


class SpeakerFailureTest(unittest.TestCase):
    def test_service_adapter_and_power_errors_have_different_responses(self) -> None:
        cases = [([RuntimeError("inactive")], "藍牙服務尚未啟動"),
                 (["active", "No default controller available"], "找不到藍牙裝置"),
                 (["active", "Controller test\nPowered: no"], "藍牙目前關閉")]
        for responses, expected in cases:
            with self.subTest(expected=expected), patch.object(player, "command", side_effect=responses):
                with self.assertRaisesRegex(player.BoseUnavailable, expected):
                    player.bluetooth_ready()

    def test_missing_and_ambiguous_pairings_have_actionable_responses(self) -> None:
        devices = "Device AA:BB:CC:DD:EE:01 Bose one\nDevice AA:BB:CC:DD:EE:02 Bose two"
        for response, expected in [("", "尚未配對 Bose"), (devices, "多台 Bose")]:
            with (self.subTest(expected=expected), patch.dict(player.os.environ, {"BOSE_DEVICE_MAC": ""}),
                  patch.object(player, "command", return_value=response)):
                with self.assertRaisesRegex(player.BoseUnavailable, expected):
                    player.bose_address()

    def test_unreachable_speaker_suggests_power_range_and_other_connections(self) -> None:
        with (patch.object(player, "bluetooth_ready"),
              patch.object(player, "bose_address", return_value="AA:BB:CC:DD:EE:FF"),
              patch.object(player, "speaker_connected", return_value=False),
              patch.object(player, "command", side_effect=subprocess.TimeoutExpired("bluetoothctl", 12))):
            with self.assertRaises(player.BoseUnavailable) as caught:
                player.bose_sink()
            self.assertIn("目前連不到 Bose", str(caught.exception))
            self.assertIn("請確認喇叭已開機", str(caught.exception))
            self.assertIn("手機", str(caught.exception))

    def test_pairing_and_busy_errors_are_not_reported_as_power_off(self) -> None:
        self.assertIn("配對驗證失敗", player.connection_message("AuthenticationFailed"))
        self.assertIn("等幾秒", player.connection_message("InProgress"))

    def test_connected_speaker_without_audio_sink_has_specific_response(self) -> None:
        with (patch.object(player, "bluetooth_ready"),
              patch.object(player, "bose_address", return_value="AA:BB:CC:DD:EE:FF"),
              patch.object(player, "speaker_connected", return_value=True),
              patch.object(player, "command", return_value="[]"),
              patch.object(player.time, "sleep")):
            with self.assertRaisesRegex(player.BoseUnavailable, "已連線，但尚未建立音訊輸出"):
                player.bose_sink()

    def test_disconnect_stops_single_playback_and_autoplay_and_records_reason(self) -> None:
        for enabled in (False, True):
            with self.subTest(autoplay=enabled), tempfile.TemporaryDirectory() as directory:
                with (patch.object(player, "STATE_DIR", Path(directory)),
                      patch.object(player, "episode_ids", return_value=[]),
                      patch.object(player, "speaker_connected", return_value=False),
                      patch.object(player, "ipc") as ipc):
                    player.write_session({"token": "test", "enabled": enabled, "monitor": True,
                                          "speaker_address": "test", "channel_id": player.JIAJIA_CHANNEL_ID})
                    self.assertEqual(player.autoplay_worker("test"), 1)
                    state = player.read_session()
                    self.assertFalse(state["enabled"])
                    self.assertFalse(state["monitor"])
                    self.assertIn("Bose 連線已中斷", state["error"])
                    ipc.assert_called_once_with(["quit"])

    def test_control_without_playback_tells_user_how_to_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(player, "STATE_DIR", Path(directory)),
                  patch.object(player, "ipc", side_effect=OSError)):
                self.assertIn("請先貼 YouTube 連結", player.execute({"action": "pause"}))


if __name__ == "__main__":
    unittest.main()
