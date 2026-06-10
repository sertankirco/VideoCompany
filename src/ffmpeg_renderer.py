"""
FFmpeg Native Renderer — 1080×1920 @ 60fps MP4 without Adobe After Effects.
Uses Pillow for frame composition and FFmpeg for video encoding (zoompan Ken Burns).
"""

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from src.player_assets import get_player_asset

logger = logging.getLogger(__name__)

W, H = 1080, 1920
FPS = 60
SCORE_BAR_H = 160

_PALETTE: dict[str, dict] = {
    "GOAL":        {"accent": (0, 230, 118),   "glow": (0, 130, 60),   "bg": (0, 8, 2)},
    "YELLOW_CARD": {"accent": (255, 214, 0),   "glow": (180, 120, 0),  "bg": (10, 8, 0)},
    "RED_CARD":    {"accent": (255, 23, 68),   "glow": (180, 0, 30),   "bg": (10, 0, 2)},
    "HALFTIME":    {"accent": (68, 138, 255),  "glow": (20, 60, 180),  "bg": (0, 3, 12)},
    "FULLTIME":    {"accent": (224, 64, 251),  "glow": (140, 0, 190),  "bg": (6, 0, 10)},
    "KICKOFF":     {"accent": (255, 255, 255), "glow": (80, 80, 80),   "bg": (4, 4, 4)},
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


def _radial_gradient(
    size: tuple[int, int],
    center: tuple[int, int],
    radius: int,
    colour: tuple[int, int, int],
    alpha_max: int = 160,
) -> Image.Image:
    w, h = size
    cx, cy = center
    y_arr, x_arr = np.ogrid[:h, :w]
    dist = np.sqrt((x_arr - cx) ** 2 + (y_arr - cy) ** 2).astype(np.float32)
    alpha = np.clip(1.0 - dist / radius, 0.0, 1.0) ** 1.8
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, 0] = colour[0]
    arr[:, :, 1] = colour[1]
    arr[:, :, 2] = colour[2]
    arr[:, :, 3] = (alpha * alpha_max).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


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
    Composes a single 1080×1920 RGBA frame for a match event.
    Player: TheSportsDB API (cached) → silhouette fallback.
    """

    def __init__(self, assets_dir: str, player_cache_dir: Optional[str] = None):
        self._assets_dir = assets_dir
        self._player_cache_dir = player_cache_dir

    def compose(self, event_data: dict) -> Image.Image:
        event_type = event_data.get("event_type", "GOAL")
        pal = _PALETTE.get(event_type, _PALETTE["GOAL"])

        # Base: near-black with palette bg tint
        base = Image.new("RGBA", (W, H), (*pal["bg"], 255))

        # Radial glow at ~35% height
        glow = _radial_gradient(
            (W, H),
            center=(W // 2, int(H * 0.35)),
            radius=620,
            colour=pal["glow"],
            alpha_max=160,
        )
        base = Image.alpha_composite(base, glow)

        # Player layer (behind text via paste before text drawing)
        player_img = self._load_player(event_data)
        px = (W - player_img.width) // 2
        py = H - SCORE_BAR_H - player_img.height + 40
        base.paste(player_img, (px, py), player_img)

        # Vignette
        vignette = self._vignette()
        base = Image.alpha_composite(base, vignette)

        # Score bar
        self._draw_score_bar(base, event_data, pal)

        # Accent top bar (3px)
        draw = ImageDraw.Draw(base)
        draw.rectangle([0, 0, W, 3], fill=(*pal["accent"], 255))

        # Event text
        self._draw_event_text(base, event_type, pal)

        # Minute badge
        self._draw_minute_badge(base, event_data, pal)

        return base.convert("RGB")

    # ------------------------------------------------------------------

    def _load_player(self, event_data: dict) -> Image.Image:
        player_name = event_data.get("player_name") or None
        team = event_data.get("team_home") or event_data.get("team") or ""
        path = get_player_asset(player_name, team, self._player_cache_dir)
        img = Image.open(path).convert("RGBA")
        target_w = 680
        ratio = target_w / img.width
        img = img.resize((target_w, int(img.height * ratio)), Image.LANCZOS)
        return img

    def _vignette(self) -> Image.Image:
        cx, cy = W // 2, H // 2
        y_arr, x_arr = np.ogrid[:H, :W]
        dist = np.sqrt(
            ((x_arr - cx) / (W * 0.6)) ** 2 + ((y_arr - cy) / (H * 0.55)) ** 2
        ).astype(np.float32)
        alpha = np.clip((dist - 0.4) / 0.6, 0.0, 1.0) ** 1.5
        arr = np.zeros((H, W, 4), dtype=np.uint8)
        arr[:, :, 3] = (alpha * 180).astype(np.uint8)
        return Image.fromarray(arr, "RGBA")

    def _draw_score_bar(self, img: Image.Image, event_data: dict, pal: dict) -> None:
        bar_y = H - SCORE_BAR_H
        overlay = Image.new("RGBA", (W, SCORE_BAR_H), (0, 0, 0, 220))
        img.paste(overlay, (0, bar_y), overlay)

        draw = ImageDraw.Draw(img)
        draw.line([(0, bar_y), (W, bar_y)], fill=(255, 255, 255, 20), width=1)

        team_home = event_data.get("team_home", "EV SAHİBİ")
        team_away = event_data.get("team_away", "DEPLASMAN")
        score_home = event_data.get("score_home", 0)
        score_away = event_data.get("score_away", 0)
        accent = pal["accent"]
        cy = bar_y + SCORE_BAR_H // 2

        fn_team = _load_font(48)
        fn_score = _load_font(86)

        score_str = f"{score_home}  —  {score_away}"
        bb = draw.textbbox((0, 0), score_str, font=fn_score)
        sw, sh = bb[2] - bb[0], bb[3] - bb[1]
        draw.text(((W - sw) // 2, cy - sh // 2 - 4), score_str,
                  font=fn_score, fill=(*accent, 255))

        tl_bb = draw.textbbox((0, 0), team_home.upper(), font=fn_team)
        tl_h = tl_bb[3] - tl_bb[1]
        draw.text((40, cy - tl_h // 2), team_home.upper(),
                  font=fn_team, fill=(220, 220, 220, 255))

        tr_bb = draw.textbbox((0, 0), team_away.upper(), font=fn_team)
        tr_w, tr_h = tr_bb[2] - tr_bb[0], tr_bb[3] - tr_bb[1]
        draw.text((W - 40 - tr_w, cy - tr_h // 2), team_away.upper(),
                  font=fn_team, fill=(220, 220, 220, 255))

    def _draw_event_text(self, img: Image.Image, event_type: str, pal: dict) -> None:
        label = _EVENT_DISPLAY.get(event_type, event_type)
        lines = label.split("\n")
        font_size = {1: 280, 2: 230, 3: 180}.get(len(lines), 180)
        font = _load_font(font_size)
        draw = ImageDraw.Draw(img)

        line_dims = []
        for line in lines:
            bb = draw.textbbox((0, 0), line, font=font)
            line_dims.append((bb[2] - bb[0], bb[3] - bb[1]))

        start_y = int(H * 0.18)
        for i, (line, (lw, lh)) in enumerate(zip(lines, line_dims)):
            x = (W - lw) // 2
            y = start_y + i * (lh + 10)
            draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 180))
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

    def _draw_minute_badge(self, img: Image.Image, event_data: dict, pal: dict) -> None:
        minute = event_data.get("minute", 0)
        team_home = event_data.get("team_home", "")
        team_away = event_data.get("team_away", "")
        text = f"{minute}. DAKİKA  ·  {team_home.upper()} - {team_away.upper()}"

        font = _load_font(30)
        draw = ImageDraw.Draw(img)
        bb = draw.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        pad_x, pad_y = 24, 12
        bx = (W - tw) // 2
        by = 20

        badge = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(badge)
        bd.rounded_rectangle(
            [bx - pad_x, by, bx + tw + pad_x, by + th + pad_y * 2],
            radius=20,
            fill=(0, 0, 0, 160),
            outline=(*pal["accent"], 60),
            width=1,
        )
        img.paste(badge, (0, 0), badge)
        draw.text((bx, by + pad_y), text, font=font, fill=(160, 160, 160, 255))


class FFmpegNativeRenderer:
    """
    Compose frame (Pillow) + encode video (FFmpeg).
    Produces 1080×1920 @ 60fps MP4 — no Adobe After Effects needed.
    """

    def __init__(self, config: FFmpegRendererConfig):
        self.config = config
        self._composer = FrameComposer(
            assets_dir=config.assets_dir,
            player_cache_dir=config.player_cache_dir,
        )

    def render(self, event_data: dict, output_path: Optional[str] = None) -> str:
        """Compose + encode. Returns output MP4 path."""
        os.makedirs(self.config.output_dir, exist_ok=True)

        if output_path is None:
            import uuid
            job_id = uuid.uuid4().hex[:8]
            et = event_data.get("event_type", "EVENT")
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
    """Pure function — builds FFmpeg command. Testable without side effects."""
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
