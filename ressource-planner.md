# Resource Planner — Requirements & Feature Specification

Status: Draft v2, implemented except P2b, P2d (SAP) and P2e · Language: English · License: MIT

This document specifies an open-source, browser-based **resource planning tool** for agile teams. It is written so that it can be implemented step by step, phase by phase (§12). Requirement keywords **MUST**, **SHOULD**, **MAY** follow RFC 2119. Every requirement has an ID (e.g. `FR-TASK-03`) that can be referenced in issues, commits and tests.

The single-file prototype `index.html` in this repository is the **behavioural reference** for the planning UI (board, drag & drop, overview, calendar). Where this document and the prototype differ, this document wins.

The static mock-up `ressource-planner-mockup.html` (to be moved to `/docs/mockup/`) is the **visual reference**: the UI follows the design of the papaia-manager web interface (§2.2, FR-UI-06). The mock-up adds up numbers in the browser only so that its screens behave plausibly; in the product every figure comes from the API.

> **Implementation status.** Phases P1a–P1c, P2a and P2c are built. The Jira provider (P2b), a SAP provider (P2d) and the MCP server (P2e) are not; the API is prepared for the MCP server (`docs/mcp-preparation.md`). Where the implementation differs from this text, or settles a point left open here, `docs/implementation-notes.md` says so. The open decisions of §14 are marked there.

---

## 1. Purpose and context

Teams work with user stories in a project-management (PM) tool (currently Jira). Every 4 weeks they estimate a rough effort per user story. Each story needs certain **skills** (currently Jira labels). Team members have skills and limited availability (part-time, absences).

The tool answers:

1. Do we have enough people with the right skills to deliver the estimated stories in the next 4 weeks?
2. Who works on which story, for which skill, in which week?
3. Where are over-bookings and skill bottlenecks?

### 1.1 Glossary

| Term | Meaning |
|---|---|
| **Member** | A person who can be planned (a resource). Belongs to exactly one team. |
| **User** | A login account of the application. May be linked to a Member (1:0..1). |
| **Team** | A group of members. A member belongs to exactly one team. |
| **Skill** | A capability (e.g. Backend, QA, iOS). Matches a label/tag in the PM tool. |
| **Task** | A unit of work to plan (user story). Created manually or imported from a PM tool. |
| **Effort** | Estimated work of a Task for one Skill, in **person-days (PD)**. |
| **Assignment** | A booking: *PD of Skill S of Task T by Member M in Week W*. |
| **Absence** | A period in which a member (or everybody) is not available. Has a configurable **Absence Type**. |
| **Capacity** | Available PD of a member in a week. |
| **Provider** | A pluggable integration module (task source, directory, absence source). |

### 1.2 Scope

In scope: planning, capacity, absences, master data, authentication/authorization, integrations via provider interface.

Out of scope (non-goals): time tracking / actuals, invoicing, cost planning, sprint execution, story-point velocity, automatic optimisation/solver (the planner is manual with decision support), writing planning results back to the PM tool (MAY be a later extension).

---

## 2. Architecture requirements

| ID | Requirement |
|---|---|
| NFR-ARCH-01 | API and UI **MUST** be separate deployable components in one monorepo; a later MCP server (§10) is a third one. The UI talks to the backend **only** via the public HTTP API: no shared database access and no business logic in the UI. In particular the UI **MUST NOT** recompute any figure of §4. The UI is server-rendered (§2.2), but it renders only what the API returns. |
| NFR-ARCH-02 | The API **MUST** be a REST/JSON API under `/api/v1`, described by an **OpenAPI 3.1** document. The document is generated from the API code, committed as a snapshot (`src/contracts/openapi.json`) and checked for drift in CI; it is the contract for every client. A typed Python client for the UI and the MCP server **SHOULD** be generated from it into `src/contracts`. |
| NFR-ARCH-03 | Persistence **MUST** be a relational database accessed through a migration-managed schema (see §3). The prototype's `localStorage` persistence is dropped. |
| NFR-ARCH-04 | Integrations (task sources, directories, absence sources) **MUST** be implemented behind provider interfaces (see §9). Core domain code **MUST NOT** reference Jira, Keycloak or SAP directly. |
| NFR-ARCH-05 | Authentication/authorization **MUST** be provider based (local and OIDC, see §8). Domain code only sees an authenticated *principal* with a set of *permissions*. |
| NFR-ARCH-06 | All configuration **MUST** be possible via environment variables / config file (12-factor). No secrets in the repository. |
| NFR-ARCH-07 | The system **MUST** run with `docker compose up` (reverse proxy, api, ui, database; optional profiles: `mcp` for the MCP server once it exists, `dev-keycloak` for a development Keycloak). |
| NFR-ARCH-08 | UI and MCP server call the API **with the credentials of the acting user** (session cookie plus CSRF header for the UI; see §10 for the MCP server). They hold no privileged credential of their own, so the API stays the only enforcement point for authorization (FR-AUTHZ-05). |
| NFR-ARCH-09 | The API is layered: **routers** (HTTP adapters, thin) → **services** (use cases; they receive an authenticated principal and check its permissions themselves) → **domain** (pure functions and value objects for §4; no I/O, `Decimal` for person-days) → **repositories** (database access). No business rule lives in a router or in the UI. |
| NFR-ARCH-10 | Every present or future adapter (HTTP API, CLI such as `seed-demo`, sync jobs) goes through the service layer; the UI and the MCP server go through the HTTP API. Nothing but the repositories touches the database. |

### 2.1 Technology stack

The **backend language (Python)** and the **UI stack (the one of papaia-manager)** are decided. Everything else is a recommendation; any other choice is acceptable if the HTTP/OpenAPI contract and the data model in this document are preserved.

| Layer | Choice | Reason |
|---|---|---|
| Database | **PostgreSQL 16+** (recommended) | Mature, open source, strong constraints (exclusion/check constraints, `jsonb` for provider config, `daterange`), first-class in every ecosystem. |
| API | **Python 3.12+ with FastAPI** and Pydantic v2; SQLAlchemy 2 (async, psycopg 3) with Alembic migrations (decided: Python; the libraries are recommended) | Typed models drive validation and the generated OpenAPI 3.1 document; one language for API, UI and MCP server. |
| UI | **Python 3.12+ with FastAPI, Jinja2, HTMX, Alpine.js and Sortable.js; Tailwind CSS with daisyUI** — the stack of papaia-manager (decided) | Same look, tooling and contributor knowledge as papaia-manager; business logic stays in the API (§2.2). |
| MCP server (later) | **FastMCP** (Python) | A thin client of the HTTP API (§10). |
| Auth libs | Authlib, or `httpx` + PyJWT (OIDC, JWT, JWKS); `argon2-cffi` (local passwords) | Standard, audited libraries. |
| Scheduling | APScheduler (cron per integration); PostgreSQL advisory lock for the one-run-at-a-time rule | P2 |
| Tooling | `uv` (workspace, one lockfile), `ruff`, `mypy --strict` | Same toolchain as the other Python projects around papaia-manager. |
| Tests | pytest + pytest-asyncio (unit), Testcontainers for Python (API + PostgreSQL), Playwright for Python (UI e2e) | |
| Packaging | Docker images for api, ui and (later) mcp, Compose file, optional Helm chart (later) | |

Proposed repository layout:

```
/src/api             backend: HTTP API, services, domain, providers (Python, FastAPI)
/src/ui              frontend: server-rendered web app (Python, Jinja2 + HTMX), Tailwind/daisyUI build
/src/mcp             MCP server, added in P2e (Python, FastMCP); a client of the HTTP API
/src/contracts       OpenAPI snapshot + generated typed Python client, used by ui and mcp
/tests               api, ui, mcp and end-to-end tests (sibling of /src)
/docker              Dockerfiles, reverse-proxy example config, development compose file, Keycloak realm export (dev); docker-compose.yml and the example env file are in the root
/docs                architecture, provider development guide, mock-up (/docs/mockup)
ressource-planner.md this document
```

`/src` is a `uv` workspace: every component has its own `pyproject.toml`, the lockfile lives at the repository root.

### 2.2 UI architecture

The UI follows `Fidonis/papaia-manager` in technology and in design, so that both tools feel like one product family and share tooling and contributor knowledge.

| ID | Requirement |
|---|---|
| NFR-UI-01 | `src/ui` is a separate web app (FastAPI + Jinja2) that renders HTML pages and HTMX fragments. It has no database access and no business logic; all data comes from `/api/v1` through the generated client in `src/contracts`. |
| NFR-UI-02 | Request path: browser → reverse proxy (one origin) → `ui` for pages and fragments; `/api` → `api` for the machine API and the authentication endpoints (login, OIDC redirect and callback). Because the origin is shared, the UI needs no CORS and the session cookie set by the API reaches both. |
| NFR-UI-03 | For every server-side call the UI forwards the user's session cookie and the CSRF header to the API (NFR-ARCH-08). A `401` from the API redirects to the login page; a `403` disables the control or renders a "not allowed" page. |
| NFR-UI-04 | Browser-side behaviour: HTMX for requests and partial swaps, Alpine.js for local state (filters, dialogs, the "armed" item), Sortable.js for drag & drop. All three are pinned and served from the image: no CDN and no Node at run time. |
| NFR-UI-05 | Styling: Tailwind CSS with daisyUI, built at image-build time. The Tailwind configuration (themes `fidonis-light` and `fidonis-dark`, fonts) and the brand layer are vendored verbatim from papaia-manager together with its `fidonis-brand: <n>` stamp. A brand change is made in the same milestone in every interface that carries the stamp. |
| NFR-UI-06 | Security headers include a CSP (NFR-SEC-01) with a per-request nonce. Where papaia-manager uses inline Alpine expressions, the planner registers components with `Alpine.data` and uses Alpine's CSP-compatible build; templates are therefore adapted, not copied. |
| NFR-UI-07 | A change on the board returns the refreshed fragments (changed cells, pool item, KPI tiles) as out-of-band swaps, rendered from numbers the API computed. On `409`/`412` the UI shows a conflict toast with a reload action (FR-PLAN-10). |

Trade-offs accepted with this choice:

- Sortable.js has no keyboard dragging. FR-PLAN-07 therefore needs a separate keyboard path, covered by an end-to-end test.
- Every drop is a server round trip. The UI shows the new chip dimmed until the API confirms it.
- Large boards (500 members) produce large fragments. The default filter is one team; rows below the fold are loaded on reveal (`hx-trigger="revealed"`).
- The vendored brand files can drift from papaia-manager; the stamp makes drift visible.
- There are no generated TypeScript types; contract safety comes from the generated Python client and the OpenAPI drift check (NFR-QA-02).

### 2.3 Backend structure and MCP readiness

The MCP server (§10) is planned for later, but the API is built for it from the start:

- **Layering** (NFR-ARCH-09, NFR-ARCH-10): permissions are checked in the service layer, so no adapter can bypass them.
- **Machine-readable API** (NFR-API-09): stable `operationId`s and described fields and errors double as the definition of MCP tools.
- **Dry run** (NFR-API-10): a client can ask what a change would do before it makes it.
- **Availability read model** (`GET /availability`, §7.1): answers "who has free capacity for skill S in week W?" without downloading the whole planning model.
- **Audit** (§3.1): every change records how it reached the API (`via`) and the name of the calling client (`client_name`), so changes made through the MCP server are recognisable.
- **Providers** (§9) are Python packages that register themselves in the entry-point group `resource_planner.providers`; the provider interfaces are `typing.Protocol` classes.

---

## 3. Domain model and persistence

All tables use `uuid` primary keys, `created_at`, `updated_at`, and an integer `version` for optimistic locking. Names below are logical; use snake_case in SQL. All time-related values are **dates** (no time zones); weeks are identified by the **ISO date of their Monday** (`week_start`).

### 3.1 Entities

**team**
`id`, `name` (unique), `color` (hex), `external_source` (nullable), `external_id` (nullable).

**skill**
`id`, `key` (unique, case-insensitive, matches PM-tool label), `name`, `external_source`, `external_id`.

**member**
`id`, `team_id` (FK, NOT NULL → exactly one team), `display_name`, `email` (nullable, unique if set), `allocation_percent` (int 10–100: share of working time available for planned tasks; default 80), `active` (bool), `user_id` (nullable FK → app_user), `external_source`, `external_id`.

**member_skill**
`member_id`, `skill_id` (PK pair). Many-to-many.

**absence_type**
`id`, `key` (unique), `name`, `color`, `reduces_capacity` (bool, default true), `active` (bool), `sort_order`. Seed: *Vacation, Sick, Training, Public holiday*. Types **MUST** be manageable by users with the right permission (CRUD; a type that is in use can only be deactivated, not deleted).

**absence**
`id`, `member_id` (nullable; **NULL = applies to all members**, e.g. company holiday), `absence_type_id`, `start_date`, `end_date` (inclusive, `end >= start`), `start_part` and `end_part` (`FULL` | `AM` | `PM`, default `FULL`; SHOULD be supported — a half day counts 0.5), `note` (nullable), `external_source`, `external_id`.

**task** (user story)
`id`, `key` (human key, e.g. `SHOP-101`; unique per `external_source`, or unique among manual tasks), `title`, `description` (nullable), `owner_team_id` (nullable; informational — a task **MAY** require skills from any team), `status` (free text or enum `open|done|cancelled`; default `open`), `external_source`, `external_id`, `external_url` (nullable), `external_updated_at` (nullable), `locked_fields` (jsonb, see §9).

**task_effort**
`task_id`, `skill_id`, `person_days` (numeric(6,2) > 0). PK `(task_id, skill_id)`. Effort is stored **per skill** (the sum is the task's total effort).

**assignment**
`id`, `task_id`, `skill_id`, `member_id`, `week_start` (Monday), `person_days` (numeric(6,2) > 0).
Unique `(task_id, skill_id, member_id, week_start)` (merge instead of duplicate).

**app_user**
`id`, `username` (unique), `display_name`, `email`, `auth_provider` (`local` | `oidc:<provider-id>`), `password_hash` (nullable, argon2id, only for local), `external_subject` (OIDC `sub`, unique per issuer), `active`, `last_login_at`.

**role / permission model** — see §8: `role(id, key, name, builtin)`, `role_permission(role_id, permission)`, `user_role(user_id, role_id)`, `external_role_mapping(provider_id, external_role, role_id)`.

**integration_config**
`id`, `type` (`task_source` | `directory` | `absence_source`), `provider_key` (e.g. `jira`, `keycloak`, `csv`), `name`, `enabled`, `config` (jsonb, secrets encrypted), `schedule` (cron, nullable), `last_sync_at`, `last_sync_status`.

**sync_run**
`id`, `integration_id`, `started_at`, `finished_at`, `status`, `created_count`, `updated_count`, `skipped_count`, `error_count`, `log` (text/jsonb).

**audit_log**
`id`, `at`, `actor_user_id`, `via` (`session` | `api-token` | `bearer` | `system`), `client_name` (nullable; taken from the `X-Client-Name` request header, e.g. `planner-ui`, `planner-mcp`), `action`, `entity_type`, `entity_id`, `before` (jsonb), `after` (jsonb).

**app_setting**
Key/value (jsonb): `planning_horizon_weeks` (default 4), `default_allocation_percent` (80), `locale`.

### 3.2 Integrity rules

| ID | Rule |
|---|---|
| DM-01 | A member has exactly one team. Moving a member to another team **MUST NOT** delete assignments. |
| DM-02 | An assignment **MUST** reference a skill that the member currently has. If a member loses a skill, existing assignments for it **MUST** be reported as conflicts (warning) and **MUST NOT** be silently deleted (the prototype deleted them; the real system must not). |
| DM-03 | If a task effort for a skill is removed or reduced below the assigned sum, the API **MUST** reject the change with a clear error unless `?force=true`, in which case the excess assignments are removed and the audit log records it. |
| DM-04 | Deleting a team with members, a skill in use, or a member with assignments **MUST** be rejected with `409 Conflict` listing the blockers; deactivation (`active=false`) is the alternative. |
| DM-05 | Over-booking (assigned PD in a member-week > capacity) is **allowed** but **MUST** be flagged in API responses (`overbooked: true`) and in the UI. |
| DM-06 | An assignment's total per `(task, skill)` **MAY** exceed the task effort (flagged as `overplanned`), never silently clipped. |

---

## 4. Calculation rules (normative)

All calculations are done by the API (single source of truth) and exposed through the planning endpoints, so every client shows identical numbers.

1. **Working days of a week**: Monday–Friday (5 days). Configurable work weeks are out of scope for v1.
2. **Absent fraction of a day** for member *m* on day *d*: the maximum over all matching absences (member-specific or `member_id = NULL`) whose type has `reduces_capacity = true`; `FULL` = 1, `AM`/`PM` = 0.5; two half-day absences on the same day (AM + PM) = 1. Absences of types with `reduces_capacity = false` are shown but ignored.
3. **Present days** `presentDays(m, w) = 5 − Σ absentFraction(m, d)` for the five days of week *w*.
4. **Capacity** `capacity(m, w) = presentDays(m, w) × allocation_percent(m) / 100` in PD.
5. **Booked** `booked(m, w) = Σ assignment.person_days` of member *m* in week *w*.
6. **Free** `free(m, w) = capacity − booked` (may be negative = overbooked).
7. **Task skill status**: `open(t, s) = effort(t, s) − Σ assignment.person_days(t, s)`; `planned%` = `min(assigned, effort) / effort`.
8. **Skill pool** over a horizon: `pool(s) = Σ capacity(m, w)` over all active members having skill *s* (any team) and all weeks; `poolFree(s) = pool(s) − Σ booked` of those members (all skills). A multi-skilled member counts in every skill pool (documented in the UI as a hint). Bottleneck status for skill *s*: `BOTTLENECK` if `open(s) > poolFree(s)`, `TIGHT` if `open(s) > 0.8 × poolFree(s)`, else `OK`.
9. **Team utilisation**: `Σ booked / Σ capacity` for members of the team over the horizon.
10. **Default drop amount** (UI hint only, not business rule): when dragging an open effort onto a member-week, propose `min(open, floor2(free))` where `floor2` rounds down to 0.5 PD; if that is ≤ 0, propose `min(open, 1)` and show the over-booking.
11. Rounding: store 2 decimals; display 1 decimal.

---

## 5. Functional requirements

Phase tags: **P1** = first release (manual data entry, local or OIDC login), **P2** = integrations (Jira, Keycloak import, absence source) and the MCP server, **P3** = later.

### 5.1 Planning board (P1)

| ID | Requirement |
|---|---|
| FR-PLAN-01 | The board shows a **planning horizon** of N consecutive weeks (default 4, from `planning_horizon_weeks`), starting at a selectable Monday. Prev/next buttons shift by one week; a "today" button jumps to the current week. |
| FR-PLAN-02 | The board has a **pool panel** listing tasks with their open effort per skill, and a **grid** with members (grouped by team) as rows and weeks as columns. |
| FR-PLAN-03 | Each grid cell shows `booked / capacity PD`, the number of absent days, and its assignments as chips (`TASK-KEY · Skill · PD`). Cells are visually marked as **full** (≥ capacity) and **overbooked** (> capacity); the marking **MUST NOT** rely on colour alone (icon/text as well). |
| FR-PLAN-04 | The user creates an assignment by **dragging a (task, skill) item from the pool onto a member-week cell**. Valid targets (member has the skill) are highlighted while dragging; invalid cells are dimmed and reject the drop. The proposed amount follows rule 10 of §4. Implementation: pool items and cells are Sortable.js lists, a cell accepts an item only if the member has its skill, and the drop is sent to the API, which answers with the refreshed fragments (NFR-UI-07). |
| FR-PLAN-05 | An existing assignment can be **dragged to another cell** (changes member and/or week). The skill constraint (DM-02) applies. |
| FR-PLAN-06 | Clicking an assignment opens a dialog to change its PD or delete it. |
| FR-PLAN-07 | **Accessibility alternative to drag & drop**: every drag action **MUST** also be possible via keyboard and touch (e.g. select an item, then select a target cell; or an "Assign…" dialog with member and week selectors). Keyboard path: focus a pool item, press Enter or Space to *arm* it, move to a highlighted cell, press Enter to assign; Escape disarms. Both paths are covered by end-to-end tests (NFR-QA-01). |
| FR-PLAN-08 | Filters: by team (rows), by skill (pool), "only tasks with open effort" (default on). |
| FR-PLAN-09 | Pool items already fully planned are shown as done (struck through) and not draggable. Over-planned items are flagged. |
| FR-PLAN-10 | All changes are persisted immediately through the API; concurrent edits are detected via `version` (see NFR-API-04) and the UI shows a conflict message with a reload option. |
| FR-PLAN-11 | Users without write permission see the board read-only (no drag, no dialogs). |
| FR-PLAN-12 | The "Assign…" dialog proposes the members that have the skill, ordered by free capacity in the chosen week (data from `GET /availability`, §7.1), and shows an over-booking before the user confirms. |

### 5.2 Overview / reporting (P1)

| ID | Requirement |
|---|---|
| FR-OVR-01 | KPI tiles: total demand (PD), assigned (PD, %), still open (PD), number of overbooked member-weeks. |
| FR-OVR-02 | Table per skill: demand, assigned, open, pool, pool free, status (§4 rule 8), assigned-progress bar. |
| FR-OVR-03 | Table per team: members, capacity, booked, free, utilisation bar. |
| FR-OVR-04 | CSV export of the overview and of all assignments for the visible horizon (P2). |

### 5.3 Teams, skills, members (P1)

| ID | Requirement |
|---|---|
| FR-MD-01 | CRUD for **teams** (name, colour). |
| FR-MD-02 | CRUD for **skills** (key, name). The key is the matching key for PM-tool labels. |
| FR-MD-03 | CRUD for **members**: name, email, team (exactly one), allocation %, skills (multi-select), active flag. |
| FR-MD-04 | Deleting/deactivating follows DM-04. Skill removal from a member follows DM-02. |
| FR-MD-05 | Entities created by an integration carry `external_source`/`external_id`. Fields owned by the external source are **read-only in the UI** (shown with a "managed by <source>" badge); locally managed fields (e.g. skills, allocation %, team assignment of imported members) remain editable. |
| FR-MD-06 | Master data pages are protected by permissions (§8). |

### 5.4 Tasks / user stories (P1 manual, P2 import)

| ID | Requirement |
|---|---|
| FR-TASK-01 | CRUD for tasks: key, title, description, owner team (optional), status, and **effort per skill** (a list of skill + PD; skills may come from any team). |
| FR-TASK-02 | A task list view with filters (team, status, skill, source, free text), total effort, and an "assigned %" bar. |
| FR-TASK-03 | Manual tasks and imported tasks coexist. Imported tasks show a link to the external system (`external_url`). |
| FR-TASK-04 | For imported tasks, the fields controlled by the import (title, status, effort mapping inputs) are read-only unless unlocked (§9.4). |
| FR-TASK-05 | CSV import (`Key;Title;Team;Skill=PD|Skill=PD`) as a generic manual bulk path (P1). Same key updates the task. Unknown skills are reported, not auto-created, unless the user opts in. |
| FR-TASK-06 | Closing a task (`done`/`cancelled`) removes its open effort from the pool but keeps its assignments for history. |

### 5.5 Absences (P1 manual, P2 import)

| ID | Requirement |
|---|---|
| FR-ABS-01 | CRUD for absences: member (or "all members"), absence type, start, end (inclusive), optional half-day parts, note. |
| FR-ABS-02 | CRUD for **absence types** (key, name, colour, `reduces_capacity`, active, sort order). Admin permission required. |
| FR-ABS-03 | A calendar view (members × workdays of the horizon) colour-coded by type with a text/letter fallback, plus a list view with working-day counts. |
| FR-ABS-04 | A user with only `absence:write:own` can manage absences of the member linked to their own account. |
| FR-ABS-05 | Overlapping absences for the same member are allowed; the capacity calculation follows §4 rule 2. |
| FR-ABS-06 | CSV import `Name;From;To;Type` (dates `DD.MM.YYYY` or `YYYY-MM-DD`; name `*` = all) as the generic manual bulk path (P1). A future absence-source provider (e.g. SAP) reuses the same import pipeline (P2). |
| FR-ABS-07 | Public holidays are modelled as absences with `member_id = NULL` (P1). Region-specific holiday calendars per team **MAY** be added (P3). |

### 5.6 Administration (P1)

| ID | Requirement |
|---|---|
| FR-ADM-01 | **User management** (local users): create, deactivate, reset password, assign roles, link to a member. |
| FR-ADM-02 | **Role management**: list built-in roles, create custom roles from a permission list, map external (OIDC) roles to internal roles. |
| FR-ADM-03 | **Integration management** (P2): create/enable/test/sync integrations, view sync runs and errors. |
| FR-ADM-04 | **Settings**: planning horizon, default allocation %, auth modes (local/OIDC), UI default language. |
| FR-ADM-05 | **Audit log** view (read-only) with filters (actor, entity, time). |
| FR-ADM-06 | JSON export/import of the complete planning data (members, teams, skills, tasks, assignments, absences) for backup/migration (P2). |

### 5.7 UI general (P1)

| ID | Requirement |
|---|---|
| FR-UI-01 | Navigation in a sidebar, grouped like the one of papaia-manager: **Planning** and **Overview**; then, under the caption *Data*, **Teams & Skills**, **Tasks** and **Absences**; then, under the caption *Administration*, **Users & Roles**, **Integrations** (P2), **Audit log** and **Settings**. Items are hidden if the user lacks any read permission for them; a caption without a visible item is hidden too. |
| FR-UI-02 | Responsive layout as in papaia-manager: full sidebar on desktop, collapsible to an icon rail (the choice is remembered), icon rail below 1024 px; usable on tablet, phone read-only is acceptable. Light and dark theme follow the OS and can be switched by hand. |
| FR-UI-03 | Internationalisation: UI strings externalised in gettext catalogues (`.po`, read at start-up and formatted with Babel, as in papaia-manager); English and German shipped; the language is a per-account preference. |
| FR-UI-04 | WCAG 2.1 AA as target (keyboard operability, contrast, labels, no colour-only signalling). |
| FR-UI-05 | Dates displayed in the user's locale; week numbers are ISO calendar weeks. |
| FR-UI-06 | **Design**: the UI follows papaia-manager. Application shell with sidebar, sticky page header with at most one filled primary action, and a toast area; the daisyUI themes `fidonis-light` and `fidonis-dark`; Manrope for headings and IBM Plex Sans for text, both self-hosted; cards as raised surfaces (`base-100`) on the page ground (`base-200`), separated by borders instead of shadows; one meaning per colour role (primary = filled action, secondary = navigation and focus, info/success/warning/error = state and nothing else). The mock-up is the visual reference for new screens. |
| FR-UI-07 | **No third-party hosts at run time**: fonts, scripts and styles are served by the application itself, and the UI works without internet access. |

---

## 6. Seed / demo data (P1)

A CLI command (`seed-demo`) or an equivalent API call, both disabled in production by default, **MUST** load demo data equivalent to the prototype: 3 teams, 12 members with 1–2 skills each, 8 skills, 13 tasks (one cross-team task needing five skills), some assignments, absences of several types and one company-wide holiday — all dates relative to the current week. First start with an empty database **MUST** create the built-in roles, the default absence types and (if local auth is on) a bootstrap admin from `BOOTSTRAP_ADMIN_USER` / `BOOTSTRAP_ADMIN_PASSWORD` environment variables (forced password change on first login).

---

## 7. API requirements

| ID | Requirement |
|---|---|
| NFR-API-01 | Base path `/api/v1`, JSON, UTF-8. Resource names plural kebab-case. |
| NFR-API-02 | Standard list parameters: `page`, `pageSize` (max 200), `sort`, and resource-specific filters. Responses: `{ "items": [...], "total": n }`. |
| NFR-API-03 | Errors use **RFC 9457** `application/problem+json` with a stable machine-readable `code` (e.g. `ASSIGNMENT_SKILL_MISMATCH`, `DELETE_BLOCKED`) and field-level `errors[]` for validation. |
| NFR-API-04 | Optimistic concurrency: every mutable resource returns `version`/`ETag`; `PUT`/`PATCH`/`DELETE` require `If-Match` (or `version` in the body) and return `409`/`412` on mismatch. |
| NFR-API-05 | All endpoints require authentication except `/health`, `/api/v1/auth/*` and the OIDC callback. Authorization is checked per endpoint against permissions (§8). `403` for missing permission, `401` for missing/invalid credentials. |
| NFR-API-06 | `GET /health/live`, `GET /health/ready` (database reachable), `GET /openapi.json`. |
| NFR-API-07 | CORS allow-list configurable (`CORS_ORIGINS`, empty by default). The bundled UI is served from the same origin via the reverse proxy (NFR-UI-02) and needs no CORS; the list matters only for third-party browser clients. |
| NFR-API-08 | Machine clients (integrations, scripts, the MCP server in local mode) authenticate with **API tokens** (hashed at rest, scoped to permissions, revocable) in addition to user sessions. |
| NFR-API-09 | Every operation has a unique, stable `operationId` (verb and noun, e.g. `planning_get`, `assignments_create`), a summary and a description; request and response fields carry descriptions and examples; the `code` values of errors are documented. This metadata is the source for generated clients and for MCP tool definitions (§10). |
| NFR-API-10 | Mutating planning endpoints (`POST/PUT/DELETE /assignments`, `POST/PUT/DELETE /absences`) accept `?dryRun=true`: the request is validated and its effect computed (resulting `overbooked` and `overplanned` flags, conflicts), but nothing is persisted and no audit entry is written. The response has the shape of the real one plus `dryRun: true`. |

### 7.1 Endpoints (minimum set)

| Resource | Endpoints | Permission (see §8) |
|---|---|---|
| Teams | `GET/POST /teams`, `GET/PUT/DELETE /teams/{id}` | read: `team:read`; write: `team:write` |
| Skills | `GET/POST /skills`, `GET/PUT/DELETE /skills/{id}` | `skill:read` / `skill:write` |
| Members | `GET/POST /members`, `GET/PUT/DELETE /members/{id}`, `PUT /members/{id}/skills` | `member:read` / `member:write` |
| Tasks | `GET/POST /tasks`, `GET/PUT/DELETE /tasks/{id}`, `PUT /tasks/{id}/efforts`, `POST /tasks/import-csv` | `task:read` / `task:write` |
| Assignments | `GET /assignments?from=&to=&memberId=&taskId=`, `POST /assignments`, `PUT/DELETE /assignments/{id}` | `assignment:read` / `assignment:write` |
| Absence types | `GET/POST /absence-types`, `PUT/DELETE /absence-types/{id}` | `absence-type:read` / `absence-type:write` |
| Absences | `GET/POST /absences`, `PUT/DELETE /absences/{id}`, `POST /absences/import-csv` | `absence:read` / `absence:write` (or `absence:write:own`) |
| Planning (read model) | `GET /planning?from=YYYY-MM-DD&weeks=4&teamId=&skillId=` | `assignment:read` |
| Overview (read model) | `GET /overview?from=&weeks=` | `assignment:read` |
| Availability (read model) | `GET /availability?skillId=&from=&weeks=` | `assignment:read` |
| Users | `GET/POST /users`, `PUT/DELETE /users/{id}`, `POST /users/{id}/reset-password` | `user:admin` |
| Roles | `GET/POST /roles`, `PUT/DELETE /roles/{id}`, `GET/PUT /role-mappings` | `role:admin` |
| API tokens | `GET/POST/DELETE /api-tokens` | `token:admin` |
| Integrations | `GET/POST /integrations`, `PUT/DELETE /integrations/{id}`, `POST /integrations/{id}/test`, `POST /integrations/{id}/sync`, `GET /integrations/{id}/runs` | `integration:admin` |
| Ingest (machine API) | `POST /ingest/tasks`, `POST /ingest/members`, `POST /ingest/absences` (idempotent upsert by `source` + `externalId`) | `ingest:write` |
| Settings | `GET/PUT /settings` | read: any authenticated; write: `settings:admin` |
| Audit | `GET /audit-log` | `audit:read` |
| Auth | `GET /auth/config`, `POST /auth/login` (local), `POST /auth/logout`, `GET /auth/oidc/login`, `GET /auth/oidc/callback`, `GET /auth/me` | public / authenticated |

The **planning read model** (`GET /planning`) returns, for the requested horizon: the weeks; teams with members (skills, per-week `capacity`, `absentDays`, `booked`, `overbooked`, assignments); and tasks with per-skill `effort`, `assigned`, `open`. The UI **MUST NOT** recompute capacity on its own.

The **availability read model** (`GET /availability`) returns, for one skill and a horizon, every active member that has the skill with per-week `capacity`, `booked`, `free` and `absentDays`, ordered by total free PD. It feeds the "Assign…" dialog (FR-PLAN-12) and the MCP tool `find_availability` (§10).

---

## 8. Authentication and authorization

### 8.1 Authentication modes

| ID | Requirement |
|---|---|
| FR-AUTH-01 | Both modes **MUST** be supported and independently switchable by configuration: `AUTH_LOCAL_ENABLED` and `AUTH_OIDC_ENABLED`. Both may be on at the same time (login page shows both options). At least one **MUST** be enabled. |
| FR-AUTH-02 | **Local users**: username + password, stored with **argon2id**, password policy configurable (min length ≥ 12 default), login rate limiting and lockout, forced password change on bootstrap/reset. |
| FR-AUTH-03 | **OIDC**: Authorization Code flow with **PKCE**, handled by the API (confidential client). The API issues an HTTP-only, `SameSite=Lax`, `Secure` session cookie; the UI never sees OIDC tokens and forwards the cookie on its server-side calls (NFR-UI-03). Config: `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_SCOPES` (default `openid profile email`), `OIDC_ROLES_CLAIM` (JSON path, default `realm_access.roles`; Keycloak client roles via `resource_access.<client>.roles`), `OIDC_USERNAME_CLAIM` (default `preferred_username`). Any standards-compliant OIDC provider **MUST** work; Keycloak is the reference. |
| FR-AUTH-04 | On first OIDC login a local `app_user` record is created (JIT provisioning) keyed by `(issuer, sub)`; it **MAY** be auto-linked to a member by matching email. |
| FR-AUTH-05 | Bearer-token validation (JWT, signature via JWKS with caching, `iss`, `aud`, `exp`) **SHOULD** be supported for API clients in OIDC mode, in addition to API tokens (NFR-API-08); it is one of the ways the MCP server (§10) can act for a user. |
| FR-AUTH-06 | Logout ends the local session; for OIDC it **SHOULD** also call RP-initiated logout. |
| FR-AUTH-07 | CSRF protection for cookie-based sessions (double-submit token or `SameSite` + custom header); the UI forwards the header (NFR-UI-03). |

### 8.2 Authorization (RBAC)

| ID | Requirement |
|---|---|
| FR-AUTHZ-01 | Authorization is **permission based**. Roles are named sets of permissions. Code checks permissions only, never role names. |
| FR-AUTHZ-02 | The effective permissions of a principal are the union of the permissions of all its internal roles. Local users get roles through `user_role`. OIDC users get roles by mapping the **external roles** found in the token claim (`OIDC_ROLES_CLAIM`) through `external_role_mapping` to internal roles (re-evaluated on each login/token refresh; optionally additionally honouring locally assigned roles, config `OIDC_MERGE_LOCAL_ROLES`). |
| FR-AUTHZ-03 | Built-in roles are seeded and cannot be deleted; admins can create custom roles. |
| FR-AUTHZ-04 | **Scoping**: a role **MAY** be restricted to specific teams (`user_role.team_id` nullable list). With team scope, write permissions on members, assignments and absences apply only to members of those teams. Without scope = all teams. (P1: implement for `member`, `assignment`, `absence`.) |
| FR-AUTHZ-05 | The UI hides or disables controls the user may not use, but the API is the only enforcement point. |

### 8.3 Permission catalogue

| Permission | Allows |
|---|---|
| `team:read`, `team:write` | view / create-edit-delete teams |
| `skill:read`, `skill:write` | view / manage skills |
| `member:read`, `member:write` | view / manage members and their skills, allocation, team |
| `task:read`, `task:write` | view / manage tasks and efforts |
| `assignment:read`, `assignment:write` | view planning / create-move-delete assignments |
| `absence:read` | view all absences |
| `absence:write` | manage absences of any (in-scope) member |
| `absence:write:own` | manage absences of the member linked to the own account |
| `absence-type:read`, `absence-type:write` | view / manage absence types |
| `user:admin`, `role:admin`, `token:admin` | manage local users / roles & mappings / API tokens |
| `integration:admin` | manage integrations and run syncs |
| `ingest:write` | use the machine ingest API |
| `settings:admin`, `audit:read` | manage settings / read the audit log |

### 8.4 Built-in roles (default permission sets)

| Role key | Permissions |
|---|---|
| `viewer` | all `*:read` |
| `member` | `viewer` + `absence:write:own` |
| `team-lead` (team-scoped) | `viewer` + `assignment:write`, `absence:write`, `member:write` within scope |
| `planner` | `viewer` + `task:write`, `assignment:write`, `absence:write` (all teams) |
| `master-data-admin` | `viewer` + `team:write`, `skill:write`, `member:write`, `absence-type:write` |
| `admin` | everything |

Default external-role mapping for Keycloak (editable): `rp-admin → admin`, `rp-planner → planner`, `rp-viewer → viewer`.

---

## 9. Integrations (provider architecture, P2)

The core **MUST** stay tool-agnostic. All external systems are reached through three provider interfaces. Providers are modules registered at start-up (in-process plugin mechanism, one module per provider, selected by `provider_key`). In Python each provider is a package that registers itself in the entry-point group `resource_planner.providers`. A provider **MAY** alternatively push data through the generic **ingest API** (§7.1) from outside the system, which allows integrating any tool without writing a module.

### 9.1 Provider interfaces (language-neutral notation)

In code they are `typing.Protocol` classes, and the `fetch*` methods are async generators.

```
TaskSourceProvider
  describeConfigSchema(): JSONSchema             // for the admin UI form
  testConnection(config): Result
  fetchTasks(config, since?: timestamp): AsyncIterable<ExternalTask>
ExternalTask { externalId, key, title, description?, status, url, updatedAt,
               labels: string[], estimate?: number, estimateUnit?, customFields?: map,
               teamHint?: string }

DirectoryProvider                                  // users, groups, roles
  describeConfigSchema(), testConnection(config)
  fetchMembers(config): AsyncIterable<ExternalPerson>
  fetchGroups(config): AsyncIterable<ExternalGroup>
ExternalPerson { externalId, username, displayName, email, active, groups: string[], roles: string[] }
ExternalGroup  { externalId, name }

AbsenceSourceProvider
  describeConfigSchema(), testConnection(config)
  fetchAbsences(config, from, to): AsyncIterable<ExternalAbsence>
ExternalAbsence { externalId, personRef (email|username|externalId), from, to,
                  startPart?, endPart?, typeRef (provider-specific code) }
```

### 9.2 Generic sync engine requirements

| ID | Requirement |
|---|---|
| FR-INT-01 | Sync is **idempotent**: entities are upserted by `(external_source = integration id, external_id)`. Re-running a sync never creates duplicates. |
| FR-INT-02 | Sync runs manually (button/API) and on a cron schedule per integration; one run per integration at a time (lock, e.g. a PostgreSQL advisory lock). Each run writes a `sync_run` with counts and error details. |
| FR-INT-03 | Incremental sync via `since` where the provider supports it; a full re-sync option exists. |
| FR-INT-04 | A **mapping layer** (configurable per integration, stored in `config`) translates external data into domain data — no mapping logic is hard-coded in the core. |
| FR-INT-05 | Entities that disappear in the source are **deactivated/closed**, not deleted (configurable). Local assignments are never removed by a sync. |
| FR-INT-06 | Sync errors for single records are collected and do not abort the run; connection/auth errors abort and mark the run failed. |
| FR-INT-07 | Secrets in `integration_config.config` are encrypted at rest (key from `APP_ENCRYPTION_KEY`) and never returned by the API. |
| FR-INT-08 | A **dry-run** mode returns what would be created/updated without writing. |
| FR-INT-09 | Provider development guide in `/docs` with the interfaces, a minimal example provider and a contract test kit. |

### 9.3 Task source: effort mapping strategies

Jira stores one estimate and labels, while the planner needs **effort per skill**. The mapping is a configurable strategy per integration:

| Strategy | Behaviour |
|---|---|
| `EVEN_SPLIT` | Total estimate is converted to PD (`unitFactor`, e.g. story points × factor, hours ÷ 8) and split evenly across the labels that match a skill. |
| `LABEL_ENCODED` | Labels carry the effort, e.g. `be-3`, or `Backend:3` via a configurable regex with named groups `skill` and `effort`. |
| `CUSTOM_FIELDS` | A map `skill → source field id` (e.g. Jira custom fields "Effort Backend", "Effort Frontend"). |
| `MANUAL` | Only basic data is imported; the effort per skill is entered in the planner. |

Further mapping settings: label → skill mapping (`label` → `skill.key`, default identity, case-insensitive), policy for unknown labels (`IGNORE` | `CREATE_SKILL` | `REPORT`), source status → planner status, which items to import (Jira: JQL/board/sprint filter), team hint → owner team (e.g. by project key or component).

The **Jira provider** (first provider) **MUST** support Jira Cloud and Jira Data Center/Server via REST, authenticated by API token/PAT or OAuth 2.0 (3LO) — config documented. A second reference provider **SHOULD** be a trivial `csv`/file provider to prove that the abstraction works; providers for GitHub Issues, GitLab, Azure DevOps, Linear etc. are explicitly anticipated.

### 9.4 Locked fields for imported tasks

`task.locked_fields` lists fields controlled by the source (default: `title`, `status`, `efforts` for strategies other than `MANUAL`). The UI shows them read-only; an admin **MAY** unlock a field per task, in which case later syncs do not overwrite it.

### 9.5 Directory import (Keycloak, P2)

| ID | Requirement |
|---|---|
| FR-DIR-01 | The Keycloak directory provider imports users via the Keycloak Admin REST API (service account with `view-users`/`query-groups` rights) as **members** (resources). Import is selectable per group/role to avoid importing technical accounts. |
| FR-DIR-02 | Keycloak **groups** can be mapped to **teams** (`group → team`, optionally "create team from group"). A person is assigned to the team of the mapped group; if several mapped groups match, the configured priority wins (a member has exactly one team). Persons without a mapped group are imported into an "Unassigned" state (`team_id` NULL is not allowed → they are created in a configurable default team or held in an import-review list). |
| FR-DIR-03 | **Skills** are **not** owned by Keycloak by default; they remain managed in the planner. Optionally, skills **MAY** be derived from Keycloak groups/roles/attributes via a mapping (`attribute → skill`). |
| FR-DIR-04 | The import creates/updates members only; creating **app users (logins)** is handled by OIDC JIT provisioning (FR-AUTH-04). Linking a login to a member happens by email/username match. |
| FR-DIR-05 | Deactivated Keycloak users deactivate the corresponding member (assignments are kept; future-dated assignments are listed as conflicts). |

### 9.6 Absence source (e.g. SAP, P2)

Same pipeline as manual CSV import (FR-ABS-06): person matched via `personRef`, provider type codes mapped to absence types via a mapping table (`typeRef → absence_type.key`), unmapped codes reported. Absences with an `external_source` are read-only in the UI; manual absences remain editable. A sync replaces only absences of that integration within the synced date range.

---

## 10. MCP server (P2e)

A Model Context Protocol (MCP) server lets MCP clients read the plan and, if enabled, prepare changes to it. It is a separate component (`src/mcp`, Python, FastMCP) and a pure client of the HTTP API (NFR-ARCH-01, NFR-ARCH-08). It is planned for phase P2e; the API is prepared for it from P1 (§2.3).

| ID | Requirement |
|---|---|
| FR-MCP-01 | **Component**: `src/mcp` has its own image and the Compose profile `mcp`. It has no database access. Configuration by environment: `PLANNER_API_URL`, `MCP_TRANSPORT` (`streamable-http` by default, `stdio` for local use), `MCP_WRITE_TOOLS_ENABLED` (default `false`) and the OIDC settings of FR-MCP-02. |
| FR-MCP-02 | **Identity**: the API alone decides what a caller may do (FR-AUTHZ-05). Over `streamable-http` the server validates the bearer token of the MCP client (signature via JWKS, issuer, expiry, and an audience that names the MCP server) and calls the API as the same user. A token issued to the MCP server is **never** forwarded unchanged; how the user's identity reaches the API is an open decision (§14, item 2). In `stdio` mode the server uses a user-created API token (`PLANNER_API_TOKEN`, NFR-API-08). The MCP server adds no permissions. |
| FR-MCP-03 | **Read tools** are always available and return only what the acting user may read (table below). |
| FR-MCP-04 | **Write tools** exist only if `MCP_WRITE_TOOLS_ENABLED=true`. Each takes `dry_run` (default `true`, sent as `?dryRun=true`, NFR-API-10) and, where it changes an existing assignment, its `version` (NFR-API-04). With `dry_run=false` the change is persisted with the user's own permissions. |
| FR-MCP-05 | **Limits**: there are no tools to create, change or delete teams, skills, members, tasks, absence types, users, roles, tokens, integrations, settings or the audit log, and none to delete absences. Lists are capped like the API (`pageSize` ≤ 200). |
| FR-MCP-06 | **Definitions**: tool names, descriptions and parameters derive from the OpenAPI operations (NFR-API-09). Results are the JSON the API returns, including the flags `overbooked` and `overplanned` and the open effort. |
| FR-MCP-07 | **Errors**: the `code` and `detail` of an RFC 9457 error are returned as the tool error, so that a caller can correct itself (for example after `ASSIGNMENT_SKILL_MISMATCH`). |
| FR-MCP-08 | **Audit**: every call sends `X-Client-Name: planner-mcp`; changes appear in the audit log with that `client_name` (§3.1). |
| FR-MCP-09 | **Tests**: a contract test per tool against a real API instance, and the permission cases of acceptance criterion 14. |

| Tool | API operation | Permission |
|---|---|---|
| `list_teams` | `GET /teams` | `team:read` |
| `list_skills` | `GET /skills` | `skill:read` |
| `list_members` | `GET /members` | `member:read` |
| `list_tasks` | `GET /tasks` | `task:read` |
| `list_absences` | `GET /absences` | `absence:read` |
| `get_planning` | `GET /planning` | `assignment:read` |
| `get_overview` | `GET /overview` | `assignment:read` |
| `find_availability` | `GET /availability` | `assignment:read` |
| `create_assignment` (write) | `POST /assignments` | `assignment:write` |
| `move_assignment` (write) | `PUT /assignments/{id}` | `assignment:write` |
| `delete_assignment` (write) | `DELETE /assignments/{id}` | `assignment:write` |
| `create_absence` (write) | `POST /absences` | `absence:write` or `absence:write:own` |

---

## 11. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-SEC-01 | OWASP ASVS L2 as guideline: input validation on every endpoint, parameterised queries only, output encoding in the UI, security headers (CSP, `X-Content-Type-Options`, `Referrer-Policy`), dependency scanning in CI. The UI uses a nonce-based CSP (NFR-UI-06). |
| NFR-SEC-02 | Passwords, tokens and integration secrets are never logged. Audit log records who changed what (FR-ADM-05). |
| NFR-PERF-01 | Planning read model for 500 members, 2,000 tasks and a 4-week horizon responds in < 1 s (p95) on a 2-vCPU instance. |
| NFR-OPS-01 | Structured JSON logging, request IDs, `/health` probes, graceful shutdown. |
| NFR-OPS-02 | DB migrations are versioned, forward-only and run automatically on start-up (configurable). Backup/restore is documented (`pg_dump`). |
| NFR-QA-01 | Unit tests for all §4 calculation rules (including half-day and company-holiday cases); API integration tests against a real PostgreSQL (Testcontainers); e2e tests for the planning board incl. keyboard assignment; contract tests for each provider and, from P2e, for each MCP tool (§10). |
| NFR-QA-02 | CI (GitHub Actions or equivalent): lint (`ruff`), type-check (`mypy --strict`), tests (`pytest`), OpenAPI snapshot check (the document exported from the code must equal `src/contracts/openapi.json`), container build. |
| NFR-OSS-01 | Repository contains `LICENSE` (Apache-2.0 proposed), `README.md` (quick start with Compose), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue/PR templates, a CHANGELOG (Conventional Commits / semver). |
| NFR-OSS-02 | No proprietary dependencies; no vendor-specific code outside provider modules (Jira, Keycloak, SAP). |

---

## 12. Delivery plan

| Phase | Content | Done when |
|---|---|---|
| **P1a — Foundation** | Monorepo (`uv` workspace), DB schema + migrations, OpenAPI skeleton, local auth + RBAC, bootstrap admin, Compose with reverse proxy, UI shell in the papaia-manager design (login, sidebar, themes) | Admin can log in; teams/skills/members CRUD works via API and UI |
| **P1b — Planning core** | Tasks with per-skill effort, absences + absence types, calculation module (§4), planning/overview/availability read models, `dryRun` support, board with drag & drop and keyboard alternative, demo seed, CSV import (tasks, absences) | Prototype behaviour is reproduced on the real stack; §4 unit tests pass |
| **P1c — OIDC & roles** | OIDC login, external role mapping, role/user admin UI, team-scoped roles, audit log, API tokens | Keycloak dev realm login works; permissions enforced per §8.3 |
| **P2a — Provider framework** | Sync engine, integration admin UI, ingest API, CSV/file provider | Idempotent sync with run log proven by tests |
| **P2b — Jira** | Jira task source with mapping strategies | Stories from a Jira project appear as tasks with per-skill effort |
| **P2c — Keycloak import** | Directory provider, group→team mapping | Members imported and assigned to teams from Keycloak groups |
| **P2d — Absence source** | Absence provider interface + first concrete provider (SAP or file-based) | Absences synced read-only with type mapping |
| **P2e — MCP server** | `src/mcp` (§10): read tools first, then write tools behind `MCP_WRITE_TOOLS_ENABLED` and `dry_run`; needs the API tokens and OIDC bearer validation of P1c | An MCP client answers "who has free Backend capacity in week W?" and prepares an assignment with `dry_run`; permissions are those of the acting user |
| **P3** | Region holiday calendars, write-back to PM tool, more providers, capacity forecast beyond the horizon, reports | — |

---

## 13. Acceptance criteria (excerpt, Gherkin-style)

1. **Capacity with absence** — *Given* member Anna (80 %) with vacation Monday–Friday in week W, *when* the planning is read for W, *then* `capacity = 0`, `absentDays = 5`.
2. **Half day** — *Given* Anna (100 %) with an `AM` absence on one day, *then* that week's capacity is `4.5`.
3. **Company holiday** — *Given* an absence with `member_id = NULL` on Thursday, *then* every active member loses one day in that week.
4. **Type without capacity effect** — *Given* an absence type with `reduces_capacity = false`, *then* the absence is shown in the calendar but capacity is unchanged.
5. **Skill-constrained assignment** — *When* an assignment is created for a member lacking the skill, *then* the API returns `422` with code `ASSIGNMENT_SKILL_MISMATCH`.
6. **Over-booking** — *Given* capacity 4 and booked 5, *then* the planning read model returns `overbooked: true` and the UI shows a non-colour-only warning.
7. **Cross-team task** — *Given* a task owned by Team Beta with effort for skills from Teams Alpha, Beta and Gamma, *then* members of all three teams can be assigned to it.
8. **Permission** — *Given* a user with only `viewer`, *when* they call `POST /assignments`, *then* `403`; the board renders read-only.
9. **OIDC role mapping** — *Given* a Keycloak user with realm role `rp-planner`, *then* after login they hold exactly the permissions of the internal `planner` role.
10. **Idempotent sync** — *When* the same Jira sync runs twice without source changes, *then* the second run reports `0 created, 0 updated`.
11. **Locked field** — *Given* an imported task, *when* a user tries to change `title` via the API, *then* `409` with code `FIELD_LOCKED`.
12. **Concurrency** — *Given* two clients holding the same assignment version, *when* both update it, *then* the second receives `409/412` and the UI offers reload.
13. **Dry run** — *Given* capacity 4 and booked 4 for a member-week, *when* `POST /assignments?dryRun=true` is called with `person_days = 1`, *then* the response contains `overbooked: true` and `dryRun: true`, a following `GET /assignments` shows no new assignment, and the audit log has no new entry.
14. **MCP permissions** — *Given* an MCP client acting for a user with only `viewer`, *then* the write tools are not listed, and with `MCP_WRITE_TOOLS_ENABLED=true` a write tool called with `dry_run=false` returns the API's `403` with its `code`; the read tools return only what that user may read.

---

## 14. Open decisions (to be confirmed by the maintainers)

1. ~~Final license (Apache-2.0 vs. MIT vs. AGPL).~~ **Decided: MIT.**
2. How the identity of the acting user reaches the API from the MCP server (FR-MCP-02): OAuth 2.0 token exchange (RFC 8693) where the identity provider supports it, or a dedicated service identity that passes the user's subject along, or user-created API tokens only. A token issued to the MCP server is never forwarded unchanged. **Open**; the API accepts bearer tokens and API tokens today (`docs/mcp-preparation.md`).
3. ~~OIDC flow ownership.~~ **Decided: API-handled session cookie, forwarded by the UI.** Bearer tokens (FR-AUTH-05) are accepted as well, when `OIDC_API_AUDIENCES` is set.
4. Default effort mapping strategy for the Jira provider (depends on how estimates and labels are used in the team's Jira). **Open**; the Jira provider is not built. The generic task mapping defaults to `MANUAL`.
5. ~~Behaviour for imported Keycloak persons without a mapped group.~~ **Decided: the default team of the mapping, which must exist; without one they are reported and not imported.**
6. ~~Whether half-day absences are required in P1.~~ **Decided: built in P1.**
7. Whether multi-tenancy (several organisations in one instance) is ever needed — currently **out of scope**; the data model has no tenant column.
