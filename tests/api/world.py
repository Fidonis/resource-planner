"""Small builders for the planning tests. They call the real API as the caller they are given."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

import httpx
from tests.api.support import FIRST_WEEK, Caller

TEAMS = "/api/v1/teams"
SKILLS = "/api/v1/skills"
MEMBERS = "/api/v1/members"
TASKS = "/api/v1/tasks"
ASSIGNMENTS = "/api/v1/assignments"
ABSENCES = "/api/v1/absences"
TYPES = "/api/v1/absence-types"
PLANNING = "/api/v1/planning"
OVERVIEW = "/api/v1/overview"
AVAILABILITY = "/api/v1/availability"
AUDIT = "/api/v1/audit-log"


def week(offset: int = 0) -> str:
    """ISO date of the Monday ``offset`` weeks after the first Monday the tests book into."""
    return (FIRST_WEEK + timedelta(weeks=offset)).isoformat()


def day(offset: int) -> str:
    """ISO date ``offset`` days after the first Monday (0 is Monday, 3 is Thursday)."""
    return (FIRST_WEEK + timedelta(days=offset)).isoformat()


def monday_after(start: date, weeks: int) -> date:
    return start + timedelta(weeks=weeks)


async def team(admin: Caller, name: str = "Alpha") -> dict[str, Any]:
    return await admin.create(TEAMS, {"name": name, "color": "#336699"})


async def skill(admin: Caller, key: str = "Backend") -> dict[str, Any]:
    return await admin.create(SKILLS, {"key": key, "name": f"{key} work"})


async def member(
    admin: Caller,
    team_id: object,
    name: str = "Ada",
    skills: tuple[object, ...] = (),
    **extra: object,
) -> dict[str, Any]:
    body = {"teamId": team_id, "displayName": name, "skillIds": list(skills), **extra}
    return await admin.create(MEMBERS, body)


async def task(
    admin: Caller,
    key: str = "T-1",
    efforts: Mapping[object, str] | None = None,
    **extra: object,
) -> dict[str, Any]:
    """A task with ``efforts`` as ``{skill id: person-days}``."""
    body = {
        "key": key,
        "title": f"Task {key}",
        "efforts": [{"skillId": s, "personDays": float(d)} for s, d in (efforts or {}).items()],
        **extra,
    }
    return await admin.create(TASKS, body)


async def assign(
    caller: Caller,
    task_id: object,
    skill_id: object,
    member_id: object,
    when: str | None = None,
    days: float = 1.0,
    **params: Any,
) -> httpx.Response:
    return await caller.post(
        ASSIGNMENTS,
        {
            "taskId": task_id,
            "skillId": skill_id,
            "memberId": member_id,
            "weekStart": when or week(),
            "personDays": days,
        },
        params=params or None,
    )


async def booked(
    caller: Caller, task_id: object, skill_id: object, member_id: object, when: str, days: float
) -> dict[str, Any]:
    response = await assign(caller, task_id, skill_id, member_id, when, days)
    assert response.status_code in (200, 201), response.text
    body: dict[str, Any] = response.json()
    return body


async def plan(caller: Caller, weeks: int = 1, start: str | None = None, **params: Any) -> Any:
    response = await caller.get(
        PLANNING, params={"from": start or week(), "weeks": weeks, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def cell(planning: dict[str, Any], member_id: str, index: int = 0) -> dict[str, Any]:
    """The week ``index`` of a member in a planning document."""
    for t in planning["teams"]:
        for m in t["members"]:
            if m["id"] == member_id:
                found: dict[str, Any] = m["weeks"][index]
                return found
    raise AssertionError(f"member {member_id} is not in the planning")


async def audit_count(admin: Caller) -> int:
    response = await admin.get(AUDIT, params={"pageSize": 1})
    assert response.status_code == 200, response.text
    total: int = response.json()["total"]
    return total
