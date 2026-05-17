"""
Maç API Dinleyicisi

İki çalışma modu:
  - webhook: Flask HTTP sunucusu, POST /event endpoint'i üzerinden event alır.
  - poll: Belirli aralıklarla bir REST endpoint'i sorgular.

Gerçek API entegrasyonu için SPORTS_API_URL ve SPORTS_API_KEY env değişkenlerini ayarlayın.
"""

import hashlib
import hmac
import json
import logging
import os
import time
import threading
from typing import Callable

import requests

logger = logging.getLogger(__name__)


class MatchAPIListener:
    """
    Maç eventi kaynaklarını dinleyen ve render callback'ini tetikleyen sınıf.

    Args:
        on_event: Yeni bir MatchEvent dict'i geldiğinde çağrılacak fonksiyon.
        webhook_secret: HMAC-SHA256 doğrulaması için gizli anahtar (opsiyonel).
    """

    def __init__(
        self,
        on_event: Callable[[dict], None],
        webhook_secret: str = "",
    ):
        self.on_event = on_event
        self.webhook_secret = webhook_secret
        self._seen_event_ids: set[str] = set()
        self._polling = False
        self._poll_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Webhook modu
    # ------------------------------------------------------------------

    def create_flask_app(self):
        """
        Flask uygulaması oluşturur.

        Endpoint'ler:
            GET  /health  — 200 OK
            GET  /jobs    — Son işlerin listesi (ileride genişletilebilir)
            POST /event   — Maç eventi (JSON body)
        """
        try:
            from flask import Flask, request, jsonify
        except ImportError:
            raise ImportError("flask yüklü değil. 'pip install flask' çalıştırın.")

        app = Flask(__name__)

        @app.get("/health")
        def health():
            return jsonify({"status": "ok"})

        @app.post("/event")
        def receive_event():
            raw = request.get_data()

            if self.webhook_secret:
                sig_header = request.headers.get("X-Hub-Signature-256", "")
                if not self._verify_signature(raw, sig_header, self.webhook_secret):
                    return jsonify({"error": "Invalid signature"}), 401

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return jsonify({"error": "Invalid JSON"}), 400

            event_id = data.get("event_id") or data.get("match_id", "")
            if event_id and event_id in self._seen_event_ids:
                return jsonify({"status": "duplicate", "event_id": event_id}), 200

            if event_id:
                self._seen_event_ids.add(event_id)

            try:
                self.on_event(data)
            except Exception as exc:
                logger.exception("on_event handler failed: %s", exc)
                return jsonify({"error": str(exc)}), 500

            return jsonify({"status": "accepted", "event_id": event_id}), 202

        return app

    def start_webhook_server(self, host: str = "0.0.0.0", port: int = 5000) -> None:
        """Flask webhook sunucusunu başlatır (bloklayıcı)."""
        app = self.create_flask_app()
        logger.info("Webhook server starting on %s:%d", host, port)
        app.run(host=host, port=port)

    # ------------------------------------------------------------------
    # Polling modu
    # ------------------------------------------------------------------

    def start_polling(
        self,
        api_url: str,
        interval_seconds: int = 30,
        api_key: str = "",
    ) -> None:
        """
        Arka planda REST endpoint'i sorgulayan thread'i başlatır.

        Args:
            api_url: JSON eventi dönen endpoint URL'i.
            interval_seconds: Sorgulama aralığı (saniye).
            api_key: Authorization: Bearer token (opsiyonel).
        """
        if self._polling:
            logger.warning("Polling already running.")
            return

        self._polling = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            args=(api_url, interval_seconds, api_key),
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("Polling started: %s (every %ds)", api_url, interval_seconds)

    def stop_polling(self) -> None:
        self._polling = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5.0)
        logger.info("Polling stopped.")

    def _poll_loop(self, api_url: str, interval: int, api_key: str) -> None:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        while self._polling:
            try:
                resp = requests.get(api_url, headers=headers, timeout=10)
                resp.raise_for_status()
                data = resp.json()

                events = data if isinstance(data, list) else [data]
                for ev in events:
                    ev_id = ev.get("event_id") or ev.get("match_id", "")
                    if ev_id and ev_id in self._seen_event_ids:
                        continue
                    if ev_id:
                        self._seen_event_ids.add(ev_id)
                    try:
                        self.on_event(ev)
                    except Exception as exc:
                        logger.exception("on_event handler failed: %s", exc)

            except requests.RequestException as exc:
                logger.warning("API poll failed: %s", exc)

            time.sleep(interval)

    # ------------------------------------------------------------------
    # Mock event (test / geliştirme)
    # ------------------------------------------------------------------

    def emit_mock_event(self, event_type: str = "GOAL") -> None:
        """
        Geliştirme sırasında gerçek API olmadan test eventi üretir.
        """
        from datetime import datetime
        mock = {
            "match_id": "WC2026_TUR_BRA_001",
            "event_type": event_type,
            "minute": 73,
            "team": "Türkiye",
            "player_name": "Arda Güler",
            "score_home": 2,
            "score_away": 1,
            "team_home": "Türkiye",
            "team_away": "Brezilya",
            "timestamp": datetime.utcnow().isoformat(),
        }
        logger.info("Emitting mock event: %s", event_type)
        self.on_event(mock)

    # ------------------------------------------------------------------
    # HMAC doğrulaması
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_signature(payload: bytes, header: str, secret: str) -> bool:
        """
        X-Hub-Signature-256: sha256=<hex> biçimindeki başlığı doğrular.
        timing-safe karşılaştırma için hmac.compare_digest kullanır.
        """
        if not header.startswith("sha256="):
            return False
        expected = hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        received = header[len("sha256="):]
        return hmac.compare_digest(expected, received)
