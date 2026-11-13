# syntax=docker/dockerfile:1
# Build from the repository root:  docker build -f docker/ui.Dockerfile .

# ---- assets: compile the stylesheet and copy the vendored browser files (Node only at build time) ----
FROM node:25-slim AS assets

WORKDIR /build
COPY src/ui/package.json src/ui/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY src/ui/scripts ./scripts
COPY src/ui/tailwind.config.js src/ui/tailwind.brand.css src/ui/tailwind.app.css ./
# The templates and the page script are scanned for class names.
COPY src/ui/planner_ui/templates ./planner_ui/templates
COPY src/ui/planner_ui/static/js ./planner_ui/static/js
RUN npm run build

# ---- builder: install the locked dependencies and the packages into a venv ----
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.24 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

COPY pyproject.toml uv.lock ./
COPY src/api/pyproject.toml src/api/pyproject.toml
COPY src/ui/pyproject.toml src/ui/pyproject.toml
COPY src/contracts/pyproject.toml src/contracts/pyproject.toml

RUN uv sync --frozen --no-dev --no-install-workspace --package planner-ui

COPY src/contracts/planner_client src/contracts/planner_client
COPY src/ui/planner_ui src/ui/planner_ui
COPY --from=assets /build/planner_ui/static src/ui/planner_ui/static
COPY --from=assets /build/planner_ui/icons src/ui/planner_ui/icons
RUN uv sync --frozen --no-dev --no-editable --package planner-ui

# ---- runtime ----
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app \
    && chown -R app:app /app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"]

# The proxy in front sets the client address; trust it.
CMD ["uvicorn", "planner_ui.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
