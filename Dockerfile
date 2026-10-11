# Production image for the KLEVS FX signals app (FastAPI + Uvicorn).
#
# Build:  docker build -t klevs-fx:latest .
# Run:    see docs/CLOUDFLARE_DEPLOYMENT.md (the container is reached through a
#         Cloudflare Tunnel, so it is published only on 127.0.0.1 or not at all).
#
# Build arg INSTALL_LIVE=0 skips yfinance (live market data). The app then runs
# on the synthetic market only, which is what KFX_LIVE_DATA=0 selects anyway.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

ARG INSTALL_LIVE=1

# Dependency layer first so code edits do not reinvalidate the pip install.
COPY requirements.txt requirements-live.txt ./
RUN pip install -r requirements.txt "psycopg[binary]>=3.1" \
    && if [ "$INSTALL_LIVE" = "1" ]; then pip install -r requirements-live.txt; fi

COPY fxbot ./fxbot
COPY static ./static

# Non-root user. data/ holds the SQLite file when KLEVS_DATABASE_URL is unset,
# and models/ holds pickled per-pair models. Both are regenerable, but they must
# be writable. Mount a volume on them to keep them across container recreation.
RUN useradd --system --uid 10001 --create-home klevs \
    && mkdir -p /app/data /app/models \
    && chown -R klevs:klevs /app/data /app/models
USER klevs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# One worker on purpose: the market engine and its cache live in process memory,
# so extra workers would each hold a full copy and see different caches.
CMD ["uvicorn", "fxbot.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
