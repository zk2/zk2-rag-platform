# syntax=docker/dockerfile:1.7
# ─── Stage 1: builder ───────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /usr/local/bin/uv

WORKDIR /app

# The cross-encoder used for reranking. Baked in rather than downloaded on
# first use: a model that arrives minutes after the first question is a feature
# that silently is not there when someone tries it.
ARG RERANK_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
ENV HF_HUB_CACHE=/opt/hf \
    RERANK_MODEL_ID=${RERANK_MODEL}

# Cache deps layer
COPY apps/api/pyproject.toml apps/api/uv.lock* /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra rerank || \
    uv sync --no-install-project --no-dev --extra rerank

# App source. README.md comes along because pyproject declares it as the
# package readme, and hatchling refuses to build the wheel without it.
COPY apps/api/README.md /app/README.md
COPY apps/api/src /app/src
COPY apps/api/alembic /app/alembic
COPY apps/api/alembic.ini /app/alembic.ini
COPY apps/api/templates /app/templates
COPY apps/api/scripts /app/scripts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --extra rerank

RUN /app/.venv/bin/python -c "\
from sentence_transformers import CrossEncoder; \
import os; CrossEncoder(os.environ['RERANK_MODEL_ID'])" \
    && chmod -R a+rX /opt/hf
# ─── Stage 2: runtime ───────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    HF_HUB_CACHE=/opt/hf

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

WORKDIR /app

# Ownership is set by the COPY, not by a chown afterwards: `chown -R` rewrites
# every file it touches into a new layer, which with torch and a model in the
# image meant carrying two gigabytes twice.
COPY --from=builder --chown=app:app /app /app
COPY --from=builder --chown=app:app /opt/hf /opt/hf
# Mount points for named volumes shared with the worker. Creating them here
# means Docker seeds the volume with an app-owned directory - created on the
# fly they would belong to root, and the unprivileged process could not write
# an upload into them. /opt/hf is seeded with the baked model for the same
# reason, and stays writable so a different RERANK_MODEL can be fetched.
RUN mkdir -p /app/uploads && chown app:app /app/uploads

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "zk2.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
