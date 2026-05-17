"""
Growlabs render motor test paketi.

aerender ve After Effects kurulu olmayan CI ortamlarında çalışacak şekilde
tasarlanmıştır. Tüm subprocess çağrıları mock'lanır.
Ses analizi testleri, stdlib wave modülüyle oluşturulan sentetik WAV dosyaları kullanır.
"""

import io
import json
import math
import os
import struct
import wave
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_wav(tmp_path) -> Path:
    """
    1 saniyelik 44100Hz mono 16-bit WAV dosyası oluşturur.
    Peak amplitude sample 22050'de (t=0.5s).
    60fps'de beklenen peak_frame_offset = int(22050/44100*60) = 30
    """
    wav_path = tmp_path / "test_sfx.wav"
    sr = 44100
    duration_samples = sr  # 1 saniye

    samples = [0] * duration_samples
    # Peak: sample 22050
    samples[22050] = 32767  # 16-bit max
    # Gürültü ekle (peak'i baskılamayacak kadar düşük)
    for i in range(0, duration_samples, 500):
        if i != 22050:
            samples[i] = 1000

    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        data = struct.pack(f"<{duration_samples}h", *samples)
        wf.writeframes(data)

    return wav_path


@pytest.fixture
def silent_wav(tmp_path) -> Path:
    """Tamamen sessiz WAV (tüm örnekler 0)."""
    wav_path = tmp_path / "silent.wav"
    sr = 44100
    samples = [0] * sr
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{sr}h", *samples))
    return wav_path


@pytest.fixture
def sample_event() -> dict:
    return {
        "match_id": "WC2026_TUR_BRA_001",
        "event_type": "GOAL",
        "minute": 73,
        "team": "Türkiye",
        "player_name": "Arda Güler",
        "score_home": 2,
        "score_away": 1,
        "team_home": "Türkiye",
        "team_away": "Brezilya",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# sfx_sync testleri
# ---------------------------------------------------------------------------


class TestSFXSync:
    def test_peak_frame_at_known_position(self, synthetic_wav):
        """Peak sample 22050 / sr 44100 * fps 60 = 30 frame."""
        from src.sfx_sync import find_peak_frame
        frame = find_peak_frame(str(synthetic_wav), video_fps=60)
        assert frame == 30, f"Beklenen 30, gelen {frame}"

    def test_lead_frames_equals_peak_frame(self, synthetic_wav):
        """get_sfx_lead_frames, find_peak_frame ile aynı değeri döndürmeli."""
        from src.sfx_sync import find_peak_frame, get_sfx_lead_frames
        assert find_peak_frame(str(synthetic_wav), 60) == get_sfx_lead_frames(str(synthetic_wav), 60)

    def test_silent_wav_peak_at_zero(self, silent_wav):
        """Sessiz WAV'da peak frame 0 olmalı."""
        from src.sfx_sync import find_peak_frame
        frame = find_peak_frame(str(silent_wav), video_fps=60)
        assert frame == 0

    def test_missing_file_raises(self, tmp_path):
        from src.sfx_sync import find_peak_frame
        with pytest.raises(Exception):
            find_peak_frame(str(tmp_path / "nonexistent.wav"), 60)

    def test_analyze_library_returns_dict(self, tmp_path, synthetic_wav):
        """analyze_sfx_library tüm ses dosyalarını tarar."""
        import shutil
        sfx_dir = tmp_path / "sfx_lib"
        sfx_dir.mkdir()
        shutil.copy(synthetic_wav, sfx_dir / "sfx1.wav")
        shutil.copy(synthetic_wav, sfx_dir / "sfx2.wav")
        # .gitkeep gibi desteklenmeyen dosyalar atlanmalı
        (sfx_dir / ".gitkeep").touch()

        from src.sfx_sync import analyze_sfx_library
        result = analyze_sfx_library(str(sfx_dir), video_fps=60)
        assert len(result) == 2
        for path, lead in result.items():
            assert isinstance(lead, int)
            assert lead >= 0


# ---------------------------------------------------------------------------
# Kesim planı testleri (Anayasa kuralları)
# ---------------------------------------------------------------------------


class TestCutPlan:
    def test_segments_within_18_24_frame_window(self, tmp_path):
        from src.render_engine import plan_cuts
        cuts = plan_cuts(total_frames=1800, sfx_paths=[], fps=60)
        for i, cp in enumerate(cuts):
            assert 18 <= cp.segment_duration_frames <= 24, (
                f"Segment {i} süresi {cp.segment_duration_frames}f, "
                f"beklenen 18-24f arası"
            )

    def test_first_cut_frame_ge_18(self, tmp_path):
        from src.render_engine import plan_cuts
        cuts = plan_cuts(total_frames=1800, sfx_paths=[], fps=60)
        assert cuts[0].frame >= 18

    def test_cut_frames_monotonically_increasing(self, tmp_path):
        from src.render_engine import plan_cuts
        cuts = plan_cuts(total_frames=1800, sfx_paths=[], fps=60)
        for i in range(1, len(cuts)):
            assert cuts[i].frame > cuts[i - 1].frame

    def test_no_cut_beyond_total_frames(self, tmp_path):
        from src.render_engine import plan_cuts
        total = 360
        cuts = plan_cuts(total_frames=total, sfx_paths=[], fps=60)
        for cp in cuts:
            assert cp.frame < total

    def test_sfx_start_frame_le_cut_frame(self, tmp_path, synthetic_wav):
        """SFX başlangıç frame'i hiçbir zaman kesim frame'inden sonra olmamalı."""
        from src.render_engine import plan_cuts
        cuts = plan_cuts(
            total_frames=1800,
            sfx_paths=[str(synthetic_wav)],
            fps=60,
        )
        for cp in cuts:
            assert cp.sfx_start_frame <= cp.frame, (
                f"SFX start {cp.sfx_start_frame} > cut {cp.frame}. "
                "Ses görüntüden sonra geliyor — ANAYASA İHLALİ!"
            )

    def test_sfx_start_frame_non_negative(self, tmp_path, synthetic_wav):
        from src.render_engine import plan_cuts
        cuts = plan_cuts(total_frames=1800, sfx_paths=[str(synthetic_wav)], fps=60)
        for cp in cuts:
            assert cp.sfx_start_frame >= 0

    def test_sfx_cycled_across_cuts(self, tmp_path, synthetic_wav):
        """Birden fazla SFX dosyası döngüsel atanmalı."""
        sfx1 = str(synthetic_wav)
        sfx2 = str(synthetic_wav)
        from src.render_engine import plan_cuts
        cuts = plan_cuts(total_frames=600, sfx_paths=[sfx1, sfx2], fps=60)
        assert len(cuts) >= 2


# ---------------------------------------------------------------------------
# Text layer eşleme testleri
# ---------------------------------------------------------------------------


class TestTextLayerMapping:
    def test_goal_includes_all_required_layers(self, sample_event):
        from src.render_engine import build_text_updates, MatchEvent
        event = MatchEvent.from_dict(sample_event)
        updates = build_text_updates(event)
        names = {u["layer"] for u in updates}
        assert "TEAM_HOME" in names
        assert "TEAM_AWAY" in names
        assert "SCORE" in names
        assert "PLAYER_NAME" in names
        assert "MINUTE" in names
        assert "EVENT_LABEL" in names

    def test_score_format(self, sample_event):
        from src.render_engine import build_text_updates, MatchEvent
        event = MatchEvent.from_dict(sample_event)
        updates = build_text_updates(event)
        score = next(u["text"] for u in updates if u["layer"] == "SCORE")
        assert score == "2-1"

    def test_minute_has_apostrophe(self, sample_event):
        from src.render_engine import build_text_updates, MatchEvent
        event = MatchEvent.from_dict(sample_event)
        updates = build_text_updates(event)
        minute = next(u["text"] for u in updates if u["layer"] == "MINUTE")
        assert minute == "73'"

    def test_goal_label_in_turkish(self, sample_event):
        from src.render_engine import build_text_updates, MatchEvent
        event = MatchEvent.from_dict(sample_event)
        updates = build_text_updates(event)
        label = next(u["text"] for u in updates if u["layer"] == "EVENT_LABEL")
        assert label == "GOL!"

    def test_kickoff_has_no_player_or_score(self):
        from src.render_engine import build_text_updates, MatchEvent
        event = MatchEvent(
            match_id="TEST", event_type="KICKOFF", minute=0,
            team="Türkiye", score_home=0, score_away=0,
            team_home="Türkiye", team_away="Brezilya",
        )
        updates = build_text_updates(event)
        names = {u["layer"] for u in updates}
        assert "SCORE" not in names
        assert "PLAYER_NAME" not in names


# ---------------------------------------------------------------------------
# ae_bridge testleri
# ---------------------------------------------------------------------------


class TestAEBridge:
    def test_jsx_contains_shutter_angle_360(self):
        from src.ae_bridge import generate_jsx
        jsx = generate_jsx(
            project_path="/templates/template.aep",
            comp_name="WorldCupShort",
            text_updates=[],
        )
        assert "shutterAngle = 360" in jsx

    def test_jsx_enables_motion_blur(self):
        from src.ae_bridge import generate_jsx
        jsx = generate_jsx(
            project_path="/templates/template.aep",
            comp_name="WorldCupShort",
            text_updates=[],
        )
        assert "motionBlur = true" in jsx

    def test_jsx_contains_text_updates(self):
        from src.ae_bridge import generate_jsx
        updates = [{"layer": "TEAM_HOME", "text": "Türkiye"}]
        jsx = generate_jsx(
            project_path="/templates/template.aep",
            comp_name="WorldCupShort",
            text_updates=updates,
        )
        assert "Türkiye" in jsx
        assert "TEAM_HOME" in jsx

    def test_jsx_calls_project_save(self):
        from src.ae_bridge import generate_jsx
        jsx = generate_jsx(
            project_path="/templates/template.aep",
            comp_name="WorldCupShort",
            text_updates=[],
        )
        assert "proj.save()" in jsx or "app.project.save()" in jsx

    def test_jsx_written_to_file(self, tmp_path):
        from src.ae_bridge import generate_jsx
        out = tmp_path / "test.jsx"
        generate_jsx(
            project_path="/templates/template.aep",
            comp_name="WorldCupShort",
            text_updates=[],
            output_jsx_path=str(out),
        )
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "shutterAngle" in content

    def test_build_aerender_command_structure(self):
        from src.ae_bridge import build_aerender_command
        cmd = build_aerender_command(
            aerender_bin="/bin/aerender",
            project_path="/templates/template.aep",
            comp_name="WorldCupShort",
            output_path="/output/test.mp4",
            fps=60,
        )
        assert "/bin/aerender" in cmd
        assert "-project" in cmd
        assert "-comp" in cmd
        assert "-output" in cmd
        assert "/output/test.mp4" in cmd
        assert "-fps" in cmd
        assert "60" in cmd

    def test_build_aerender_command_includes_rs_template(self):
        from src.ae_bridge import build_aerender_command
        cmd = build_aerender_command(
            aerender_bin="/bin/aerender",
            project_path="/templates/template.aep",
            comp_name="WorldCupShort",
            output_path="/output/test.mp4",
        )
        assert "-RStemplate" in cmd
        assert "Best Settings" in cmd

    @patch("subprocess.Popen")
    def test_run_render_returns_zero_on_success(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.stdout.__iter__ = MagicMock(return_value=iter(["Rendering..."]))
        mock_proc.stderr.__iter__ = MagicMock(return_value=iter([]))
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        from src.ae_bridge import run_render
        code = run_render(
            aerender_bin="/bin/aerender",
            command=["/bin/aerender", "-project", "x.aep"],
        )
        assert code == 0


# ---------------------------------------------------------------------------
# RenderEngine entegrasyon testi
# ---------------------------------------------------------------------------


class TestRenderEngineIntegration:
    @patch("src.render_engine.run_render", return_value=0)
    @patch("src.render_engine.execute_jsx", return_value=MagicMock(returncode=0))
    @patch("src.render_engine.get_aerender_path", return_value="/bin/aerender")
    def test_process_match_event_full_pipeline(
        self, mock_aerender_path, mock_jsx, mock_render, tmp_path, sample_event
    ):
        from src.render_engine import RenderEngine, EngineConfig

        # Sahte .aep dosyası oluştur
        template = tmp_path / "template.aep"
        template.write_bytes(b"RIFF fake aep")
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        config = EngineConfig(
            template_path=str(template),
            output_dir=str(output_dir),
            assets_dir=str(assets_dir),
            aerender_bin="/bin/aerender",
            duration_sec=3,
        )
        engine = RenderEngine(config)
        output_path = engine.process_match_event(sample_event)

        assert output_path.endswith(".mp4")
        assert mock_render.called
        assert mock_jsx.called

    @patch("src.render_engine.run_render", return_value=0)
    @patch("src.render_engine.execute_jsx", return_value=MagicMock(returncode=0))
    @patch("src.render_engine.get_aerender_path", return_value="/bin/aerender")
    def test_manifest_generated(
        self, mock_aerender_path, mock_jsx, mock_render, tmp_path, sample_event
    ):
        from src.render_engine import RenderEngine, EngineConfig

        template = tmp_path / "template.aep"
        template.write_bytes(b"RIFF fake aep")
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        config = EngineConfig(
            template_path=str(template),
            output_dir=str(output_dir),
            assets_dir=str(tmp_path / "assets_empty"),
            aerender_bin="/bin/aerender",
            duration_sec=3,
        )
        os.makedirs(config.assets_dir, exist_ok=True)
        engine = RenderEngine(config)
        output_path = engine.process_match_event(sample_event)

        manifest_path = output_path.replace(".mp4", "_manifest.json")
        assert Path(manifest_path).exists()
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        assert manifest["fps"] == 60
        assert "cut_points" in manifest
        assert isinstance(manifest["cut_points"], list)
