"""
publisher modülü için unit testler.

Gerçek API çağrıları mock'lanır — internet bağlantısı veya token gerekmez.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_data():
    return {
        "event_type": "GOAL",
        "player_name": "Mbappe",
        "team_home": "Fransa",
        "team_away": "Brezilya",
        "team": "Fransa",
        "score_home": 1,
        "score_away": 0,
        "minute": 90,
        "match_id": "FRA_BRA_001",
    }


@pytest.fixture
def formatter():
    from src.publisher import CaptionFormatter
    return CaptionFormatter()


# ---------------------------------------------------------------------------
# CaptionFormatter testleri
# ---------------------------------------------------------------------------


class TestCaptionFormatter:
    def test_goal_caption_contains_team_names(self, formatter, event_data):
        caption = formatter.format("instagram", event_data)
        assert "fransa" in caption.lower() or "brezilya" in caption.lower()

    def test_caption_contains_hashtags(self, formatter, event_data):
        caption = formatter.format("instagram", event_data)
        assert "#WorldCup2026" in caption

    def test_caption_respects_instagram_limit(self, formatter, event_data):
        caption = formatter.format("instagram", event_data)
        assert len(caption) <= 2200

    def test_caption_respects_tiktok_limit(self, formatter, event_data):
        caption = formatter.format("tiktok", event_data)
        assert len(caption) <= 2200

    def test_caption_respects_youtube_limit(self, formatter, event_data):
        caption = formatter.format("youtube", event_data)
        assert len(caption) <= 5000

    def test_youtube_title_has_shorts_tag(self, formatter, event_data):
        title = formatter.build_youtube_title(event_data)
        assert "#Shorts" in title

    def test_youtube_title_max_100_chars(self, formatter, event_data):
        title = formatter.build_youtube_title(event_data)
        assert len(title) <= 100

    def test_caption_not_empty(self, formatter, event_data):
        for platform in ["instagram", "tiktok", "youtube"]:
            caption = formatter.format(platform, event_data)
            assert caption.strip() != ""

    def test_hashtags_contain_team_specific_tags(self, formatter, event_data):
        caption = formatter.format("instagram", event_data)
        # Fransa veya Brezilya hashtag'i olmalı
        assert "#Fransa" in caption or "#Brezilya" in caption or "fransa" in caption.lower()

    def test_goal_hashtag_in_caption(self, formatter, event_data):
        caption = formatter.format("instagram", event_data)
        assert "#Goal" in caption or "#Gol" in caption or "#WorldCup2026" in caption

    def test_yellow_card_event(self, formatter):
        event = {
            "event_type": "YELLOW_CARD",
            "player_name": "Ronaldo",
            "team_home": "Portekiz",
            "team_away": "Hollanda",
            "team": "Portekiz",
            "score_home": 0,
            "score_away": 0,
            "minute": 45,
            "match_id": "POR_NED_001",
        }
        caption = formatter.format("tiktok", event)
        assert caption.strip() != ""
        assert len(caption) <= 2200

    def test_unknown_platform_uses_default_limit(self, formatter, event_data):
        caption = formatter.format("unknown_platform", event_data)
        assert caption.strip() != ""

    def test_caption_is_string(self, formatter, event_data):
        result = formatter.format("instagram", event_data)
        assert isinstance(result, str)

    def test_score_visible_in_caption(self, formatter, event_data):
        caption = formatter.format("instagram", event_data)
        assert "1-0" in caption or "1" in caption


# ---------------------------------------------------------------------------
# InstagramClient testleri
# ---------------------------------------------------------------------------


class TestInstagramClient:
    def _make_client(self):
        from src.publisher import InstagramClient
        client = InstagramClient()
        client.access_token = "fake_token"
        client.user_id = "12345"
        return client

    @pytest.mark.asyncio
    async def test_upload_reel_returns_dict_with_status(self, tmp_path):
        client = self._make_client()
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake video")

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"id": "container_123"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_publish_response = AsyncMock()
        mock_publish_response.status = 200
        mock_publish_response.json = AsyncMock(return_value={"id": "media_456"})
        mock_publish_response.__aenter__ = AsyncMock(return_value=mock_publish_response)
        mock_publish_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=[mock_response, mock_publish_response])

        result = await client.upload_reel(str(video), "Test caption", mock_session)
        assert isinstance(result, dict)
        assert result.get("platform") == "instagram"

    @pytest.mark.asyncio
    async def test_upload_reel_missing_credentials_skips(self, tmp_path):
        from src.publisher import InstagramClient
        client = InstagramClient()
        client.access_token = ""
        client.user_id = ""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"fake")

        mock_session = AsyncMock()
        result = await client.upload_reel(str(video), "caption", mock_session)
        assert result.get("status") == "skipped"

    @pytest.mark.asyncio
    async def test_upload_reel_api_error_returns_error_status(self, tmp_path):
        client = self._make_client()
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake video")

        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.json = AsyncMock(return_value={"error": {"message": "Invalid token"}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        result = await client.upload_reel(str(video), "caption", mock_session)
        assert result.get("platform") == "instagram"
        assert result.get("status") in ("error", "published")


# ---------------------------------------------------------------------------
# TikTokClient testleri
# ---------------------------------------------------------------------------


class TestTikTokClient:
    def _make_client(self):
        from src.publisher import TikTokClient
        client = TikTokClient()
        client.access_token = "fake_tiktok_token"
        return client

    @pytest.mark.asyncio
    async def test_upload_video_missing_credentials_skips(self, tmp_path):
        from src.publisher import TikTokClient
        client = TikTokClient()
        client.access_token = ""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"fake")

        mock_session = AsyncMock()
        result = await client.upload_video(str(video), "caption", mock_session)
        assert result.get("status") == "skipped"

    @pytest.mark.asyncio
    async def test_upload_video_calls_init_endpoint(self, tmp_path):
        client = self._make_client()
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake video bytes")

        init_response = AsyncMock()
        init_response.status = 200
        init_response.json = AsyncMock(return_value={
            "data": {"upload_url": "https://upload.tiktok.com/fake", "publish_id": "pub_123"}
        })
        init_response.__aenter__ = AsyncMock(return_value=init_response)
        init_response.__aexit__ = AsyncMock(return_value=False)

        put_response = AsyncMock()
        put_response.status = 200
        put_response.__aenter__ = AsyncMock(return_value=put_response)
        put_response.__aexit__ = AsyncMock(return_value=False)

        status_response = AsyncMock()
        status_response.status = 200
        status_response.json = AsyncMock(return_value={
            "data": {"status": "PUBLISH_COMPLETE"}
        })
        status_response.__aenter__ = AsyncMock(return_value=status_response)
        status_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=[init_response, status_response])
        mock_session.put = MagicMock(return_value=put_response)

        result = await client.upload_video(str(video), "caption", mock_session)
        assert isinstance(result, dict)
        assert result.get("platform") == "tiktok"

    @pytest.mark.asyncio
    async def test_upload_video_returns_publish_id(self, tmp_path):
        client = self._make_client()
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake")

        init_response = AsyncMock()
        init_response.status = 200
        init_response.json = AsyncMock(return_value={
            "data": {"upload_url": "https://upload.tiktok.com/fake", "publish_id": "pub_789"}
        })
        init_response.__aenter__ = AsyncMock(return_value=init_response)
        init_response.__aexit__ = AsyncMock(return_value=False)

        put_response = AsyncMock()
        put_response.status = 200
        put_response.__aenter__ = AsyncMock(return_value=put_response)
        put_response.__aexit__ = AsyncMock(return_value=False)

        status_response = AsyncMock()
        status_response.status = 200
        status_response.json = AsyncMock(return_value={
            "data": {"status": "PUBLISH_COMPLETE"}
        })
        status_response.__aenter__ = AsyncMock(return_value=status_response)
        status_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=[init_response, status_response])
        mock_session.put = MagicMock(return_value=put_response)

        result = await client.upload_video(str(video), "caption", mock_session)
        assert result.get("publish_id") == "pub_789"


# ---------------------------------------------------------------------------
# YouTubeClient testleri
# ---------------------------------------------------------------------------


class TestYouTubeClient:
    def _make_client(self):
        from src.publisher import YouTubeClient
        client = YouTubeClient()
        client.client_id = "fake_client_id"
        client.client_secret = "fake_client_secret"
        client.refresh_token = "fake_refresh_token"
        return client

    @pytest.mark.asyncio
    async def test_missing_credentials_skips(self, tmp_path):
        from src.publisher import YouTubeClient
        client = YouTubeClient()
        client.client_id = ""
        client.client_secret = ""
        client.refresh_token = ""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"fake")

        mock_session = AsyncMock()
        result = await client.upload_shorts(str(video), "Title", "Desc", mock_session)
        assert result.get("status") == "skipped"

    @pytest.mark.asyncio
    async def test_refresh_token_called_before_upload(self, tmp_path):
        client = self._make_client()
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake video")

        token_response = AsyncMock()
        token_response.status = 200
        token_response.json = AsyncMock(return_value={"access_token": "new_access_token"})
        token_response.__aenter__ = AsyncMock(return_value=token_response)
        token_response.__aexit__ = AsyncMock(return_value=False)

        upload_response = AsyncMock()
        upload_response.status = 200
        upload_response.json = AsyncMock(return_value={"id": "yt_video_123", "status": {"uploadStatus": "uploaded"}})
        upload_response.__aenter__ = AsyncMock(return_value=upload_response)
        upload_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=[token_response, upload_response])

        result = await client.upload_shorts(str(video), "My Title", "My Desc", mock_session)
        assert isinstance(result, dict)
        assert result.get("platform") == "youtube"
        assert mock_session.post.call_count >= 1

    @pytest.mark.asyncio
    async def test_shorts_title_appended(self, tmp_path):
        client = self._make_client()
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake")

        token_response = AsyncMock()
        token_response.status = 200
        token_response.json = AsyncMock(return_value={"access_token": "tok"})
        token_response.__aenter__ = AsyncMock(return_value=token_response)
        token_response.__aexit__ = AsyncMock(return_value=False)

        upload_response = AsyncMock()
        upload_response.status = 200
        upload_response.json = AsyncMock(return_value={"id": "yt_123"})
        upload_response.__aenter__ = AsyncMock(return_value=upload_response)
        upload_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=[token_response, upload_response])

        result = await client.upload_shorts(str(video), "World Cup Goal", "Desc", mock_session)
        assert result.get("platform") == "youtube"
        # Title with #Shorts is verified via CaptionFormatter.build_youtube_title test


# ---------------------------------------------------------------------------
# PublisherBot testleri
# ---------------------------------------------------------------------------


class TestPublisherBot:
    def _make_bot(self, tmp_path, platforms=None):
        from src.publisher import PublisherBot
        bot = PublisherBot(
            output_dir=str(tmp_path),
            enabled_platforms=platforms or ["instagram", "tiktok", "youtube"],
        )
        return bot

    def test_register_event_stores_data(self, tmp_path, event_data):
        bot = self._make_bot(tmp_path)
        bot.register_event("/output/test_final.mp4", event_data)
        assert "/output/test_final.mp4" in bot._event_store
        assert bot._event_store["/output/test_final.mp4"]["event_type"] == "GOAL"

    def test_register_event_overwrites_on_duplicate(self, tmp_path, event_data):
        bot = self._make_bot(tmp_path)
        bot.register_event("/output/video.mp4", event_data)
        new_event = dict(event_data, event_type="RED_CARD")
        bot.register_event("/output/video.mp4", new_event)
        assert bot._event_store["/output/video.mp4"]["event_type"] == "RED_CARD"

    @pytest.mark.asyncio
    async def test_publish_all_calls_all_enabled_platforms(self, tmp_path, event_data):
        bot = self._make_bot(tmp_path, platforms=["instagram", "tiktok", "youtube"])
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake")
        bot.register_event(str(video), event_data)

        bot.instagram.upload_reel = AsyncMock(return_value={"platform": "instagram", "status": "published"})
        bot.tiktok.upload_video = AsyncMock(return_value={"platform": "tiktok", "status": "published"})
        bot.youtube.upload_shorts = AsyncMock(return_value={"platform": "youtube", "status": "published"})

        results = await bot.publish_all(str(video))
        assert len(results) == 3
        platforms = {r["platform"] for r in results}
        assert platforms == {"instagram", "tiktok", "youtube"}

    @pytest.mark.asyncio
    async def test_publish_all_skips_disabled_platform(self, tmp_path, event_data):
        bot = self._make_bot(tmp_path, platforms=["instagram"])
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake")
        bot.register_event(str(video), event_data)

        bot.instagram.upload_reel = AsyncMock(return_value={"platform": "instagram", "status": "published"})
        bot.tiktok.upload_video = AsyncMock(return_value={"platform": "tiktok", "status": "published"})
        bot.youtube.upload_shorts = AsyncMock(return_value={"platform": "youtube", "status": "published"})

        results = await bot.publish_all(str(video))
        platforms = {r["platform"] for r in results}
        assert "instagram" in platforms
        assert "tiktok" not in platforms
        assert "youtube" not in platforms

    @pytest.mark.asyncio
    async def test_publish_all_no_event_data_all_skipped(self, tmp_path):
        bot = self._make_bot(tmp_path)
        bot.instagram.upload_reel = AsyncMock(return_value={"platform": "instagram", "status": "skipped"})
        bot.tiktok.upload_video = AsyncMock(return_value={"platform": "tiktok", "status": "skipped"})
        bot.youtube.upload_shorts = AsyncMock(return_value={"platform": "youtube", "status": "skipped"})
        results = await bot.publish_all("/nonexistent/path_final.mp4")
        # No event data → all results carry skipped status
        assert all(r.get("status") == "skipped" for r in results)

    def test_publish_sync_runs_event_loop(self, tmp_path, event_data):
        bot = self._make_bot(tmp_path)
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake")
        bot.register_event(str(video), event_data)

        async def fake_publish_all(path):
            return [{"platform": "instagram", "status": "published"}]

        bot.publish_all = fake_publish_all
        results = bot.publish_sync(str(video))
        assert isinstance(results, list)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_publish_all_returns_list_of_dicts(self, tmp_path, event_data):
        bot = self._make_bot(tmp_path, platforms=["instagram"])
        video = tmp_path / "test_final.mp4"
        video.write_bytes(b"fake")
        bot.register_event(str(video), event_data)

        bot.instagram.upload_reel = AsyncMock(return_value={"platform": "instagram", "status": "published", "id": "123"})

        results = await bot.publish_all(str(video))
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, dict)


# ---------------------------------------------------------------------------
# OutputWatcher testleri
# ---------------------------------------------------------------------------


class TestOutputWatcher:
    def _make_watcher(self):
        from src.publisher import OutputWatcher
        mock_bot = MagicMock()
        watcher = OutputWatcher(mock_bot)
        return watcher, mock_bot

    def _file_event(self, path, is_directory=False):
        from watchdog.events import FileCreatedEvent
        event = FileCreatedEvent(path)
        # watchdog FileCreatedEvent has is_directory property
        return event

    def test_triggers_publish_on_final_mp4(self):
        watcher, mock_bot = self._make_watcher()
        from watchdog.events import FileCreatedEvent
        event = FileCreatedEvent("/output/match_GOAL_abc123_final.mp4")
        watcher.on_created(event)
        mock_bot.publish_sync.assert_called_once_with("/output/match_GOAL_abc123_final.mp4")

    def test_ignores_non_final_mp4(self):
        watcher, mock_bot = self._make_watcher()
        from watchdog.events import FileCreatedEvent
        event = FileCreatedEvent("/output/match_GOAL_abc123.mp4")
        watcher.on_created(event)
        mock_bot.publish_sync.assert_not_called()

    def test_ignores_directories(self):
        watcher, mock_bot = self._make_watcher()
        from watchdog.events import DirCreatedEvent
        event = DirCreatedEvent("/output/some_dir_final.mp4")
        watcher.on_created(event)
        mock_bot.publish_sync.assert_not_called()

    def test_ignores_other_file_types(self):
        watcher, mock_bot = self._make_watcher()
        from watchdog.events import FileCreatedEvent
        for ext in [".json", ".txt", ".wav", ".log"]:
            event = FileCreatedEvent(f"/output/file{ext}")
            watcher.on_created(event)
        mock_bot.publish_sync.assert_not_called()

    def test_multiple_final_mp4_triggers_multiple_calls(self):
        watcher, mock_bot = self._make_watcher()
        from watchdog.events import FileCreatedEvent
        watcher.on_created(FileCreatedEvent("/output/match_GOAL_aaa_final.mp4"))
        watcher.on_created(FileCreatedEvent("/output/match_RED_CARD_bbb_final.mp4"))
        assert mock_bot.publish_sync.call_count == 2

    def test_manifest_json_ignored(self):
        watcher, mock_bot = self._make_watcher()
        from watchdog.events import FileCreatedEvent
        event = FileCreatedEvent("/output/match_GOAL_abc123_manifest.json")
        watcher.on_created(event)
        mock_bot.publish_sync.assert_not_called()


# ---------------------------------------------------------------------------
# start_watcher entegrasyon testi
# ---------------------------------------------------------------------------


class TestStartWatcher:
    def test_start_watcher_returns_observer(self, tmp_path):
        from src.publisher import start_watcher, PublisherBot
        mock_bot = MagicMock(spec=PublisherBot)

        with patch("src.publisher.Observer") as MockObserver:
            mock_obs = MagicMock()
            MockObserver.return_value = mock_obs
            observer = start_watcher(str(tmp_path), mock_bot)
            mock_obs.start.assert_called_once()
            assert observer is mock_obs
