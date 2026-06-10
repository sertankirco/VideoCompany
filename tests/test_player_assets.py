"""tests for src/player_assets.py"""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


_SAMPLE_PNG = None


def _make_png_bytes(size=(200, 300), colour=(100, 150, 200, 255)):
    img = Image.new("RGBA", size, colour)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class TestFetchPlayerImage:
    def test_returns_none_for_empty_string(self, tmp_path):
        from src.player_assets import fetch_player_image
        assert fetch_player_image("", cache_dir=str(tmp_path)) is None

    def test_returns_none_for_whitespace(self, tmp_path):
        from src.player_assets import fetch_player_image
        assert fetch_player_image("   ", cache_dir=str(tmp_path)) is None

    def test_returns_cached_path_when_file_exists(self, tmp_path):
        from src.player_assets import fetch_player_image
        dest = tmp_path / "mbappe.png"
        Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(str(dest))
        result = fetch_player_image("Mbappe", cache_dir=str(tmp_path))
        assert result == str(dest)

    def test_downloads_and_caches_on_first_call(self, tmp_path):
        from src.player_assets import fetch_player_image

        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = {
            "player": [{"strThumb": "http://example.com/player.png"}]
        }
        img_resp = MagicMock()
        img_resp.raise_for_status = MagicMock()
        img_resp.content = _make_png_bytes()

        with patch("requests.get", side_effect=[api_resp, img_resp]):
            result = fetch_player_image("Mbappe", cache_dir=str(tmp_path))

        assert result is not None
        assert Path(result).exists()

    def test_returns_none_on_network_error(self, tmp_path):
        from src.player_assets import fetch_player_image
        with patch("requests.get", side_effect=Exception("connection error")):
            result = fetch_player_image("Mbappe", cache_dir=str(tmp_path))
        assert result is None

    def test_returns_none_when_player_not_found(self, tmp_path):
        from src.player_assets import fetch_player_image
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"player": None}
        with patch("requests.get", return_value=resp):
            result = fetch_player_image("Unknown XYZ", cache_dir=str(tmp_path))
        assert result is None

    def test_returns_none_when_no_image_url(self, tmp_path):
        from src.player_assets import fetch_player_image
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"player": [{"strName": "NoPhoto Player"}]}
        with patch("requests.get", return_value=resp):
            result = fetch_player_image("NoPhoto Player", cache_dir=str(tmp_path))
        assert result is None

    def test_uses_cache_on_second_call(self, tmp_path):
        from src.player_assets import fetch_player_image

        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = {
            "player": [{"strThumb": "http://example.com/r.png"}]
        }
        img_resp = MagicMock()
        img_resp.raise_for_status = MagicMock()
        img_resp.content = _make_png_bytes()

        with patch("requests.get", side_effect=[api_resp, img_resp]) as mock_get:
            fetch_player_image("Ronaldo", cache_dir=str(tmp_path))
            fetch_player_image("Ronaldo", cache_dir=str(tmp_path))
            # Only 2 calls for the first download (api + image); second call hits cache
            assert mock_get.call_count == 2

    def test_uses_strrender_when_strthumb_missing(self, tmp_path):
        from src.player_assets import fetch_player_image

        api_resp = MagicMock()
        api_resp.raise_for_status = MagicMock()
        api_resp.json.return_value = {
            "player": [{"strRender": "http://example.com/render.png"}]
        }
        img_resp = MagicMock()
        img_resp.raise_for_status = MagicMock()
        img_resp.content = _make_png_bytes()

        with patch("requests.get", side_effect=[api_resp, img_resp]):
            result = fetch_player_image("Salah", cache_dir=str(tmp_path))
        assert result is not None


class TestGenerateSilhouette:
    def test_creates_png_file(self, tmp_path):
        from src.player_assets import generate_silhouette
        out = str(tmp_path / "sil.png")
        result = generate_silhouette("fransa", output_path=out)
        assert result == out
        assert Path(out).exists()

    def test_correct_size_default(self, tmp_path):
        from src.player_assets import generate_silhouette
        out = str(tmp_path / "sil.png")
        generate_silhouette("france", output_path=out)
        img = Image.open(out)
        assert img.size == (500, 800)

    def test_correct_size_custom(self, tmp_path):
        from src.player_assets import generate_silhouette
        out = str(tmp_path / "sil.png")
        generate_silhouette("spain", output_path=out, size=(300, 600))
        img = Image.open(out)
        assert img.size == (300, 600)

    def test_transparent_background(self, tmp_path):
        from src.player_assets import generate_silhouette
        out = str(tmp_path / "sil.png")
        generate_silhouette("", output_path=out)
        img = Image.open(out).convert("RGBA")
        assert img.getpixel((0, 0))[3] == 0

    def test_unknown_team_does_not_raise(self, tmp_path):
        from src.player_assets import generate_silhouette
        out = str(tmp_path / "sil.png")
        generate_silhouette("unknownteamxyz999", output_path=out)
        assert Path(out).exists()

    def test_creates_parent_dirs(self, tmp_path):
        from src.player_assets import generate_silhouette
        out = str(tmp_path / "deep" / "nested" / "sil.png")
        generate_silhouette("turkey", output_path=out)
        assert Path(out).exists()


class TestGetPlayerAsset:
    def test_always_returns_string(self, tmp_path):
        from src.player_assets import get_player_asset
        with patch("src.player_assets.fetch_player_image", return_value=None):
            result = get_player_asset(None, team="fransa", cache_dir=str(tmp_path))
        assert isinstance(result, str)

    def test_returned_file_exists(self, tmp_path):
        from src.player_assets import get_player_asset
        with patch("src.player_assets.fetch_player_image", return_value=None):
            result = get_player_asset(None, team="brezilya", cache_dir=str(tmp_path))
        assert Path(result).exists()

    def test_prefers_fetched_image_over_silhouette(self, tmp_path):
        from src.player_assets import get_player_asset
        fake_path = str(tmp_path / "player.png")
        Image.new("RGBA", (100, 100)).save(fake_path)
        with patch("src.player_assets.fetch_player_image", return_value=fake_path):
            result = get_player_asset("Mbappe", team="fransa", cache_dir=str(tmp_path))
        assert result == fake_path

    def test_falls_back_silhouette_when_api_fails(self, tmp_path):
        from src.player_assets import get_player_asset
        with patch("src.player_assets.fetch_player_image", return_value=None):
            result = get_player_asset("Unknown Player", team="spain", cache_dir=str(tmp_path))
        assert Path(result).exists()

    def test_none_name_skips_api(self, tmp_path):
        from src.player_assets import get_player_asset
        with patch("src.player_assets.fetch_player_image") as mock_fetch:
            get_player_asset(None, team="germany", cache_dir=str(tmp_path))
            mock_fetch.assert_not_called()
