"""
Growlabs 2026 — CLI Entry Point

Usage:
  python -m src simulate [--interval N] [--web] [--port N]
  python -m src watch    [--output-dir DIR] [--dashboard]
  python -m src emit     EVENT_TYPE [--match MATCH_ID]
  python -m src log      [--last N]
  python -m src status
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_env(var: str) -> bool:
    return bool(os.getenv(var, "").strip())


def _start_web_dashboard(output_dir: str = "output/", port: int = 8080) -> None:
    from src.api_listener import create_dashboard_app
    app = create_dashboard_app(output_dir=output_dir)
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True,
    )
    t.start()
    print(f"  Dashboard → http://localhost:{port}")


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------


def cmd_simulate(args: argparse.Namespace) -> None:
    from src.render_engine import RenderEngine, EngineConfig
    from src.data_simulator import MatchDataSimulator

    try:
        from src.tui import SimulatorTUI, is_available as tui_available
        _TUI_OK = tui_available()
    except ImportError:
        SimulatorTUI = None  # type: ignore[assignment]
        _TUI_OK = False

    output_dir = args.output_dir
    config = EngineConfig(
        template_path=os.getenv("AE_TEMPLATE_PATH", "template.aep"),
        output_dir=output_dir,
        assets_dir=os.getenv("ASSETS_DIR", "assets/"),
        aerender_bin=os.getenv("AERENDER_PATH", ""),
        enable_publisher=False,
    )
    engine = RenderEngine(config)

    tui = None
    if _TUI_OK and SimulatorTUI is not None:
        tui = SimulatorTUI(output_dir=output_dir)
        engine._on_event_processed = tui.on_event  # type: ignore[method-assign]

    simulator = MatchDataSimulator(engine, interval_sec=args.interval)

    if args.web:
        _start_web_dashboard(output_dir=output_dir, port=args.port)

    engine.start()
    simulator.start()
    if tui:
        tui.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        simulator.stop()
        engine.stop()
        if tui:
            tui.stop()


def cmd_watch(args: argparse.Namespace) -> None:
    from src.publisher import PublisherBot, start_watcher

    output_dir = args.output_dir
    sse_notify = None

    if args.dashboard:
        from src.api_listener import push_sse_event, create_dashboard_app
        sse_notify = push_sse_event
        app = create_dashboard_app(output_dir=output_dir)
        t = threading.Thread(
            target=lambda: app.run(host="0.0.0.0", port=args.port, use_reloader=False),
            daemon=True,
        )
        t.start()
        print(f"  Dashboard → http://localhost:{args.port}")

    bot = PublisherBot(output_dir=output_dir)
    observer = start_watcher(output_dir, bot, sse_notify=sse_notify)
    print(f"Watching {output_dir!r} — Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


def cmd_emit(args: argparse.Namespace) -> None:
    from src.data_simulator import generate_hook_text, _MATCH_POOL

    match = next(
        (m for m in _MATCH_POOL if args.match and args.match in m["match_id"]),
        _MATCH_POOL[0],
    )

    event_data = {
        "event_type": args.event_type,
        "player_name": match["players_home"][0],
        "team_home": match["team_home"],
        "team_away": match["team_away"],
        "team": match["team_home"],
        "score_home": 1,
        "score_away": 0,
        "minute": 73,
        "match_id": match["match_id"],
    }

    hook = generate_hook_text(event_data)
    print(f"\n{'─'*40}")
    print(f"  {event_data['event_type']} — {match['team_home']} vs {match['team_away']}")
    print(f"  Min: {event_data['minute']}    Score: {event_data['score_home']}-{event_data['score_away']}")
    print(f"\nHook:\n{hook}")
    print(f"{'─'*40}\n")


def cmd_log(args: argparse.Namespace) -> None:
    log_path = Path(args.output_dir) / "upload_log.json"
    if not log_path.exists():
        print(f"Upload log not found: {log_path}")
        return

    with open(log_path, encoding="utf-8") as f:
        entries = json.load(f)

    recent = entries[-args.last:] if args.last > 0 else entries
    print(f"\n{'─'*60}")
    print(f"  Son {len(recent)} kayıt ({len(entries)} toplam)")
    print(f"{'─'*60}")
    for e in reversed(recent):
        ts = e.get("timestamp", "?")[:19]
        vid = Path(e.get("video", "?")).name
        results = e.get("results", [])
        summary = ", ".join(f"{r['platform']}:{r['status']}" for r in results)
        print(f"  {ts}  {vid}\n    → {summary}")
    print()


def cmd_status(args: argparse.Namespace) -> None:
    import subprocess

    print("\n  GROWLABS 2026 — SİSTEM DURUMU")
    print("  " + "─" * 38)

    # Python version
    v = sys.version.split()[0]
    ok = sys.version_info >= (3, 11)
    print(f"  Python  {v}  {'✅' if ok else '⚠️  ≥3.11 gerekli'}")

    # FFmpeg
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        print(f"  FFmpeg  {'✅' if r.returncode == 0 else '❌ bulunamadı'}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  FFmpeg  ❌ bulunamadı")

    # Env tokens
    _ENV_VARS = [
        ("INSTAGRAM_ACCESS_TOKEN", "Instagram token"),
        ("INSTAGRAM_USER_ID",      "Instagram user ID"),
        ("TIKTOK_ACCESS_TOKEN",    "TikTok token"),
        ("YOUTUBE_REFRESH_TOKEN",  "YouTube refresh token"),
        ("AERENDER_PATH",          "aerender path"),
    ]
    for var, label in _ENV_VARS:
        icon = "✅" if _check_env(var) else "⚠️ "
        print(f"  {label:<25}  {icon}")

    # Upload log stats
    log_path = Path(args.output_dir) / "upload_log.json"
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            entries = json.load(f)
        print(f"\n  Upload log: {len(entries)} kayıt ({log_path})")
    else:
        print(f"\n  Upload log: henüz yok")

    print()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src",
        description="Growlabs 2026 — Otomatik Video Makinesi",
    )
    p.add_argument(
        "--output-dir", default=os.getenv("PUBLISHER_OUTPUT_DIR", "output/"),
        metavar="DIR", help="Çıktı klasörü (varsayılan: output/)",
    )

    sub = p.add_subparsers(dest="command", required=True)

    # simulate
    sim = sub.add_parser("simulate", help="Maç simülatörünü başlat")
    sim.add_argument("--interval", type=int, default=30, metavar="N",
                     help="Event üretme aralığı saniye cinsinden (varsayılan: 30)")
    sim.add_argument("--web", action="store_true",
                     help="Web dashboard'u başlat (Flask SSE)")
    sim.add_argument("--port", type=int, default=8080, metavar="N",
                     help="Web dashboard portu (varsayılan: 8080)")

    # watch
    w = sub.add_parser("watch", help="/output izle, platformlara yayınla")
    w.add_argument("--dashboard", action="store_true",
                   help="Web dashboard'u başlat (SSE bildirimli)")
    w.add_argument("--port", type=int, default=8080, metavar="N",
                   help="Dashboard portu (varsayılan: 8080)")

    # emit
    em = sub.add_parser("emit", help="Tek test eventi üret")
    em.add_argument("event_type",
                    choices=["GOAL", "YELLOW_CARD", "RED_CARD", "HALFTIME", "FULLTIME"],
                    help="Event türü")
    em.add_argument("--match", default="", metavar="ID",
                    help="Maç ID'si (örn: FRA_BRA)")

    # log
    lg = sub.add_parser("log", help="Upload log kayıtlarını göster")
    lg.add_argument("--last", type=int, default=10, metavar="N",
                    help="Son N kaydı göster (varsayılan: 10)")

    # status
    sub.add_parser("status", help="Sistem durumu kontrolü")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Attach output_dir to simulate/watch/emit/log/status that don't have it
    if not hasattr(args, "output_dir"):
        args.output_dir = os.getenv("PUBLISHER_OUTPUT_DIR", "output/")

    dispatch = {
        "simulate": cmd_simulate,
        "watch":    cmd_watch,
        "emit":     cmd_emit,
        "log":      cmd_log,
        "status":   cmd_status,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
