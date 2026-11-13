# Resource Planner — project context

This document provides structural and architectural context for contributors and automated tooling
working in this repository. It summarises; the documents it links to are authoritative.

---

## What this project does

Resource Planner is a browser-based resource planning tool for agile teams. It answers three
questions: are there enough people with the right skills for the work estimated for the next weeks,
who works on which task in which week, and where are the over-bookings and skill bottlenecks.

| Document | Content |
|---|---|
| [`README.md`](README.md) | Features, running it, configuration, development setup |
| [`ressource-planner.md`](ressource-planner.md) | The specification, with requirement IDs (`FR-PLAN-04`, `NFR-API-09`, …) |
| [`docs/implementation-notes.md`](docs/implementation-notes.md) | Decisions, deviations from the specification, what is not done |
| [`docs/architecture.md`](docs/architecture.md) | Components, layers, data flow, security |
| [`docs/providers.md`](docs/providers.md) | Integrations and writing a provider |
| [`docs/mcp-preparation.md`](docs/mcp-preparation.md) | How the API is prepared for a later MCP server |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Workflow, checks, what a change needs |
| [`SECURITY.md`](SECURITY.md) | Reporting vulnerabilities, security design |

---

## Repository layout

```
src/api/        planner_api – the HTTP API (FastAPI): routers, services, domain, repositories,
                auth, providers, sync, models, schemas, migrations (Alembic), cli
src/ui/         planner_ui – the web interface (FastAPI, Jinja2, HTMX, Alpine.js): routes,
                templates, static files, message catalogues; Tailwind and daisyUI sources and the
                npm build in package.json
src/contracts/  openapi.json (the contract snapshot) and planner_client, generated from it
tests/          unit, api (against a real PostgreSQL), ui (the interface in front of the real API),
                e2e (browser, against a running stack)
docker/         api.Dockerfile, ui.Dockerfile, nginx.conf, the development compose file and the
                development Keycloak realm
docs/           documentation
scripts/        init_env.py, generate_client.py, i18n.py, check_wording.py
```

The repository root is a uv workspace with the members `src/api`, `src/ui` and `src/contracts`;
`uv.lock` at the root is the one lock file. Python 3.12 is the minimum.

---

## Architecture rules

- **One enforcement point.** The API holds all rules and all data access and decides what a user
  may do. Every route declares the permission it needs.
- **The interface is a client.** `planner_ui` calls the API over HTTP with the signed-in user's
  session cookie and CSRF token, has no database access and never recomputes a figure the API
  returns. Neither `planner_ui` nor `planner_client` imports `planner_api`.
- **Layers point downwards.** `routers → services → domain`; database access only in
  `repositories`. Core code does not import a specific provider. `lint-imports` enforces these
  contracts (`[tool.importlinter]` in `pyproject.toml`).
- **The contract is versioned.** A change to an endpoint, a model or a description refreshes
  `src/contracts/openapi.json` and the generated client; every operation keeps a stable
  `operationId`. CI fails when the snapshot or the client drift from the code.
- **Texts are translated.** Every text of the web interface goes through `_()` and has a German
  translation in `src/ui/planner_ui/locale/de/LC_MESSAGES/messages.po`.
- **Accessibility.** Information is never conveyed by colour alone; dialogs and the board work with
  the keyboard.
- **Browser files are vendored** from npm packages at build time, never loaded from a CDN.

---

## Engineering conventions

### Branches

| Branch | Use |
|---|---|
| `main` | Integration branch; changes only through pull requests |
| `releases/<x.y.z>` | Milestone branch of a planned release; kept after the release |
| `feat/<short>` | New user-facing feature |
| `fix/<short>` | Bug fix |
| `docs/<short>` | Documentation only |
| `refactor/<short>` | Refactoring without behaviour change |
| `test/<short>` | Test additions or fixes |
| `ci/<short>` | CI/CD configuration |
| `chore/<short>` | Maintenance |

Never push directly to `main` or a `releases/*` branch, never force-push them, never delete them.
Rulesets enforce this, together with a code-owner review and green status checks.

### PR titles — Conventional Commits

Format: `<type>[(<scope>)][!]: <subject>`

- Types: `feat`, `fix`, `docs`, `refactor`, `perf`, `style`, `test`, `ci`, `build`, `chore`,
  `revert`
- Subject: lowercase, imperative mood, no trailing period
- `!` suffix marks a breaking change (triggers a major version bump)
- CI enforces this format on every PR; release-drafter labels and groups PRs by their title

### Merge strategy

All PRs are **squash-merged** into their target branch (`main` or the milestone branch). The PR
title becomes the single commit message. The one exception is the release cut: the milestone
branch is merged into `main` with a **merge commit**, so the squashed commits of its PRs stay
visible on `main`.

### Releases

1. In a PR into the milestone branch, bump the version by hand (`pyproject.toml` of the root and of
   the three members, `src/api/planner_api/__init__.py`, `src/ui/package.json`), refresh the
   OpenAPI snapshot and the client (the API version is part of the contract) and write the
   `CHANGELOG.md` entry.
2. Merge `releases/x.y.z` into `main` with a merge commit; the PR body lists every issue of the
   milestone as `Closes #N`, because only PRs into `main` close issues.
3. Publish the release `vX.Y.Z` on `main`. Compare it with the release-drafter draft before
   publishing. Publishing builds, pushes and signs `ghcr.io/fidonis/resource-planner-api` and
   `ghcr.io/fidonis/resource-planner-ui` (`.github/workflows/docker.yml`).

---

## Code style and local checks

Run all checks locally before pushing, from the repository root, as CI does:

```bash
uv sync
(cd src/ui && npm ci && npm run build)   # style sheet and vendored browser files, for the tests

uvx yamllint .
uv run ruff check . && uv run ruff format --check .
uv run mypy src/api/planner_api src/ui/planner_ui src/contracts/planner_client tests
uv run lint-imports
uv run python scripts/generate_client.py --check
uv run python scripts/i18n.py missing
uv run python scripts/check_wording.py   # needs WORDING_RULES or WORDING_RULES_FILE
uv run pytest -q                         # starts PostgreSQL in a container
```

- **Python linting**: ruff with rule sets E, F, I, B, UP, N, RET, SIM, ASYNC, line length 100,
  and `ruff format`
- **Type checking**: mypy in strict mode with the pydantic plugin
- **YAML**: yamllint with the project `.yamllint` config
- **Line endings**: LF (`.gitattributes`); indentation as in `.editorconfig`
- **Tests**: a behaviour change comes with a test on the level where it lives; a bug fix starts
  with a failing test. `TEST_DATABASE_URL` points the API tests at an existing database, which
  they empty.

| CI workflow | Runs on | Jobs |
|---|---|---|
| `ci.yml` | push to `main`, every PR | Yamllint, Ruff, Mypy, Contracts and wording, Pytest, Compose stack starts and answers, Conventional Commit PR title |
| `license-check.yml` | push to `main`, PRs into `main` | Rejects copyleft dependencies; refreshes `THIRD_PARTY_LICENSES.md` |
| `release-drafter.yml` | push to `main`, PRs | Maintains the draft release and labels PRs |
| `label-issues-on-milestone-merge.yml` | PR merged into `releases/*` | Labels linked issues `merged-to-milestone` |
| `docker.yml` | release published | Builds, pushes and signs the images |

---

## Configuration and security

All settings are environment variables, listed with comments in [`.env.example`](.env.example);
`scripts/init_env.py` writes a `.env` with generated secrets. `.env` is never committed, and
`.env.example` holds `CHANGE_ME` placeholders only. Secrets stored by integrations are encrypted
with `APP_ENCRYPTION_KEY`. The security design (sessions, CSRF, tokens, content security policy,
deployment behind a TLS proxy) is described in [`SECURITY.md`](SECURITY.md).
