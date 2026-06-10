"""tests for src/ffmpeg_renderer.py"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

_SAMPLE_EVENT = {
    "event_type": "GOAL",
    "player_name": "Mbappe",
    "team_home": "Fransa",
    "team_away": "Brezilya",
    "score_home": 2,
    "score_away": 1,
    "minute": 77,
    "match_id": "FRA_BRA",
}


def _fake_player(tmp_path: Path) -> str:
    path = str(tmp_path / "player.png")
    Image.new("RGBA", (300, 500), (100, 100, 200, 200)).save(path)
    return path


# ---------------------------------------------------------------------------
# build_ffmpeg_command
# ---------------------------------------------------------------------------

class TestBuildFFmpegCommand:
    def test_returns_list(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("ffmpeg", "/tmp/f.png", "/tmp/o.mp4")
        assert isinstance(cmd, list)

    def test_first_element_is_ffmpeg_bin(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("/usr/bin/ffmpeg", "/tmp/f.png", "/tmp/o.mp4")
        assert cmd[0] == "/usr/bin/ffmpeg"

    def test_input_path_present(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("ffmpeg", "/tmp/frame.png", "/tmp/out.mp4")
        assert "/tmp/frame.png" in cmd

    def test_output_path_is_last(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("ffmpeg", "/tmp/f.png", "/tmp/out.mp4")
        assert cmd[-1] == "/tmp/out.mp4"

    def test_libx264_codec(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("ffmpeg", "/tmp/f.png", "/tmp/o.mp4")
        assert "libx264" in cmd

    def test_yuv420p_in_command(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("ffmpeg", "/tmp/f.png", "/tmp/o.mp4")
        assert "yuv420p" in " ".join(cmd)

    def test_custom_fps(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("ffmpeg", "/tmp/f.png", "/tmp/o.mp4", fps=30)
        assert "30" in cmd

    def test_custom_duration(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("ffmpeg", "/tmp/f.png", "/tmp/o.mp4", duration_sec=15)
        assert "15" in cmd

    def test_overwrite_flag_present(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("ffmpeg", "/tmp/f.png", "/tmp/o.mp4")
        assert "-y" in cmd

    def test_zoompan_in_vf(self):
        from src.ffmpeg_renderer import build_ffmpeg_command
        cmd = build_ffmpeg_command("ffmpeg", "/tmp/f.png", "/tmp/o.mp4")
        vf = next(cmd[i + 1] for i, a in enumerate(cmd) if a == "-vf")
        assert "zoompan" in vf


# ---------------------------------------------------------------------------
# FrameComposer
# ---------------------------------------------------------------------------

class TestFrameComposer:
    def _make_composer(self, tmp_path):
        from src.ffmpeg_renderer import FrameComposer
        return FrameComposer(
            assets_dir=str(tmp_path),
            player_cache_dir=str(tmp_path),
        )

    def test_compose_returns_image(self, tmp_path):
        composer = self._make_composer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)):
            result = composer.compose(_SAMPLE_EVENT)
        assert isinstance(result, Image.Image)

    def test_compose_correct_dimensions(self, tmp_path):
        composer = self._make_composer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)):
            result = composer.compose(_SAMPLE_EVENT)
        assert result.size == (1080, 1920)

    def test_compose_rgb_mode(self, tmp_path):
        composer = self._make_composer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)):
            result = composer.compose(_SAMPLE_EVENT)
        assert result.mode == "RGB"

    def test_compose_all_event_types(self, tmp_path):
        composer = self._make_composer(tmp_path)
        for et in ["GOAL", "YELLOW_CARD", "RED_CARD", "HALFTIME", "FULLTIME", "KICKOFF"]:
            event = {**_SAMPLE_EVENT, "event_type": et}
            with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)):
                result = composer.compose(event)
            assert result.size == (1080, 1920), f"Wrong size for {et}"

    def test_goal_top_bar_is_green(self, tmp_path):
        composer = self._make_composer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)):
            result = composer.compose({**_SAMPLE_EVENT, "event_type": "GOAL"})
        r, g, b = result.getpixel((0, 1))
        assert g > r and g > b

    def test_red_card_top_bar_is_red(self, tmp_path):
        composer = self._make_composer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)):
            result = composer.compose({**_SAMPLE_EVENT, "event_type": "RED_CARD"})
        r, g, b = result.getpixel((0, 1))
        assert r > g and r > b

    def test_yellow_card_top_bar_is_warm(self, tmp_path):
        composer = self._make_composer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)):
            result = composer.compose({**_SAMPLE_EVENT, "event_type": "YELLOW_CARD"})
        r, g, b = result.getpixel((0, 1))
        assert r > b and g > b  # yellow: high R+G, low B

    def test_compose_without_player_name(self, tmp_path):
        composer = self._make_composer(tmp_path)
        event = {**_SAMPLE_EVENT, "player_name": None}
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)):
            result = composer.compose(event)
        assert result.size == (1080, 1920)

    def test_score_bar_is_dark_at_bottom(self, tmp_path):
        from src.ffmpeg_renderer import SCORE_BAR_H
        composer = self._make_composer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)):
            result = composer.compose(_SAMPLE_EVENT)
        # Bottom strip should be darker than upper area (score bar is near-black)
        bottom_pixel = result.getpixel((540, 1920 - 20))
        top_pixel = result.getpixel((540, 400))
        bottom_brightness = sum(bottom_pixel) / 3
        assert bottom_brightness < 80  # dark score bar


# ---------------------------------------------------------------------------
# FFmpegNativeRenderer
# ---------------------------------------------------------------------------

class TestFFmpegNativeRenderer:
    def _make_renderer(self, tmp_path):
        from src.ffmpeg_renderer import FFmpegNativeRenderer, FFmpegRendererConfig
        return FFmpegNativeRenderer(FFmpegRendererConfig(
            output_dir=str(tmp_path),
            assets_dir=str(tmp_path),
            fps=60,
            duration_sec=5,
            ffmpeg_bin="ffmpeg",
            player_cache_dir=str(tmp_path),
        ))

    def test_render_returns_mp4_path(self, tmp_path):
        renderer = self._make_renderer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)), \
             patch("src.ffmpeg_renderer._run_ffmpeg", return_value=0):
            result = renderer.render(_SAMPLE_EVENT)
        assert result.endswith(".mp4")

    def test_render_calls_ffmpeg(self, tmp_path):
        renderer = self._make_renderer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)), \
             patch("src.ffmpeg_renderer._run_ffmpeg", return_value=0) as mock_ffmpeg:
            renderer.render(_SAMPLE_EVENT)
        mock_ffmpeg.assert_called_once()

    def test_render_raises_on_ffmpeg_failure(self, tmp_path):
        renderer = self._make_renderer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)), \
             patch("src.ffmpeg_renderer._run_ffmpeg", return_value=1):
            with pytest.raises(RuntimeError, match="FFmpeg encoding failed"):
                renderer.render(_SAMPLE_EVENT)

    def test_render_uses_custom_output_path(self, tmp_path):
        renderer = self._make_renderer(tmp_path)
        custom_out = str(tmp_path / "custom_output.mp4")
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)), \
             patch("src.ffmpeg_renderer._run_ffmpeg", return_value=0):
            result = renderer.render(_SAMPLE_EVENT, output_path=custom_out)
        assert result == custom_out

    def test_render_creates_output_dir(self, tmp_path):
        from src.ffmpeg_renderer import FFmpegNativeRenderer, FFmpegRendererConfig
        new_dir = str(tmp_path / "deep" / "nested")
        renderer = FFmpegNativeRenderer(FFmpegRendererConfig(
            output_dir=new_dir,
            assets_dir=str(tmp_path),
            player_cache_dir=str(tmp_path),
        ))
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)), \
             patch("src.ffmpeg_renderer._run_ffmpeg", return_value=0):
            renderer.render(_SAMPLE_EVENT)
        assert Path(new_dir).exists()

    def test_render_output_path_contains_event_type(self, tmp_path):
        renderer = self._make_renderer(tmp_path)
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)), \
             patch("src.ffmpeg_renderer._run_ffmpeg", return_value=0):
            result = renderer.render({**_SAMPLE_EVENT, "event_type": "RED_CARD"})
        assert "RED_CARD" in result

    def test_render_all_event_types(self, tmp_path):
        renderer = self._make_renderer(tmp_path)
        for et in ["GOAL", "YELLOW_CARD", "RED_CARD", "HALFTIME", "FULLTIME"]:
            event = {**_SAMPLE_EVENT, "event_type": et}
            with patch("src.ffmpeg_renderer.get_player_asset", return_value=_fake_player(tmp_path)), \
                 patch("src.ffmpeg_renderer._run_ffmpeg", return_value=0):
                result = renderer.render(event)
            assert result.endswith(".mp4"), f"Failed for {et}"


# ---------------------------------------------------------------------------
# RenderEngine integration
# ---------------------------------------------------------------------------

class TestRenderEngineFFmpegNative:
    def _make_engine(self, tmp_path, use_ffmpeg_native=True):
        from src.render_engine import EngineConfig, RenderEngine
        config = EngineConfig(
            template_path=str(tmp_path / "fake.aep"),
            output_dir=str(tmp_path),
            assets_dir=str(tmp_path),
            use_ffmpeg_native=use_ffmpeg_native,
        )
        with patch("src.ffmpeg_renderer.get_player_asset", return_value=str(tmp_path / "p.png")):
            Image.new("RGBA", (300, 500)).save(str(tmp_path / "p.png"))
            return RenderEngine(config)

    def test_ffmpeg_renderer_instantiated_when_flag_set(self, tmp_path):
        engine = self._make_engine(tmp_path, use_ffmpeg_native=True)
        assert engine._ffmpeg_renderer is not None

    def test_ffmpeg_renderer_none_by_default(self, tmp_path):
        from src.render_engine import EngineConfig, RenderEngine
        config = EngineConfig(
            template_path=str(tmp_path / "fake.aep"),
            output_dir=str(tmp_path),
            assets_dir=str(tmp_path),
        )
        engine = RenderEngine(config)
        assert engine._ffmpeg_renderer is None

    def test_process_event_uses_ffmpeg_renderer(self, tmp_path):
        engine = self._make_engine(tmp_path, use_ffmpeg_native=True)
        fake_out = str(tmp_path / "FRA_BRA_GOAL_abc.mp4")

        event = {
            "match_id": "FRA_BRA", "event_type": "GOAL",
            "minute": 77, "team": "Fransa",
            "score_home": 2, "score_away": 1,
            "team_home": "Fransa", "team_away": "Brezilya",
            "player_name": "Mbappe",
        }
        with patch.object(engine._ffmpeg_renderer, "render", return_value=fake_out) as mock_render:
            result = engine.process_match_event(event)

        mock_render.assert_called_once()
        assert result == fake_out
