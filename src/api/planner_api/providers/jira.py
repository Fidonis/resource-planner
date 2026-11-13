"""Task source for Jira Cloud and Jira Data Center / Server, through the REST API version 2.

Jira Cloud signs in with the e-mail address of an account and an API token (HTTP Basic) at the
address of the site, or with a scoped API token at ``https://api.atlassian.com/ex/jira/<cloud
id>``. Jira Data Center and Server take a personal access token (Bearer). Version 2 of the API is
used on both, so that descriptions come as text and not as Atlassian Document Format.

Issues become tasks with their planning attributes (due date, start date, original estimate,
story points, priority, assignee, reporter). The effort per skill is not read from Jira: with the
mapping's default strategy ``MANUAL`` it is entered in the planner.
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar, Literal

import httpx
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from planner_api.config import Settings
from planner_api.providers.base import (
    CONNECTION,
    KIND_TASK_SOURCE,
    REMOTE_OPTIONS,
    SHOW_WHEN,
    ConfigModel,
    ExternalTask,
    FieldOption,
    ProviderBase,
    ProviderError,
    SourceProblem,
    TestResult,
    is_draft,
)

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
CHUNK = 100  # projects per query: Jira refuses a query without a bound, and very long ones
RETRIES = 4
MAX_WAIT = 30.0
# Jira finds an issue a moment after it changed, so a run reads the minutes before the last one
# again; reading an issue twice changes nothing.
OVERLAP_MINUTES = 30
DONE_CATEGORY = 3  # the id of the status category "Done", the same in every Jira
CATEGORIES = {"new": "todo", "indeterminate": "in_progress", "done": "done"}
STORY_POINT_NAMES = ("story points", "story point estimate")
START_DATE_NAMES = ("start date", "target start")
START_DATE_TYPES = ("com.atlassian.jpo:jpo-custom-field-baseline-start",)
EPIC_NAMES = ("epic",)
# The fields of an issue the planner reads, and the attribute each one fills.
SYSTEM_FIELDS = {
    "duedate": "due_date",
    "timeoriginalestimate": "original_estimate_hours",
    "priority": "priority",
    "assignee": "assignee",
    "reporter": "reporter",
}
BASE_FIELDS = ("summary", "status", "description", "labels", "updated", "project", "issuetype")


async def _sleep(seconds: float) -> None:
    """Waiting before a retry; the tests replace it."""
    await asyncio.sleep(seconds)


class JiraConfig(ConfigModel):
    deployment: Literal["CLOUD", "DATA_CENTER"] = Field(
        default="CLOUD",
        title="Jira edition",
        description="CLOUD: Jira Cloud (atlassian.net). DATA_CENTER: Jira Data Center or Server.",
        json_schema_extra={CONNECTION: True},
    )
    site_url: str = Field(
        title="Jira URL",
        description="Address of Jira, for example https://example.atlassian.net. Links to the "
        "issues point there.",
        min_length=1,
        max_length=300,
        pattern=r"^https?://",
        json_schema_extra={CONNECTION: True},
    )
    api_url: str | None = Field(
        default=None,
        title="API URL",
        description="Only for a scoped API token of Jira Cloud: "
        "https://api.atlassian.com/ex/jira/<cloud id>. Empty: the Jira URL.",
        max_length=300,
        pattern=r"^https?://",
        json_schema_extra={CONNECTION: True},
    )
    email: str | None = Field(
        default=None,
        title="E-mail address",
        description="Jira Cloud: the account the API token belongs to. Empty for Data Center.",
        max_length=255,
        validate_default=True,
        json_schema_extra={CONNECTION: True},
    )
    api_token: str = Field(
        title="API token",
        description="Jira Cloud: an API token of the account. Data Center: a personal access "
        "token.",
        min_length=1,
        max_length=2000,
        json_schema_extra={"writeOnly": True},
    )
    project_scope: Literal["SELECTED", "ALL"] = Field(
        default="SELECTED",
        title="Projects",
        description="SELECTED: only the projects chosen below. ALL: every project the account "
        "can see.",
    )
    projects: list[str] = Field(
        default_factory=list,
        title="Selected projects",
        description="Load the projects from Jira and tick the ones to import.",
        validate_default=True,
        json_schema_extra={
            REMOTE_OPTIONS: True,
            SHOW_WHEN: {"field": "projectScope", "equals": "SELECTED"},
        },
    )
    issue_types: list[str] = Field(
        default_factory=list,
        title="Issue types",
        description="Only issues of the ticked types. None ticked: every standard type, without "
        "epics and sub-tasks, so that no work is counted twice.",
        json_schema_extra={REMOTE_OPTIONS: True},
    )
    ignore_done_older_than_days: int = Field(
        default=90,
        ge=0,
        le=3650,
        title="Leave out issues done longer ago (days)",
        description="A complete read skips issues finished more than this many days ago. 0: "
        "read them all.",
    )
    jql_filter: str | None = Field(
        default=None,
        max_length=2000,
        title="Additional JQL",
        description="Added to the query with AND, for example labels = planning.",
    )
    story_points_field: str | None = Field(
        default=None,
        max_length=200,
        title="Story points field",
        description="Id or name of the field, for example customfield_10016. Empty: found by "
        "its name.",
    )
    start_date_field: str | None = Field(
        default=None,
        max_length=200,
        title="Start date field",
        description="Id or name of the field. Empty: found by its name or type.",
    )
    verify_tls: bool = Field(
        default=True,
        title="Verify the certificate",
        description="Switch off only for tests.",
        json_schema_extra={CONNECTION: True},
    )
    page_size: int = Field(default=100, ge=10, le=100, title="Page size")

    @field_validator("email")
    @classmethod
    def _cloud_needs_an_account(cls, value: str | None, info: ValidationInfo) -> str | None:
        cleaned = (value or "").strip() or None
        if info.data.get("deployment") == "CLOUD" and cleaned is None:
            raise ValueError("Jira Cloud needs the e-mail address of the account.")
        return cleaned

    @field_validator("projects")
    @classmethod
    def _something_to_import(cls, value: list[str], info: ValidationInfo) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        selected = info.data.get("project_scope") == "SELECTED"
        if selected and not cleaned and not is_draft(info.context):
            raise ValueError("Choose at least one project, or import all projects.")
        return cleaned

    @property
    def api_base(self) -> str:
        return (self.api_url or self.site_url).rstrip("/") + "/rest/api/2/"


# --- reading an issue -------------------------------------------------------------------------


def _date(value: Any, name: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError(f"The {name} '{value}' is not a date.") from exc


def _number(value: Any, name: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"The {name} '{value}' is not a number.") from exc
    return number if number.is_finite() else None


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    for pattern in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None  # the change time only helps; a strange one is not worth a refused issue
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _name(value: Any) -> str | None:
    if isinstance(value, dict):
        found = value.get("displayName") or value.get("name")
        return str(found) if found else None
    return None


def issue_to_task(
    issue: dict[str, Any],
    site_url: str,
    story_point_fields: Sequence[str],
    start_date_fields: Sequence[str],
) -> ExternalTask:
    """One issue as a task. Raises ``ValueError`` for an issue that cannot be read."""
    key = str(issue.get("key") or "")
    fields: dict[str, Any] = issue.get("fields") or {}
    title = str(fields.get("summary") or "").strip()
    if not key or not title:
        raise ValueError("The issue has no key or no summary.")
    unknown = {attr for field, attr in SYSTEM_FIELDS.items() if field not in fields}
    if not story_point_fields:
        unknown.add("story_points")
    if not start_date_fields:
        unknown.add("start_date")
    points = next(
        (
            found
            for found in (_number(fields.get(f), "story points") for f in story_point_fields)
            if found is not None
        ),
        None,
    )
    start = next(
        (
            found
            for found in (_date(fields.get(f), "start date") for f in start_date_fields)
            if found is not None
        ),
        None,
    )
    seconds = _number(fields.get("timeoriginalestimate"), "original estimate")
    status = fields.get("status") or {}
    category = (status.get("statusCategory") or {}).get("key")
    description = fields.get("description")
    return ExternalTask(
        external_id=str(issue.get("id") or key),
        key=key,
        title=title,
        status=str(status.get("name") or "open"),
        status_category=CATEGORIES.get(str(category)),
        description=description if isinstance(description, str) else None,
        url=f"{site_url.rstrip('/')}/browse/{key}",
        updated_at=_moment(fields.get("updated")),
        labels=tuple(str(label) for label in fields.get("labels") or ()),
        estimate=points,
        estimate_unit="points" if points is not None else None,
        team_hint=(fields.get("project") or {}).get("key"),
        due_date=_date(fields.get("duedate"), "due date"),
        start_date=start,
        original_estimate_hours=seconds / 3600 if seconds is not None else None,
        story_points=points,
        priority=_name(fields.get("priority")),
        assignee=_name(fields.get("assignee")),
        reporter=_name(fields.get("reporter")),
        unknown_attributes=frozenset(unknown),
    )


def build_jql(
    project_ids: Sequence[str],
    issue_type_ids: Sequence[str],
    extra: str | None,
    *,
    since_minutes: int | None,
    done_days: int,
) -> str:
    """The query of one block of projects.

    Only a complete read (``since_minutes`` None) leaves out long finished issues: a run that
    reads changes must see an issue that has just been finished.
    """
    parts = [f"project in ({', '.join(project_ids)})"]
    if issue_type_ids:
        parts.append(f"issuetype in ({', '.join(issue_type_ids)})")
    if extra and extra.strip():
        parts.append(f"({extra.strip()})")
    if since_minutes is not None:
        parts.append(f'updated >= "-{since_minutes}m"')
    elif done_days:
        parts.append(f'(statusCategory != {DONE_CATEGORY} OR updated >= "-{done_days}d")')
    return " AND ".join(parts) + " ORDER BY id ASC"


def find_fields(
    fields: Iterable[dict[str, Any]],
    configured: str | None,
    names: Sequence[str],
    types: Sequence[str] = (),
) -> list[str]:
    """Ids of the fields meant: the configured one, or those found by type or name."""
    rows = list(fields)
    if configured and configured.strip():
        wanted = configured.strip().lower()
        for row in rows:
            labels = {str(row.get(k) or "").lower() for k in ("id", "name", "untranslatedName")}
            if wanted in labels:
                return [str(row["id"])]
        raise ProviderError(f"Jira has no field '{configured.strip()}'.")
    found: list[str] = []
    for row in rows:
        kind = str((row.get("schema") or {}).get("custom") or "")
        label = str(row.get("untranslatedName") or row.get("name") or "").strip().lower()
        if kind in types or label in names:
            found.append(str(row["id"]))
    return found


def standard_types(types: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The issue types of the level of stories and bugs: no epics, no sub-tasks."""
    found = []
    for row in types:
        level = row.get("hierarchyLevel")
        if level is not None:
            if int(level) == 0:
                found.append(row)
            continue
        label = str(row.get("untranslatedName") or row.get("name") or "").strip().lower()
        if not row.get("subtask") and label not in EPIC_NAMES:
            found.append(row)
    return found


def _wait(answer: httpx.Response, attempt: int) -> float:
    after = answer.headers.get("Retry-After")
    if after:
        try:
            return min(MAX_WAIT, max(0.0, float(after)))
        except ValueError:
            pass
    return min(MAX_WAIT, math.pow(2, attempt) * random.uniform(0.7, 1.3))


def _jira_messages(answer: httpx.Response) -> str:
    """What Jira says about a refused query (a JQL mistake), short; never the request."""
    try:
        body = answer.json()
    except ValueError:
        return ""
    messages = [str(m) for m in (body.get("errorMessages") or []) if m]
    messages += [str(m) for m in (body.get("errors") or {}).values() if m]
    return " ".join(messages)[:300]


class _Jira:
    """Requests against one Jira, with the answers turned into data or a ``ProviderError``."""

    def __init__(self, client: httpx.AsyncClient, config: JiraConfig) -> None:
        self.client = client
        self.config = config

    async def call(self, method: str, path: str, **kwargs: Any) -> Any:
        answer: httpx.Response | None = None
        for attempt in range(RETRIES + 1):
            try:
                answer = await self.client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise ProviderError(f"Jira cannot be reached ({type(exc).__name__}).") from exc
            if answer.status_code in (429, 503) and attempt < RETRIES:
                await _sleep(_wait(answer, attempt))
                continue
            break
        assert answer is not None
        status = answer.status_code
        if status == 401 or answer.headers.get("X-AUSERNAME", "").lower() == "anonymous":
            if self.config.deployment == "CLOUD":
                raise ProviderError("Jira refused the e-mail address or the API token.")
            raise ProviderError("Jira refused the personal access token.")
        if status == 403:
            if "denied" in answer.headers.get("X-Seraph-LoginReason", "").lower():
                raise ProviderError(
                    "Jira wants the account to sign in once in the browser (a CAPTCHA)."
                )
            raise ProviderError(f"The account may not read '{path}' in Jira.")
        if status == 404:
            raise ProviderError(f"Jira has no '{path}' at this address: check the Jira URL.")
        if status == 400:
            said = _jira_messages(answer)
            raise ProviderError(f"Jira refused the query{': ' + said if said else '.'}")
        if status in (429, 503):
            raise ProviderError("Jira is busy: try again later.")
        if status >= 300:
            raise ProviderError(f"Jira answered {status} for '{path}'.")
        try:
            return answer.json()
        except ValueError as exc:
            raise ProviderError(f"Jira sent no JSON for '{path}': check the Jira URL.") from exc

    async def projects(self) -> list[dict[str, Any]]:
        if self.config.deployment == "DATA_CENTER":
            found = await self.call("GET", "project")
            return [p for p in found if isinstance(p, dict) and p.get("id")]
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            page = await self.call(
                "GET", "project/search", params={"startAt": start, "maxResults": 50}
            )
            values = [p for p in page.get("values") or [] if isinstance(p, dict) and p.get("id")]
            rows.extend(values)
            if page.get("isLast", True) or not values:
                return rows
            start += len(values)

    async def fields(self) -> list[dict[str, Any]]:
        found = await self.call("GET", "field")
        return [f for f in found if isinstance(f, dict) and f.get("id")]

    async def issue_types(self) -> list[dict[str, Any]]:
        found = await self.call("GET", "issuetype")
        return [t for t in found if isinstance(t, dict) and t.get("id")]

    async def search(self, jql: str, fields: Sequence[str]) -> AsyncIterator[dict[str, Any]]:
        size = self.config.page_size
        if self.config.deployment == "DATA_CENTER":
            start = 0
            while True:
                page = await self.call(
                    "POST",
                    "search",
                    json={"jql": jql, "fields": list(fields), "startAt": start, "maxResults": size},
                )
                issues = page.get("issues") or []
                for issue in issues:
                    yield issue
                start += len(issues)
                if not issues or start >= int(page.get("total") or 0):
                    return
        token: str | None = None
        while True:
            body: dict[str, Any] = {"jql": jql, "fields": list(fields), "maxResults": size}
            if token:
                body["nextPageToken"] = token
            page = await self.call("POST", "search/jql", json=body)
            for issue in page.get("issues") or []:
                yield issue
            token = page.get("nextPageToken")
            if page.get("isLast", True) or not token:
                return


class JiraTaskSource(ProviderBase):
    key: ClassVar[str] = "jira"
    kind: ClassVar[str] = KIND_TASK_SOURCE
    title: ClassVar[str] = "Jira"
    config_model: ClassVar[type[BaseModel]] = JiraConfig
    supports_since: ClassVar[bool] = True
    # Tests put a mock transport here.
    transport: ClassVar[httpx.AsyncBaseTransport | None] = None

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @asynccontextmanager
    async def _jira(self, config: JiraConfig) -> AsyncIterator[_Jira]:
        headers = {"Accept": "application/json"}
        auth: httpx.Auth | None = None
        if config.deployment == "CLOUD":
            auth = httpx.BasicAuth(config.email or "", config.api_token)
        else:
            headers["Authorization"] = f"Bearer {config.api_token}"
        async with httpx.AsyncClient(
            base_url=config.api_base,
            headers=headers,
            auth=auth,
            timeout=TIMEOUT,
            verify=config.verify_tls,
            transport=self.transport,
        ) as client:
            yield _Jira(client, config)

    # --- the provider interface ------------------------------------------------------------

    async def test_connection(self, config: BaseModel) -> TestResult:
        assert isinstance(config, JiraConfig)
        try:
            async with self._jira(config) as jira:
                me = await jira.call("GET", "myself")
                projects = await jira.projects()
                fields = await jira.fields()
                points = find_fields(fields, config.story_points_field, STORY_POINT_NAMES)
                starts = find_fields(
                    fields, config.start_date_field, START_DATE_NAMES, START_DATE_TYPES
                )
        except ProviderError as exc:
            return TestResult(False, str(exc))
        who = me.get("displayName") or me.get("name") or "an account without a name"
        parts = [f"Connected as {who}; {len(projects)} projects visible."]
        parts.append(
            f"Story points: {', '.join(points)}." if points else "Story points: no field found."
        )
        parts.append(
            f"Start date: {', '.join(starts)}." if starts else "Start date: no field found."
        )
        details = {"projects": len(projects), "storyPoints": points, "startDate": starts}
        if config.project_scope == "SELECTED":
            visible = {str(p["id"]) for p in projects}
            hidden = [p for p in config.projects if p not in visible]
            if hidden:
                parts.append(f"Not visible to the account: project {', '.join(hidden)}.")
                return TestResult(False, " ".join(parts), details)
        return TestResult(True, " ".join(parts), details)

    async def field_options(self, config: BaseModel, field: str) -> list[FieldOption]:
        assert isinstance(config, JiraConfig)
        async with self._jira(config) as jira:
            if field == "projects":
                rows = sorted(await jira.projects(), key=lambda p: str(p.get("key") or ""))
                return [
                    FieldOption(str(p["id"]), f"{p.get('key')} — {p.get('name') or p.get('key')}")
                    for p in rows
                ]
            if field == "issueTypes":
                rows = await jira.issue_types()
                standard = {str(t["id"]) for t in standard_types(rows)}
                names = [str(t.get("name") or t["id"]) for t in rows]
                options = []
                for row, name in zip(rows, names, strict=True):
                    label = name if names.count(name) == 1 else f"{name} ({row['id']})"
                    if str(row["id"]) not in standard:
                        label += " (sub-task)" if row.get("subtask") else " (above stories)"
                    options.append(FieldOption(str(row["id"]), label))
                return sorted(options, key=lambda o: o.label.lower())
        raise ProviderError(f"There are no choices for '{field}'.")

    async def fetch_tasks(
        self, config: BaseModel, since: datetime | None
    ) -> AsyncIterator[ExternalTask | SourceProblem]:
        assert isinstance(config, JiraConfig)
        async with self._jira(config) as jira:
            fields = await jira.fields()
            points = find_fields(fields, config.story_points_field, STORY_POINT_NAMES)
            starts = find_fields(
                fields, config.start_date_field, START_DATE_NAMES, START_DATE_TYPES
            )
            visible = [str(p["id"]) for p in await jira.projects()]
            if config.project_scope == "ALL":
                wanted = visible
            else:
                wanted = [p for p in config.projects if p in visible]
                for hidden in (p for p in config.projects if p not in visible):
                    # Reported, so that this run closes nothing: a project that lost its
                    # permission or was archived must not cancel its tasks.
                    yield SourceProblem(
                        f"project {hidden}",
                        "The project is not visible to the account (removed, archived or no "
                        "permission).",
                    )
            types = list(config.issue_types)
            if not types:
                types = [str(t["id"]) for t in standard_types(await jira.issue_types())]
            minutes = None
            if since is not None:
                elapsed = (datetime.now(UTC) - since).total_seconds() / 60
                minutes = max(0, math.ceil(elapsed)) + OVERLAP_MINUTES
            wanted_fields = [*BASE_FIELDS, *SYSTEM_FIELDS, *points, *starts]
            for block in range(0, len(wanted), CHUNK):
                jql = build_jql(
                    wanted[block : block + CHUNK],
                    types,
                    config.jql_filter,
                    since_minutes=minutes,
                    done_days=config.ignore_done_older_than_days,
                )
                async for issue in jira.search(jql, wanted_fields):
                    try:
                        yield issue_to_task(issue, config.site_url, points, starts)
                    except ValueError as exc:
                        yield SourceProblem(str(issue.get("key") or issue.get("id")), str(exc))
