FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /srv/app

# The thin Oracle driver uses the system trust store for TCPS connections.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /srv/app --shell /usr/sbin/nologin app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app app/ ./app/
COPY --chown=app:app scripts/ ./scripts/
COPY --chown=app:app sql/ ./sql/
COPY --chown=app:app examples/ ./examples/

USER app
EXPOSE 8000

# /health returns HTTP 200 while degraded, so inspect its JSON status too.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c 'import json, os, sys, urllib.request; url = "http://127.0.0.1:" + os.environ.get("PORT", "8000") + "/health"; body = json.load(urllib.request.urlopen(url, timeout=8)); sys.exit(0 if body.get("status") == "ok" and body.get("database") == "connected" else 1)'

CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
