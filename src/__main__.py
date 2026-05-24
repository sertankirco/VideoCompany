"""
Growlabs CLI — python -m growlabs

Subcommands:
  simulate   Maç simülatörünü başlat (render engine'e event gönder)
  watch      /output klasörünü izle ve platformlara yayınla
  emit       Tek bir test eventi gönder
  log        Upload geçmişini görüntüle
  status     Sistem konfigürasyonunu kontrol et
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("growlabs")


# ---------------------------------------------------------------------------
# simulate
# ---------------------------------------------------------------------------

def cmd_simulate(args: argparse.Namespace) -> None:
    """Maç simülatörünü başlatır; Ctrl+C ile durdurulur."""
    from src.render_engine import EngineConfig, RenderEngine
    from src.data_simulator import MatchDataSimulator

    config = EngineConfig(
        template_path=args.template,
        output_dir=args.output_dir,
        assets_dir=args.assets_dir,
        enable_sound_design=not args.no_sound,
        enable_publisher=args.publish,
    )
    engine = RenderEngine(config)
    engine.start()

    sim = MatchDataSimulator(engine, interval_sec=args.interval)
    sim.start()

    logger.info("Simülatör başlatıldı. Durdurmak için Ctrl+C.")
    try:
        signal.pause()
    except (KeyboardInterrupt, AttributeError):
        pass
    finally:
        sim.stop()
        engine.stop()
        logger.info("Simülatör durduruldu.")


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

def cmd_watch(args: argparse.Namespace) -> None:
    """/output klasörünü izler; yeni _final.mp4 gelince platformlara yükler."""
    from src.publisher import PublisherBot, start_watcher

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    bot = PublisherBot(output_dir=args.output_dir, enabled_platforms=platforms)

    observer = start_watcher(args.output_dir, bot)
    logger.info("İzleme başladı: %s  |  Platformlar: %s", args.output_dir, platforms)
    logger.info("Durdurmak için Ctrl+C.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        logger.info("İzleme durduruldu.")


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------

def cmd_emit(args: argparse.Namespace) -> None:
    """Tek bir test eventi üretir (render yapmaz, sadece event verisini gösterir)."""
    from src.data_simulator import MatchDataSimulator, _MATCH_POOL, generate_hook_text
    import random

    match = next(
        (m for m in _MATCH_POOL if args.match and args.match in m["match_id"]),
        random.choice(_MATCH_POOL),
    )

    from unittest.mock import MagicMock
    mock_engine = MagicMock()
    mock_engine.submit = MagicMock(return_value=MagicMock())
    sim = MatchDataSimulator(mock_engine, interval_sec=999)
    sim._current_match = match.copy()

    event = sim.emit_single(args.event_type)
    hook  = generate_hook_text(event)

    print("\n" + "─" * 50)
    print(f"  EVENT   : {event['event_type']}")
    print(f"  MAÇ     : {event['team_home']} vs {event['team_away']}")
    print(f"  SKOR    : {event['score_home']}-{event['score_away']}")
    print(f"  DAKİKA  : {event['minute']}'")
    if event.get("player_name"):
        print(f"  OYUNCU  : {event['player_name']}")
    print("─" * 50)
    print("  HOOK TEXT:")
    for line in hook.split("\n"):
        print(f"    {line}")
    print("─" * 50 + "\n")


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

def cmd_log(args: argparse.Namespace) -> None:
    """Upload logunu görüntüler."""
    log_path = Path(args.output_dir) / "upload_log.json"
    if not log_path.exists():
        print(f"Log dosyası bulunamadı: {log_path}")
        sys.exit(0)

    with open(log_path, encoding="utf-8") as f:
        entries = json.load(f)

    if not entries:
        print("Log boş.")
        sys.exit(0)

    show = entries[-args.last:] if args.last else entries

    print(f"\n{'─'*60}")
    print(f"  UPLOAD LOGU — {len(entries)} kayıt toplam")
    print(f"{'─'*60}")
    for entry in show:
        ts    = entry.get("timestamp", "?")[:19].replace("T", " ")
        video = Path(entry.get("video", "?")).name
        results = entry.get("results", [])
        statuses = "  ".join(
            f"{r.get('platform','?')}:{r.get('status','?')}" for r in results
        )
        print(f"  {ts}  |  {video}")
        print(f"           {statuses or '(sonuç yok)'}")
    print(f"{'─'*60}\n")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(_args: argparse.Namespace) -> None:
    """Ortam değişkenleri ve bağımlılıkları kontrol eder."""
    from dotenv import load_dotenv
    load_dotenv()

    checks = {
        "Instagram": ["INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID"],
        "TikTok":    ["TIKTOK_ACCESS_TOKEN", "TIKTOK_CLIENT_KEY"],
        "YouTube":   ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"],
        "Render":    ["AERENDER_PATH", "AFTERFX_PATH"],
    }

    print("\n" + "─" * 50)
    print("  GROWLABS 2026 — SİSTEM DURUMU")
    print("─" * 50)

    for platform, keys in checks.items():
        missing = [k for k in keys if not os.getenv(k)]
        status  = "✅" if not missing else f"❌ eksik: {', '.join(missing)}"
        print(f"  {platform:<12} {status}")

    # FFmpeg kontrolü
    import shutil
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    print(f"  {'FFmpeg':<12} {'✅' if ffmpeg_ok else '❌ ffmpeg bulunamadı (PATH)'}")

    # Python sürümü
    py = sys.version.split()[0]
    py_ok = tuple(int(x) for x in py.split(".")) >= (3, 11)
    print(f"  {'Python':<12} {'✅' if py_ok else '❌'} {py}")

    print("─" * 50 + "\n")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m growlabs",
        description="Growlabs 2026 Dünya Kupası Video Otomasyonu",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # simulate
    p_sim = sub.add_parser("simulate", help="Maç simülatörünü başlat")
    p_sim.add_argument("--template",   default="templates/WorldCupShort.aep")
    p_sim.add_argument("--output-dir", default="output/")
    p_sim.add_argument("--assets-dir", default="assets/")
    p_sim.add_argument("--interval",   type=int, default=30, help="Event aralığı (saniye)")
    p_sim.add_argument("--no-sound",   action="store_true", help="SFX miksajını devre dışı bırak")
    p_sim.add_argument("--publish",    action="store_true", help="Sosyal medya yayınını etkinleştir")

    # watch
    p_watch = sub.add_parser("watch", help="/output klasörünü izle ve yayınla")
    p_watch.add_argument("--output-dir", default="output/")
    p_watch.add_argument("--platforms",  default="instagram,tiktok,youtube")

    # emit
    p_emit = sub.add_parser("emit", help="Tek test eventi üret")
    p_emit.add_argument(
        "event_type",
        choices=["GOAL", "YELLOW_CARD", "RED_CARD", "HALFTIME", "FULLTIME"],
        nargs="?",
        default="GOAL",
    )
    p_emit.add_argument("--match", default="", help="Match ID filtresi (örn. FRA_BRA)")

    # log
    p_log = sub.add_parser("log", help="Upload geçmişini görüntüle")
    p_log.add_argument("--output-dir", default="output/")
    p_log.add_argument("--last", type=int, default=20, help="Son N kaydı göster")

    # status
    sub.add_parser("status", help="Sistem konfigürasyonunu kontrol et")

    return parser


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

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
