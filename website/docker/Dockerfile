# Build from the website/ folder:   docker build -f docker/Dockerfile -t itad-platform .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production \
    ITAD_DATABASE=/data/itad.sqlite3 ITAD_UPLOAD_DIR=/data/uploads

# Font with wide character coverage for PDFs
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY frontend frontend
COPY database database
COPY integrations integrations

RUN useradd --create-home --uid 10001 itad && mkdir -p /data && chown -R itad /data
USER itad
VOLUME ["/data"]
EXPOSE 8000
WORKDIR /app/backend

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

# One worker count is fine for a single-site SQLite setup; the login rate limiter is per process.
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "wsgi:app"]
