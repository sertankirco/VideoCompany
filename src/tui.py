"""
Growlabs 2026 — Rich Terminal UI

python -m src simulate komutuna canlı panel ekler.
rich kütüphanesi yüklü değilse sessizce devre dışı kalır.
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False

_console = None
if _RICH_AVAILABLE:
    _console = Console()

# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """rich yüklü ve terminal destekliyorsa True döner."""
    if not _RICH_AVAILABLE:
        return False
    return _console is not None and _console.is_terminal


# ---------------------------------------------------------------------------
# SimulatorTUI
# ---------------------------------------------------------------------------

class SimulatorTUI:
    """
    RenderEngine olaylarını canlı olarak terminalda gösterir.

    Layout:
    ┌─────────────────────────────────────────────────┐
    │  MAÇ BİLGİSİ        │  SON EVENT               │  (üst)
    ├─────────────────────────────────────────────────┤
    │  UPLOAD İSTATİSTİK  │  SON 10 EVENT LOGu       │  (alt)
    └─────────────────────────────────────────────────┘
    """

    def __init__(self, output_dir: str = "output/"):
        self._output_dir = Path(output_dir)
        self._lock = threading.Lock()
        self._recent: list[dict] = []       # son 20 event
        self._current: Optional[dict] = None
        self._live: Optional["Live"] = None
        self._start_time = datetime.utcnow()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Main thread'de çağrılmalı — Live context'i burada açılır."""
        if not _RICH_AVAILABLE:
            return
        self._live = Live(
            self._build(),
            console=_console,
            refresh_per_second=2,
            screen=False,
        )
        self._live.__enter__()

    def stop(self) -> None:
        if self._live:
            self._live.__exit__(None, None, None)
            self._live = None

    # ------------------------------------------------------------------
    # Worker thread callback
    # ------------------------------------------------------------------

    def on_event(self, event: dict) -> None:
        """RenderEngine worker thread'inden çağrılır — thread-safe."""
        with self._lock:
            self._current = dict(event)
            self._recent.append(dict(event))
            if len(self._recent) > 20:
                self._recent = self._recent[-20:]
        if self._live:
            self._live.update(self._build())

    # ------------------------------------------------------------------
    # Layout builder
    # ------------------------------------------------------------------

    def _build(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="top", size=9),
            Layout(name="bottom"),
        )
        layout["top"].split_row(
            Layout(name="match", ratio=1),
            Layout(name="event", ratio=2),
        )
        layout["bottom"].split_row(
            Layout(name="stats", ratio=1),
            Layout(name="log",   ratio=2),
        )
        layout["match"].update(self._panel_match())
        layout["event"].update(self._panel_event())
        layout["stats"].update(self._panel_stats())
        layout["log"].update(self._panel_log())
        return layout

    # ------------------------------------------------------------------
    # Panel renderers
    # ------------------------------------------------------------------

    def _panel_match(self) -> Panel:
        with self._lock:
            ev = self._current

        uptime = str(datetime.utcnow() - self._start_time).split(".")[0]

        t = Text()
        if ev:
            t.append(f"  {ev.get('team_home','?')} ", style="bold cyan")
            t.append("vs ", style="dim")
            t.append(f"{ev.get('team_away','?')}\n", style="bold cyan")
            score = f"{ev.get('score_home',0)}-{ev.get('score_away',0)}"
            t.append(f"  Skor: {score}\n", style="bold white")
            t.append(f"  Maç: {ev.get('match_id','?')}\n", style="dim")
        else:
            t.append("  Bekleniyor…\n", style="dim")
        t.append(f"\n  Çalışma süresi: {uptime}", style="dim green")

        return Panel(t, title="[bold green]⚽ MAÇ[/]", border_style="green")

    def _panel_event(self) -> Panel:
        with self._lock:
            ev = self._current

        if not ev:
            return Panel(
                Text("  Henüz event yok.", style="dim"),
                title="[bold yellow]SON EVENT[/]",
                border_style="yellow",
            )

        _TYPE_ICON = {
            "GOAL": "⚽", "YELLOW_CARD": "🟡", "RED_CARD": "🔴",
            "HALFTIME": "⏸", "FULLTIME": "🏁",
        }
        icon = _TYPE_ICON.get(ev.get("event_type", ""), "📺")
        event_type = ev.get("event_type", "?")

        t = Text()
        t.append(f"  {icon}  {event_type}\n", style="bold yellow")
        if ev.get("player_name"):
            t.append(f"  {ev['player_name']}\n", style="bold white")
        t.append(f"  {ev.get('minute','?')}. dakika\n", style="cyan")

        hook = ev.get("hook_text", "")
        if hook:
            t.append("\n")
            for line in hook.split("\n"):
                t.append(f"  {line}\n", style="italic dim white")

        return Panel(t, title="[bold yellow]SON EVENT[/]", border_style="yellow")

    def _panel_stats(self) -> Panel:
        log_file = self._output_dir / "upload_log.json"
        counts: dict[str, dict] = {
            "instagram": {"success": 0, "error": 0},
            "tiktok":    {"success": 0, "error": 0},
            "youtube":   {"success": 0, "error": 0},
        }
        total = 0
        try:
            if log_file.exists():
                with open(log_file, encoding="utf-8") as f:
                    entries = json.load(f)
                total = len(entries)
                for entry in entries:
                    for r in entry.get("results", []):
                        p = r.get("platform", "")
                        s = r.get("status", "")
                        if p in counts:
                            if s == "published":
                                counts[p]["success"] += 1
                            elif s in ("error", "exception"):
                                counts[p]["error"] += 1
        except (OSError, json.JSONDecodeError):
            pass

        tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta")
        tbl.add_column("Platform", style="cyan", no_wrap=True)
        tbl.add_column("✅", justify="right", style="green")
        tbl.add_column("❌", justify="right", style="red")

        _ICONS = {"instagram": "📸 IG", "tiktok": "🎵 TT", "youtube": "▶️  YT"}
        for p, c in counts.items():
            tbl.add_row(_ICONS.get(p, p), str(c["success"]), str(c["error"]))

        return Panel(
            tbl,
            title=f"[bold magenta]UPLOAD ({total} toplam)[/]",
            border_style="magenta",
        )

    def _panel_log(self) -> Panel:
        with self._lock:
            recent = list(self._recent[-10:])

        _ICONS = {
            "GOAL": "⚽", "YELLOW_CARD": "🟡", "RED_CARD": "🔴",
            "HALFTIME": "⏸", "FULLTIME": "🏁",
        }

        t = Text()
        for ev in reversed(recent):
            icon = _ICONS.get(ev.get("event_type", ""), "📺")
            et   = ev.get("event_type", "?")[:12]
            home = (ev.get("team_home") or "?")[:8]
            away = (ev.get("team_away") or "?")[:8]
            min_ = ev.get("minute", "?")
            t.append(f"  {icon} {et:<12} {home} vs {away}  {min_}'\n", style="dim white")

        if not recent:
            t.append("  Bekleniyor…", style="dim")

        return Panel(t, title="[bold blue]SON EVENTLER[/]", border_style="blue")
