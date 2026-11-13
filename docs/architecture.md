# Architecture

## Components

```
 browser ──► reverse proxy (yours; one address)
               ├── /api/…, /openapi.json, /health/… ──► api   (FastAPI, /api/v1)  ──► db (PostgreSQL)
               └── everything else ───────────────────► ui    (FastAPI, Jinja2, HTMX)
                                                         │
                                                         └── calls /api/v1 with the user's own
                                                             session cookie and CSRF token
```

The reverse proxy is not part of the Compose file (see *Deployment*); `docker/nginx.conf` shows the
routing it needs.

| Component | Package | Responsibility |
|---|---|---|
| API | `src/api/planner_api` | All rules and all data access. The only place that decides what a user may do. |
| Web interface | `src/ui/planner_ui` | Renders pages and HTML fragments from what the API returns. No database, no business rule; it never recomputes a figure. |
| Contract | `src/contracts` | `openapi.json` (a snapshot checked for drift) and `planner_client`, the typed Python client generated from it. |

The interface and the client never import `planner_api` (checked by `lint-imports`). A later MCP
server would be a third component and a pure client of the API as well
([mcp-preparation.md](mcp-preparation.md)).

## The API in layers

```
routers  →  services  →  domain
                │
                └──►  repositories  →  models (SQLAlchemy)
```

- **routers** are thin HTTP adapters: parse the request, call one service, shape the response.
  Each route declares the permission it needs (`x-permission` in the contract); a test fails when a
  route has neither a permission nor an entry on the list of public routes.
- **services** are the use cases. They receive the authenticated principal and check its
  permissions themselves, so the command line (`seed-demo`) and the sync jobs go through the same
  code as HTTP does.
- **domain** is pure: the rules of section 4 of the specification (capacity, booked and free
  days, half days, the proposed amount) as functions on value objects, with `Decimal` and no I/O.
- **repositories** are the only code that talks to the database. The three big read models
  (`/planning`, `/overview`, `/availability`) are a few SQLAlchemy Core queries and the capacity
  maths in Python, so a board for 500 members and 2000 tasks stays well below a second.
- **providers** and **sync** (see below) sit beside the services.

Import rules are enforced by `lint-imports`: the layers only point downwards, and core code never
names a concrete provider.

### Data and concurrency

- PostgreSQL 16, SQLAlchemy 2 (async, psycopg 3) with relationships set to `lazy="raise"`, Alembic
  migrations written by hand. A test applies all migrations to an empty database and fails if the
  result differs from the models.
- Built-in roles, absence types and settings are reconciled at start, not by migrations.
- Every mutable resource has a `version`. A change is a single `UPDATE … WHERE version = :v`. The
  caller gives the version in `If-Match` (`412` when it is stale, `428` when it is missing) or in
  the body (`409`). Changing efforts or skills of a task also moves the version of the task.
- An assignment for a task, skill, member and week that exists already is merged into it in one
  transaction (`mergedInto`, `deletedId` in the answer).
- `?dryRun=true` runs the real service path inside a transaction and rolls it back; permission and
  `If-Match` are checked first.
- Errors are `application/problem+json` (RFC 9457) with a stable `code`.

## Sign-in and permissions

- **Local accounts**: argon2id passwords, a lock after repeated failures plus a rate limit on the
  sign-in, a forced password change for the first administrator.
- **Sessions**: a row in the database, an `HttpOnly`, `SameSite=Lax` cookie (`Secure` when
  `PUBLIC_BASE_URL` is `https://`), an absolute lifetime (`SESSION_TTL_HOURS`) and a CSRF token
  that must come back in `X-CSRF-Token` on every unsafe request.
- **OpenID Connect** (authorisation code with PKCE) is handled by the API, which then sets the
  same session cookie; the interface never sees an OIDC token. The state, nonce and verifier of a
  sign-in live in a table bound to the browser by a cookie; only relative `return_to` addresses are
  accepted. Roles from the identity provider are stored at sign-in and resolved through the role
  mapping on every request.
- **API tokens** (`rpt_…`, hashed) and **bearer tokens** of the identity provider for programs.
- **Authorisation**: roles hold permissions (22 of them, listed at `GET /roles/permissions`);
  a role can be limited to teams. A principal's rights are either "all" or a set of team ids, and
  moving an assignment checks the source and the target team.

## The web interface

- Server-rendered Jinja2 with HTMX for partial updates. A `401` from the API becomes a redirect to
  the sign-in page; `409`, `412` and `422` answers are swapped in so that conflicts and field errors
  show as toasts or next to the field.
- A change on the board is a request to the interface, which calls the API and answers with the
  board drawn again from the API's numbers.
- Alpine.js (CSP build) holds small local state through `Alpine.data`; Sortable provides the drag
  and drop. All browser files are served by the interface itself; there is no CDN and no Node at
  run time.
- **Content security policy**: scripts, style sheets, fonts, images and connections only from the
  interface's own origin (no inline script, no `eval`, no CDN); `frame-ancestors 'none'`,
  `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, and
  `Strict-Transport-Security` when `PUBLIC_BASE_URL` is `https://`. Only inline style *attributes*
  are allowed, for the team colours. Pages are sent with `Cache-Control: no-store`.
- **Style**: Tailwind CSS and daisyUI, built into `static/app.css` when the image is built; the
  brand layer carries the stamp `fidonis-brand: 2`. Fonts (IBM Plex Sans, Manrope) and the icon
  subset (Tabler) are included as files.
- **Languages**: English and German from `.po` catalogues read at start. The language is a setting
  of the account, otherwise the browser's. A test fails when a text or an error code has no
  translation.

## Integrations

```
 source (file, Keycloak, …) ──► provider ──► sync engine ──► service layer ──► database
                                    ▲              │
                         settings + mapping        └──► run log (counts, per-record notes)
                         of the integration
```

- A **provider** implements one of three protocols (task source, directory, absence source), is
  found through the entry-point group `resource_planner.providers`, and describes its settings with
  a Pydantic model. The settings form of the interface is drawn from that schema, so a new provider
  needs no interface code. See [providers.md](providers.md).
- The **sync engine** reads a source, hands records to the applier, and writes a run with counts and
  a log. It takes an advisory lock so that one integration runs once at a time, can run as a dry
  run (everything is done, then rolled back), closes what has disappeared only after a complete
  read, and acts as the system principal `sync:<name>` in the audit log.
- The **scheduler** (APScheduler, in the API process) starts runs by each integration's cron
  expression.
- The **ingest API** (`POST /ingest/tasks|members|absences`) is the push alternative: a program
  with an API token that has `ingest:write` sends records, and they go through the same applier.

## Deployment

`docker-compose.yml` starts `db`, `api` (runs migrations first) and `ui`. The interface and the API
publish a port each, on the loopback address. For anything but a local trial, put a reverse proxy
that terminates TLS in front of them and serves one address: `/api/`, `/openapi.json` and `/health/`
to the API, everything else to the interface (single sign-on and API clients need that common
address; local sign-in works with the interface alone). The identity provider is yours as well.
`docker/docker-compose.dev.yml` adds an nginx proxy and a throw-away Keycloak for development and
for the browser tests. The API image runs one worker. The images do not contain Node or the test
tools.
