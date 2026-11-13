# Integrations and providers

An **integration** is a configured use of a **provider**: which source, with which settings, how its
data maps to the planner's, and when it runs. Administrators manage them under *Integrations*
(permission `integration:admin`).

| Kind | Brings in | Providers in this version |
|---|---|---|
| Task source | Tasks with an effort per skill | `csv-tasks` |
| Directory | People as members, with team and skills | `keycloak`, `csv-members` |
| Absence source | Absences (read only) | `csv-absences` |

Jira and SAP providers are not part of this version; the interface below is what they would
implement.

## Using an integration

1. *Integrations → New integration*, choose the provider, give the integration a name, fill in
   the settings and the mapping, and optionally a schedule (a cron expression, evaluated in UTC).
2. **Test** checks that the source can be reached and read.
3. **Sync now** offers a **preview**: the whole run is done and then rolled back, and the result
   shows what would be created, updated, closed or skipped.
4. **Runs** lists every run with its counts; a run opens to a log with one line per record that
   was created, changed, skipped or refused, and the reason.

Things to know:

- A run with no changes writes nothing; running twice changes nothing the second time.
- A record that cannot be read or written is noted in the log and the run goes on; the run then
  has the status *partial*. A source that cannot be reached at all gives *failed*.
- What the source owns is read-only in the planner: the title, status and efforts of an imported
  task (shown with a lock; the API answers `FIELD_LOCKED`). For members, a local change is kept
  until the source changes the value again.
- What disappears from a completely read source is closed (tasks become *cancelled*, members and
  absences are deactivated or removed) when the mapping says `missing: CLOSE`, which is the
  default; `KEEP` leaves them.
- **Deleting an integration** that has brought in data is refused unless the data is kept as
  manual data: the link to the source is removed and nobody owns the fields any more. The dialog
  says how many tasks, members and absences are affected.
- Secrets in the settings are stored encrypted (`APP_ENCRYPTION_KEY`) and are not shown again; leave
  the field empty to keep the stored value.
- Files are read from inside the import directory (`IMPORT_DIR`, the volume `imports` in the
  Compose file) only. Copy a file in with
  `docker compose cp tasks.csv api:/data/imports/tasks.csv`.

## Task mapping

How the estimate and labels of a source task turn into an effort per skill:

| `effortStrategy` | Meaning |
|---|---|
| `EVEN_SPLIT` | The estimate is shared evenly over the labels that name a skill. |
| `LABEL_ENCODED` | Each label carries the effort, for example `Backend-3` (pattern in `labelPattern`; groups `skill` and `effort`). |
| `CUSTOM_FIELDS` | One field per skill (`customFieldSkills`: skill key to field name). |
| `MANUAL` | No effort is imported; people enter it. |

Other settings: `unitFactor` (person-days per unit of the estimate; hours / 8 is `0.125`),
`labelSkills` (label to skill key), `unknownLabels` (`IGNORE`, `CREATE_SKILL`, `REPORT`),
`statusMap` (state of the source to `open`, `done` or `cancelled`), `teamMap` (team hint of the
source to the owning team) and `missing`.

## CSV files

Delimiter `;`, `,` or tab is detected. The file may have a byte order mark and Windows line
endings; numbers may use a decimal comma. Column names ignore case, spaces and underscores. Rows
with errors are reported with their line number and skipped.

### Tasks (`csv-tasks`)

| Column | | |
|---|---|---|
| `Key` | required | Task key |
| `Title` | required | |
| `ExternalId` | | Identity in the source; default: the key |
| `Description`, `Status`, `Url`, `UpdatedAt` | | |
| `Labels` | | Separated by `\|` |
| `Estimate`, `EstimateUnit` | | The estimate and its unit |
| `Team` | | Team hint, see `teamMap` |
| any other column | | Handed to the mapping as a custom field |

### Members (`csv-members`)

`Name` (required), `ExternalId` (default: e-mail or user name), `Username`, `Email`, `Active`
(`yes`/`no`, default yes), `Groups` and `Roles` (separated by `|` or `,`). The directory mapping
below applies.

### Absences (`csv-absences`)

| Column | | |
|---|---|---|
| `Person` | required | E-mail, user name or name; `*` for everybody (a company holiday) |
| `From`, `To` | required | `YYYY-MM-DD` or `DD.MM.YYYY` |
| `Type` | required | The source's code; `typeMap` turns it into an absence type |
| `ExternalId` | | Default: built from the other columns |
| `StartPart`, `EndPart` | | `FULL`, `AM` or `PM` |
| `Note` | | |

Mapping: `typeMap` (code to absence type key), `lookbackDays` (default 30), `lookaheadDays`
(default 365) and `missing`.

## Keycloak directory import

Brings the users of a realm in as members. Set up in Keycloak:

1. A client in the realm to import from, with *client authentication* and *service accounts*
   enabled.
2. For its service account the `realm-management` roles `view-users`, `query-users` and
   `query-groups`, and `view-realm` if realm roles are to be read.

In the planner choose *Keycloak* and give the address **as the API reaches it** (for the bundled
development Keycloak `http://keycloak:8080`, not the address of the browser), the realm, the client
id and the client secret. The development realm in `docker/keycloak/` has such a client,
`resource-planner-sync`; its secret is `KEYCLOAK_SYNC_CLIENT_SECRET` in `.env`.

Directory mapping:

| Setting | Meaning |
|---|---|
| `includeGroups`, `includeRoles` | Import only people in one of these groups or holding one of these roles. Empty: everybody. |
| `groupTeams` | `Group=Team`, one per line, most important first. A person gets the team of the first of their groups that is listed. Without `=` the team has the group's name. |
| `createTeams` | Create a listed team that does not exist yet (applies only to the listed groups). |
| `defaultTeam` | Team for people without a listed group. **It must exist.** Without it such people are reported and not imported. |
| `groupSkills` | `Group=Skill`, one per line. People in the group get the skill added; the import never removes a skill. |
| `defaultAllocationPercent` | Share for new members; default: the setting. |
| `missing` | `CLOSE` deactivates members the directory no longer returns; `KEEP` leaves them. |

A person whose e-mail address matches an existing member is adopted by that member. A person
whose address is already used by a member of another integration is reported ("already in use").

## Pushing data: the ingest API

A program that already has the data can send it instead of being read:
`POST /api/v1/ingest/tasks`, `/ingest/members` and `/ingest/absences` with an API token that holds
`ingest:write`. Records are matched by `source` and `externalId`, so sending the same data again
changes nothing; a record that cannot be written is reported in `messages` and the others are
saved. Assignments are never touched. `?dryRun=true` saves nothing. The request and answer
shapes are in `GET /openapi.json`.

## Writing a provider

A provider is a class with a `kind`, a `key`, a `title`, a settings model and the methods of one
of the protocols in `planner_api/providers/base.py`:

```python
from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import BaseModel, Field

from planner_api.providers.base import (
    ConfigModel,
    ExternalTask,
    KIND_TASK_SOURCE,
    ProviderBase,
    SourceProblem,
    TestResult,
)


class ExampleConfig(ConfigModel):
    base_url: str = Field(title="Address", description="Where the tracker is.")
    token: str = Field(title="Token", json_schema_extra={"writeOnly": True})


class ExampleTasks(ProviderBase):
    kind = KIND_TASK_SOURCE
    key = "example"
    title = "Example tracker"
    config_model = ExampleConfig
    supports_since = False

    def __init__(self, settings) -> None:
        self._settings = settings

    async def test_connection(self, config: BaseModel) -> TestResult:
        ...  # reach the source; no secrets in the message
        return TestResult(ok=True, message="Connected.")

    async def fetch_tasks(
        self, config: BaseModel, since: datetime | None
    ) -> AsyncIterator[ExternalTask | SourceProblem]:
        ...  # yield ExternalTask for each record, SourceProblem for one that cannot be read
        yield ExternalTask(external_id="1", key="EX-1", title="First")
```

Rules:

- Yield a `SourceProblem` for a record that cannot be read instead of raising; raise
  `ProviderError` only when the whole source cannot be read (the message is shown to the
  administrator and must not contain a secret).
- Mark secrets `writeOnly` in `json_schema_extra`. The form of the interface is drawn from the
  schema of the settings model (and of the mapping), including field titles, descriptions,
  defaults, choices, lists and `Key=Value` lines, so no interface code is needed.
- Set `supports_since = True` only if `since` really narrows the result. After a run that did not
  see the whole source nothing is closed.
- Directories also provide `fetch_groups`; absence sources provide `fetch_absences(config, start,
  end)` for the window the mapping asks for.

Register the class in the entry-point group `resource_planner.providers` of your package:

```toml
[project.entry-points."resource_planner.providers"]
example = "my_package.provider:ExampleTasks"
```

Install the package into the API's environment; at start the planner lists the providers it found
(a provider that fails to load is logged and left out). Core code must never import a concrete
provider: `lint-imports` enforces this for the providers of this repository.

**Contract tests.** `tests/api/provider_kit.py` has `check_provider(provider_class, settings,
config, expect_records=n)`, which checks what the sync engine relies on: a plain JSON schema of
type `object`, a connection test that succeeds and says something, records of the right type, and
external ids that are unique and the same on a second read. Call it from a test of your package
with a valid configuration.
