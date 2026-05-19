# Growlabs 2026 — Claude Code Kılavuzu

## Proje Tanımı

2026 Dünya Kupası maç olaylarını otomatik olarak 1080×1920 @ 60fps dikey kısa videoya
dönüştürüp Instagram Reels, TikTok ve YouTube Shorts'a yayınlayan tam otomasyon sistemi.

**Repo:** `sertankirco/VideoCompany`
**Dil:** Python 3.11
**Branch kuralı:** Her yeni faz için `claude/growlabs-video-engine-IVu8c` üzerinde geliştir, PR ile main'e squash merge yap.

---

## Kurgu Anayasası (Asla İhlal Edilmez)

| Kural | Değer |
|-------|-------|
| Segment süresi | 18–24 frame @ 60fps (0.30–0.40 s) |
| SFX senkronu | Waveform peak = görsel kesim frame'i (ses asla geç gelemez) |
| Motion Blur | Shutter Angle 360°, JSX ile AEP'e yazılır |
| Çıktı | 1080×1920, 60fps, MP4 |
| Hook metni | Maksimum 3 satır |

---

## Modüller

| Dosya | Sorumluluk |
|-------|------------|
| `src/render_engine.py` | Ana orkestratör — pipeline'ı yönetir |
| `src/ae_bridge.py` | After Effects JSX üretimi + aerender CLI |
| `src/sfx_sync.py` | librosa ile SFX peak frame tespiti |
| `src/sound_designer.py` | FFmpeg adelay+amix ile frame-accurate SFX miksajı |
| `src/data_simulator.py` | WC2026 maç simülatörü + Türkçe hook text üreteci |
| `src/api_listener.py` | Webhook (Flask) + polling ile canlı maç eventi alma |
| `src/publisher.py` | Instagram/TikTok/YouTube async upload + watchdog |

---

## Geliştirme Ortamı

```bash
pip install -r requirements.txt
cp .env.example .env   # token'ları doldur
pytest tests/ -v       # 111 test — hepsi geçmeli
```

---

## Test Kuralları

```python
# Mock hedefi: her zaman kullanan modülün namespace'i
@patch("src.render_engine.execute_jsx")   # ✅
@patch("src.ae_bridge.execute_jsx")       # ❌ — render_engine'i etkilemez

# Türkçe string: lower() kullan
assert "türkiye" in hook.lower()   # ✅
assert "TÜRKİYE" in hook.upper()   # ❌ — Python ASCII upper() üretir

# Async mock: MagicMock(side_effect=[...]) — async def kullanma
MagicMock(side_effect=[resp1, resp2])   # ✅
async def fake_post(...): ...           # ❌ — context manager desteklemiyor
```

---

## Ortam Değişkenleri

`.env.example` dosyasına bak. Tüm token'lar `.env`'den yüklenir, kodda hardcode yok.
`enable_publisher=False` varsayılanı — token olmadan sistem güvenle çalışır.

---

## Commit Formatı

```
feat: kısa İngilizce açıklama
fix: hata düzeltmesi
refactor: yeniden yapılandırma
test: sadece test değişikliği
```

Her push öncesi `pytest tests/ -v` geçmeli.

---

## Sonraki Fazlar (Backlog)

| # | Görev | Öncelik |
|---|-------|---------|
| 5c | Upload retry (exponential backoff) | Orta |
| 5d | Upload log — SQLite/JSON | Düşük |
| 5e | CLI entry point `python -m growlabs` | Düşük |
| 5f | Docker container | Düşük |
