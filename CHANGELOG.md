# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-15

Tasks from Jira, with the planning information of the source shown in the planner.

### Added

- Jira task source for Jira Cloud and Jira Data Center / Server: issues become tasks with their
  planning attributes, while the effort per skill is entered in the planner (`MANUAL`, the
  default). Projects and issue types are ticked in lists loaded from Jira; scheduled runs read
  only what changed. See [`docs/providers.md`](docs/providers.md#jira-jira).
- Planning attributes of tasks: due date, start date, original estimate, story points, priority,
  assignee and reporter. Integrations and the ingest API write them; the planner shows them in the
  task list, an expandable row and the task dialog, read only. A state the mapping does not know
  falls back on the category the source gives, so localised state names work.
- The list of tasks to plan on the board can show these attributes, the owner team, the source and
  the open effort. The settings choose the fields for everyone (none by default); each account can
  choose its own on its account page.
- Schedule presets for integrations (by hand only, every 15 or 30 minutes, every hour, every 4
  hours, daily or on workdays at 05:00 UTC) next to a cron line of your own.
- Integration settings whose choices are loaded from the source, also for a form not saved yet, and
  settings shown only while another one has a value.
- `SYNC_FULL_INTERVAL_HOURS` (default 24): a scheduled run of a provider that reads only what
  changed reads the whole source again after this many hours. Only a complete read closes what has
  disappeared from the source.
- [`docs/keycloak.md`](docs/keycloak.md): setting up single sign-on with Keycloak.
- API, all additive: `POST /integrations/field-options`, `PUT /auth/me/pool-task-fields`,
  `poolTaskFields` in the settings and the account, the planning attributes and `sourceName` on
  tasks and in the ingest API, and the attributes of the tasks to plan in `GET /planning`.

### Fixed

- Task keys with `_` (such as `MY_PROJ-1`) can be saved from the task dialog.
- `docker-compose.yml` passes `SYNC_FULL_INTERVAL_HOURS` to the API.

### Upgrading

- The API runs the database migrations `0002` (planning attributes of tasks) and `0003` (the
  choice of fields of an account) when it starts. Back up the database first.
- `.env` needs no change.

### Not included

- The SAP provider, the MCP server and the P3 items of the specification; OAuth 2.0 (3LO) for
  Jira. See [`docs/implementation-notes.md`](docs/implementation-notes.md).

## [0.1.0] - 2026-11-13

First version of the implementation of the specification (phases P1a to P1c, P2a and P2c).

### Added

- Planning board with a pool of tasks, a grid of members and weeks, drag and drop, the *Assign…*
  dialog, capacity with part-time shares, absences and half days, over-booking marks.
- Overview of demand, planned work and utilisation; availability read model.
- Master data: teams, skills, members, absence types, absences with a calendar; tasks with an
  effort per skill; CSV import and export.
- Local accounts and OpenID Connect sign-in, roles with permissions and team limits, role mapping
  from the identity provider, API tokens, bearer tokens, audit log, settings.
- Provider framework with a sync engine, run log, scheduler and ingest API; CSV providers for
  tasks, members and absences; Keycloak directory import.
- REST API under `/api/v1` with an OpenAPI 3.1 contract, optimistic locking, dry runs and problem
  documents; generated Python client.
- Web interface in English and German, light and dark theme.
- `docker-compose.yml` with database, API and interface; `docker/docker-compose.dev.yml` with a
  reverse proxy and a development Keycloak; demo data.

### Not included

- Jira and SAP providers, the MCP server and the P3 items of the specification.
  See [`docs/implementation-notes.md`](docs/implementation-notes.md).

[Unreleased]: https://github.com/Fidonis/resource-planner/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Fidonis/resource-planner/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Fidonis/resource-planner/releases/tag/v0.1.0
