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
import queue as _queue
import time
import threading
from pathlib import Path
from typing import Callable

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level SSE state (shared across all Flask requests)
# ---------------------------------------------------------------------------

_sse_lock = threading.Lock()
_sse_subscribers: list[_queue.Queue] = []

_DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"


def push_sse_event(payload: dict) -> None:
    """Push a JSON payload to all connected SSE dashboard clients."""
    with _sse_lock:
        dead = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(payload)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            _sse_subscribers.remove(q)


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

    def create_flask_app(self, output_dir: str = "output/"):
        """
        Tam özellikli Flask uygulaması oluşturur (webhook + dashboard).
        Sadece dashboard gerektiğinde modül-seviye create_dashboard_app() kullanın.

        Endpoint'ler:
            GET  /          — Canlı dashboard (dashboard.html)
            GET  /health    — 200 OK
            GET  /stream    — SSE canlı event akışı
            GET  /api/stats — Upload istatistikleri (JSON)
            POST /event     — Maç eventi (JSON body, opsiyonel HMAC imzalı)
        """
        try:
            from flask import Flask, request, jsonify, Response, stream_with_context, send_file
        except ImportError:
            raise ImportError("flask yüklü değil. 'pip install flask' çalıştırın.")

        app = Flask(__name__)
        _output_dir = output_dir

        @app.get("/")
        def dashboard():
            return send_file(str(_DASHBOARD_HTML))

        @app.get("/health")
        def health():
            return jsonify({"status": "ok"})

        @app.get("/stream")
        def sse_stream():
            def generate():
                q: _queue.Queue = _queue.Queue(maxsize=50)
                with _sse_lock:
                    _sse_subscribers.append(q)
                try:
                    yield ":ok\n\n"
                    while True:
                        try:
                            payload = q.get(timeout=15)
                            yield f"data: {json.dumps(payload)}\n\n"
                        except _queue.Empty:
                            yield ": keepalive\n\n"
                finally:
                    with _sse_lock:
                        try:
                            _sse_subscribers.remove(q)
                        except ValueError:
                            pass

            resp = Response(
                stream_with_context(generate()),
                content_type="text/event-stream",
            )
            resp.headers["Cache-Control"] = "no-cache"
            resp.headers["X-Accel-Buffering"] = "no"
            return resp

        @app.get("/api/stats")
        def api_stats():
            log_path = Path(_output_dir) / "upload_log.json"
            entries: list[dict] = []
            if log_path.exists():
                try:
                    with open(log_path, encoding="utf-8") as f:
                        entries = json.load(f)
                except (OSError, json.JSONDecodeError):
                    entries = []

            by_platform: dict[str, dict] = {
                "instagram": {"success": 0, "error": 0, "skipped": 0},
                "tiktok":    {"success": 0, "error": 0, "skipped": 0},
                "youtube":   {"success": 0, "error": 0, "skipped": 0},
            }
            for entry in entries:
                for r in entry.get("results", []):
                    p = r.get("platform", "")
                    s = r.get("status", "")
                    if p in by_platform:
                        if s in ("error", "exception"):
                            by_platform[p]["error"] += 1
                        elif s in ("skipped", "dry_run"):
                            by_platform[p]["skipped"] += 1
                        else:
                            by_platform[p]["success"] += 1

            recent_events = [
                e["event_data"] for e in entries[-10:] if "event_data" in e
            ]
            last_event = recent_events[-1] if recent_events else None

            return jsonify({
                "total_uploads": len(entries),
                "by_platform": by_platform,
                "last_event": last_event,
                "recent_events": recent_events,
            })

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


# ---------------------------------------------------------------------------
# Standalone dashboard factory (no webhook, just read-only dashboard)
# ---------------------------------------------------------------------------


def create_dashboard_app(output_dir: str = "output/"):
    """
    Sadece dashboard route'larını içeren bağımsız Flask uygulaması oluşturur.
    Webhook veya polling işlevselliği içermez.
    """
    listener = MatchAPIListener(on_event=lambda _: None)
    return listener.create_flask_app(output_dir=output_dir)
