# Production image for the KLEVS FX signals app (FastAPI + Uvicorn).
#
# Deployed as a Cloudflare Container by edge/ (see docs/CLOUDFLARE_DEPLOYMENT.md).
# Browsers never reach this image directly: the Worker in edge/ is the only public
# entry point, so the port is not published anywhere outside the Container runtime.
#
# Build (local check only; Docker is not needed for the Cloudflare deploy path, which builds the image):
#   docker build -t klevs-fx:latest .
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
# SQLite is the only database on this path: the container restores it from R2 and replicates it back.
COPY requirements.txt requirements-live.txt ./
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_LIVE" = "1" ]; then pip install -r requirements-live.txt; fi

COPY fxbot ./fxbot
COPY static ./static

# Non-root user. data/ holds the SQLite file (restored from the R2 replica on start when
# it is missing), and models/ holds pickled per-pair models, which are regenerable.
# Both must be writable.
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
