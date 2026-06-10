"""
FFmpeg Native Renderer — 1080×1920 @ 60fps MP4 without Adobe After Effects.
Transfermarkt-style: coloured gradient bg + watermark shapes + centred player + info panel.
"""

import logging
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from src.player_assets import get_player_asset

logger = logging.getLogger(__name__)

W, H = 1080, 1920
FPS  = 60
SCORE_BAR_H = 280   # exported for tests

# Per-event colour palette: bg gradient (light→dark), accent, watermark colour
_PALETTE: dict[str, dict] = {
    "GOAL":        {"top": (0,  140, 70),  "bot": (0,  30, 15),  "accent": (0, 230, 118),  "wm": (0, 180, 80)},
    "YELLOW_CARD": {"top": (180, 130, 0),  "bot": (40,  28,  0), "accent": (255, 214, 0),  "wm": (220, 170, 0)},
    "RED_CARD":    {"top": (180,  10, 30), "bot": (40,   0,  8), "accent": (255,  50, 80), "wm": (220,  30, 50)},
    "HALFTIME":    {"top": (20,  80, 200), "bot": (0,   15, 60), "accent": (100, 180, 255),"wm": (60, 130, 220)},
    "FULLTIME":    {"top": (120, 20, 180), "bot": (25,   0, 50), "accent": (220,  80, 255),"wm": (170, 50, 220)},
    "KICKOFF":     {"top": (40,  40,  80), "bot": (5,    5, 20), "accent": (255, 255, 255),"wm": (120,120, 180)},
}

_EVENT_DISPLAY: dict[str, str] = {
    "GOAL":        "GOL!",
    "YELLOW_CARD": "SARI KART!",
    "RED_CARD":    "KIRMIZI KART!",
    "HALFTIME":    "DEVRE ARASI",
    "FULLTIME":    "MAÇ SONA ERDİ!",
    "KICKOFF":     "BAŞLIYOR!",
}

_FONT_PATHS: list[str] = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "C:/Windows/Fonts/impact.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_PATHS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------

def _gradient_bg(pal: dict) -> Image.Image:
    """Top-to-bottom vertical gradient."""
    r0, g0, b0 = pal["top"]
    r1, g1, b1 = pal["bot"]
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    for y in range(H):
        t = y / (H - 1)
        arr[y, :, 0] = int(r0 + (r1 - r0) * t)
        arr[y, :, 1] = int(g0 + (g1 - g0) * t)
        arr[y, :, 2] = int(b0 + (b1 - b0) * t)
    return Image.fromarray(arr, "RGB").convert("RGBA")


def _draw_watermark_shapes(img: Image.Image, event_type: str, pal: dict) -> None:
    """
    Large semi-transparent decorative shapes scattered in bg.
    GOAL → soccer ball circles  |  cards → tilted rect  |  rest → checkmarks
    """
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d     = ImageDraw.Draw(layer)
    wm    = pal["wm"]

    placements = [
        (int(W * 0.82), int(H * 0.12), 210, 28),
        (int(W * 0.88), int(H * 0.40), 160, 22),
        (int(W * 0.06), int(H * 0.22), 155, 20),
        (int(W * 0.12), int(H * 0.55), 115, 18),
        (int(W * 0.78), int(H * 0.65), 100, 15),
    ]

    for cx, cy, size, alpha in placements:
        if event_type in ("YELLOW_CARD", "RED_CARD"):
            _draw_card_shape(d, cx, cy, size, wm, alpha)
        elif event_type == "GOAL":
            _draw_ball_shape(d, cx, cy, size, wm, alpha)
        else:
            _draw_checkmark_shape(d, cx, cy, size, wm, alpha)

    img.alpha_composite(layer)


def _draw_checkmark_shape(
    d: ImageDraw.ImageDraw, cx: int, cy: int,
    size: int, colour: tuple, alpha: int,
) -> None:
    s = size
    pts = [
        (cx - s * 0.50, cy + s * 0.05),
        (cx - s * 0.12, cy + s * 0.52),
        (cx + s * 0.52, cy - s * 0.45),
        (cx + s * 0.40, cy - s * 0.58),
        (cx - s * 0.12, cy + s * 0.32),
        (cx - s * 0.40, cy - s * 0.08),
    ]
    pts = [(float(x), float(y)) for x, y in pts]
    d.polygon(pts, fill=(*colour, alpha))


def _draw_card_shape(
    d: ImageDraw.ImageDraw, cx: int, cy: int,
    size: int, colour: tuple, alpha: int,
) -> None:
    hw, hh = int(size * 0.35), int(size * 0.50)
    angle  = math.radians(20)
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    pts = [
        (cx + x * math.cos(angle) - y * math.sin(angle),
         cy + x * math.sin(angle) + y * math.cos(angle))
        for x, y in corners
    ]
    d.polygon(pts, fill=(*colour, alpha))


def _draw_ball_shape(
    d: ImageDraw.ImageDraw, cx: int, cy: int,
    size: int, colour: tuple, alpha: int,
) -> None:
    r = int(size * 0.55)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*colour, alpha), width=max(4, size // 20))
    # Pentagon patches (simplified as small circles)
    for angle_deg in range(0, 360, 72):
        a  = math.radians(angle_deg)
        px = cx + int(r * 0.55 * math.cos(a))
        py = cy + int(r * 0.55 * math.sin(a))
        pr = max(3, size // 18)
        d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=(*colour, alpha))


# ---------------------------------------------------------------------------
# FrameComposer
# ---------------------------------------------------------------------------

@dataclass
class FFmpegRendererConfig:
    output_dir: str
    assets_dir: str
    fps: int = FPS
    duration_sec: int = 30
    ffmpeg_bin: str = "ffmpeg"
    player_cache_dir: Optional[str] = None


class FrameComposer:
    """
    Transfermarkt-style 1080×1920 frame:
      1. Coloured vertical gradient background
      2. Large semi-transparent watermark shapes (event-specific)
      3. Player cut-out — centred, lower-half
      4. Top header: branding line + giant event text
      5. Bottom info panel: flag circle + team + score + minute
    """

    def __init__(self, assets_dir: str, player_cache_dir: Optional[str] = None):
        self._assets_dir    = assets_dir
        self._player_cache_dir = player_cache_dir

    def compose(self, event_data: dict) -> Image.Image:
        event_type = event_data.get("event_type", "GOAL")
        pal = _PALETTE.get(event_type, _PALETTE["GOAL"])

        # 1. Gradient background
        base = _gradient_bg(pal)

        # 2. Watermark decorations
        _draw_watermark_shapes(base, event_type, pal)

        # 3. Player cut-out (lower half)
        self._paste_player(base, event_data)

        # 4. Top block: score → teams → event text
        self._draw_top_block(base, event_type, event_data, pal)

        # 5. Bottom accent bar + minute
        self._draw_bottom_bar(base, event_data, pal)

        return base.convert("RGB")

    # ------------------------------------------------------------------

    def _paste_player(self, img: Image.Image, event_data: dict) -> None:
        player_name = event_data.get("player_name") or None
        team        = event_data.get("team_home") or event_data.get("team") or ""
        path = get_player_asset(player_name, team, self._player_cache_dir)
        player = Image.open(path).convert("RGBA")

        # Scale so player height = 65% of canvas height
        target_h = int(H * 0.65)
        scale    = target_h / player.height
        target_w = int(player.width * scale)
        player   = player.resize((target_w, target_h), Image.LANCZOS)

        # Slightly brighten the cut-out so it pops from the gradient
        player_rgb = player.convert("RGB")
        player_rgb = ImageEnhance.Brightness(player_rgb).enhance(1.15)
        player.paste(player_rgb, mask=player.split()[3])

        # Centre horizontally; player fills lower 55% of frame
        px = (W - target_w) // 2
        py = H - target_h - 30
        img.paste(player, (px, py), player)

    def _draw_top_block(
        self, img: Image.Image, event_type: str, event_data: dict, pal: dict
    ) -> None:
        """
        Top section (dark semi-transparent panel):
          1. Score  (büyük, accent rengi)
          2. Takım adları  (sol — orta — sağ)
          3. Event metni  (GOL! / SARI KART! vb.)
        """
        draw = ImageDraw.Draw(img)

        # Accent bar at very top
        draw.rectangle([0, 0, W, 8], fill=(*pal["accent"], 255))

        score_home = event_data.get("score_home", 0)
        score_away = event_data.get("score_away", 0)
        team_home  = event_data.get("team_home", "EV SAHİBİ").upper()
        team_away  = event_data.get("team_away", "DEPLASMAN").upper()

        fn_score = _load_font(160)
        fn_teams = _load_font(52)
        label    = _EVENT_DISPLAY.get(event_type, event_type)
        fn_size  = 200 if len(label) <= 6 else (155 if len(label) <= 12 else 115)
        fn_event = _load_font(fn_size)

        # --- measure all three rows ---
        score_str = f"{score_home}  —  {score_away}"
        sb  = draw.textbbox((0, 0), score_str, font=fn_score)
        sh  = sb[3] - sb[1]

        # teams row: "TEAM_HOME   vs   TEAM_AWAY"
        vs_str  = f"{team_home}   vs   {team_away}"
        vb  = draw.textbbox((0, 0), vs_str, font=fn_teams)
        vh  = vb[3] - vb[1]

        eb  = draw.textbbox((0, 0), label, font=fn_event)
        eh  = eb[3] - eb[1]

        gap   = 18
        block_h = sh + gap + vh + gap + eh + gap * 2  # top/bottom padding
        panel_y = 8  # just below accent bar

        # Dark semi-transparent panel behind the block
        panel = Image.new("RGBA", (W, block_h + gap), (0, 0, 0, 190))
        img.paste(panel, (0, panel_y), panel)

        y = panel_y + gap

        # Row 1: Score
        sw_ = sb[2] - sb[0]
        draw.text(
            ((W - sw_) // 2, y), score_str, font=fn_score,
            fill=(*pal["accent"], 255),
            stroke_width=4, stroke_fill=(0, 0, 0, 180),
        )
        y += sh + gap

        # Row 2: Team names
        vw = vb[2] - vb[0]
        draw.text(
            ((W - vw) // 2, y), vs_str, font=fn_teams,
            fill=(230, 230, 230, 255),
        )
        y += vh + gap

        # Row 3: Event text (accent colour, bold)
        ew_ = eb[2] - eb[0]
        draw.text(
            ((W - ew_) // 2, y), label, font=fn_event,
            fill=(255, 255, 255, 255),
            stroke_width=6, stroke_fill=(*pal["accent"], 120),
        )

    def _draw_bottom_bar(self, img: Image.Image, event_data: dict, pal: dict) -> None:
        """Thin minute badge at very bottom."""
        draw    = ImageDraw.Draw(img)
        minute  = event_data.get("minute", 0)
        fn_min  = _load_font(40)
        min_str = f"{minute}'"
        mb  = draw.textbbox((0, 0), min_str, font=fn_min)
        mw  = mb[2] - mb[0]
        draw.text(
            (W - 52 - mw, H - 50), min_str, font=fn_min,
            fill=(*pal["accent"], 220),
            stroke_width=2, stroke_fill=(0, 0, 0, 160),
        )


# ---------------------------------------------------------------------------
# Renderer + FFmpeg command
# ---------------------------------------------------------------------------

class FFmpegNativeRenderer:
    def __init__(self, config: FFmpegRendererConfig):
        self.config    = config
        self._composer = FrameComposer(
            assets_dir=config.assets_dir,
            player_cache_dir=config.player_cache_dir,
        )

    def render(self, event_data: dict, output_path: Optional[str] = None) -> str:
        os.makedirs(self.config.output_dir, exist_ok=True)
        if output_path is None:
            import uuid
            job_id = uuid.uuid4().hex[:8]
            et  = event_data.get("event_type", "EVENT")
            mid = event_data.get("match_id", "match")
            output_path = os.path.join(
                self.config.output_dir, f"{mid}_{et}_{job_id}.mp4"
            )

        frame = self._composer.compose(event_data)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            frame_path = tmp.name
        frame.save(frame_path, "PNG")

        try:
            cmd = build_ffmpeg_command(
                ffmpeg_bin=self.config.ffmpeg_bin,
                frame_path=frame_path,
                output_path=output_path,
                fps=self.config.fps,
                duration_sec=self.config.duration_sec,
            )
            if _run_ffmpeg(cmd) != 0:
                raise RuntimeError("FFmpeg encoding failed")
            logger.info("FFmpeg render complete → %s", output_path)
        finally:
            try:
                os.unlink(frame_path)
            except OSError:
                pass
        return output_path


def build_ffmpeg_command(
    ffmpeg_bin: str,
    frame_path: str,
    output_path: str,
    fps: int = FPS,
    duration_sec: int = 30,
) -> list[str]:
    total_frames = duration_sec * fps
    zoom_rate    = round(0.04 / total_frames, 6)
    return [
        ffmpeg_bin, "-loop", "1", "-framerate", str(fps), "-i", frame_path,
        "-vf", (
            f"zoompan=z='min(zoom+{zoom_rate},1.04)':d={total_frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H},"
            f"fps={fps},format=yuv420p"
        ),
        "-t", str(duration_sec),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-y", output_path,
    ]


def _run_ffmpeg(cmd: list[str]) -> int:
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        logger.error("FFmpeg stderr: %s", result.stderr.decode("utf-8", errors="replace"))
    return result.returncode
