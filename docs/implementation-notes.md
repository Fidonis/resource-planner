# Implementation notes

What the implementation does differently from, or in addition to, `ressource-planner.md`
(Draft v2), and the decisions that the specification leaves open. Where this file and the
specification differ, this file describes what the code does.

## Status

| Phase | State |
|---|---|
| P1a Foundation | built |
| P1b Planning core | built |
| P1c OIDC and roles | built |
| P2a Provider framework (sync engine, ingest API, CSV providers) | built |
| P2b Jira | built (Jira Cloud and Data Center, REST API version 2) |
| P2c Keycloak directory import | built |
| P2d SAP absence source | **not built** (a CSV absence provider covers the file-based case) |
| P2e MCP server | **not built**, the API is prepared for it (see [mcp-preparation.md](mcp-preparation.md)) |
| P3 | not planned yet |

## Decisions the specification leaves open (section 14)

| Item | Decision |
|---|---|
| 1 License | MIT. |
| 2 Identity of the MCP user | Open. The API accepts the user's own bearer token and API tokens; see [mcp-preparation.md](mcp-preparation.md). |
| 3 OIDC flow | The API handles the flow and sets the session cookie (the default of the specification). It also accepts a bearer token (FR-AUTH-05) when `OIDC_API_AUDIENCES` is set. |
| 4 Effort strategy for Jira | `MANUAL`: the effort per skill is entered in the planner; Jira supplies the planning attributes. The other strategies stay available. |
| 5 Keycloak people without a mapped group | They go to the **default team** of the mapping. The team must exist; without a default team these people are reported and not imported. |
| 6 Half days in P1 | Built. An absence has a start part and an end part (`FULL`, `AM`, `PM`). |
| 7 Multi-tenancy | Out of scope, as specified. |

## Deviations and additions

### Calculation and data

- **Absences that overlap** are merged as the union of their half-day slots, so two absences on the
  same day do not remove capacity twice (this is what the mock-up shows).
- **Assignments of cancelled tasks** do not count as booked; assignments of tasks that are *done*
  still do. Creating an assignment on a closed task is refused with `TASK_CLOSED`.
- **The absence type "On-call"** (no effect on capacity) exists in the demo data only. Built-in
  types are created at start and can be changed or removed like any other.
- **Members use `external_snapshot`** instead of `locked_fields`: the value an integration last
  wrote is remembered, and a later sync overwrites the local value only while it still equals that
  snapshot. Tasks use `locked_fields` as specified (a locked field answers `FIELD_LOCKED`).
- **A sync without any change writes nothing**, so no version number moves and the audit log stays
  quiet (acceptance criterion 10).
- Two integrations cannot import a member with the same e-mail address: the second one reports
  "already in use" for that record. A member that exists locally is adopted by e-mail address
  the first time an integration sees them.
- JSON is camelCase, SQL is snake_case. Person-days travel as JSON numbers, are computed with
  `Decimal` and stored with two decimals.

- **Planning attributes of a task** beyond §3.1: `due_date`, `start_date`,
  `original_estimate_hours`, `story_points`, `priority`, `assignee`, `reporter`. They are written by
  integrations and the ingest API only and are read-only in the planner; manual tasks leave them
  empty. A task key may contain `_`, as Jira keys can.
- **States** are mapped by name (the mapping, then the usual names) and then by the category the
  source gives (`todo`, `in_progress`, `done`).

### API

Endpoints beyond section 7.1, added because the web interface needs them:

| Endpoint | Purpose |
|---|---|
| `GET /absences/calendar` | Absences laid out per day for the calendar page |
| `POST /auth/change-password` | Forced and voluntary password change |
| `PUT /auth/me/preferences` | Language and theme of the signed-in account |
| `GET /integrations/providers` | Providers found, with the schemas of their settings and mapping (`{"items": [...]}`) |
| `GET /integrations/{id}/runs/{runId}` | The log of one run (the list is `GET /integrations/{id}/runs`) |
| `POST /integrations/field-options` | The choices of a setting, loaded from the source, also for a form that is not saved |
| `GET /assignments/export-csv`, `/tasks/export-csv`, `/absences/export-csv`, `/overview/export-csv` | CSV export |
| `POST /demo-data` | Loads the demo data into an empty installation (needs `ENABLE_DEMO_SEED`) |
| `GET /roles/permissions` | The permission catalogue, for the role editor |

Also added: sums and a planned percentage on tasks, working days on absences, and a `writable`
flag per row in `/planning`, `/members` and `/auth/me`, so the interface can leave out controls the
user may not use without recomputing a permission.

`/planning` accepts `memberId` and `taskId` filters; the web interface uses them to draw only the
rows a change touched.

### Web interface

- **Drag and drop** (FR-PLAN-04, FR-PLAN-05) uses Sortable with its own drag image. A skill of a
  task is dragged from the pool onto a member's week, a chip onto another member or week. Weeks that
  take the item are framed with a dashed outline while it is dragged and the others are dimmed.
  The week under the pointer when the item is let go decides; an item that only passed over a
  week on its way is not booked there. The server proposes the amount by rule 10 of section 4 and
  answers with the board drawn again.
- **Keyboard and touch path** (FR-PLAN-07) is the "Assign…" dialog, not the arm-and-select
  sequence the specification gives as an example: every pool item and every cell has a button
  that opens it, and the dialog is covered by end-to-end tests that use the keyboard alone.
  The drag itself was tried with the mouse only; touch dragging and screen readers were not tried.
- **Provider texts** (names, field titles and hints that a provider supplies) are translated
  only for the providers that ship with the planner. Texts of other providers, and the messages a
  provider returns from a connection test, stay in English.
- The content security policy allows inline *style attributes* (`style-src-attr 'unsafe-inline'`),
  because the team colour is set as a style. Scripts and style sheets stay restricted to the
  application's own files. The specification's nonce is not needed: there is no inline script or
  style block, so `script-src 'self'` suffices.
- **The Compose file has no reverse proxy and no Keycloak** (NFR-ARCH-07 names both): the
  planner is meant to run behind the proxy and identity provider that already exist. `docker-compose.yml`
  in the repository root starts the database, the API and the interface, so `docker compose up` needs
  no `-f`, and publishes the interface (`HTTP_PORT`) and the API (`API_PORT`) on the loopback
  address. `docker/docker-compose.dev.yml` adds an nginx proxy (the specification names Caddy as an
  example) and the development Keycloak, used for single sign-on trials and the browser tests.
- The Dockerfiles, the example proxy configuration and the development realm are in `docker/`
  (the specification's layout calls the folder `deploy`).

### Integrations

- The configuration of an integration is stored as `{"provider": {...}, "mapping": {...}}` in one
  JSON column; secrets are encrypted with `APP_ENCRYPTION_KEY` (Fernet, values start with
  `enc:v1:`), are never returned (the schema marks them `writeOnly`) and an empty secret in an
  update keeps the stored one.
- One sync runs at a time per integration (an advisory lock on its own connection; a second request
  answers `409 SYNC_RUNNING`). A run that was still `running` when the API started is marked
  `aborted`.
- The scheduler runs inside the API process. Keep `SCHEDULER_ENABLED=true` on exactly one API
  instance.
- For a provider that reads only what changed, a scheduled run is complete when the last complete
  run (a run with errors counts) is older than `SYNC_FULL_INTERVAL_HOURS`. The first run of such an
  integration and the first one after its settings or mapping changed are complete as well.
- The Jira provider signs in with an API token (Cloud) or a personal access token (Data Center);
  OAuth 2.0 (3LO), which §9.3 names as an alternative, is not built. A run that reads changes asks
  for `updated >= "-<n>m"`, the minutes since the last run plus 30, which does not depend on the time
  zone of the Jira account; reading an issue twice changes nothing. Projects are queried in blocks
  of 100 ids, because Jira Cloud refuses a query without a bound.
- Jira Cloud pages are followed by `nextPageToken` alone: `isLast` is documented but is not always
  sent, so a missing one does not end the read. A token that comes again, or more than 10,000
  pages, fails the run, because a read taken for complete too early would cancel tasks.
- A provider gets its settings, not the mapping, so the Jira provider cannot see which fields
  `customFieldSkills` names. They are listed again under *Effort fields* and handed over as
  `custom_fields`, under the field's id, its names and the name typed in the setting.
- A stored secret is used for loading choices only while the settings marked `x-connection` are
  unchanged; saving an integration with a changed address still keeps the stored secret, as before.
- A source that is read completely lets the engine close what has disappeared (`missing` in the
  mapping). A provider that supports `since` returns changes only, so nothing is closed after such a
  run.
- Files are read only from inside `IMPORT_DIR`.

### Development Keycloak (`docker/docker-compose.dev.yml`)

- The realm is imported only into an empty Keycloak. After a change in `docker/keycloak/`, remove
  the container and its data before starting it again.
- The client scope `basic` is needed for the `sub` claim to appear in tokens.
- The service account of the import client has the realm-management role `view-realm`, which the
  "realm roles to read" setting needs.

## Not done, not verified

- The SAP provider, the MCP server and P3 are not built.
- The Jira provider was tested against a simulated Jira (the request and answer shapes of the
  Atlassian documentation), not against a live Cloud or Data Center instance in the test suite.
- The planning attributes have no CSV columns in `csv-tasks`; the ingest API carries them. Assignees
  are not matched to members.
- Several API workers are not supported (sessions and the login limiter are fine; the limiter keeps
  its counters in memory, and the scheduler would run twice).
- Real TLS operation, touch input, screen readers and measured colour contrast were not tried.
  The stack was tried with Microsoft Edge only.
