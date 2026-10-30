# syntax=docker/dockerfile:1
#
# One image, two entrypoints (API and worker) — `ARCHITECTURE.md §1`. The worker runs
# the same code so a job can never drift from the service it calls.

FROM python:3.11-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app

# --- dependency layer ---------------------------------------------------------
FROM base AS deps
COPY pyproject.toml README.md ./
COPY src/bm/__init__.py src/bm/__init__.py
RUN uv pip install --system --no-cache .

# --- runtime ------------------------------------------------------------------
FROM base AS runtime

COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY pyproject.toml alembic.ini README.md ./
COPY migrations ./migrations
COPY src ./src

ENV PYTHONPATH=/app/src

# Non-root. The application never needs to write to its own image; uploads go to the
# configured storage path, which is mounted.
RUN groupadd --system --gid 10001 bm \
    && useradd --system --uid 10001 --gid bm --no-create-home bm \
    && mkdir -p /var/lib/bm/storage \
    && chown -R bm:bm /var/lib/bm
USER bm:bm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Migrations are NOT run here (ADR-016). They are an explicit deploy step using
# BM_MIGRATION_DATABASE_URL, which never exists in this container.
CMD ["uvicorn", "bm.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
