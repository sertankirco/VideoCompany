"""
FFmpeg Native Renderer — 1080×1920 @ 60fps MP4 without Adobe After Effects.
ANSEDITS style: full-screen player photo + giant event text + bottom score bar.
"""

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.player_assets import get_player_asset

logger = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = 60
SCORE_BAR_H = 200

_PALETTE: dict[str, dict] = {
    "GOAL":        {"accent": (0, 230, 118),   "dark": (0, 40, 15),    "bg": (0, 8, 2)},
    "YELLOW_CARD": {"accent": (255, 214, 0),   "dark": (60, 40, 0),    "bg": (10, 8, 0)},
    "RED_CARD":    {"accent": (255, 45, 80),   "dark": (60, 0, 15),    "bg": (10, 0, 2)},
    "HALFTIME":    {"accent": (68, 138, 255),  "dark": (0, 20, 80),    "bg": (0, 3, 12)},
    "FULLTIME":    {"accent": (224, 64, 251),  "dark": (60, 0, 80),    "bg": (6, 0, 10)},
    "KICKOFF":     {"accent": (255, 255, 255), "dark": (30, 30, 30),   "bg": (4, 4, 4)},
}

_EVENT_DISPLAY: dict[str, str] = {
    "GOAL":        "GOL!",
    "YELLOW_CARD": "SARI\nKART!",
    "RED_CARD":    "KIRMIZI\nKART!",
    "HALFTIME":    "DEVRE\nARASI",
    "FULLTIME":    "MAÇ\nSONA\nERDİ!",
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
    ANSEDITS-style 1080×1920 frame:
      - Full-screen player photo (cover crop)
      - Dark gradient overlay for legibility
      - Giant accent-coloured event text (centred, bold stroke)
      - Player name + hook text below
      - Solid dark score bar at bottom
      - Thin accent line at top
    """

    def __init__(self, assets_dir: str, player_cache_dir: Optional[str] = None):
        self._assets_dir = assets_dir
        self._player_cache_dir = player_cache_dir

    def compose(self, event_data: dict) -> Image.Image:
        event_type = event_data.get("event_type", "GOAL")
        pal = _PALETTE.get(event_type, _PALETTE["GOAL"])

        # 1. Player photo full-screen (cover crop)
        base = self._player_fullscreen(event_data, pal)

        # 2. Gradient overlay: dark top + dark bottom
        overlay = self._gradient_overlay(pal)
        base = Image.alpha_composite(base, overlay)

        # 3. Score bar (solid dark strip at bottom)
        self._draw_score_bar(base, event_data, pal)

        # 4. Giant event text (centred, accent colour)
        self._draw_event_text(base, event_type, pal)

        # 5. Player name
        self._draw_player_name(base, event_data, pal)

        # 6. Hook text
        self._draw_hook_text(base, event_data)

        # 7. Minute badge (top-centre)
        self._draw_minute_badge(base, event_data, pal)

        # 8. Thin accent bar at very top
        draw = ImageDraw.Draw(base)
        draw.rectangle([0, 0, W, 8], fill=(*pal["accent"], 255))

        return base.convert("RGB")

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------

    def _player_fullscreen(self, event_data: dict, pal: dict) -> Image.Image:
        player_name = event_data.get("player_name") or None
        team = event_data.get("team_home") or event_data.get("team") or ""
        path = get_player_asset(player_name, team, self._player_cache_dir)
        img = Image.open(path).convert("RGBA")

        # Cover: scale so the image fills the entire W×H canvas
        scale = max(W / img.width, H / img.height)
        nw, nh = int(img.width * scale), int(img.height * scale)
        img = img.resize((nw, nh), Image.LANCZOS)

        # Center crop
        cx, cy = (nw - W) // 2, (nh - H) // 2
        img = img.crop((cx, cy, cx + W, cy + H))

        # Slight desaturate so text pops
        from PIL import ImageEnhance
        img = ImageEnhance.Color(img).enhance(0.75)

        base = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        base.paste(img, (0, 0), img)
        return base

    def _gradient_overlay(self, pal: dict) -> Image.Image:
        arr = np.zeros((H, W, 4), dtype=np.uint8)
        r, g, b = pal["dark"]
        arr[:, :, 0] = r
        arr[:, :, 1] = g
        arr[:, :, 2] = b

        # Top: dark fade-in (0→250px)
        for y in range(250):
            arr[y, :, 3] = int(200 * (1 - y / 250))

        # Mid-centre: slight tint
        for y in range(250, int(H * 0.5)):
            arr[y, :, 3] = 40

        # Bottom: strong dark for score bar legibility
        fade_start = int(H * 0.5)
        for y in range(fade_start, H):
            progress = (y - fade_start) / (H - fade_start)
            arr[y, :, 3] = min(255, int(220 * progress ** 1.4))

        return Image.fromarray(arr, "RGBA")

    def _draw_score_bar(self, img: Image.Image, event_data: dict, pal: dict) -> None:
        bar_y = H - SCORE_BAR_H
        bar = Image.new("RGBA", (W, SCORE_BAR_H), (0, 0, 0, 235))
        img.paste(bar, (0, bar_y), bar)

        draw = ImageDraw.Draw(img)
        # Accent line at top of score bar
        draw.rectangle([0, bar_y, W, bar_y + 4], fill=(*pal["accent"], 200))

        cy = bar_y + SCORE_BAR_H // 2

        team_home = event_data.get("team_home", "EV SAHİBİ").upper()
        team_away = event_data.get("team_away", "DEPLASMAN").upper()
        score_home = event_data.get("score_home", 0)
        score_away = event_data.get("score_away", 0)

        fn_team  = _load_font(46)
        fn_score = _load_font(100)

        score_str = f"{score_home}  —  {score_away}"
        sb = draw.textbbox((0, 0), score_str, font=fn_score)
        sw = sb[2] - sb[0]
        sh = sb[3] - sb[1]
        draw.text(
            ((W - sw) // 2, cy - sh // 2 - 2),
            score_str, font=fn_score,
            fill=(*pal["accent"], 255),
            stroke_width=2, stroke_fill=(0, 0, 0, 200),
        )

        # Home team (left)
        htb = draw.textbbox((0, 0), team_home, font=fn_team)
        draw.text(
            (44, cy - (htb[3] - htb[1]) // 2),
            team_home, font=fn_team,
            fill=(240, 240, 240, 255),
        )

        # Away team (right)
        atb = draw.textbbox((0, 0), team_away, font=fn_team)
        draw.text(
            (W - 44 - (atb[2] - atb[0]), cy - (atb[3] - atb[1]) // 2),
            team_away, font=fn_team,
            fill=(240, 240, 240, 255),
        )

    def _draw_event_text(self, img: Image.Image, event_type: str, pal: dict) -> None:
        label = _EVENT_DISPLAY.get(event_type, event_type)
        lines = label.split("\n")
        font_size = {1: 310, 2: 240, 3: 185}.get(len(lines), 185)
        font = _load_font(font_size)
        draw = ImageDraw.Draw(img)

        line_dims = []
        for line in lines:
            bb = draw.textbbox((0, 0), line, font=font)
            line_dims.append((bb[2] - bb[0], bb[3] - bb[1]))

        line_h = max(h for _, h in line_dims)
        gap = 16
        total_h = len(lines) * line_h + (len(lines) - 1) * gap
        # Centre block between minute badge (~120px) and player-name area
        start_y = int(H * 0.22)

        for i, (line, (lw, lh)) in enumerate(zip(lines, line_dims)):
            x = (W - lw) // 2
            y = start_y + i * (line_h + gap)
            # Black stroke for contrast
            draw.text(
                (x, y), line, font=font,
                fill=(*pal["accent"], 255),
                stroke_width=8, stroke_fill=(0, 0, 0, 230),
            )

    def _draw_player_name(self, img: Image.Image, event_data: dict, pal: dict) -> None:
        name = event_data.get("player_name") or ""
        if not name:
            return
        font = _load_font(58)
        draw = ImageDraw.Draw(img)
        text = name.upper()
        bb = draw.textbbox((0, 0), text, font=font)
        tw = bb[2] - bb[0]
        # Place below event text block — roughly 62% down
        y = int(H * 0.62)
        x = (W - tw) // 2
        draw.text(
            (x, y), text, font=font,
            fill=(255, 255, 255, 240),
            stroke_width=3, stroke_fill=(0, 0, 0, 200),
        )

    def _draw_hook_text(self, img: Image.Image, event_data: dict) -> None:
        hook = (event_data.get("hook_text") or "").strip()
        if not hook:
            return
        lines = [l.strip() for l in hook.split("\n") if l.strip()][:3]
        font = _load_font(44)
        draw = ImageDraw.Draw(img)
        y = int(H * 0.70)
        for line in lines:
            bb = draw.textbbox((0, 0), line, font=font)
            tw = bb[2] - bb[0]
            th = bb[3] - bb[1]
            draw.text(
                ((W - tw) // 2, y), line, font=font,
                fill=(200, 200, 200, 220),
                stroke_width=2, stroke_fill=(0, 0, 0, 180),
            )
            y += th + 10

    def _draw_minute_badge(self, img: Image.Image, event_data: dict, pal: dict) -> None:
        minute = event_data.get("minute", 0)
        text = f"{minute}'"
        font = _load_font(52)
        draw = ImageDraw.Draw(img)
        bb = draw.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        pad_x, pad_y = 28, 14
        bx = (W - tw) // 2
        by = 22

        badge = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(badge)
        bd.rounded_rectangle(
            [bx - pad_x, by, bx + tw + pad_x, by + th + pad_y * 2],
            radius=30,
            fill=(*pal["accent"], 220),
        )
        img.paste(badge, (0, 0), badge)
        draw.text(
            (bx, by + pad_y), text, font=font,
            fill=(0, 0, 0, 255),
        )


class FFmpegNativeRenderer:
    """
    Compose frame (Pillow) + encode video (FFmpeg zoompan Ken Burns).
    Produces 1080×1920 @ 60fps MP4 — no Adobe After Effects needed.
    """

    def __init__(self, config: FFmpegRendererConfig):
        self.config = config
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
            exit_code = _run_ffmpeg(cmd)
            if exit_code != 0:
                raise RuntimeError(f"FFmpeg encoding failed (exit {exit_code})")
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
    zoom_rate = round(0.04 / total_frames, 6)
    return [
        ffmpeg_bin,
        "-loop", "1",
        "-framerate", str(fps),
        "-i", frame_path,
        "-vf", (
            f"zoompan=z='min(zoom+{zoom_rate},1.04)'"
            f":d={total_frames}"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='ih/2-(ih/zoom/2)'"
            f":s={W}x{H},"
            f"fps={fps},"
            f"format=yuv420p"
        ),
        "-t", str(duration_sec),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-y",
        output_path,
    ]


def _run_ffmpeg(cmd: list[str]) -> int:
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        logger.error("FFmpeg stderr: %s", result.stderr.decode("utf-8", errors="replace"))
    return result.returncode
