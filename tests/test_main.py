"""Tests for src/__main__.py CLI entry point."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParser:
    def setup_method(self):
        from src.__main__ import build_parser
        self.parser = build_parser()

    def test_simulate_default_interval(self):
        args = self.parser.parse_args(["simulate"])
        assert args.interval == 30

    def test_simulate_custom_interval(self):
        args = self.parser.parse_args(["simulate", "--interval", "10"])
        assert args.interval == 10

    def test_simulate_web_flag(self):
        args = self.parser.parse_args(["simulate", "--web"])
        assert args.web is True

    def test_simulate_web_default_false(self):
        args = self.parser.parse_args(["simulate"])
        assert args.web is False

    def test_simulate_port(self):
        args = self.parser.parse_args(["simulate", "--port", "9090"])
        assert args.port == 9090

    def test_watch_command(self):
        args = self.parser.parse_args(["watch"])
        assert args.command == "watch"

    def test_watch_dashboard_flag(self):
        args = self.parser.parse_args(["watch", "--dashboard"])
        assert args.dashboard is True

    def test_emit_goal(self):
        args = self.parser.parse_args(["emit", "GOAL"])
        assert args.event_type == "GOAL"

    def test_emit_with_match(self):
        args = self.parser.parse_args(["emit", "GOAL", "--match", "FRA_BRA"])
        assert args.match == "FRA_BRA"

    def test_emit_invalid_type(self):
        with pytest.raises(SystemExit):
            self.parser.parse_args(["emit", "UNKNOWN"])

    def test_log_default_last(self):
        args = self.parser.parse_args(["log"])
        assert args.last == 10

    def test_log_custom_last(self):
        args = self.parser.parse_args(["log", "--last", "5"])
        assert args.last == 5

    def test_status_command(self):
        args = self.parser.parse_args(["status"])
        assert args.command == "status"

    def test_output_dir_default(self, monkeypatch):
        monkeypatch.delenv("PUBLISHER_OUTPUT_DIR", raising=False)
        args = self.parser.parse_args(["status"])
        assert args.output_dir == "output/"

    def test_output_dir_env(self, monkeypatch):
        monkeypatch.setenv("PUBLISHER_OUTPUT_DIR", "/tmp/out/")
        from src.__main__ import build_parser as _bp
        parser = _bp()
        args = parser.parse_args(["status"])
        assert args.output_dir == "/tmp/out/"


# ---------------------------------------------------------------------------
# cmd_emit tests
# ---------------------------------------------------------------------------


class TestCmdEmit:
    def test_emit_prints_hook(self, capsys):
        from src.__main__ import cmd_emit
        args = MagicMock(event_type="GOAL", match="FRA_BRA", output_dir="output/")
        cmd_emit(args)
        out = capsys.readouterr().out
        assert "GOAL" in out

    def test_emit_prints_teams(self, capsys):
        from src.__main__ import cmd_emit
        args = MagicMock(event_type="GOAL", match="FRA_BRA", output_dir="output/")
        cmd_emit(args)
        out = capsys.readouterr().out
        assert "Fransa" in out or "Brezilya" in out

    def test_emit_no_match_uses_first_pool(self, capsys):
        from src.__main__ import cmd_emit
        args = MagicMock(event_type="HALFTIME", match="", output_dir="output/")
        cmd_emit(args)
        out = capsys.readouterr().out
        assert "HALFTIME" in out

    def test_emit_yellow_card(self, capsys):
        from src.__main__ import cmd_emit
        args = MagicMock(event_type="YELLOW_CARD", match="TUR_ARG", output_dir="output/")
        cmd_emit(args)
        out = capsys.readouterr().out
        assert "YELLOW_CARD" in out


# ---------------------------------------------------------------------------
# cmd_log tests
# ---------------------------------------------------------------------------


class TestCmdLog:
    def test_log_no_file(self, tmp_path, capsys):
        from src.__main__ import cmd_log
        args = MagicMock(output_dir=str(tmp_path), last=10)
        cmd_log(args)
        out = capsys.readouterr().out
        assert "not found" in out or "henüz" in out or "Upload log" in out

    def test_log_shows_entries(self, tmp_path, capsys):
        from src.__main__ import cmd_log
        log = [
            {
                "timestamp": "2026-05-25T12:00:00",
                "video": "/output/match_GOAL_abc_final.mp4",
                "results": [{"platform": "instagram", "status": "published"}],
            }
        ]
        (tmp_path / "upload_log.json").write_text(json.dumps(log), encoding="utf-8")
        args = MagicMock(output_dir=str(tmp_path), last=10)
        cmd_log(args)
        out = capsys.readouterr().out
        assert "instagram" in out

    def test_log_respects_last(self, tmp_path, capsys):
        from src.__main__ import cmd_log
        entries = [
            {
                "timestamp": f"2026-05-25T12:0{i}:00",
                "video": f"/output/match_{i}_final.mp4",
                "results": [{"platform": "tiktok", "status": "skipped"}],
            }
            for i in range(5)
        ]
        (tmp_path / "upload_log.json").write_text(json.dumps(entries), encoding="utf-8")
        args = MagicMock(output_dir=str(tmp_path), last=2)
        cmd_log(args)
        out = capsys.readouterr().out
        assert "2 kayıt" in out or "Son 2" in out


# ---------------------------------------------------------------------------
# cmd_status tests
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def test_status_prints_python_version(self, tmp_path, capsys):
        from src.__main__ import cmd_status
        args = MagicMock(output_dir=str(tmp_path))
        cmd_status(args)
        out = capsys.readouterr().out
        assert "Python" in out

    def test_status_shows_ffmpeg(self, tmp_path, capsys):
        from src.__main__ import cmd_status
        args = MagicMock(output_dir=str(tmp_path))
        cmd_status(args)
        out = capsys.readouterr().out
        assert "FFmpeg" in out

    def test_status_shows_upload_log_when_present(self, tmp_path, capsys):
        from src.__main__ import cmd_status
        (tmp_path / "upload_log.json").write_text("[]", encoding="utf-8")
        args = MagicMock(output_dir=str(tmp_path))
        cmd_status(args)
        out = capsys.readouterr().out
        assert "Upload log" in out

    def test_status_shows_no_log_when_missing(self, tmp_path, capsys):
        from src.__main__ import cmd_status
        args = MagicMock(output_dir=str(tmp_path))
        cmd_status(args)
        out = capsys.readouterr().out
        assert "Upload log" in out


# ---------------------------------------------------------------------------
# cmd_watch tests
# ---------------------------------------------------------------------------


class TestCmdWatch:
    def test_watch_starts_and_stops(self, tmp_path):
        from src.__main__ import cmd_watch

        mock_observer = MagicMock()
        mock_bot = MagicMock()

        with (
            patch("src.publisher.PublisherBot", return_value=mock_bot),
            patch("src.publisher.start_watcher", return_value=mock_observer),
            patch("builtins.input", side_effect=KeyboardInterrupt),
            patch("time.sleep", side_effect=KeyboardInterrupt),
        ):
            args = MagicMock(
                output_dir=str(tmp_path),
                dashboard=False,
                port=8080,
            )
            cmd_watch(args)

        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once()


# ---------------------------------------------------------------------------
# cmd_simulate tests
# ---------------------------------------------------------------------------


class TestCmdSimulate:
    def test_simulate_starts_and_stops(self, tmp_path):
        from src.__main__ import cmd_simulate

        mock_engine = MagicMock()
        mock_simulator = MagicMock()

        with (
            patch("src.render_engine.RenderEngine", return_value=mock_engine),
            patch("src.data_simulator.MatchDataSimulator", return_value=mock_simulator),
            patch("time.sleep", side_effect=KeyboardInterrupt),
        ):
            args = MagicMock(
                output_dir=str(tmp_path),
                interval=30,
                web=False,
            )
            cmd_simulate(args)

        mock_engine.start.assert_called_once()
        mock_simulator.start.assert_called_once()
        mock_simulator.stop.assert_called_once()
        mock_engine.stop.assert_called_once()


# ---------------------------------------------------------------------------
# main dispatcher tests
# ---------------------------------------------------------------------------


class TestMainDispatcher:
    def test_main_status(self, tmp_path, capsys):
        from src.__main__ import main
        with patch("sys.argv", ["src", "--output-dir", str(tmp_path), "status"]):
            main()
        out = capsys.readouterr().out
        assert "Python" in out

    def test_main_emit(self, capsys):
        from src.__main__ import main
        with patch("sys.argv", ["src", "emit", "GOAL"]):
            main()
        out = capsys.readouterr().out
        assert "GOAL" in out
