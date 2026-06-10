"""
FFmpeg Native Renderer — 1080×1920 @ 60fps MP4 without Adobe After Effects.
SoccerStar-style: large player portrait BG + foreground action shot + watermark text.
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

_PALETTE: dict[str, dict] = {
    "GOAL":        {"top": (0,  100, 50),  "bot": (0,   8,  4),  "accent": (0, 230, 118),  "badge": (0, 200, 80)},
    "YELLOW_CARD": {"top": (120, 90,  0),  "bot": (20, 14,  0),  "accent": (255, 214, 0),  "badge": (220,170, 0)},
    "RED_CARD":    {"top": (140,  8, 20),  "bot": (20,  0,  4),  "accent": (255,  50, 80), "badge": (220, 30,50)},
    "HALFTIME":    {"top": (10,  60,160),  "bot": (0,   8, 30),  "accent": (100, 180,255), "badge": (50,130,220)},
    "FULLTIME":    {"top": (90,  15,130),  "bot": (10,  0, 20),  "accent": (210,  70,255), "badge": (160,40,210)},
    "KICKOFF":     {"top": (30,  30, 60),  "bot": (4,   4, 12),  "accent": (255, 255,255), "badge": (120,120,180)},
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


def _gradient_bg(pal: dict) -> Image.Image:
    r0, g0, b0 = pal["top"]
    r1, g1, b1 = pal["bot"]
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    for y in range(H):
        t = y / (H - 1)
        arr[y, :, 0] = int(r0 + (r1 - r0) * t)
        arr[y, :, 1] = int(g0 + (g1 - g0) * t)
        arr[y, :, 2] = int(b0 + (b1 - b0) * t)
    return Image.fromarray(arr, "RGB").convert("RGBA")


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
    SoccerStar-style 1080×1920 frame:
      1. Gradient background
      2. Large player portrait (faded, fills upper frame) — background layer
      3. Player name + minute watermark (ghost outline text)
      4. Dark gradient overlay for legibility
      5. Small player (foreground, full body, lower-centre)
      6. Top bar: team badge circles + competition badge
      7. Left info block: team names + score + minute
      8. Bottom event bar: event label badge
    """

    def __init__(self, assets_dir: str, player_cache_dir: Optional[str] = None):
        self._assets_dir = assets_dir
        self._player_cache_dir = player_cache_dir

    def compose(self, event_data: dict) -> Image.Image:
        event_type = event_data.get("event_type", "GOAL")
        pal = _PALETTE.get(event_type, _PALETTE["GOAL"])

        # 1. Gradient bg
        base = _gradient_bg(pal)

        # 2. Large faded player portrait (BG layer)
        self._paste_player_bg(base, event_data)

        # 3. Watermark: player name + minute as ghost outline text
        self._draw_watermarks(base, event_data, pal)

        # 4. Dark gradient overlay
        self._apply_gradient_overlay(base, pal)

        # 5. Small foreground player
        self._paste_player_fg(base, event_data)

        # 6. Top bar (team badges)
        self._draw_top_bar(base, event_data, pal)

        # 7. Left info block
        self._draw_info_block(base, event_type, event_data, pal)

        # 8. Bottom event bar
        self._draw_bottom_bar(base, event_type, pal)

        return base.convert("RGB")

    # ------------------------------------------------------------------
    # Layers
    # ------------------------------------------------------------------

    def _load_player_img(self, event_data: dict) -> Image.Image:
        player_name = event_data.get("player_name") or None
        team = event_data.get("team_home") or event_data.get("team") or ""
        path = get_player_asset(player_name, team, self._player_cache_dir)
        return Image.open(path).convert("RGBA")

    def _paste_player_bg(self, img: Image.Image, event_data: dict) -> None:
        """Large portrait filling upper ~80% of frame — low opacity background."""
        player = self._load_player_img(event_data)

        # Scale to fill width
        scale = W / player.width
        nw, nh = W, int(player.height * scale)
        player = player.resize((nw, nh), Image.LANCZOS)

        # Desaturate + lower brightness for background feel
        rgb = ImageEnhance.Color(player.convert("RGB")).enhance(0.5)
        rgb = ImageEnhance.Brightness(rgb).enhance(0.6)
        player.paste(rgb, mask=player.split()[3])

        # Reduce overall opacity to 55%
        alpha = player.split()[3]
        alpha = alpha.point(lambda p: int(p * 0.55))
        player.putalpha(alpha)

        # Position: top-aligned, slight right offset
        px = int(W * 0.08)
        py = int(H * 0.04)
        img.paste(player, (px, py), player)

    def _paste_player_fg(self, img: Image.Image, event_data: dict) -> None:
        """Full-body action shot, smaller, lower-centre foreground."""
        player = self._load_player_img(event_data)

        # Scale to ~50% frame width
        target_w = int(W * 0.58)
        scale = target_w / player.width
        nw, nh = target_w, int(player.height * scale)
        player = player.resize((nw, nh), Image.LANCZOS)

        # Slight brightness boost so it pops
        rgb = ImageEnhance.Brightness(player.convert("RGB")).enhance(1.1)
        player.paste(rgb, mask=player.split()[3])

        # Position: slight right of centre, bottom-anchored
        px = int(W * 0.38)
        py = H - nh - int(H * 0.07)
        img.paste(player, (px, py), player)

    def _draw_watermarks(self, img: Image.Image, event_data: dict, pal: dict) -> None:
        """Ghost outline text: player name + large minute number."""
        draw = ImageDraw.Draw(img)
        accent = pal["accent"]

        # Player name — very large, right side, slight rotation
        name = (event_data.get("player_name") or "GROWLABS").upper()
        fn_name = _load_font(180)
        nb = draw.textbbox((0, 0), name, font=fn_name)
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]

        # Draw as outline only (stroke, transparent fill trick)
        nx = int(W * 0.04)
        ny = int(H * 0.38)
        # Multiple thin strokes for outline effect
        for dx, dy in [(-3,0),(3,0),(0,-3),(0,3)]:
            draw.text((nx+dx, ny+dy), name, font=fn_name,
                      fill=(*accent, 18))
        draw.text((nx, ny), name, font=fn_name,
                  fill=(255, 255, 255, 12))

        # Large minute number — ghost, upper-right
        minute = str(event_data.get("minute", 0))
        fn_num = _load_font(400)
        mb = draw.textbbox((0, 0), minute, font=fn_num)
        mw, mh = mb[2] - mb[0], mb[3] - mb[1]
        mx = W - mw - int(W * 0.02)
        my = int(H * 0.08)
        for dx, dy in [(-4,0),(4,0),(0,-4),(0,4)]:
            draw.text((mx+dx, my+dy), minute, font=fn_num,
                      fill=(*accent, 22))
        draw.text((mx, my), minute, font=fn_num,
                  fill=(255, 255, 255, 10))

    def _apply_gradient_overlay(self, img: Image.Image, pal: dict) -> None:
        """Strong dark gradient: bottom 60% + left strip for text readability."""
        arr = np.zeros((H, W, 4), dtype=np.uint8)

        # Bottom gradient
        fade_start = int(H * 0.25)
        for y in range(fade_start, H):
            t = (y - fade_start) / (H - fade_start)
            arr[y, :, 3] = min(255, int(210 * t ** 1.2))

        # Left strip (for info block)
        strip_w = int(W * 0.65)
        for x in range(strip_w):
            t = 1.0 - (x / strip_w) ** 1.5
            arr[int(H*0.30):int(H*0.80), x, 3] = np.maximum(
                arr[int(H*0.30):int(H*0.80), x, 3],
                int(160 * t)
            )

        overlay = Image.fromarray(arr, "RGBA")
        img.alpha_composite(overlay)

    def _draw_top_bar(self, img: Image.Image, event_data: dict, pal: dict) -> None:
        draw = ImageDraw.Draw(img)

        # Thin accent line at very top
        draw.rectangle([0, 0, W, 6], fill=(*pal["accent"], 255))

        team_home = event_data.get("team_home", "EV SAHİBİ")
        team_away = event_data.get("team_away", "DEPLASMAN")

        # Team badge circles (coloured discs with initials)
        def badge(cx, cy, r, label):
            # Outer ring
            draw.ellipse([cx-r, cy-r, cx+r, cy+r],
                         fill=(255,255,255,230), outline=(*pal["accent"],200), width=3)
            # Initials
            fn = _load_font(r - 10)
            init = label[:3].upper()
            tb = draw.textbbox((0,0), init, font=fn)
            tw, th = tb[2]-tb[0], tb[3]-tb[1]
            draw.text((cx - tw//2, cy - th//2), init, font=fn,
                      fill=(20, 20, 20, 255))

        badge(64, 70, 52, team_home)
        badge(W - 64, 70, 52, team_away)

        # "WC 2026" centre badge
        fn_wc = _load_font(24)
        wc = "WC 2026"
        wb = draw.textbbox((0,0), wc, font=fn_wc)
        ww, wh = wb[2]-wb[0], wb[3]-wb[1]
        draw.rounded_rectangle(
            [(W-ww)//2 - 16, 30, (W+ww)//2 + 16, 30+wh+14],
            radius=12,
            fill=(0,0,0,160), outline=(*pal["accent"],120), width=2
        )
        draw.text(((W-ww)//2, 37), wc, font=fn_wc,
                  fill=(*pal["accent"], 240))

    def _draw_info_block(
        self, img: Image.Image, event_type: str, event_data: dict, pal: dict
    ) -> None:
        """Left-aligned block: team names / score / minute."""
        draw = ImageDraw.Draw(img)

        team_home  = event_data.get("team_home", "EV SAHİBİ").upper()
        team_away  = event_data.get("team_away", "DEPLASMAN").upper()
        score_home = event_data.get("score_home", 0)
        score_away = event_data.get("score_away", 0)
        minute     = event_data.get("minute", 0)

        x0 = 52
        y  = int(H * 0.46)

        # Team names
        fn_teams = _load_font(52)
        draw.text((x0, y), team_home, font=fn_teams, fill=(255,255,255,240))
        tb = draw.textbbox((0,0), team_home, font=fn_teams)
        y += tb[3] - tb[1] + 4
        draw.text((x0, y), f"vs  {team_away}", font=fn_teams,
                  fill=(200,200,200,200))
        tb2 = draw.textbbox((0,0), f"vs  {team_away}", font=fn_teams)
        y += tb2[3] - tb2[1] + 18

        # Score — large, accent colour
        fn_score = _load_font(150)
        score_str = f"{score_home} — {score_away}"
        draw.text(
            (x0, y), score_str, font=fn_score,
            fill=(*pal["accent"], 255),
            stroke_width=4, stroke_fill=(0,0,0,180),
        )
        sb = draw.textbbox((0,0), score_str, font=fn_score)
        y += sb[3] - sb[1] + 14

        # Minute
        fn_min = _load_font(40)
        min_str = f"{minute}. DAKİKA"
        draw.text((x0, y), min_str, font=fn_min,
                  fill=(180, 180, 180, 200))

    def _draw_bottom_bar(
        self, img: Image.Image, event_type: str, pal: dict
    ) -> None:
        bar_h = 90
        bar_y = H - bar_h

        bar = Image.new("RGBA", (W, bar_h), (0, 0, 0, 220))
        img.paste(bar, (0, bar_y), bar)

        draw = ImageDraw.Draw(img)
        draw.rectangle([0, bar_y, W, bar_y + 4], fill=(*pal["accent"], 255))

        label = _EVENT_DISPLAY.get(event_type, event_type)
        fn_ev = _load_font(52)
        eb = draw.textbbox((0,0), label, font=fn_ev)
        ew, eh = eb[2]-eb[0], eb[3]-eb[1]

        # Coloured badge left
        pad = 20
        badge = Image.new("RGBA", (W, H), (0,0,0,0))
        bd = ImageDraw.Draw(badge)
        bd.rounded_rectangle(
            [40, bar_y+12, 40 + ew + pad*2, bar_y + bar_h - 12],
            radius=16,
            fill=(*pal["badge"], 230),
        )
        img.alpha_composite(badge)

        draw.text(
            (40 + pad, bar_y + (bar_h - eh)//2 - 2),
            label, font=fn_ev,
            fill=(0, 0, 0, 255),
        )

        # Branding right
        fn_br = _load_font(26)
        brand = "GROWLABS 2026"
        brb = draw.textbbox((0,0), brand, font=fn_br)
        draw.text(
            (W - (brb[2]-brb[0]) - 40, bar_y + (bar_h - (brb[3]-brb[1]))//2),
            brand, font=fn_br, fill=(160,160,160,200),
        )


# ---------------------------------------------------------------------------
# Renderer + FFmpeg
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
