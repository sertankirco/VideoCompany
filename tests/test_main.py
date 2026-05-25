"""
src/__main__.py CLI testleri.

Gerçek render/publish çağrıları mock'lanır.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Parser testleri
# ---------------------------------------------------------------------------

class TestParser:
    def _parse(self, args):
        from src.__main__ import build_parser
        return build_parser().parse_args(args)

    def test_simulate_defaults(self):
        args = self._parse(["simulate"])
        assert args.command == "simulate"
        assert args.interval == 30
        assert args.publish is False
        assert args.no_sound is False

    def test_simulate_custom_interval(self):
        args = self._parse(["simulate", "--interval", "10"])
        assert args.interval == 10

    def test_simulate_publish_flag(self):
        args = self._parse(["simulate", "--publish"])
        assert args.publish is True

    def test_simulate_web_flag(self):
        args = self._parse(["simulate", "--web"])
        assert args.web is True

    def test_simulate_web_default_false(self):
        args = self._parse(["simulate"])
        assert args.web is False

    def test_simulate_port(self):
        args = self._parse(["simulate", "--port", "9090"])
        assert args.port == 9090

    def test_watch_defaults(self):
        args = self._parse(["watch"])
        assert args.command == "watch"
        assert "instagram" in args.platforms
        assert "tiktok" in args.platforms
        assert "youtube" in args.platforms

    def test_watch_custom_platforms(self):
        args = self._parse(["watch", "--platforms", "instagram,tiktok"])
        assert "youtube" not in args.platforms

    def test_watch_dashboard_flag(self):
        args = self._parse(["watch", "--dashboard"])
        assert args.dashboard is True

    def test_watch_dashboard_default_false(self):
        args = self._parse(["watch"])
        assert args.dashboard is False

    def test_emit_default_event(self):
        args = self._parse(["emit"])
        assert args.command == "emit"
        assert args.event_type == "GOAL"

    def test_emit_red_card(self):
        args = self._parse(["emit", "RED_CARD"])
        assert args.event_type == "RED_CARD"

    def test_emit_with_match_filter(self):
        args = self._parse(["emit", "GOAL", "--match", "FRA_BRA"])
        assert args.match == "FRA_BRA"

    def test_log_defaults(self):
        args = self._parse(["log"])
        assert args.command == "log"
        assert args.last == 20

    def test_log_custom_last(self):
        args = self._parse(["log", "--last", "5"])
        assert args.last == 5

    def test_status_command(self):
        args = self._parse(["status"])
        assert args.command == "status"

    def test_invalid_event_type_rejected(self):
        from src.__main__ import build_parser
        with pytest.raises(SystemExit):
            build_parser().parse_args(["emit", "INVALID_TYPE"])

    def test_no_command_exits(self):
        from src.__main__ import build_parser
        with pytest.raises(SystemExit):
            build_parser().parse_args([])


# ---------------------------------------------------------------------------
# cmd_emit testleri
# ---------------------------------------------------------------------------

class TestCmdEmit:
    def _make_args(self, event_type="GOAL", match=""):
        from src.__main__ import build_parser
        return build_parser().parse_args(["emit", event_type, "--match", match])

    def test_emit_goal_prints_output(self, capsys):
        from src.__main__ import cmd_emit
        cmd_emit(self._make_args("GOAL"))
        captured = capsys.readouterr()
        assert "GOAL" in captured.out or "GOL" in captured.out or "EVENT" in captured.out

    def test_emit_all_event_types(self, capsys):
        from src.__main__ import cmd_emit
        for et in ["GOAL", "YELLOW_CARD", "RED_CARD", "HALFTIME", "FULLTIME"]:
            cmd_emit(self._make_args(et))
        captured = capsys.readouterr()
        assert captured.out.strip() != ""

    def test_emit_with_match_filter(self, capsys):
        from src.__main__ import cmd_emit
        cmd_emit(self._make_args("GOAL", "FRA_BRA"))
        captured = capsys.readouterr()
        assert "Fransa" in captured.out or "Brezilya" in captured.out or "FRA" in captured.out

    def test_emit_shows_hook_text(self, capsys):
        from src.__main__ import cmd_emit
        cmd_emit(self._make_args("GOAL"))
        captured = capsys.readouterr()
        assert "HOOK" in captured.out


# ---------------------------------------------------------------------------
# cmd_log testleri
# ---------------------------------------------------------------------------

class TestCmdLog:
    def test_log_no_file_prints_message(self, tmp_path, capsys):
        from src.__main__ import build_parser, cmd_log
        args = build_parser().parse_args(["log", "--output-dir", str(tmp_path)])
        with pytest.raises(SystemExit):
            cmd_log(args)
        captured = capsys.readouterr()
        assert "bulunamadı" in captured.out or "Log" in captured.out

    def test_log_displays_entries(self, tmp_path, capsys):
        from src.__main__ import build_parser, cmd_log
        log_data = [
            {
                "timestamp": "2026-05-24T12:00:00",
                "video": "/output/match_GOAL_abc_final.mp4",
                "results": [{"platform": "instagram", "status": "published"}],
            }
        ]
        (tmp_path / "upload_log.json").write_text(json.dumps(log_data))

        args = build_parser().parse_args(["log", "--output-dir", str(tmp_path)])
        cmd_log(args)
        captured = capsys.readouterr()
        assert "instagram" in captured.out
        assert "published" in captured.out

    def test_log_last_limits_output(self, tmp_path, capsys):
        from src.__main__ import build_parser, cmd_log
        log_data = [
            {"timestamp": f"2026-05-24T{i:02d}:00:00", "video": f"/output/v{i}.mp4", "results": []}
            for i in range(10)
        ]
        (tmp_path / "upload_log.json").write_text(json.dumps(log_data))

        args = build_parser().parse_args(["log", "--output-dir", str(tmp_path), "--last", "3"])
        cmd_log(args)
        captured = capsys.readouterr()
        lines_with_time = [l for l in captured.out.split("\n") if "2026" in l]
        assert len(lines_with_time) == 3


# ---------------------------------------------------------------------------
# cmd_status testleri
# ---------------------------------------------------------------------------

class TestCmdStatus:
    def test_status_runs_without_error(self, capsys):
        from src.__main__ import build_parser, cmd_status
        args = build_parser().parse_args(["status"])
        cmd_status(args)
        captured = capsys.readouterr()
        assert "GROWLABS" in captured.out

    def test_status_checks_all_platforms(self, capsys):
        from src.__main__ import build_parser, cmd_status
        args = build_parser().parse_args(["status"])
        cmd_status(args)
        captured = capsys.readouterr()
        for platform in ["Instagram", "TikTok", "YouTube"]:
            assert platform in captured.out

    def test_status_shows_ffmpeg_check(self, capsys):
        from src.__main__ import build_parser, cmd_status
        args = build_parser().parse_args(["status"])
        cmd_status(args)
        captured = capsys.readouterr()
        assert "FFmpeg" in captured.out

    def test_status_shows_python_version(self, capsys):
        from src.__main__ import build_parser, cmd_status
        args = build_parser().parse_args(["status"])
        cmd_status(args)
        captured = capsys.readouterr()
        assert "Python" in captured.out


# ---------------------------------------------------------------------------
# cmd_watch testleri
# ---------------------------------------------------------------------------

class TestCmdWatch:
    def test_watch_starts_and_stops_observer(self, tmp_path):
        from src.__main__ import build_parser, cmd_watch

        args = build_parser().parse_args([
            "watch",
            "--output-dir", str(tmp_path),
            "--platforms", "instagram",
        ])

        mock_observer = MagicMock()
        calls = []

        def fake_sleep(n):
            calls.append(n)
            raise KeyboardInterrupt

        with patch("src.publisher.start_watcher", return_value=mock_observer), \
             patch("time.sleep", side_effect=fake_sleep):
            cmd_watch(args)

        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once()

    def test_watch_with_dashboard_starts_flask(self, tmp_path):
        from src.__main__ import build_parser, cmd_watch

        args = build_parser().parse_args([
            "watch",
            "--output-dir", str(tmp_path),
            "--dashboard",
            "--port", "9999",
        ])

        mock_observer = MagicMock()
        mock_app = MagicMock()

        def fake_sleep(n):
            raise KeyboardInterrupt

        with (
            patch("src.publisher.start_watcher", return_value=mock_observer),
            patch("src.api_listener.create_dashboard_app", return_value=mock_app),
            patch("time.sleep", side_effect=fake_sleep),
        ):
            cmd_watch(args)

        mock_observer.stop.assert_called_once()


# ---------------------------------------------------------------------------
# cmd_simulate testleri
# ---------------------------------------------------------------------------

class TestCmdSimulate:
    def test_simulate_starts_and_stops(self, tmp_path):
        from src.__main__ import build_parser, cmd_simulate

        args = build_parser().parse_args([
            "simulate",
            "--output-dir", str(tmp_path),
            "--assets-dir", str(tmp_path),
        ])

        mock_engine = MagicMock()
        mock_sim = MagicMock()

        def fake_sleep(n):
            raise KeyboardInterrupt

        with (
            patch("src.render_engine.RenderEngine", return_value=mock_engine),
            patch("src.data_simulator.MatchDataSimulator", return_value=mock_sim),
            patch("time.sleep", side_effect=fake_sleep),
        ):
            cmd_simulate(args)

        mock_engine.start.assert_called_once()
        mock_sim.start.assert_called_once()
        mock_sim.stop.assert_called_once()
        mock_engine.stop.assert_called_once()


# ---------------------------------------------------------------------------
# main() dispatcher testleri
# ---------------------------------------------------------------------------

class TestMainDispatcher:
    def test_main_emit_dispatches(self, capsys):
        from src.__main__ import main
        with patch("sys.argv", ["growlabs", "emit", "GOAL"]):
            main()
        captured = capsys.readouterr()
        assert captured.out.strip() != ""

    def test_main_status_dispatches(self, capsys):
        from src.__main__ import main
        with patch("sys.argv", ["growlabs", "status"]):
            main()
        captured = capsys.readouterr()
        assert "GROWLABS" in captured.out
