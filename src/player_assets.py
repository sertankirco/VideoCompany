"""
Player image fetcher — TheSportsDB API with local disk cache + silhouette fallback.
"""

import io
import logging
import re
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.thesportsdb.com/api/v1/json/3/searchplayers.php"
_DEFAULT_CACHE = Path("assets/players/cache")

_TEAM_COLOURS: dict[str, tuple[int, int, int]] = {
    "fransa": (0, 35, 149),       "france": (0, 35, 149),
    "brezilya": (0, 156, 59),     "brazil": (0, 156, 59),
    "ispanya": (170, 21, 27),     "spain": (170, 21, 27),
    "almanya": (30, 30, 30),      "germany": (30, 30, 30),
    "ingiltere": (200, 20, 20),   "england": (200, 20, 20),
    "türkiye": (227, 10, 23),     "turkiye": (227, 10, 23),  "turkey": (227, 10, 23),
    "arjantin": (117, 170, 219),  "argentina": (117, 170, 219),
    "portekiz": (0, 102, 0),      "portugal": (0, 102, 0),
    "hollanda": (255, 110, 0),    "netherlands": (255, 110, 0),
    "japonya": (188, 0, 45),      "japan": (188, 0, 45),
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.lower().strip())


def fetch_player_image(
    player_name: str,
    cache_dir: Optional[str] = None,
    timeout: int = 8,
) -> Optional[str]:
    """Return local path to player cutout PNG. Downloads and caches on first call."""
    if not player_name or not player_name.strip():
        return None
    cache = Path(cache_dir) if cache_dir else _DEFAULT_CACHE
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / f"{_slug(player_name)}.png"
    if dest.exists():
        return str(dest)
    try:
        resp = requests.get(_SEARCH_URL, params={"p": player_name}, timeout=timeout)
        resp.raise_for_status()
        players = (resp.json() or {}).get("player") or []
        if not players:
            return None
        p = players[0]
        img_url = p.get("strThumb") or p.get("strRender") or p.get("strCutout")
        if not img_url:
            return None
        img_resp = requests.get(img_url, timeout=15)
        img_resp.raise_for_status()
        img = Image.open(io.BytesIO(img_resp.content)).convert("RGBA")
        img.save(str(dest), "PNG")
        logger.info("Cached player image: %s → %s", player_name, dest)
        return str(dest)
    except Exception as exc:
        logger.debug("Player image fetch failed (%s): %s", player_name, exc)
        return None


def generate_silhouette(
    team: str = "",
    output_path: Optional[str] = None,
    size: tuple[int, int] = (500, 800),
) -> str:
    """Generate a coloured player silhouette PNG. Returns local path."""
    colour = _TEAM_COLOURS.get(team.lower().strip(), (120, 120, 120))
    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = w // 2
    # Head
    hr = w // 6
    draw.ellipse([cx - hr, h // 8 - hr, cx + hr, h // 8 + hr], fill=(*colour, 230))
    # Torso
    bt = h // 8 + hr
    bb = int(h * 0.58)
    draw.polygon(
        [(cx - w // 5, bt), (cx + w // 5, bt), (cx + w // 4, bb), (cx - w // 4, bb)],
        fill=(*colour, 220),
    )
    # Arms
    draw.polygon(
        [(cx - w // 5, bt + 10), (cx - w // 4, bt + 10),
         (cx - int(w * 0.45), int(h * 0.5)), (cx - int(w * 0.35), int(h * 0.5))],
        fill=(*colour, 200),
    )
    draw.polygon(
        [(cx + w // 5, bt + 10), (cx + w // 4, bt + 10),
         (cx + int(w * 0.45), int(h * 0.5)), (cx + int(w * 0.35), int(h * 0.5))],
        fill=(*colour, 200),
    )
    # Legs
    lt = int(h * 0.58)
    draw.rectangle([cx - w // 5, lt, cx - w // 20, int(h * 0.95)], fill=(*colour, 190))
    draw.rectangle([cx + w // 20, lt, cx + w // 5, int(h * 0.95)], fill=(*colour, 190))

    sil_name = f"silhouette_{_slug(team) or 'default'}.png"
    if output_path:
        dest = output_path
    elif cache_dir_arg := None:  # resolved below
        dest = str(_DEFAULT_CACHE / sil_name)
    else:
        dest = str(_DEFAULT_CACHE / sil_name)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")
    return dest


def get_player_asset(
    player_name: Optional[str],
    team: str = "",
    cache_dir: Optional[str] = None,
) -> str:
    """Always returns a valid PNG path — API fetch first, silhouette fallback."""
    if player_name:
        path = fetch_player_image(player_name, cache_dir)
        if path:
            return path
    sil_name = f"silhouette_{_slug(team or 'default')}.png"
    sil_path = str(Path(cache_dir) / sil_name) if cache_dir else None
    return generate_silhouette(team, output_path=sil_path)
