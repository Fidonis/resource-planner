# syntax=docker/dockerfile:1
# Build from the repository root:  docker build -f docker/api.Dockerfile .

# ---- builder: install the locked dependencies and the API package into a venv ----
FROM python:3.12-slim AS builder

# Pinned uv release so resolution is reproducible across rebuilds.
COPY --from=ghcr.io/astral-sh/uv:0.9.24 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# The workspace needs every member's manifest to resolve the lock file. Only the manifests are
# copied here, so this layer is reused until a dependency changes.
COPY pyproject.toml uv.lock ./
COPY src/api/pyproject.toml src/api/pyproject.toml
COPY src/ui/pyproject.toml src/ui/pyproject.toml
COPY src/contracts/pyproject.toml src/contracts/pyproject.toml

RUN uv sync --frozen --no-dev --no-install-workspace --package planner-api

COPY src/api/planner_api src/api/planner_api
RUN uv sync --frozen --no-dev --no-editable --package planner-api

# ---- runtime ----
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /data/imports \
    && chown -R app:app /app /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["planner-api", "healthcheck"]

# The proxy and the UI sit in front of the API; trust their forwarded client address.
CMD ["planner-api", "serve", "--forwarded-allow-ips", "*"]
