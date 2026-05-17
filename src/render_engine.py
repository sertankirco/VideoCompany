"""
Growlabs 2026 Dünya Kupası — Ana Render Motoru

Kurgu Anayası kuralları:
- Her görsel segment 18-24 frame (60fps = 0.30-0.40s). Durağan kare yok.
- Her SFX'in waveform peak'i tam kesim frame'ine senkronize edilir.
  Ses asla görüntüden sonra gelmez.
- Motion Blur: Shutter Angle 360° (JSX ile .aep'e yazılır, aerender korur).
- Çıktı: 1080x1920 (9:16 dikey), 60fps, MP4.
"""

import json
import logging
import os
import queue
import random
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.ae_bridge import (
    build_aerender_command,
    execute_jsx,
    generate_jsx,
    get_aerender_path,
    run_render,
)
from src.data_simulator import generate_hook_text
from src.sfx_sync import analyze_sfx_library, get_sfx_lead_frames
from src.sound_designer import SoundDesigner

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kurgu sabitleri (Growlabs Anayasası)
# ---------------------------------------------------------------------------

VIDEO_FPS: int = 60
CUT_MIN_FRAMES: int = 18   # 0.300 s — alt sınır
CUT_MAX_FRAMES: int = 24   # 0.400 s — üst sınır
DEFAULT_DURATION_SEC: int = 30
DEFAULT_COMP_NAME: str = "WorldCupShort"


# ---------------------------------------------------------------------------
# Veri modelleri
# ---------------------------------------------------------------------------

@dataclass
class MatchEvent:
    match_id: str
    event_type: str          # GOAL, YELLOW_CARD, RED_CARD, KICKOFF, HALFTIME, FULLTIME
    minute: int
    team: str
    score_home: int
    score_away: int
    team_home: str
    team_away: str
    player_name: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def from_dict(cls, data: dict) -> "MatchEvent":
        data = dict(data)
        if "timestamp" in data and isinstance(data["timestamp"], str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


@dataclass
class CutPoint:
    frame: int                     # Videonun kaçıncı frame'inde görsel kesim
    sfx_path: str                  # Hangi SFX dosyası çalınacak
    sfx_start_frame: int           # SFX katmanının AE'deki in-point frame'i
    segment_duration_frames: int   # Önceki kesimden bu kesime kadar frame sayısı


@dataclass
class RenderJob:
    job_id: str
    match_event: MatchEvent
    output_path: str
    status: str = "PENDING"        # PENDING | RENDERING | DONE | FAILED
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Text layer eşlemesi
# ---------------------------------------------------------------------------

_EVENT_LAYER_MAP: dict[str, list[str]] = {
    "GOAL":        ["TEAM_HOME", "TEAM_AWAY", "SCORE", "PLAYER_NAME", "MINUTE", "EVENT_LABEL"],
    "YELLOW_CARD": ["TEAM_HOME", "TEAM_AWAY", "PLAYER_NAME", "MINUTE", "EVENT_LABEL"],
    "RED_CARD":    ["TEAM_HOME", "TEAM_AWAY", "PLAYER_NAME", "MINUTE", "EVENT_LABEL"],
    "KICKOFF":     ["TEAM_HOME", "TEAM_AWAY", "MATCH_DATE", "EVENT_LABEL"],
    "HALFTIME":    ["TEAM_HOME", "TEAM_AWAY", "SCORE", "EVENT_LABEL"],
    "FULLTIME":    ["TEAM_HOME", "TEAM_AWAY", "SCORE", "EVENT_LABEL"],
}

_EVENT_LABELS: dict[str, str] = {
    "GOAL": "GOL!",
    "YELLOW_CARD": "SARI KART",
    "RED_CARD": "KIRMIZI KART",
    "KICKOFF": "BAŞLIYOR",
    "HALFTIME": "DEVRE ARASI",
    "FULLTIME": "MAÇ SONU",
}


def build_text_updates(event: MatchEvent) -> list[dict]:
    """
    MatchEvent alanlarını AE katman adı/değer çiftlerine dönüştürür.

    Returns:
        [{"layer": "TEAM_HOME", "text": "Türkiye"}, ...]
    """
    score = f"{event.score_home}-{event.score_away}"
    minute_str = f"{event.minute}'"
    label = _EVENT_LABELS.get(event.event_type, event.event_type)

    mapping: dict[str, str] = {
        "TEAM_HOME": event.team_home,
        "TEAM_AWAY": event.team_away,
        "SCORE": score,
        "PLAYER_NAME": event.player_name or "",
        "MINUTE": minute_str,
        "EVENT_LABEL": label,
        "MATCH_DATE": event.timestamp.strftime("%d %b %Y"),
    }

    active_layers = _EVENT_LAYER_MAP.get(event.event_type, list(mapping.keys()))
    return [{"layer": k, "text": mapping[k]} for k in active_layers if k in mapping]


# ---------------------------------------------------------------------------
# Kesim planı (Pacing Matematiği)
# ---------------------------------------------------------------------------


def plan_cuts(
    total_frames: int,
    sfx_paths: list[str],
    fps: int = VIDEO_FPS,
) -> list[CutPoint]:
    """
    Anayasa kurallarına göre kesim planı üretir.

    Her segment 18-24 frame (0.30-0.40s @ 60fps). SFX dosyaları döngüsel
    olarak atanır. Her CutPoint'in sfx_start_frame değeri, SFX'in waveform
    peak'inin tam kesim frame'ine denk gelmesini sağlayacak şekilde hesaplanır.

    Args:
        total_frames: Videonun toplam frame sayısı.
        sfx_paths: Varlık klasöründeki SFX dosyalarının yolları.
        fps: Video kare hızı.

    Returns:
        CutPoint listesi, ilk CutPoint ilk kesimi temsil eder.
    """
    if not sfx_paths:
        sfx_paths = [""]   # boş path: ses yok, sadece görsel kesim

    cut_points: list[CutPoint] = []
    current_frame = 0
    sfx_index = 0

    while current_frame < total_frames:
        segment = random.randint(CUT_MIN_FRAMES, CUT_MAX_FRAMES)
        cut_frame = current_frame + segment

        if cut_frame >= total_frames:
            break  # Son segmenti zorla ekleme; video sonu doğal kapanır

        sfx_path = sfx_paths[sfx_index % len(sfx_paths)]

        # SFX peak senkronizasyonu: ses asla görüntüden sonra gelmeyecek
        if sfx_path and Path(sfx_path).exists():
            lead = get_sfx_lead_frames(sfx_path, fps)
        else:
            lead = 0

        sfx_start = max(0, cut_frame - lead)

        cut_points.append(CutPoint(
            frame=cut_frame,
            sfx_path=sfx_path,
            sfx_start_frame=sfx_start,
            segment_duration_frames=segment,
        ))

        current_frame = cut_frame
        sfx_index += 1

    return cut_points


# ---------------------------------------------------------------------------
# Motor konfigürasyonu
# ---------------------------------------------------------------------------

@dataclass
class EngineConfig:
    template_path: str    # .aep şablon dosyası
    output_dir: str       # MP4 çıktı klasörü
    assets_dir: str       # SFX ses efektleri klasörü
    aerender_bin: str = ""  # boş bırakılırsa get_aerender_path() kullanılır
    comp_name: str = DEFAULT_COMP_NAME
    fps: int = VIDEO_FPS
    duration_sec: int = DEFAULT_DURATION_SEC
    ffmpeg_bin: str = "ffmpeg"         # FFmpeg binary yolu
    enable_sound_design: bool = True   # FFmpeg SFX miksajı aktif/pasif


# ---------------------------------------------------------------------------
# Ana render motoru
# ---------------------------------------------------------------------------

class RenderEngine:
    """
    Growlabs yüksek-tutunma video motorunun merkezi orkestratörü.

    process_match_event() → headless tam pipeline:
      1. AE text layerları güncelle (JSX)
      2. Motion Blur 360° aktifte (JSX)
      3. Kesim planı üret (18-24f segmentler)
      4. SFX → cut senkronizasyonu
      5. aerender ile MP4 üret
      6. Manifest JSON kaydet
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self._sfx_paths: list[str] = self._discover_sfx()
        self._job_queue: queue.Queue[RenderJob] = queue.Queue()
        self._running = False
        self._worker: threading.Thread | None = None
        self._sound_designer: SoundDesigner | None = (
            SoundDesigner(
                assets_dir=config.assets_dir,
                fps=config.fps,
                ffmpeg_bin=config.ffmpeg_bin,
            )
            if config.enable_sound_design
            else None
        )

    def _discover_sfx(self) -> list[str]:
        """assets_dir içindeki ses dosyalarını listeler."""
        supported = ('.wav', '.mp3', '.aiff', '.aif', '.flac')
        assets = Path(self.config.assets_dir)
        if not assets.exists():
            logger.warning("assets_dir not found: %s", assets)
            return []
        paths = sorted(
            str(p) for p in assets.iterdir()
            if p.suffix.lower() in supported
        )
        logger.info("Discovered %d SFX file(s): %s", len(paths), paths)
        return paths

    # ------------------------------------------------------------------
    # Senkron pipeline (tek event, test veya CLI için)
    # ------------------------------------------------------------------

    def process_match_event(self, match_data: dict | MatchEvent) -> str:
        """
        Bir maç eventi için tam render pipeline'ını senkron çalıştırır.

        Args:
            match_data: MatchEvent nesnesi veya eşdeğer dict.

        Returns:
            Üretilen MP4 dosyasının yolu.
        """
        if isinstance(match_data, dict):
            event = MatchEvent.from_dict(match_data)
        else:
            event = match_data

        job_id = uuid.uuid4().hex[:8]
        output_path = os.path.join(
            self.config.output_dir,
            f"{event.match_id}_{event.event_type}_{job_id}.mp4",
        )
        os.makedirs(self.config.output_dir, exist_ok=True)

        logger.info("Processing event: %s | %s | %s", event.match_id, event.event_type, job_id)

        # 1. Text katmanı güncellemeleri
        text_updates = build_text_updates(event)
        logger.debug("Text updates: %s", text_updates)

        # 2. Hook metni üret ve HOOK_TEXT katmanına ekle (Growlabs Anayasası)
        hook_text = generate_hook_text(match_data if isinstance(match_data, dict) else {
            "event_type": event.event_type,
            "player_name": event.player_name,
            "team_home": event.team_home,
            "team_away": event.team_away,
            "team": event.team,
            "score_home": event.score_home,
            "score_away": event.score_away,
            "minute": event.minute,
        })
        text_updates.append({"layer": "HOOK_TEXT", "text": hook_text})
        logger.info("Hook text: %s", hook_text.replace("\n", " | "))

        # 3. JSX oluştur (Motion Blur 360° dahil)
        jsx_content = generate_jsx(
            project_path=str(Path(self.config.template_path).resolve()),
            comp_name=self.config.comp_name,
            text_updates=text_updates,
        )

        # 4. JSX çalıştır → .aep güncelle
        execute_jsx(jsx_content)

        # 5. Kesim planı + SFX senkronizasyonu
        total_frames = self.config.duration_sec * self.config.fps
        cuts = plan_cuts(total_frames, self._sfx_paths, self.config.fps)
        logger.info("Cut plan: %d cut points over %d frames", len(cuts), total_frames)

        # 6. Render manifest kaydet
        manifest_path = output_path.replace(".mp4", "_manifest.json")
        self._save_manifest(event, cuts, output_path, manifest_path)

        # 7. aerender tetikle
        aerender_bin = self.config.aerender_bin or get_aerender_path()
        end_frame = total_frames - 1
        cmd = build_aerender_command(
            aerender_bin=aerender_bin,
            project_path=str(Path(self.config.template_path).resolve()),
            comp_name=self.config.comp_name,
            output_path=output_path,
            start_frame=0,
            end_frame=end_frame,
            fps=self.config.fps,
        )
        exit_code = run_render(aerender_bin, cmd)

        if exit_code != 0:
            raise RuntimeError(f"aerender failed with exit code {exit_code}")

        # 8. Frame-accurate SFX miksajı (FFmpeg)
        if self._sound_designer and cuts:
            sfx_output = output_path.replace(".mp4", "_final.mp4")
            mixed = self._sound_designer.mix_sfx_to_video(
                video_path=output_path,
                cut_points=cuts,
                output_path=sfx_output,
            )
            if mixed != output_path:
                output_path = mixed
                logger.info("Sound design applied → %s", output_path)

        logger.info("Render complete: %s", output_path)
        return output_path

    # ------------------------------------------------------------------
    # Asenkron iş kuyruğu
    # ------------------------------------------------------------------

    def submit(self, match_data: dict | MatchEvent) -> RenderJob:
        """Render işini kuyruğa ekler ve hemen RenderJob döner."""
        if isinstance(match_data, dict):
            event = MatchEvent.from_dict(match_data)
        else:
            event = match_data

        job_id = uuid.uuid4().hex[:8]
        output_path = os.path.join(
            self.config.output_dir,
            f"{event.match_id}_{event.event_type}_{job_id}.mp4",
        )
        job = RenderJob(job_id=job_id, match_event=event, output_path=output_path)
        self._job_queue.put(job)
        logger.info("Job %s queued: %s", job_id, event.event_type)
        return job

    def start(self) -> None:
        """Arka plan worker thread'ini başlatır."""
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        logger.info("RenderEngine worker started.")

    def stop(self, timeout: float = 5.0) -> None:
        """Worker'ı durdurur (kuyruğu boşaltır)."""
        self._running = False
        if self._worker:
            self._worker.join(timeout=timeout)
        logger.info("RenderEngine worker stopped.")

    def _worker_loop(self) -> None:
        while self._running:
            try:
                job: RenderJob = self._job_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            job.status = "RENDERING"
            try:
                self.process_match_event(job.match_event)
                job.status = "DONE"
            except Exception as exc:
                job.status = "FAILED"
                job.error = str(exc)
                logger.exception("Job %s failed: %s", job.job_id, exc)
            finally:
                job.completed_at = datetime.utcnow()
                self._job_queue.task_done()

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def _save_manifest(
        self,
        event: MatchEvent,
        cuts: list[CutPoint],
        output_path: str,
        manifest_path: str,
    ) -> None:
        manifest = {
            "match_id": event.match_id,
            "event_type": event.event_type,
            "output_path": output_path,
            "fps": self.config.fps,
            "duration_sec": self.config.duration_sec,
            "total_cuts": len(cuts),
            "cut_points": [
                {
                    "frame": cp.frame,
                    "time_sec": round(cp.frame / self.config.fps, 4),
                    "sfx_path": cp.sfx_path,
                    "sfx_start_frame": cp.sfx_start_frame,
                    "segment_duration_frames": cp.segment_duration_frames,
                    "segment_duration_sec": round(cp.segment_duration_frames / self.config.fps, 4),
                }
                for cp in cuts
            ],
        }
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        logger.info("Manifest saved: %s", manifest_path)
