"""
sound_designer modülü için unit testler.

FFmpeg subprocess çağrıları mock'lanır — gerçek FFmpeg kurulu olmayabilir.
Matematiksel hesaplamalar (delay_ms) ve komut yapısı doğrulanır.
"""

import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.render_engine import CutPoint


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_wav(tmp_path) -> Path:
    """Peak sample 22050 olan 1 saniyelik 44100Hz WAV. lead_frames=30 @ 60fps."""
    wav_path = tmp_path / "test_sfx.wav"
    sr = 44100
    samples = [0] * sr
    samples[22050] = 32767
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{sr}h", *samples))
    return wav_path


@pytest.fixture
def sound_designer(tmp_path):
    from src.sound_designer import SoundDesigner
    return SoundDesigner(assets_dir=str(tmp_path), fps=60, ffmpeg_bin="ffmpeg")


# ---------------------------------------------------------------------------
# Gecikme matematiği testleri
# ---------------------------------------------------------------------------


class TestDelayMath:
    def test_delay_ms_basic(self, sound_designer):
        """cut_frame=120, lead=3, fps=60 → sfx_start=117 → delay=1950ms"""
        delay = sound_designer._compute_delay_ms(cut_frame=120, lead_frames=3, fps=60)
        assert delay == pytest.approx(1950.0)

    def test_delay_ms_zero_lead(self, sound_designer):
        """lead_frames=0 durumunda delay = cut_frame / fps * 1000"""
        delay = sound_designer._compute_delay_ms(cut_frame=60, lead_frames=0, fps=60)
        assert delay == pytest.approx(1000.0)

    def test_delay_ms_never_negative(self, sound_designer):
        """lead_frames > cut_frame olsa bile delay >= 0 olmalı."""
        delay = sound_designer._compute_delay_ms(cut_frame=5, lead_frames=30, fps=60)
        assert delay >= 0.0

    def test_delay_ms_clamped_to_zero(self, sound_designer):
        """SFX başlangıcı 0'ın altına inmemeli."""
        delay = sound_designer._compute_delay_ms(cut_frame=2, lead_frames=100, fps=60)
        assert delay == 0.0

    def test_audio_peak_lands_on_cut_frame(self, sound_designer):
        """
        peak_time = sfx_start_ms/1000 + lead_frames/fps
                  = (cut_frame - lead_frames)/fps + lead_frames/fps
                  = cut_frame/fps
        → peak tam kesim anında.
        """
        fps = 60
        cut_frame = 120
        lead_frames = 7
        delay_ms = sound_designer._compute_delay_ms(cut_frame, lead_frames, fps)
        sfx_start_sec = delay_ms / 1000
        peak_time_sec = sfx_start_sec + lead_frames / fps
        assert peak_time_sec == pytest.approx(cut_frame / fps, abs=1e-6)

    def test_audio_never_after_cut(self, sound_designer):
        """delay_ms asla cut_frame/fps*1000'den büyük olmamalı."""
        fps = 60
        for cut_frame in [18, 36, 60, 120, 600, 1800]:
            for lead in [0, 1, 3, 10, 30]:
                delay = sound_designer._compute_delay_ms(cut_frame, lead, fps)
                cut_ms = cut_frame / fps * 1000
                assert delay <= cut_ms + 1e-6, (
                    f"cut={cut_frame}, lead={lead}: delay {delay}ms > cut {cut_ms}ms — "
                    "SES GÖRÜNTÜden SONRA GELİYOR! ANAYASA İHLALİ"
                )


# ---------------------------------------------------------------------------
# FFmpeg komut yapısı testleri
# ---------------------------------------------------------------------------


class TestFFmpegCommand:
    def test_command_contains_video_input(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/sub-whoosh.wav", 1950.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        assert "/output/test.mp4" in cmd

    def test_command_contains_sfx_input(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/sub-whoosh.wav", 1950.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        assert "/assets/sub-whoosh.wav" in cmd

    def test_command_contains_adelay(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/whoosh.wav", 1950.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        filter_complex = " ".join(cmd)
        assert "adelay=1950|1950" in filter_complex

    def test_command_contains_amix(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/whoosh.wav", 500.0), ("/assets/click.wav", 1000.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        filter_complex = " ".join(cmd)
        assert "amix" in filter_complex
        assert "inputs=2" in filter_complex

    def test_command_maps_video_channel(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/whoosh.wav", 500.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        assert "-map" in cmd
        assert "0:v" in cmd

    def test_command_maps_audio_out(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/whoosh.wav", 500.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        assert "[aout]" in " ".join(cmd)

    def test_command_uses_copy_for_video(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/whoosh.wav", 500.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        assert "-c:v" in cmd
        assert "copy" in cmd

    def test_command_encodes_audio_aac(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/whoosh.wav", 500.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        assert "-c:a" in cmd
        assert "aac" in cmd

    def test_command_contains_output_path(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/whoosh.wav", 500.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        assert "/output/final.mp4" in cmd

    def test_multiple_sfx_multiple_inputs(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[
                ("/assets/whoosh.wav", 500.0),
                ("/assets/click.wav", 1000.0),
                ("/assets/hit.wav", 1500.0),
            ],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        # 1 video + 3 SFX = 4 -i flags
        input_count = sum(1 for i, arg in enumerate(cmd) if arg == "-i")
        assert input_count == 4

    def test_volume_in_filter(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/whoosh.wav", 500.0)],
            output_path="/output/final.mp4",
            base_volume=0.75,
        )
        filter_complex = " ".join(cmd)
        assert "volume=0.75" in filter_complex

    def test_overwrite_flag_present(self, sound_designer):
        cmd = sound_designer._build_ffmpeg_command(
            video_path="/output/test.mp4",
            sfx_schedule=[("/assets/whoosh.wav", 500.0)],
            output_path="/output/final.mp4",
            base_volume=0.8,
        )
        assert "-y" in cmd


# ---------------------------------------------------------------------------
# mix_sfx_to_video testleri
# ---------------------------------------------------------------------------


class TestMixSFXToVideo:
    @patch("subprocess.run")
    def test_calls_ffmpeg_subprocess(self, mock_run, sound_designer, tmp_path, synthetic_wav):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        video = tmp_path / "test.mp4"
        video.write_bytes(b"fake mp4")
        output = tmp_path / "final.mp4"

        cuts = [
            CutPoint(frame=120, sfx_path=str(synthetic_wav), sfx_start_frame=90, segment_duration_frames=20),
        ]

        sound_designer.mix_sfx_to_video(
            video_path=str(video),
            cut_points=cuts,
            output_path=str(output),
        )
        mock_run.assert_called_once()

    def test_no_valid_sfx_returns_original_video(self, sound_designer, tmp_path):
        """SFX dosyası yoksa orijinal video path'i döner."""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"fake mp4")
        output = tmp_path / "final.mp4"

        cuts = [
            CutPoint(frame=120, sfx_path="", sfx_start_frame=90, segment_duration_frames=20),
        ]

        result = sound_designer.mix_sfx_to_video(
            video_path=str(video),
            cut_points=cuts,
            output_path=str(output),
        )
        assert result == str(video)

    @patch("subprocess.run")
    def test_failed_ffmpeg_returns_original(self, mock_run, sound_designer, tmp_path, synthetic_wav):
        """FFmpeg hata verirse orijinal video path döner."""
        mock_run.return_value = MagicMock(returncode=1, stderr="error", stdout="")
        video = tmp_path / "test.mp4"
        video.write_bytes(b"fake mp4")
        output = tmp_path / "final.mp4"

        cuts = [
            CutPoint(frame=60, sfx_path=str(synthetic_wav), sfx_start_frame=30, segment_duration_frames=18),
        ]

        result = sound_designer.mix_sfx_to_video(
            video_path=str(video),
            cut_points=cuts,
            output_path=str(output),
        )
        assert result == str(video)

    @patch("subprocess.run")
    def test_successful_mix_returns_output_path(self, mock_run, sound_designer, tmp_path, synthetic_wav):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        video = tmp_path / "test.mp4"
        video.write_bytes(b"fake mp4")
        output = tmp_path / "final.mp4"

        cuts = [
            CutPoint(frame=60, sfx_path=str(synthetic_wav), sfx_start_frame=30, segment_duration_frames=18),
        ]

        result = sound_designer.mix_sfx_to_video(
            video_path=str(video),
            cut_points=cuts,
            output_path=str(output),
        )
        assert result == str(output)


# ---------------------------------------------------------------------------
# Önbellek testi
# ---------------------------------------------------------------------------


class TestLeadFrameCache:
    def test_same_file_analyzed_once(self, tmp_path, synthetic_wav):
        from src.sound_designer import SoundDesigner
        sd = SoundDesigner(assets_dir=str(tmp_path), fps=60)

        with patch("src.sfx_sync.find_peak_frame", return_value=30) as mock_find:
            sd._get_lead_frames_cached(str(synthetic_wav))
            sd._get_lead_frames_cached(str(synthetic_wav))
            sd._get_lead_frames_cached(str(synthetic_wav))
            assert mock_find.call_count == 1, "Aynı dosya birden fazla kez analiz edildi!"

    def test_different_files_each_analyzed(self, tmp_path, synthetic_wav):
        import shutil
        from src.sound_designer import SoundDesigner
        sfx2 = tmp_path / "sfx2.wav"
        shutil.copy(synthetic_wav, sfx2)

        sd = SoundDesigner(assets_dir=str(tmp_path), fps=60)

        with patch("src.sfx_sync.find_peak_frame", return_value=30) as mock_find:
            sd._get_lead_frames_cached(str(synthetic_wav))
            sd._get_lead_frames_cached(str(sfx2))
            assert mock_find.call_count == 2
