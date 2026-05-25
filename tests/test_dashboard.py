"""Tests for SSE/dashboard functionality in api_listener.py and tui.py."""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# push_sse_event tests
# ---------------------------------------------------------------------------


class TestPushSseEvent:
    def setup_method(self):
        import src.api_listener as mod
        mod._sse_subscribers.clear()

    def test_push_delivers_to_subscriber(self):
        import queue
        import src.api_listener as mod

        q = queue.Queue()
        mod._sse_subscribers.append(q)
        mod.push_sse_event({"type": "new_video"})
        assert not q.empty()
        assert q.get_nowait() == {"type": "new_video"}

    def test_push_to_multiple_subscribers(self):
        import queue
        import src.api_listener as mod

        q1, q2 = queue.Queue(), queue.Queue()
        mod._sse_subscribers.extend([q1, q2])
        mod.push_sse_event({"type": "test"})
        assert q1.get_nowait() == {"type": "test"}
        assert q2.get_nowait() == {"type": "test"}

    def test_push_removes_full_queue(self):
        import queue
        import src.api_listener as mod

        full_q = queue.Queue(maxsize=1)
        full_q.put_nowait("block")  # fill it
        mod._sse_subscribers.append(full_q)
        mod.push_sse_event({"type": "overflow"})  # should not raise
        assert full_q not in mod._sse_subscribers

    def test_push_empty_subscribers_no_error(self):
        import src.api_listener as mod
        assert len(mod._sse_subscribers) == 0
        mod.push_sse_event({"type": "test"})  # must not raise


# ---------------------------------------------------------------------------
# /api/stats endpoint tests
# ---------------------------------------------------------------------------


class TestApiStats:
    def _make_app(self, tmp_path):
        from src.api_listener import create_dashboard_app
        app = create_dashboard_app(output_dir=str(tmp_path))
        app.testing = True
        return app

    def test_stats_empty_log(self, tmp_path):
        client = self._make_app(tmp_path).test_client()
        r = client.get("/api/stats")
        data = json.loads(r.data)
        assert data["total_uploads"] == 0
        assert data["last_event"] is None
        assert data["recent_events"] == []

    def test_stats_counts_success(self, tmp_path):
        log = [
            {
                "timestamp": "2026-05-25T10:00:00",
                "video": "/output/a_final.mp4",
                "results": [
                    {"platform": "instagram", "status": "published"},
                    {"platform": "tiktok",    "status": "skipped"},
                    {"platform": "youtube",   "status": "error"},
                ],
            }
        ]
        (tmp_path / "upload_log.json").write_text(json.dumps(log), encoding="utf-8")
        client = self._make_app(tmp_path).test_client()
        r = client.get("/api/stats")
        data = json.loads(r.data)
        assert data["by_platform"]["instagram"]["success"] == 1
        assert data["by_platform"]["tiktok"]["skipped"] == 1
        assert data["by_platform"]["youtube"]["error"] == 1

    def test_stats_total_uploads(self, tmp_path):
        entries = [
            {
                "timestamp": f"2026-05-25T10:0{i}:00",
                "video": f"/output/v{i}_final.mp4",
                "results": [],
            }
            for i in range(3)
        ]
        (tmp_path / "upload_log.json").write_text(json.dumps(entries), encoding="utf-8")
        client = self._make_app(tmp_path).test_client()
        r = client.get("/api/stats")
        data = json.loads(r.data)
        assert data["total_uploads"] == 3

    def test_stats_last_event_from_event_data(self, tmp_path):
        event_data = {
            "event_type": "GOAL",
            "team_home": "Fransa",
            "team_away": "Brezilya",
            "minute": 77,
        }
        log = [
            {
                "timestamp": "2026-05-25T12:00:00",
                "video": "/output/a_final.mp4",
                "results": [],
                "event_data": event_data,
            }
        ]
        (tmp_path / "upload_log.json").write_text(json.dumps(log), encoding="utf-8")
        client = self._make_app(tmp_path).test_client()
        r = client.get("/api/stats")
        data = json.loads(r.data)
        assert data["last_event"]["event_type"] == "GOAL"
        assert data["last_event"]["team_home"] == "Fransa"

    def test_stats_recent_events_max_ten(self, tmp_path):
        entries = [
            {
                "timestamp": f"2026-05-25T10:{i:02d}:00",
                "video": f"/output/v{i}_final.mp4",
                "results": [],
                "event_data": {"event_type": "GOAL", "minute": i},
            }
            for i in range(15)
        ]
        (tmp_path / "upload_log.json").write_text(json.dumps(entries), encoding="utf-8")
        client = self._make_app(tmp_path).test_client()
        r = client.get("/api/stats")
        data = json.loads(r.data)
        assert len(data["recent_events"]) == 10

    def test_dashboard_route_serves_html(self, tmp_path):
        client = self._make_app(tmp_path).test_client()
        r = client.get("/")
        assert r.status_code == 200
        assert b"GROWLABS" in r.data

    def test_health_route(self, tmp_path):
        client = self._make_app(tmp_path).test_client()
        r = client.get("/health")
        assert r.status_code == 200
        assert json.loads(r.data)["status"] == "ok"

    def test_stats_handles_corrupt_log(self, tmp_path):
        (tmp_path / "upload_log.json").write_text("not json", encoding="utf-8")
        client = self._make_app(tmp_path).test_client()
        r = client.get("/api/stats")
        data = json.loads(r.data)
        assert data["total_uploads"] == 0


# ---------------------------------------------------------------------------
# OutputWatcher sse_notify tests
# ---------------------------------------------------------------------------


class TestOutputWatcherSseNotify:
    def test_sse_notify_called_after_publish(self, tmp_path):
        from src.publisher import OutputWatcher
        from watchdog.events import FileCreatedEvent

        bot = MagicMock()
        notify = MagicMock()
        w = OutputWatcher(bot, sse_notify=notify)
        w.on_created(FileCreatedEvent(str(tmp_path / "match_GOAL_abc_final.mp4")))

        bot.publish_sync.assert_called_once()
        notify.assert_called_once()
        assert notify.call_args[0][0]["type"] == "new_video"

    def test_sse_notify_not_called_for_non_final(self, tmp_path):
        from src.publisher import OutputWatcher
        from watchdog.events import FileCreatedEvent

        bot = MagicMock()
        notify = MagicMock()
        w = OutputWatcher(bot, sse_notify=notify)
        w.on_created(FileCreatedEvent(str(tmp_path / "match_GOAL_abc.mp4")))

        bot.publish_sync.assert_not_called()
        notify.assert_not_called()

    def test_sse_notify_none_still_publishes(self, tmp_path):
        from src.publisher import OutputWatcher
        from watchdog.events import FileCreatedEvent

        bot = MagicMock()
        w = OutputWatcher(bot, sse_notify=None)
        w.on_created(FileCreatedEvent(str(tmp_path / "match_GOAL_abc_final.mp4")))

        bot.publish_sync.assert_called_once()

    def test_start_watcher_passes_sse_notify(self, tmp_path):
        from src.publisher import start_watcher

        bot = MagicMock()
        notify = MagicMock()

        with patch("src.publisher.Observer") as mock_obs_cls:
            mock_obs = MagicMock()
            mock_obs_cls.return_value = mock_obs
            observer = start_watcher(str(tmp_path), bot, sse_notify=notify)

        # Verify schedule was called with an OutputWatcher that has sse_notify set
        call_args = mock_obs.schedule.call_args
        handler = call_args[0][0]
        assert handler._sse_notify is notify


# ---------------------------------------------------------------------------
# render_engine._on_event_processed callback test
# ---------------------------------------------------------------------------


class TestRenderEngineCallback:
    def test_on_event_processed_called_after_render(self, tmp_path):
        from src.render_engine import RenderEngine, EngineConfig

        config = EngineConfig(
            template_path="fake.aep",
            output_dir=str(tmp_path),
            assets_dir=str(tmp_path),
            enable_sound_design=False,
        )
        engine = RenderEngine(config)
        callback = MagicMock()
        engine._on_event_processed = callback

        event = {
            "match_id": "TEST_001",
            "event_type": "GOAL",
            "minute": 10,
            "team": "Fransa",
            "player_name": "Mbappe",
            "score_home": 1,
            "score_away": 0,
            "team_home": "Fransa",
            "team_away": "Brezilya",
            "timestamp": "2026-05-25T10:00:00",
        }

        with (
            patch("src.render_engine.execute_jsx", return_value=MagicMock(returncode=0)),
            patch("src.render_engine.generate_jsx", return_value=""),
            patch("src.render_engine.run_render", return_value=0),
            patch("src.render_engine.get_aerender_path", return_value="/fake/aerender"),
        ):
            engine.process_match_event(event)

        callback.assert_called_once()
        payload = callback.call_args[0][0]
        assert payload["event_type"] == "GOAL"
        assert "hook_text" in payload

    def test_callback_not_set_by_default(self):
        from src.render_engine import RenderEngine, EngineConfig

        config = EngineConfig(
            template_path="fake.aep",
            output_dir="/tmp/out",
            assets_dir="/tmp/assets",
            enable_sound_design=False,
        )
        engine = RenderEngine(config)
        assert engine._on_event_processed is None


# ---------------------------------------------------------------------------
# TUI availability test
# ---------------------------------------------------------------------------


class TestTuiAvailability:
    def test_is_available_returns_bool(self):
        from src.tui import is_available
        result = is_available()
        assert isinstance(result, bool)

    def test_simulator_tui_init(self, tmp_path):
        from src.tui import SimulatorTUI
        tui = SimulatorTUI(output_dir=str(tmp_path))
        assert tui._output_dir == tmp_path

    def test_on_event_thread_safe(self, tmp_path):
        from src.tui import SimulatorTUI
        tui = SimulatorTUI(output_dir=str(tmp_path))
        event = {"event_type": "GOAL", "team_home": "Fransa", "minute": 1}
        tui.on_event(event)
        assert tui._current["event_type"] == "GOAL"
        assert len(tui._recent) == 1

    def test_on_event_caps_recent_at_20(self, tmp_path):
        from src.tui import SimulatorTUI
        tui = SimulatorTUI(output_dir=str(tmp_path))
        for i in range(25):
            tui.on_event({"event_type": "GOAL", "minute": i})
        assert len(tui._recent) == 20

    def test_write_log_stores_event_data(self, tmp_path):
        from src.publisher import PublisherBot
        bot = PublisherBot(output_dir=str(tmp_path), enabled_platforms=[])
        event_data = {"event_type": "GOAL", "team_home": "Fransa", "minute": 88}
        bot._write_log("/output/v_final.mp4", [], event_data=event_data)

        log_path = tmp_path / "upload_log.json"
        assert log_path.exists()
        entries = json.loads(log_path.read_text())
        assert entries[0]["event_data"]["event_type"] == "GOAL"
