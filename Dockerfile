# Production image for Certificate Review.
#   docker build -t certapp .
#   docker run -p 8000:8000 --env-file .env certapp
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CERTAPP_ENV=production \
    CERTAPP_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY certapp ./certapp
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 1000 certapp \
    && mkdir -p /data && chown certapp /data

USER certapp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

# One worker: certificate reading runs in the app process, and sign-in lockouts are kept in memory.
CMD ["uvicorn", "certapp.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]
