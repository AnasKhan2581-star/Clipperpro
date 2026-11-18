FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/cache/huggingface

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-dejavu-core ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Bold "Shorts-style" caption font (SIL Open Font License)
RUN mkdir -p /usr/share/fonts/truetype/anton \
 && curl -fsSL --max-time 60 --retry 2 -o /usr/share/fonts/truetype/anton/Anton-Regular.ttf \
      https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf \
 || echo "Anton font download failed; captions fall back to DejaVu Sans Bold" \
 ; fc-cache -f

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Optional: bake a Whisper model into the image so the first job starts instantly
ARG WHISPER_PRELOAD=""
RUN if [ -n "$WHISPER_PRELOAD" ]; then python -c "from faster_whisper import WhisperModel; WhisperModel('$WHISPER_PRELOAD', device='cpu', compute_type='int8')"; fi

COPY clipper ./clipper
COPY web ./web
COPY config ./config

EXPOSE 8000 8090
VOLUME ["/app/data", "/cache"]
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8000/api/health || exit 1
ENTRYPOINT ["python", "-m", "clipper"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
