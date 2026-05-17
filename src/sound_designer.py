"""
Frame-Accurate FFmpeg Ses Tasarım Motoru

aerender'ın ürettiği MP4 üzerine SFX katmanlarını milimetrik frame doğruluğuyla
yerleştirir. Her SFX'in waveform peak'i, kurgudaki görsel kesim frame'inin
TAM OLARAK İLK KARESINDE patlar. Ses asla gecikmez.

Algoritma:
  sfx_start_ms = (cut_frame - lead_frames) / fps * 1000
  → FFmpeg adelay={sfx_start_ms}|{sfx_start_ms}

Bu sayede: SFX frame (cut - lead)'da başlar → lead frame sonra peak → görsel kesimle eş zamanlı.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.render_engine import CutPoint

logger = logging.getLogger(__name__)


class SoundDesigner:
    """
    CutPoint listesini alır ve her kelimenin ilk frame'ine SFX peak'ini kilitler.

    FFmpeg filter_complex ile:
    - Her CutPoint için adelay değeri hesaplanır (ms cinsinden)
    - Tüm SFX streamleri amix'e gönderilir
    - Video kanalı kopyalanır, ses kanalı AAC olarak kodlanır
    """

    def __init__(self, assets_dir: str, fps: int = 60, ffmpeg_bin: str = "ffmpeg"):
        self.assets_dir = assets_dir
        self.fps = fps
        self.ffmpeg_bin = ffmpeg_bin
        self._lead_cache: dict[str, int] = {}  # sfx_path → lead_frames önbelleği

    # ------------------------------------------------------------------
    # Genel arayüz
    # ------------------------------------------------------------------

    def mix_sfx_to_video(
        self,
        video_path: str,
        cut_points: "list[CutPoint]",
        output_path: str,
        base_volume: float = 0.8,
    ) -> str:
        """
        Verilen video'ya CutPoint'lerdeki SFX'leri frame-accurate olarak karıştırır.

        Args:
            video_path: aerender'dan gelen kaynak MP4.
            cut_points: plan_cuts() çıktısı; her CutPoint bir görsel kesimi tanımlar.
            output_path: SFX eklenmiş çıktı MP4 yolu.
            base_volume: SFX ses seviyesi (0.0-1.0, varsayılan 0.8).

        Returns:
            output_path (başarı) ya da video_path (SFX yoksa veya hata durumunda).
        """
        # Geçerli SFX'i olan CutPoint'leri filtrele
        valid_cuts = [
            cp for cp in cut_points
            if cp.sfx_path and Path(cp.sfx_path).exists()
        ]

        if not valid_cuts:
            logger.warning(
                "No valid SFX files found in cut_points. Skipping FFmpeg mix. "
                "Place WAV files in assets/ and restart."
            )
            return video_path

        # Her CutPoint için (sfx_path, delay_ms) çiftleri oluştur
        sfx_schedule: list[tuple[str, float]] = []
        for cp in valid_cuts:
            lead = self._get_lead_frames_cached(cp.sfx_path)
            delay_ms = self._compute_delay_ms(cp.frame, lead, self.fps)
            sfx_schedule.append((cp.sfx_path, delay_ms))
            logger.debug(
                "CutPoint frame=%d | sfx=%s | lead=%d | delay=%.1fms",
                cp.frame, Path(cp.sfx_path).name, lead, delay_ms,
            )

        cmd = self._build_ffmpeg_command(video_path, sfx_schedule, output_path, base_volume)
        logger.info("FFmpeg SFX mix command: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except FileNotFoundError:
            logger.error(
                "ffmpeg binary not found at '%s'. "
                "Install FFmpeg or set FFMPEG_PATH env var.", self.ffmpeg_bin
            )
            return video_path
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg SFX mix timed out after 300s.")
            return video_path

        if result.returncode != 0:
            logger.error("FFmpeg failed (exit %d):\n%s", result.returncode, result.stderr)
            return video_path

        logger.info("SFX mix complete → %s", output_path)
        return output_path

    # ------------------------------------------------------------------
    # FFmpeg komut üreticisi
    # ------------------------------------------------------------------

    def _build_ffmpeg_command(
        self,
        video_path: str,
        sfx_schedule: list[tuple[str, float]],
        output_path: str,
        base_volume: float,
    ) -> list[str]:
        """
        FFmpeg komut yapısı:

          ffmpeg -y
            -i video.mp4
            -i sfx1.wav -i sfx2.wav ...
            -filter_complex "
              [1:a]adelay=1950|1950,volume=0.8[s0];
              [2:a]adelay=3300|3300,volume=0.8[s1];
              ...
              [s0][s1]...amix=inputs=N:duration=first:dropout_transition=0[aout]
            "
            -map 0:v -map [aout]
            -c:v copy -c:a aac -b:a 192k
            output.mp4

        adelay: stereo kanalların her ikisi için delay değeri (L|R).
        amix duration=first: video uzunluğuna klamp edilir.
        -c:v copy: video yeniden encode edilmez (hız + kalite korunur).
        """
        cmd = [self.ffmpeg_bin, "-y", "-i", video_path]

        # Her SFX dosyası input olarak eklenir
        for sfx_path, _ in sfx_schedule:
            cmd += ["-i", sfx_path]

        # filter_complex oluştur
        filter_parts: list[str] = []
        for i, (_, delay_ms) in enumerate(sfx_schedule):
            delay_int = max(0, int(round(delay_ms)))
            vol = f"{base_volume:.2f}"
            filter_parts.append(
                f"[{i + 1}:a]adelay={delay_int}|{delay_int},volume={vol}[s{i}]"
            )

        n = len(sfx_schedule)
        mix_inputs = "".join(f"[s{i}]" for i in range(n))
        filter_parts.append(
            f"{mix_inputs}amix=inputs={n}:duration=first:dropout_transition=0[aout]"
        )

        filter_complex = ";\n".join(filter_parts)

        cmd += [
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            output_path,
        ]

        return cmd

    # ------------------------------------------------------------------
    # Gecikme matematiği
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_delay_ms(cut_frame: int, lead_frames: int, fps: int) -> float:
        """
        SFX'in FFmpeg adelay değerini milisaniye cinsinden hesaplar.

        Formül:
            sfx_start_frame = max(0, cut_frame - lead_frames)
            delay_ms = sfx_start_frame / fps * 1000

        Garanti: SFX peak'i cut_frame'e denk gelir. Gecikme yoktur.
        """
        sfx_start_frame = max(0, cut_frame - lead_frames)
        return (sfx_start_frame / fps) * 1000.0

    # ------------------------------------------------------------------
    # Lead frames önbelleği
    # ------------------------------------------------------------------

    def _get_lead_frames_cached(self, sfx_path: str) -> int:
        """
        sfx_sync.find_peak_frame() sonucunu önbellekte tutar.
        Aynı WAV dosyası birden fazla CutPoint'te kullanılsa bile tek analiz yapılır.
        """
        if sfx_path not in self._lead_cache:
            from src.sfx_sync import find_peak_frame
            try:
                self._lead_cache[sfx_path] = find_peak_frame(sfx_path, self.fps)
            except Exception as exc:
                logger.warning("Could not analyze SFX '%s': %s. Using lead=0.", sfx_path, exc)
                self._lead_cache[sfx_path] = 0
        return self._lead_cache[sfx_path]

    # ------------------------------------------------------------------
    # Tanılama
    # ------------------------------------------------------------------

    def verify_ffmpeg(self) -> bool:
        """FFmpeg binary'sinin erişilebilir olduğunu doğrular."""
        try:
            result = subprocess.run(
                [self.ffmpeg_bin, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                first_line = result.stdout.splitlines()[0] if result.stdout else "?"
                logger.info("FFmpeg available: %s", first_line)
                return True
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def discover_sfx(self) -> list[str]:
        """assets_dir içindeki desteklenen ses dosyalarını listeler."""
        supported = ('.wav', '.mp3', '.aiff', '.aif', '.flac')
        assets = Path(self.assets_dir)
        if not assets.exists():
            return []
        return sorted(
            str(p) for p in assets.iterdir()
            if p.suffix.lower() in supported
        )
