# Resource Planner

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
Maintained by **Fidonis** · See [TRADEMARK.md](TRADEMARK.md) for the trademark notice.

A browser-based resource planning tool for agile teams. It answers three questions: do we have
enough people with the right skills for the work estimated for the next weeks, who works on which
task in which week, and where are the over-bookings and skill bottlenecks.

- **Planning board** – tasks with an effort per skill on the left, members and weeks as a grid;
  drag a skill onto a week to book it, or use the *Assign…* dialog. Capacity follows part-time
  shares and absences (including half days); full and over-booked weeks are marked with text and
  an icon, not only with colour.
- **Overview** – demand, planned work and utilisation per skill and team.
- **Master data** – teams, skills, members, absence types, absences with a calendar.
- **Tasks** – entered by hand, imported from CSV, or synchronised from a source.
- **Sign-in** – local accounts or OpenID Connect (Keycloak is the reference), roles with
  permissions, roles limited to teams, API tokens, audit log.
- **Integrations** – a provider framework with a sync engine, a run log, a machine-to-machine
  ingest API, CSV providers for tasks, members and absences, and a Keycloak directory import.
  Jira and SAP providers are not part of this version.
- **English and German**, light and dark theme.

The specification is [ressource-planner.md](ressource-planner.md); what the implementation does
differently is in [docs/implementation-notes.md](docs/implementation-notes.md).

## Run it

You need Docker with Compose.

```bash
python scripts/init_env.py        # writes .env with generated passwords (once)
docker compose up -d --build
```

Open <http://localhost:8080> for the web interface; the API answers on <http://localhost:8081>.
The first administrator is `BOOTSTRAP_ADMIN_USER` from `.env` (`admin` by default) with
`BOOTSTRAP_ADMIN_PASSWORD`; a new password is demanded at the first sign-in. Both ports are
published on the loopback address only.

The Compose file starts the database, the API and the web interface, and nothing else: **bring
your own reverse proxy and identity provider.** For anything beyond a local try-out, put a proxy
that terminates TLS in front, serving one address for both services: `/api/`, `/openapi.json` and
`/health/` go to the API (`http://api:8000` on the compose network, or the published API port),
everything else to the interface (`http://ui:8000`, or the published interface port).
[docker/nginx.conf](docker/nginx.conf) shows the routing. Set `PUBLIC_BASE_URL` to that public
address (an `https://` address switches the session cookies to `Secure`). Local sign-in works
with the interface alone; single sign-on, API clients and the contract at `/openapi.json` need the
common address.

To look around with example data, set `ENABLE_DEMO_SEED=true` in `.env`, restart, and run:

```bash
docker compose exec api planner-api seed-demo
```

### Trying it with a proxy and single sign-on

`docker/docker-compose.dev.yml` adds a ready nginx proxy (one address, <http://localhost:8080>) and,
with the profile `dev-keycloak`, a throw-away Keycloak at <http://localhost:8180> with the realm
`planner` and the demo accounts `admin-demo`, `planner-demo` and `viewer-demo`:

```bash
docker compose -f docker-compose.yml -f docker/docker-compose.dev.yml --profile dev-keycloak up -d --build
```

The settings that connect the planner to that Keycloak are listed in [.env.example](.env.example).
It is a development aid: development mode, no TLS.

### Services

| Service | Image | Role |
|---|---|---|
| `ui` | built from `docker/ui.Dockerfile` | The web interface (FastAPI, Jinja2, HTMX) |
| `api` | built from `docker/api.Dockerfile` | The HTTP API under `/api/v1`, migrations at start, the scheduler |
| `db` | postgres:16 | The database |
| `proxy` | nginx (dev file only) | One address for everything, as a real proxy in front would give |
| `keycloak` | Keycloak (dev file, profile `dev-keycloak`) | Development identity provider |

The interface talks to the API only over HTTP, with the credentials of the signed-in user. The API
is the one place that decides what a user may do.

## Configuration

Everything is set through environment variables, listed with comments in
[.env.example](.env.example). Nothing secret is stored in the repository. Secrets that integrations
store (a Keycloak client secret, for example) are encrypted with `APP_ENCRYPTION_KEY`.

## Documentation

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Components, layers, data flow, security |
| [docs/providers.md](docs/providers.md) | Integrations: the CSV formats, the Keycloak import, writing your own provider |
| [docs/mcp-preparation.md](docs/mcp-preparation.md) | How the API is prepared for a later MCP server |
| [docs/implementation-notes.md](docs/implementation-notes.md) | Decisions, deviations, what is not done |
| `GET /openapi.json` | The API contract; the snapshot is `src/contracts/openapi.json` |

## Develop

You need Python 3.12 or newer with [uv](https://docs.astral.sh/uv/), Node.js 20 or newer (for
the style sheet), and Docker (for a database in the tests).

```bash
uv sync
(cd src/ui && npm ci && npm run build)   # style sheet and the vendored browser files

uv run ruff check . && uv run ruff format --check .
uv run mypy src/api/planner_api src/ui/planner_ui src/contracts/planner_client tests
uv run lint-imports
uv run pytest                            # unit, API and interface tests
```

The API tests start a PostgreSQL container. To use a database you already run, set
`TEST_DATABASE_URL` (for example `postgresql+psycopg://planner:dev@127.0.0.1:55432/planner`). The
tests empty that database, so never point it at data you want to keep.

The browser tests run against a running stack and need an account that may change master data:

```bash
uv run playwright install chromium       # or set E2E_BROWSER=msedge / chrome
docker compose -f docker-compose.yml -f docker/docker-compose.dev.yml up -d --build
E2E_USER=admin E2E_PASSWORD=... uv run pytest tests/e2e
```

When the API changes, refresh the contract and the client:

```bash
uv run planner-api export-openapi src/contracts/openapi.json
uv run python scripts/generate_client.py
```

CI fails when the snapshot or the generated client differ from the code. Other helpers:
`scripts/i18n.py` (message catalogues), `scripts/check_wording.py`.

### Repository layout

```
src/api/        planner_api – routers, services, domain, repositories, auth, providers, sync, cli
src/ui/         planner_ui – templates, static files, message catalogues, style sheet sources
src/contracts/  openapi.json and the generated Python client (planner_client)
tests/          unit, api, ui (the interface in front of the real API), e2e (browser)
docker/         Dockerfiles, nginx.conf, the development compose file and Keycloak realm
docs/           documentation
scripts/        init_env.py, generate_client.py, i18n.py, check_wording.py
```

## Contributing, security, license

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). The code is licensed under
the [MIT License](LICENSE). The name "Fidonis" and its logo are not covered by it; see
[TRADEMARK.md](TRADEMARK.md). Third-party components are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); the licenses of the Python packages in the
images are in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md), generated by CI.
