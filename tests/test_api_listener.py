"""Tests for MatchAPIListener — webhook server, polling, HMAC, mock events."""

import hashlib
import hmac
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.api_listener import MatchAPIListener


# ---------------------------------------------------------------------------
# _verify_signature
# ---------------------------------------------------------------------------


class TestVerifySignature:
    def _sign(self, payload: bytes, secret: str) -> str:
        digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def test_valid_signature_returns_true(self):
        payload = b'{"event_type": "GOAL"}'
        secret = "mysecret"
        header = self._sign(payload, secret)
        assert MatchAPIListener._verify_signature(payload, header, secret) is True

    def test_wrong_secret_returns_false(self):
        payload = b'{"event_type": "GOAL"}'
        header = self._sign(payload, "correct")
        assert MatchAPIListener._verify_signature(payload, header, "wrong") is False

    def test_tampered_payload_returns_false(self):
        secret = "mysecret"
        header = self._sign(b"original", secret)
        assert MatchAPIListener._verify_signature(b"tampered", header, secret) is False

    def test_missing_sha256_prefix_returns_false(self):
        assert MatchAPIListener._verify_signature(b"data", "invalid", "secret") is False

    def test_empty_header_returns_false(self):
        assert MatchAPIListener._verify_signature(b"data", "", "secret") is False


# ---------------------------------------------------------------------------
# emit_mock_event
# ---------------------------------------------------------------------------


class TestEmitMockEvent:
    def test_emit_calls_on_event(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)
        listener.emit_mock_event("GOAL")
        handler.assert_called_once()
        payload = handler.call_args[0][0]
        assert payload["event_type"] == "GOAL"
        assert payload["match_id"] == "WC2026_TUR_BRA_001"

    def test_emit_default_event_type_is_goal(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)
        listener.emit_mock_event()
        payload = handler.call_args[0][0]
        assert payload["event_type"] == "GOAL"

    def test_emit_yellow_card(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)
        listener.emit_mock_event("YELLOW_CARD")
        payload = handler.call_args[0][0]
        assert payload["event_type"] == "YELLOW_CARD"

    def test_emit_includes_timestamp(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)
        listener.emit_mock_event("GOAL")
        payload = handler.call_args[0][0]
        assert "timestamp" in payload
        assert payload["timestamp"]  # non-empty


# ---------------------------------------------------------------------------
# POST /event endpoint (webhook receive)
# ---------------------------------------------------------------------------


class TestReceiveEvent:
    def _make_client(self, listener):
        app = listener.create_flask_app()
        app.testing = True
        return app.test_client()

    def test_valid_event_accepted(self):
        handler = MagicMock()
        client = self._make_client(MatchAPIListener(on_event=handler))
        r = client.post(
            "/event",
            data=json.dumps({"event_type": "GOAL", "match_id": "M1"}),
            content_type="application/json",
        )
        assert r.status_code == 202
        handler.assert_called_once()

    def test_invalid_json_returns_400(self):
        client = self._make_client(MatchAPIListener(on_event=MagicMock()))
        r = client.post("/event", data="not-json", content_type="application/json")
        assert r.status_code == 400

    def test_duplicate_event_id_returns_200_not_handled(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)
        client = self._make_client(listener)
        payload = json.dumps({"event_id": "EVT1", "event_type": "GOAL"})
        client.post("/event", data=payload, content_type="application/json")
        r = client.post("/event", data=payload, content_type="application/json")
        assert r.status_code == 200
        assert json.loads(r.data)["status"] == "duplicate"
        assert handler.call_count == 1  # only once

    def test_valid_signature_accepted(self):
        secret = "topsecret"
        payload = json.dumps({"event_type": "GOAL"}).encode()
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        handler = MagicMock()
        client = self._make_client(MatchAPIListener(on_event=handler, webhook_secret=secret))
        r = client.post(
            "/event",
            data=payload,
            content_type="application/json",
            headers={"X-Hub-Signature-256": sig},
        )
        assert r.status_code == 202

    def test_invalid_signature_returns_401(self):
        client = self._make_client(
            MatchAPIListener(on_event=MagicMock(), webhook_secret="secret")
        )
        r = client.post(
            "/event",
            data=json.dumps({"event_type": "GOAL"}),
            content_type="application/json",
            headers={"X-Hub-Signature-256": "sha256=badhash"},
        )
        assert r.status_code == 401

    def test_handler_exception_returns_500(self):
        def bad_handler(_):
            raise RuntimeError("boom")

        client = self._make_client(MatchAPIListener(on_event=bad_handler))
        r = client.post(
            "/event",
            data=json.dumps({"event_type": "GOAL"}),
            content_type="application/json",
        )
        assert r.status_code == 500

    def test_event_id_added_to_seen(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)
        client = self._make_client(listener)
        client.post(
            "/event",
            data=json.dumps({"event_id": "X42", "event_type": "GOAL"}),
            content_type="application/json",
        )
        assert "X42" in listener._seen_event_ids

    def test_match_id_used_as_fallback_dedup_key(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)
        client = self._make_client(listener)
        payload = json.dumps({"match_id": "MID1", "event_type": "GOAL"})
        client.post("/event", data=payload, content_type="application/json")
        client.post("/event", data=payload, content_type="application/json")
        assert handler.call_count == 1


# ---------------------------------------------------------------------------
# start_webhook_server
# ---------------------------------------------------------------------------


class TestStartWebhookServer:
    def test_start_webhook_server_calls_app_run(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)
        mock_app = MagicMock()

        with patch.object(listener, "create_flask_app", return_value=mock_app):
            listener.start_webhook_server(host="127.0.0.1", port=9999)

        mock_app.run.assert_called_once_with(host="127.0.0.1", port=9999)

    def test_start_webhook_server_default_params(self):
        listener = MatchAPIListener(on_event=MagicMock())
        mock_app = MagicMock()

        with patch.object(listener, "create_flask_app", return_value=mock_app):
            listener.start_webhook_server()

        mock_app.run.assert_called_once_with(host="0.0.0.0", port=5000)

    def test_start_webhook_server_creates_flask_app(self):
        listener = MatchAPIListener(on_event=MagicMock())
        mock_app = MagicMock()

        with patch.object(listener, "create_flask_app", return_value=mock_app) as mock_create:
            listener.start_webhook_server()

        mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# start_polling / stop_polling
# ---------------------------------------------------------------------------


class TestStartPolling:
    def test_start_polling_spawns_daemon_thread(self):
        listener = MatchAPIListener(on_event=MagicMock())

        with patch.object(listener, "_poll_loop") as mock_loop:
            listener.start_polling("http://fake/api", interval_seconds=1)
            time.sleep(0.05)
            listener.stop_polling()

        assert mock_loop.called or listener._poll_thread is not None

    def test_start_polling_sets_polling_flag(self):
        listener = MatchAPIListener(on_event=MagicMock())

        with patch.object(listener, "_poll_loop", return_value=None):
            listener.start_polling("http://fake/api", interval_seconds=60)
            assert listener._polling is True
            listener.stop_polling()

    def test_start_polling_twice_does_not_spawn_second_thread(self):
        listener = MatchAPIListener(on_event=MagicMock())

        with patch("threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            listener._polling = True  # simulate already running
            listener.start_polling("http://fake/api")

        mock_thread_cls.assert_not_called()

    def test_stop_polling_clears_flag(self):
        listener = MatchAPIListener(on_event=MagicMock())

        with patch.object(listener, "_poll_loop", return_value=None):
            listener.start_polling("http://fake/api", interval_seconds=60)
            listener.stop_polling()

        assert listener._polling is False

    def test_stop_polling_joins_thread(self):
        listener = MatchAPIListener(on_event=MagicMock())
        mock_thread = MagicMock()
        listener._poll_thread = mock_thread
        listener._polling = True

        listener.stop_polling()

        mock_thread.join.assert_called_once_with(timeout=5.0)


# ---------------------------------------------------------------------------
# _poll_loop behaviour
# ---------------------------------------------------------------------------


class TestPollLoop:
    def test_poll_loop_calls_on_event_for_each_item(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)

        events = [{"event_id": "E1", "event_type": "GOAL"}, {"event_id": "E2", "event_type": "YELLOW_CARD"}]
        mock_resp = MagicMock()
        mock_resp.json.return_value = events

        call_count = 0

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                listener._polling = False
            return mock_resp

        with patch("src.api_listener.requests.get", side_effect=fake_get):
            with patch("time.sleep"):
                listener._polling = True
                listener._poll_loop("http://fake/api", 1, "")

        assert handler.call_count == 2

    def test_poll_loop_deduplicates_events(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)

        event = {"event_id": "DUP1", "event_type": "GOAL"}
        call_count = 0

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                listener._polling = False
            mock_resp = MagicMock()
            mock_resp.json.return_value = [event]
            return mock_resp

        with patch("src.api_listener.requests.get", side_effect=fake_get):
            with patch("time.sleep"):
                listener._polling = True
                listener._poll_loop("http://fake/api", 1, "")

        assert handler.call_count == 1  # second poll skipped as duplicate

    def test_poll_loop_sends_bearer_token(self):
        listener = MatchAPIListener(on_event=MagicMock())
        listener._polling = True

        captured_headers = {}

        def fake_get(url, headers=None, timeout=None):
            captured_headers.update(headers or {})
            listener._polling = False
            mock_resp = MagicMock()
            mock_resp.json.return_value = []
            return mock_resp

        with patch("src.api_listener.requests.get", side_effect=fake_get):
            with patch("time.sleep"):
                listener._poll_loop("http://fake/api", 1, "mytoken")

        assert captured_headers.get("Authorization") == "Bearer mytoken"

    def test_poll_loop_handles_request_exception(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)

        call_count = 0

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                listener._polling = False
            raise requests.RequestException("connection refused")

        with patch("src.api_listener.requests.get", side_effect=fake_get):
            with patch("time.sleep"):
                listener._polling = True
                listener._poll_loop("http://fake/api", 1, "")

        handler.assert_not_called()

    def test_poll_loop_single_event_dict_wrapped_in_list(self):
        handler = MagicMock()
        listener = MatchAPIListener(on_event=handler)

        call_count = 0

        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                listener._polling = False
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"event_id": "S1", "event_type": "GOAL"}
            return mock_resp

        with patch("src.api_listener.requests.get", side_effect=fake_get):
            with patch("time.sleep"):
                listener._polling = True
                listener._poll_loop("http://fake/api", 1, "")

        assert handler.call_count == 1
