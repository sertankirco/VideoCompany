"""
Growlabs 2026 Dünya Kupası — Otomatik Sosyal Medya Yayıncı Modülü

Mimari:
  /output klasörü izlenir (watchdog)
  → yeni *_final.mp4 tespit edilince PublisherBot.publish_all() tetiklenir
  → asyncio.gather() ile Instagram Reels, TikTok ve YouTube Shorts'a paralel yükleme
  → CaptionFormatter takım adları + event etiketi + hashtag'ler üretir

Güvenlik: Tüm token'lar .env dosyasından okunur, kod içinde asla düz metin bulunmaz.
"""

import asyncio
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

import aiofiles
import aiohttp
from dotenv import load_dotenv
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src.data_simulator import generate_hook_text

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry yardımcısı
# ---------------------------------------------------------------------------

_RETRY_DELAYS = (2, 4, 8, 16)   # saniye — exponential backoff


async def _with_retry(coro_fn: Callable, platform: str, max_attempts: int = 4) -> dict:
    """
    Başarısız upload'ları exponential backoff ile yeniden dener.

    Başarı: status != "error" ve status != "exception" ise dur.
    Kimlik bilgisi eksikliği (skipped) → yeniden deneme yok.
    """
    last: dict = {"platform": platform, "status": "error"}
    for attempt, delay in enumerate((*_RETRY_DELAYS[:max_attempts - 1], None), start=1):
        last = await coro_fn()
        if last.get("status") not in ("error", "exception"):
            return last
        if last.get("status") == "skipped":
            return last
        if delay is None:
            break
        logger.warning(
            "%s upload failed (attempt %d/%d), retrying in %ds…",
            platform, attempt, max_attempts, delay,
        )
        await asyncio.sleep(delay)
    logger.error("%s upload failed after %d attempts.", platform, max_attempts)
    return last

# ---------------------------------------------------------------------------
# Env token'ları
# ---------------------------------------------------------------------------

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_USER_ID      = os.getenv("INSTAGRAM_USER_ID", "")
TIKTOK_ACCESS_TOKEN    = os.getenv("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_CLIENT_KEY      = os.getenv("TIKTOK_CLIENT_KEY", "")
YOUTUBE_API_KEY        = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_CLIENT_ID      = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET  = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN  = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

ENABLED_PLATFORMS = [
    p.strip()
    for p in os.getenv("PUBLISHER_ENABLED_PLATFORMS", "instagram,tiktok,youtube").split(",")
    if p.strip()
]

# ---------------------------------------------------------------------------
# Caption sabitleri
# ---------------------------------------------------------------------------

_WC_HASHTAGS = [
    "#WorldCup2026", "#WC2026", "#FIFAWorldCup",
    "#Football", "#Soccer", "#Futbol",
    "#Reels", "#Shorts", "#TikTok",
    "#DünyaKupası", "#2026",
]

_EVENT_HASHTAGS: dict[str, list[str]] = {
    "GOAL":        ["#Goal", "#Gol", "#GoalAlert"],
    "YELLOW_CARD": ["#YellowCard", "#SariKart"],
    "RED_CARD":    ["#RedCard", "#KirmiziKart"],
    "HALFTIME":    ["#HalfTime", "#DevreArasi"],
    "FULLTIME":    ["#FullTime", "#MacSonu", "#FinalScore"],
}

_PLATFORM_CHAR_LIMITS: dict[str, int] = {
    "instagram": 2200,
    "tiktok":    2200,
    "youtube":   5000,
}


# ---------------------------------------------------------------------------
# Caption Formatlayıcı
# ---------------------------------------------------------------------------


class CaptionFormatter:
    """
    Maç event verisinden platform'a özgü caption üretir.

    Yapı:
      [Hook metni — maks 3 satır]

      [Takım A] vs [Takım B]  |  [Skor]  |  [Dakika']

      [Hashtag'ler]
    """

    def format(self, platform: str, event_data: dict) -> str:
        """
        Args:
            platform: "instagram" | "tiktok" | "youtube"
            event_data: MatchEvent dict'i

        Returns:
            Platform limitine uygun caption string'i.
        """
        hook = generate_hook_text(event_data)

        team_home  = event_data.get("team_home", "")
        team_away  = event_data.get("team_away", "")
        score_home = event_data.get("score_home", 0)
        score_away = event_data.get("score_away", 0)
        minute     = event_data.get("minute", "")

        match_line = (
            f"{team_home} vs {team_away}  |  "
            f"{score_home}-{score_away}  |  {minute}'"
        )

        hashtags = self._build_hashtags(event_data, platform)

        caption = f"{hook}\n\n{match_line}\n\n{hashtags}"

        limit = _PLATFORM_CHAR_LIMITS.get(platform, 2200)
        if len(caption) > limit:
            # Hashtag'leri kırp, kritik içeriği koru
            allowed = limit - len(hook) - len(match_line) - 4
            caption = f"{hook}\n\n{match_line}\n\n{hashtags[:max(0, allowed)]}"

        return caption

    def build_youtube_title(self, event_data: dict) -> str:
        """YouTube video başlığını üretir — #Shorts zorunlu."""
        team_home  = event_data.get("team_home", "")
        team_away  = event_data.get("team_away", "")
        score      = f"{event_data.get('score_home',0)}-{event_data.get('score_away',0)}"
        event_type = event_data.get("event_type", "GOAL")
        label = {
            "GOAL": "GOL", "YELLOW_CARD": "SARI KART",
            "RED_CARD": "KIRMIZI KART", "HALFTIME": "DEVRE ARASI",
            "FULLTIME": "MAÇ SONU",
        }.get(event_type, event_type)
        title = f"{team_home} vs {team_away} | {label} {score} #Shorts #WC2026"
        return title[:100]  # YouTube başlık limiti

    def _build_hashtags(self, event_data: dict, platform: str) -> str:
        event_type = event_data.get("event_type", "")
        team_home  = event_data.get("team_home", "")
        team_away  = event_data.get("team_away", "")

        tags = list(_WC_HASHTAGS)
        tags += _EVENT_HASHTAGS.get(event_type, [])

        # Takım hashtag'leri (boşluksuz)
        for name in (team_home, team_away):
            if name:
                tag = "#" + "".join(name.split())
                if tag not in tags:
                    tags.append(tag)

        return " ".join(tags)


# ---------------------------------------------------------------------------
# Instagram Graph API Client
# ---------------------------------------------------------------------------


class InstagramClient:
    """
    Instagram Graph API v19.0 ile Reels yükleme.

    Not: Graph API gerçek video yüklemesi için video'nun kamuya açık bir
    URL'de barındırılmasını gerektirir. Bu taslak entegrasyon akışını
    ve hata yönetimini gösterir; production'da CDN/signed URL kullanın.
    """

    BASE_URL = "https://graph.instagram.com/v19.0"

    def __init__(
        self,
        access_token: str = "",
        user_id: str = "",
    ):
        self.access_token = access_token or INSTAGRAM_ACCESS_TOKEN
        self.user_id      = user_id or INSTAGRAM_USER_ID

    def _check_credentials(self) -> bool:
        if not self.access_token or not self.user_id:
            logger.warning(
                "Instagram credentials not set. "
                "Set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID in .env"
            )
            return False
        return True

    async def upload_reel(
        self,
        video_path: str,
        caption: str,
        session: aiohttp.ClientSession,
        video_url: Optional[str] = None,
    ) -> dict:
        """
        Adım 1: /{user_id}/media — video container oluştur
        Adım 2: /{user_id}/media_publish — container'ı yayınla

        Args:
            video_path: Yerel MP4 dosya yolu (loglama için).
            caption: Platform'a özel açıklama metni.
            session: Paylaşılan aiohttp ClientSession.
            video_url: Video'nun kamuya açık URL'i (CDN linki).

        Returns:
            {"platform": "instagram", "status": str, "id": str}
        """
        if not self._check_credentials():
            return {"platform": "instagram", "status": "skipped", "reason": "no_credentials"}

        media_url = f"{self.BASE_URL}/{self.user_id}/media"
        publish_url = f"{self.BASE_URL}/{self.user_id}/media_publish"

        container_payload = {
            "media_type": "REELS",
            "caption": caption,
            "access_token": self.access_token,
        }
        if video_url:
            container_payload["video_url"] = video_url

        try:
            # Adım 1: Container oluştur
            async with session.post(media_url, data=container_payload) as resp:
                data = await resp.json()
                if resp.status != 200 or "id" not in data:
                    logger.error("Instagram container creation failed: %s", data)
                    return {
                        "platform": "instagram",
                        "status": "error",
                        "error": data.get("error", {}).get("message", str(data)),
                    }
                container_id = data["id"]
                logger.info("Instagram container created: %s", container_id)

            # Adım 2: Yayınla
            publish_payload = {
                "creation_id": container_id,
                "access_token": self.access_token,
            }
            async with session.post(publish_url, data=publish_payload) as resp:
                result = await resp.json()
                if resp.status != 200:
                    logger.error("Instagram publish failed: %s", result)
                    return {
                        "platform": "instagram",
                        "status": "error",
                        "error": result.get("error", {}).get("message", str(result)),
                    }
                logger.info("Instagram Reel published: %s", result.get("id"))
                return {
                    "platform": "instagram",
                    "status": "published",
                    "id": result.get("id", ""),
                }

        except aiohttp.ClientError as exc:
            logger.exception("Instagram API request failed: %s", exc)
            return {"platform": "instagram", "status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# TikTok Content Posting API v2 Client
# ---------------------------------------------------------------------------


class TikTokClient:
    """
    TikTok Content Posting API v2 ile video yükleme.

    Akış:
    1. /post/publish/video/init/ → upload_url + publish_id
    2. Video binary'sini PUT ile upload_url'e gönder
    3. /post/publish/status/fetch/ ile yayın durumunu kontrol et
    """

    BASE_URL = "https://open.tiktokapis.com/v2"

    def __init__(self, access_token: str = "", client_key: str = ""):
        self.access_token = access_token or TIKTOK_ACCESS_TOKEN
        self.client_key   = client_key   or TIKTOK_CLIENT_KEY

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _check_credentials(self) -> bool:
        if not self.access_token:
            logger.warning(
                "TikTok credentials not set. "
                "Set TIKTOK_ACCESS_TOKEN in .env"
            )
            return False
        return True

    async def upload_video(
        self,
        video_path: str,
        caption: str,
        session: aiohttp.ClientSession,
    ) -> dict:
        """
        Args:
            video_path: Yüklenecek yerel MP4 dosyası.
            caption: Video açıklaması.
            session: Paylaşılan aiohttp ClientSession.

        Returns:
            {"platform": "tiktok", "status": str, "publish_id": str}
        """
        if not self._check_credentials():
            return {"platform": "tiktok", "status": "skipped", "reason": "no_credentials"}

        file_size = Path(video_path).stat().st_size if Path(video_path).exists() else 0

        init_url = f"{self.BASE_URL}/post/publish/video/init/"
        init_payload = {
            "post_info": {
                "title": caption[:150],  # TikTok başlık limiti
                "privacy_level": "SELF_ONLY",  # Test için; prod'da PUBLIC_TO_EVERYONE
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": file_size,
                "total_chunk_count": 1,
            },
        }

        try:
            # Adım 1: Upload URL al
            async with session.post(
                init_url, json=init_payload, headers=self._auth_headers()
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    logger.error("TikTok init failed: %s", data)
                    return {
                        "platform": "tiktok",
                        "status": "error",
                        "error": str(data),
                    }
                upload_url  = data["data"]["upload_url"]
                publish_id  = data["data"]["publish_id"]
                logger.info("TikTok upload URL acquired. publish_id=%s", publish_id)

            # Adım 2: Videoyu yükle
            if Path(video_path).exists():
                async with aiofiles.open(video_path, "rb") as f:
                    video_bytes = await f.read()
                upload_headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(file_size),
                    "Content-Range": f"bytes 0-{file_size-1}/{file_size}",
                }
                async with session.put(
                    upload_url, data=video_bytes, headers=upload_headers
                ) as resp:
                    if resp.status not in (200, 201, 204):
                        body = await resp.text()
                        logger.error("TikTok upload failed (status %d): %s", resp.status, body)
                        return {
                            "platform": "tiktok",
                            "status": "error",
                            "error": f"Upload HTTP {resp.status}",
                        }
                    logger.info("TikTok video uploaded successfully.")
            else:
                logger.warning("TikTok: video file not found at %s (dry run)", video_path)

            # Adım 3: Yayın durumunu kontrol et
            status_url = f"{self.BASE_URL}/post/publish/status/fetch/"
            async with session.post(
                status_url,
                json={"publish_id": publish_id},
                headers=self._auth_headers(),
            ) as resp:
                status_data = await resp.json()
                publish_status = (
                    status_data.get("data", {}).get("status", "PROCESSING")
                )
                logger.info("TikTok publish status: %s", publish_status)
                return {
                    "platform": "tiktok",
                    "status": publish_status.lower(),
                    "publish_id": publish_id,
                }

        except aiohttp.ClientError as exc:
            logger.exception("TikTok API request failed: %s", exc)
            return {"platform": "tiktok", "status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# YouTube Data API v3 Client
# ---------------------------------------------------------------------------


class YouTubeClient:
    """
    YouTube Data API v3 + OAuth2 ile Shorts yükleme.

    OAuth2 akışı:
    1. YOUTUBE_REFRESH_TOKEN ile yeni access_token al
    2. /upload/youtube/v3/videos endpoint'ine multipart POST
    3. Video başlığına #Shorts otomatik eklenir (YouTube Shorts algoritma koşulu)
    """

    TOKEN_URL  = "https://oauth2.googleapis.com/token"
    UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        refresh_token: str = "",
    ):
        self.client_id     = client_id     or YOUTUBE_CLIENT_ID
        self.client_secret = client_secret or YOUTUBE_CLIENT_SECRET
        self.refresh_token = refresh_token or YOUTUBE_REFRESH_TOKEN

    def _check_credentials(self) -> bool:
        if not all([self.client_id, self.client_secret, self.refresh_token]):
            logger.warning(
                "YouTube OAuth2 credentials incomplete. "
                "Set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, "
                "YOUTUBE_REFRESH_TOKEN in .env"
            )
            return False
        return True

    async def _refresh_access_token(self, session: aiohttp.ClientSession) -> str:
        """Refresh token ile yeni access_token alır."""
        payload = {
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type":    "refresh_token",
        }
        async with session.post(self.TOKEN_URL, data=payload) as resp:
            data = await resp.json()
            if "access_token" not in data:
                raise ValueError(f"YouTube token refresh failed: {data}")
            logger.info("YouTube access token refreshed.")
            return data["access_token"]

    async def upload_shorts(
        self,
        video_path: str,
        title: str,
        description: str,
        session: aiohttp.ClientSession,
    ) -> dict:
        """
        Args:
            video_path: Yüklenecek MP4 dosyası.
            title: Video başlığı (#Shorts otomatik eklenir).
            description: Video açıklaması.
            session: Paylaşılan aiohttp ClientSession.

        Returns:
            {"platform": "youtube", "status": str, "video_id": str}
        """
        if not self._check_credentials():
            return {"platform": "youtube", "status": "skipped", "reason": "no_credentials"}

        try:
            access_token = await self._refresh_access_token(session)

            # Başlığa #Shorts ekle (YouTube Shorts için zorunlu)
            if "#Shorts" not in title:
                title = f"{title} #Shorts"

            metadata = {
                "snippet": {
                    "title": title[:100],
                    "description": description[:5000],
                    "tags": ["WorldCup2026", "WC2026", "Football", "Shorts"],
                    "categoryId": "17",  # Sports
                },
                "status": {
                    "privacyStatus": "private",  # Test; prod'da "public"
                    "selfDeclaredMadeForKids": False,
                },
            }

            upload_params = {
                "uploadType": "multipart",
                "part": "snippet,status",
            }
            headers = {"Authorization": f"Bearer {access_token}"}

            if Path(video_path).exists():
                async with aiofiles.open(video_path, "rb") as f:
                    video_bytes = await f.read()

                data = aiohttp.FormData()
                data.add_field(
                    "metadata",
                    __import__("json").dumps(metadata),
                    content_type="application/json; charset=UTF-8",
                )
                data.add_field(
                    "video",
                    video_bytes,
                    content_type="video/mp4",
                    filename=Path(video_path).name,
                )

                async with session.post(
                    self.UPLOAD_URL,
                    params=upload_params,
                    headers=headers,
                    data=data,
                ) as resp:
                    result = await resp.json()
                    if resp.status not in (200, 201):
                        logger.error("YouTube upload failed: %s", result)
                        return {
                            "platform": "youtube",
                            "status": "error",
                            "error": str(result),
                        }
                    video_id = result.get("id", "")
                    logger.info("YouTube Shorts uploaded: %s", video_id)
                    return {
                        "platform": "youtube",
                        "status": "uploaded",
                        "video_id": video_id,
                    }
            else:
                logger.warning("YouTube: video file not found at %s (dry run)", video_path)
                return {"platform": "youtube", "status": "dry_run", "video_id": ""}

        except (aiohttp.ClientError, ValueError) as exc:
            logger.exception("YouTube API request failed: %s", exc)
            return {"platform": "youtube", "status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# PublisherBot — Ana Koordinatör
# ---------------------------------------------------------------------------


class PublisherBot:
    """
    Render çıktısını 3 platforma paralel yükler.

    Kullanım:
        bot = PublisherBot(output_dir="output/")
        bot.register_event("/output/match_GOAL_abc_final.mp4", event_data)
        # Watchdog bot.publish_sync() otomatik çağırır
    """

    def __init__(
        self,
        output_dir: str = "output/",
        enabled_platforms: Optional[list[str]] = None,
    ):
        self.output_dir        = output_dir
        self.enabled_platforms = enabled_platforms or ENABLED_PLATFORMS
        self.formatter         = CaptionFormatter()
        self.instagram         = InstagramClient()
        self.tiktok            = TikTokClient()
        self.youtube           = YouTubeClient()
        self._event_store: dict[str, dict] = {}
        self._lock = threading.Lock()

    def register_event(self, video_path: str, event_data: dict) -> None:
        """
        Render motoru, video üretince event verisini buraya kaydeder.
        Watchdog tetiklendiğinde bu store'dan event verisini çeker.
        """
        with self._lock:
            self._event_store[str(Path(video_path).resolve())] = event_data
        logger.debug("Event registered for: %s", video_path)

    def _get_event(self, video_path: str) -> dict:
        with self._lock:
            return self._event_store.get(str(Path(video_path).resolve()), {})

    async def publish_all(self, video_path: str) -> list[dict]:
        """
        Etkin platformlara asyncio.gather() ile paralel yükler.

        Returns:
            Her platform için sonuç dict'i içeren liste.
        """
        event_data = self._get_event(video_path)
        if not event_data:
            logger.warning("No event data found for %s — using empty dict.", video_path)

        tasks = []
        async with aiohttp.ClientSession() as session:
            if "instagram" in self.enabled_platforms:
                caption = self.formatter.format("instagram", event_data)
                tasks.append(_with_retry(
                    lambda c=caption: self.instagram.upload_reel(video_path, c, session),
                    "instagram",
                ))

            if "tiktok" in self.enabled_platforms:
                caption = self.formatter.format("tiktok", event_data)
                tasks.append(_with_retry(
                    lambda c=caption: self.tiktok.upload_video(video_path, c, session),
                    "tiktok",
                ))

            if "youtube" in self.enabled_platforms:
                title       = self.formatter.build_youtube_title(event_data)
                description = self.formatter.format("youtube", event_data)
                tasks.append(_with_retry(
                    lambda t=title, d=description: self.youtube.upload_shorts(video_path, t, d, session),
                    "youtube",
                ))

            if not tasks:
                logger.warning("No platforms enabled. Check PUBLISHER_ENABLED_PLATFORMS.")
                return []

            results = await asyncio.gather(*tasks, return_exceptions=True)

        output = []
        for r in results:
            if isinstance(r, Exception):
                logger.error("Platform publish exception: %s", r)
                output.append({"status": "exception", "error": str(r)})
            else:
                output.append(r)

        logger.info("Publish complete: %s", output)
        self._write_log(video_path, output)
        return output

    def _write_log(self, video_path: str, results: list[dict]) -> None:
        """Yayın sonuçlarını upload_log.json'a ekler."""
        log_path = Path(self.output_dir) / "upload_log.json"
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "video": str(video_path),
            "results": results,
        }
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            existing: list = []
            if log_path.exists():
                with open(log_path, encoding="utf-8") as f:
                    existing = json.load(f)
            existing.append(entry)
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            logger.debug("Upload log updated: %s", log_path)
        except OSError as exc:
            logger.warning("Could not write upload log: %s", exc)

    def publish_sync(self, video_path: str) -> list[dict]:
        """asyncio.run() sarmalayıcısı — watchdog thread'inden çağrılabilir."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Zaten bir event loop varsa (örn. Jupyter), yeni thread aç
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(asyncio.run, self.publish_all(video_path))
                    return future.result()
            else:
                return loop.run_until_complete(self.publish_all(video_path))
        except RuntimeError:
            return asyncio.run(self.publish_all(video_path))


# ---------------------------------------------------------------------------
# OutputWatcher — Watchdog Dosya İzleyicisi
# ---------------------------------------------------------------------------


class OutputWatcher(FileSystemEventHandler):
    """
    /output klasörünü gerçek zamanlı izler.

    Yalnızca *_final.mp4 dosyalarını işler:
    - aerender çıktısı (match_XXX.mp4)   → atla
    - FFmpeg SFX miksaj çıktısı (_final.mp4) → yayınla
    """

    def __init__(self, bot: PublisherBot):
        super().__init__()
        self.bot = bot

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = event.src_path
        if path.endswith("_final.mp4"):
            logger.info("[OutputWatcher] New final video detected: %s", path)
            try:
                self.bot.publish_sync(path)
            except Exception as exc:
                logger.exception("[OutputWatcher] Publish failed for %s: %s", path, exc)
        else:
            logger.debug("[OutputWatcher] Skipping non-final file: %s", path)


def start_watcher(output_dir: str, bot: PublisherBot) -> Observer:
    """
    Watchdog Observer'ı başlatır.

    Args:
        output_dir: İzlenecek klasör (/output).
        bot: Yayıncı bot örneği.

    Returns:
        Çalışan Observer (durdurmak için: observer.stop(); observer.join())
    """
    os.makedirs(output_dir, exist_ok=True)
    observer = Observer()
    observer.schedule(OutputWatcher(bot), path=output_dir, recursive=False)
    observer.start()
    logger.info("[OutputWatcher] Watching: %s", output_dir)
    return observer
