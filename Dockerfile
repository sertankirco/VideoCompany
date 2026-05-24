# ─────────────────────────────────────────────────────────────────────────────
# Growlabs 2026 — Docker Image
#
# Not: Adobe After Effects aerender binary'si kapalı kaynaklıdır ve bu
# image içinde yer almaz. Host'taki aerender yolunu aşağıdaki gibi
# bind-mount ile ekleyin:
#   -v /usr/local/bin/aerender:/usr/local/bin/aerender:ro
#
# FFmpeg ve tüm Python bağımlılıkları dahildir.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# FFmpeg kur
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bağımlılıkları önce kopyala — layer cache için
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Uygulama katmanı
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS app

COPY src/       ./src/
COPY scripts/   ./scripts/
COPY .env.example .env.example

# Çıktı ve asset klasörlerini oluştur
RUN mkdir -p output assets templates

# Varsayılan: CLI yardım mesajını göster
ENTRYPOINT ["python", "-m", "src"]
CMD ["status"]
