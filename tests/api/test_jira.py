"""The Jira task source against a mock Jira, Cloud and Data Center (§9.3, P2b)."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from tests.api.provider_kit import check_provider
from tests.api.support import Api, Caller
from tests.api.sync_support import INTEGRATIONS, counts, sync

from planner_api.config import Settings
from planner_api.providers import jira
from planner_api.providers.base import ProviderError, SourceProblem
from planner_api.providers.jira import (
    JiraConfig,
    JiraTaskSource,
    build_jql,
    find_fields,
    issue_to_task,
    standard_types,
)

SITE = "https://example.atlassian.test"
DC_SITE = "https://jira.example.test"
TASKS = "/api/v1/tasks"

PROJECTS = [
    {"id": "10000", "key": "WEB", "name": "Web shop"},
    {"id": "10001", "key": "APP", "name": "App"},
]
CLOUD_FIELDS = [
    {"id": "summary", "name": "Summary", "custom": False, "schema": {"system": "summary"}},
    {
        "id": "customfield_10016",
        "name": "Schätzung der Story-Points",
        "untranslatedName": "Story point estimate",
        "custom": True,
        "schema": {"custom": "com.pyxis.greenhopper.jira:jsw-story-points"},
    },
    {
        "id": "customfield_10015",
        "name": "Start date",
        "custom": True,
        "schema": {"custom": "com.atlassian.jira.plugin.system.customfieldtypes:datepicker"},
    },
]
DC_FIELDS = [
    {"id": "customfield_10002", "name": "Story Points", "custom": True, "schema": {}},
    {
        "id": "customfield_12000",
        "name": "Geplanter Start",
        "custom": True,
        "schema": {"custom": "com.atlassian.jpo:jpo-custom-field-baseline-start"},
    },
]
CLOUD_TYPES = [
    {"id": "1", "name": "Story", "hierarchyLevel": 0, "subtask": False},
    {"id": "2", "name": "Bug", "hierarchyLevel": 0, "subtask": False},
    {"id": "3", "name": "Epic", "hierarchyLevel": 1, "subtask": False},
    {"id": "4", "name": "Sub-task", "hierarchyLevel": -1, "subtask": True},
]
DC_TYPES = [
    {"id": "10", "name": "Story", "subtask": False},
    {"id": "11", "name": "Epic", "subtask": False},
    {"id": "12", "name": "Sub-task", "subtask": True},
]


def issue(number: int, project: str = "WEB", **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": f"Story {number}",
        "status": {"name": "In Arbeit", "statusCategory": {"key": "indeterminate"}},
        "description": "Plain *wiki* text",
        "labels": ["Backend"],
        "updated": "2026-11-10T09:15:30.000+0100",
        "project": {"key": project},
        "issuetype": {"id": "1"},
        "duedate": "2026-12-18",
        "priority": {"name": "High"},
        "assignee": {"displayName": "Alex Example"},
        "reporter": {"displayName": "Sam Example"},
        "timeoriginalestimate": 57600,
        "customfield_10016": 5.0,
        "customfield_10015": "2026-12-01",
        "customfield_10002": 3,
        "customfield_12000": "2026-11-30",
    }
    base.update(fields)
    offset = 20000 if project == "WEB" else 30000  # issue ids are unique across projects
    return {"id": str(offset + number), "key": f"{project}-{number}", "fields": base}


@dataclass
class FakeJira:
    """A Jira that answers what the provider asks, page by page, and remembers the queries."""

    edition: str = "CLOUD"
    issues: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=lambda: list(PROJECTS))
    page: int = 2  # fewer than asked for: the provider has to follow the pages
    busy: int = 0  # answers 429 that many times first
    anonymous: bool = False
    queries: list[dict[str, Any]] = field(default_factory=list)

    def authorised(self, request: httpx.Request) -> bool:
        given = str(request.headers.get("Authorization", ""))
        if self.edition == "CLOUD":
            token = base64.b64encode(b"ada@example.test:right").decode()
            return given == f"Basic {token}"
        return given == "Bearer right"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.busy:
            self.busy -= 1
            return httpx.Response(429, headers={"Retry-After": "2"})
        if not self.authorised(request):
            return httpx.Response(401, json={"errorMessages": ["no"]})
        headers = {"X-AUSERNAME": "anonymous"} if self.anonymous else {}
        path = request.url.path.split("/rest/api/2/", 1)[-1]
        cloud = self.edition == "CLOUD"
        if path == "myself":
            return httpx.Response(200, json={"displayName": "Ada Admin"}, headers=headers)
        if path == "field":
            return httpx.Response(200, json=CLOUD_FIELDS if cloud else DC_FIELDS)
        if path == "issuetype":
            return httpx.Response(200, json=CLOUD_TYPES if cloud else DC_TYPES)
        if path == "project" and not cloud:
            return httpx.Response(200, json=self.projects)
        if path == "project/search" and cloud:
            start = int(request.url.params.get("startAt", "0"))
            values = self.projects[start : start + 1]  # one per page
            last = start + 1 >= len(self.projects)
            return httpx.Response(200, json={"values": values, "isLast": last})
        if (path == "search/jql" and cloud) or (path == "search" and not cloud):
            body = json.loads(request.content)
            self.queries.append(body)
            if "bad" in body["jql"]:
                return httpx.Response(400, json={"errorMessages": ["Field 'bad' does not exist."]})
            keys = {p["key"] for p in self.projects if p["id"] in body["jql"]}
            found = [i for i in self.issues if i["fields"]["project"]["key"] in keys]
            if cloud:
                start = int(body.get("nextPageToken") or 0)
                chunk = found[start : start + self.page]
                more = start + self.page < len(found)
                answer: dict[str, Any] = {"issues": chunk, "isLast": not more}
                if more:
                    answer["nextPageToken"] = str(start + self.page)
                return httpx.Response(200, json=answer, headers=headers)
            start = int(body["startAt"])
            chunk = found[start : start + self.page]
            return httpx.Response(
                200, json={"issues": chunk, "total": len(found), "startAt": start}
            )
        return httpx.Response(404)


def config(edition: str = "CLOUD", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "deployment": edition,
        "siteUrl": SITE if edition == "CLOUD" else DC_SITE,
        "apiToken": "right",
        "projectScope": "SELECTED",
        "projects": ["10000"],
    }
    if edition == "CLOUD":
        base["email"] = "ada@example.test"
    return {**base, **extra}


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """No real waiting between retries; the waits are recorded."""
    seen: list[float] = []

    async def no_sleep(seconds: float) -> None:
        seen.append(seconds)

    monkeypatch.setattr(jira, "_sleep", no_sleep)
    return seen


def serve(monkeypatch: pytest.MonkeyPatch, fake: FakeJira) -> FakeJira:
    monkeypatch.setattr(JiraTaskSource, "transport", httpx.MockTransport(fake), raising=False)
    return fake


async def read(cfg: dict[str, Any], since: datetime | None = None) -> list[Any]:
    provider = JiraTaskSource(Settings())
    model = JiraConfig.model_validate(cfg)
    return [item async for item in provider.fetch_tasks(model, since)]


# --- the parts ---------------------------------------------------------------------------------


def test_the_query_bounds_projects_types_and_finished_issues() -> None:
    full = build_jql(
        ["10000", "10001"], ["1", "2"], " labels = x ", since_minutes=None, done_days=90
    )
    assert full == (
        "project in (10000, 10001) AND issuetype in (1, 2) AND (labels = x) AND "
        '(statusCategory != 3 OR updated >= "-90d") ORDER BY id ASC'
    )
    changes = build_jql(["10000"], [], None, since_minutes=95, done_days=90)
    assert changes == 'project in (10000) AND updated >= "-95m" ORDER BY id ASC'
    everything = build_jql(["10000"], [], "", since_minutes=None, done_days=0)
    assert everything == "project in (10000) ORDER BY id ASC"


def test_fields_are_found_by_type_or_untranslated_name_or_named() -> None:
    assert find_fields(CLOUD_FIELDS, None, jira.STORY_POINT_NAMES) == ["customfield_10016"]
    assert find_fields(DC_FIELDS, None, jira.STORY_POINT_NAMES) == ["customfield_10002"]
    assert find_fields(DC_FIELDS, None, jira.START_DATE_NAMES, jira.START_DATE_TYPES) == [
        "customfield_12000"
    ]
    assert find_fields(CLOUD_FIELDS, "start DATE", ()) == ["customfield_10015"]
    assert find_fields(CLOUD_FIELDS, "customfield_10016", ()) == ["customfield_10016"]
    assert find_fields(DC_FIELDS, None, jira.START_DATE_NAMES) == []
    with pytest.raises(ProviderError, match="no field 'Effort'"):
        find_fields(CLOUD_FIELDS, "Effort", ())


def test_standard_issue_types_leave_out_epics_and_sub_tasks() -> None:
    assert [t["id"] for t in standard_types(CLOUD_TYPES)] == ["1", "2"]
    assert [t["id"] for t in standard_types(DC_TYPES)] == ["10"]


def test_an_issue_becomes_a_task_with_its_planning_attributes() -> None:
    task = issue_to_task(issue(1), SITE + "/", ["customfield_10016"], ["customfield_10015"])
    assert (task.external_id, task.key, task.title) == ("20001", "WEB-1", "Story 1")
    assert task.url == f"{SITE}/browse/WEB-1"
    assert (task.status, task.status_category) == ("In Arbeit", "in_progress")
    assert task.team_hint == "WEB" and task.labels == ("Backend",)
    assert task.description == "Plain *wiki* text"
    assert task.updated_at == datetime(2026, 11, 10, 8, 15, 30, tzinfo=UTC)
    assert (task.due_date, task.start_date) == (date(2026, 12, 18), date(2026, 12, 1))
    assert task.original_estimate_hours == Decimal(16)
    assert task.story_points == Decimal(5) and task.estimate == Decimal(5)
    assert (task.priority, task.assignee, task.reporter) == ("High", "Alex Example", "Sam Example")
    assert task.unknown_attributes == frozenset()


def test_what_jira_does_not_send_is_unknown_and_bad_values_are_refused() -> None:
    raw = issue(2, assignee=None, description={"type": "doc"})
    for name in ("duedate", "timeoriginalestimate", "priority"):
        del raw["fields"][name]
    task = issue_to_task(raw, SITE, [], ["customfield_10015"])
    assert task.assignee is None and task.description is None
    assert task.unknown_attributes == frozenset(
        {"due_date", "original_estimate_hours", "priority", "story_points"}
    )
    with pytest.raises(ValueError, match="due date 'soon'"):
        issue_to_task(issue(3, duedate="soon"), SITE, [], [])
    with pytest.raises(ValueError, match="no key or no summary"):
        issue_to_task(issue(4, summary=" "), SITE, [], [])


def test_the_settings_need_an_account_on_cloud_and_a_project_when_selecting() -> None:
    with pytest.raises(ValueError, match="e-mail address"):
        JiraConfig.model_validate({**config(), "email": ""})
    JiraConfig.model_validate(config("DATA_CENTER"))
    with pytest.raises(ValueError, match="at least one project"):
        JiraConfig.model_validate(config(projects=[]))
    JiraConfig.model_validate(config(projects=[]), context={"draft": True})
    JiraConfig.model_validate(config(projectScope="ALL", projects=[]))
    assert JiraConfig.model_validate(
        config(apiUrl="https://api.example.test/ex/jira/1/")
    ).api_base == ("https://api.example.test/ex/jira/1/rest/api/2/")


# --- against the mock Jira ---------------------------------------------------------------------


@pytest.mark.parametrize("edition", ["CLOUD", "DATA_CENTER"])
async def test_the_provider_keeps_the_contract(
    edition: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = serve(
        monkeypatch,
        FakeJira(edition, issues=[issue(1), issue(2), issue(3), issue(1, "APP")]),
    )
    found = await check_provider(JiraTaskSource, Settings(), config(edition), expect_records=3)
    assert [t.key for t in found] == ["WEB-1", "WEB-2", "WEB-3"]
    jql = fake.queries[0]["jql"]
    types = "1, 2" if edition == "CLOUD" else "10"
    assert jql.startswith(f"project in (10000) AND issuetype in ({types}) AND (statusCategory")
    points = "customfield_10016" if edition == "CLOUD" else "customfield_10002"
    assert points in fake.queries[0]["fields"] and "duedate" in fake.queries[0]["fields"]
    tasks = await read(config(edition))
    expected = Decimal(5) if edition == "CLOUD" else Decimal(3)
    assert tasks[0].story_points == expected
    start = date(2026, 12, 1) if edition == "CLOUD" else date(2026, 11, 30)
    assert tasks[0].start_date == start


async def test_all_projects_and_a_run_that_reads_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = serve(monkeypatch, FakeJira(issues=[issue(1), issue(1, "APP")]))
    tasks = await read(config(projectScope="ALL", projects=[]))
    assert {t.key for t in tasks} == {"WEB-1", "APP-1"}
    assert "project in (10000, 10001)" in fake.queries[-1]["jql"]

    await read(config(), since=datetime.now(UTC) - timedelta(minutes=10))
    jql = fake.queries[-1]["jql"]
    # ten minutes and a little, rounded up, plus the overlap
    assert re.search(r'updated >= "-4[01]m"', jql) and "statusCategory" not in jql


async def test_a_selected_project_that_is_gone_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, FakeJira(issues=[issue(1)]))
    found = await read(config(projects=["10000", "99999"]))
    problems = [f for f in found if isinstance(f, SourceProblem)]
    assert len(problems) == 1 and problems[0].reference == "project 99999"
    assert [f.key for f in found if not isinstance(f, SourceProblem)] == ["WEB-1"]


async def test_a_field_that_was_named_but_does_not_exist_stops_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, FakeJira(issues=[issue(1)]))
    with pytest.raises(ProviderError, match="no field 'Effort'"):
        await read(config(storyPointsField="Effort"))
    result = await JiraTaskSource(Settings()).test_connection(
        JiraConfig.model_validate(config(storyPointsField="Effort"))
    )
    assert result.ok is False and "Effort" in result.message


async def test_the_connection_test_says_what_it_found(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, FakeJira("DATA_CENTER"))
    provider = JiraTaskSource(Settings())
    good = await provider.test_connection(JiraConfig.model_validate(config("DATA_CENTER")))
    assert good.ok, good.message
    assert "Ada Admin" in good.message and "2 projects" in good.message
    assert "customfield_10002" in good.message
    hidden = await provider.test_connection(
        JiraConfig.model_validate(config("DATA_CENTER", projects=["99999"]))
    )
    assert hidden.ok is False and "99999" in hidden.message


@pytest.mark.parametrize(
    ("fake", "settings", "message"),
    [
        (FakeJira(), {"apiToken": "wrong"}, "refused the e-mail address or the API token"),
        (FakeJira("DATA_CENTER"), {"apiToken": "wrong"}, "refused the personal access token"),
        (FakeJira(anonymous=True), {}, "refused"),
        (FakeJira(), {"jqlFilter": "bad = 1"}, "Field 'bad' does not exist."),
    ],
)
async def test_refusals_are_reported_without_the_secret(
    fake: FakeJira,
    settings: dict[str, Any],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake.issues = [issue(1)]
    serve(monkeypatch, fake)
    with pytest.raises(ProviderError) as caught:
        await read(config(fake.edition, **settings))
    assert message in str(caught.value) and "wrong" not in str(caught.value)


async def test_a_busy_jira_is_asked_again(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    serve(monkeypatch, FakeJira(issues=[issue(1)], busy=2))
    assert [t.key for t in await read(config())] == ["WEB-1"]
    assert waits == [2.0, 2.0]
    serve(monkeypatch, FakeJira(issues=[issue(1)], busy=9))
    with pytest.raises(ProviderError, match="busy"):
        await read(config())


async def test_projects_and_issue_types_are_offered_as_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, FakeJira())
    provider = JiraTaskSource(Settings())
    draft = JiraConfig.model_validate(config(projects=[]), context={"draft": True})
    projects = await provider.field_options(draft, "projects")
    assert [(o.value, o.label) for o in projects] == [
        ("10001", "APP — App"),
        ("10000", "WEB — Web shop"),
    ]
    types = {o.value: o.label for o in await provider.field_options(draft, "issueTypes")}
    assert types == {
        "1": "Story",
        "2": "Bug",
        "3": "Epic (above stories)",
        "4": "Sub-task (sub-task)",
    }


# --- through the API ---------------------------------------------------------------------------


async def jira_integration(admin: Caller, **settings: Any) -> dict[str, Any]:
    return await admin.create(
        INTEGRATIONS,
        {
            "name": "Jira",
            "type": "task_source",
            "providerKey": "jira",
            "config": config(**settings),
            "mapping": {"teamMap": {"WEB": "Web"}},
            "enabled": True,
        },
    )


async def tasks_by_key(admin: Caller) -> dict[str, dict[str, Any]]:
    items = (await admin.get(TASKS, params={"pageSize": 200})).json()["items"]
    return {t["key"]: t for t in items}


async def test_a_sync_brings_in_issues_with_their_values_and_keeps_efforts_here(
    sync_api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = serve(
        monkeypatch,
        FakeJira(
            issues=[
                issue(1),
                issue(
                    2,
                    status={"name": "Fertig", "statusCategory": {"key": "done"}},
                    customfield_10016=None,
                ),
                issue(3, duedate="soon"),
                issue(1, "APP"),
            ]
        ),
    )
    admin = await sync_api.admin()
    backend = await admin.create("/api/v1/skills", {"key": "Backend", "name": "Backend"})
    await admin.create("/api/v1/teams", {"name": "Web"})
    made = await jira_integration(admin, projects=["10000", "10001"])

    first = await sync(admin, made["id"])
    assert first["status"] == "partial" and counts(first) == (3, 0, 0, 1)
    assert any("due date 'soon'" in e["message"] for e in first["log"])
    assert "right" not in json.dumps(first)

    found = await tasks_by_key(admin)
    web = found["WEB-1"]
    assert web["sourceName"] == "Jira" and web["ownerTeamName"] == "Web"
    assert web["externalUrl"] == f"{SITE}/browse/WEB-1"
    assert (web["dueDate"], web["startDate"]) == ("2026-12-18", "2026-12-01")
    assert (web["originalEstimateHours"], web["storyPoints"]) == (16, 5)
    assert (web["priority"], web["assignee"], web["reporter"]) == (
        "High",
        "Alex Example",
        "Sam Example",
    )
    assert web["status"] == "open" and found["WEB-2"]["status"] == "done"
    assert web["efforts"] == [] and web["managedFields"] == ["key", "title", "status"]

    # the second run reads only changes and finds nothing new
    second = await sync(admin, made["id"])
    assert counts(second)[:3] == (0, 0, 3)
    assert 'updated >= "-' in fake.queries[-1]["jql"]

    # the title belongs to Jira, the effort to the planner
    locked = await admin.put(
        f"{TASKS}/{web['id']}",
        {"version": web["version"], "key": "WEB-1", "title": "Mine", "status": "open"},
    )
    assert locked.status_code == 409 and locked.json()["code"] == "FIELD_LOCKED"
    effort = await admin.put(
        f"{TASKS}/{web['id']}/efforts",
        {"version": web["version"], "efforts": [{"skillId": backend["id"], "personDays": 4}]},
    )
    assert effort.status_code == 200, effort.text

    # a project that is no longer selected: the next run reads everything and cancels its tasks
    stored = (await admin.get(f"{INTEGRATIONS}/{made['id']}")).json()
    fake.issues = [i for i in fake.issues if i["key"] != "WEB-3"]
    changed = await admin.put(
        f"{INTEGRATIONS}/{made['id']}",
        {
            "version": stored["version"],
            "name": "Jira",
            "enabled": True,
            "schedule": None,
            "config": {**stored["config"], "projects": ["10001"]},
            "mapping": stored["mapping"],
        },
    )
    assert changed.status_code == 200 and changed.json()["lastSyncAt"] is None
    third = await sync(admin, made["id"])
    assert third["full"] is True and third["status"] == "success", third["log"]
    found = await tasks_by_key(admin)
    assert found["WEB-1"]["status"] == "cancelled" and found["APP-1"]["status"] == "open"
    assert found["WEB-1"]["efforts"][0]["personDays"] == 4


async def test_choices_come_from_jira_for_a_draft_and_a_stored_integration(
    sync_api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, FakeJira())
    admin = await sync_api.admin()
    ask = {"type": "task_source", "providerKey": "jira", "field": "projects"}
    draft = await admin.post(
        f"{INTEGRATIONS}/field-options", {**ask, "config": config(projects=[])}
    )
    assert draft.status_code == 200, draft.text
    assert [i["value"] for i in draft.json()["items"]] == ["10001", "10000"]

    made = await jira_integration(admin)
    stored = (await admin.get(f"{INTEGRATIONS}/{made['id']}")).json()
    assert "apiToken" not in stored["config"] and stored["secretsSet"] == ["apiToken"]
    again = await admin.post(
        f"{INTEGRATIONS}/field-options",
        {**ask, "config": stored["config"], "integrationId": made["id"]},
    )
    assert again.status_code == 200, again.text
    moved = await admin.post(
        f"{INTEGRATIONS}/field-options",
        {
            **ask,
            "config": {**stored["config"], "siteUrl": "https://elsewhere.test"},
            "integrationId": made["id"],
        },
    )
    assert moved.status_code == 422 and moved.json()["errors"][0]["code"] == "secret_needed"

    wrong = await admin.post(
        f"{INTEGRATIONS}/field-options",
        {**ask, "config": config(apiToken="wrong", projects=[])},
    )
    assert wrong.status_code == 422 and wrong.json()["code"] == "PROVIDER_ERROR"
