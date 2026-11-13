"""NFR-PERF: the planning board of a large installation answers within a second."""

from __future__ import annotations

import time
from datetime import timedelta
from decimal import Decimal

from tests.api.support import FIRST_WEEK, Api
from tests.api.world import OVERVIEW, PLANNING

from planner_api.models import (
    Assignment,
    Member,
    MemberSkill,
    Skill,
    Task,
    TaskEffort,
    Team,
)

TEAMS, SKILLS, MEMBERS, TASKS = 10, 20, 500, 2000
BUDGET_SECONDS = 1.0


async def fill(api: Api) -> None:
    async with api.factory() as session:
        teams = [Team(name=f"Team {n:02}") for n in range(TEAMS)]
        skills = [Skill(key=f"Skill {n:02}", name=f"Skill {n:02}") for n in range(SKILLS)]
        session.add_all([*teams, *skills])
        await session.flush()
        members = [
            Member(
                team_id=teams[n % TEAMS].id,
                display_name=f"Member {n:03}",
                allocation_percent=60 + n % 5 * 10,
                skill_links=[MemberSkill(skill_id=skills[(n + k) % SKILLS].id) for k in range(3)],
            )
            for n in range(MEMBERS)
        ]
        tasks = [
            Task(
                key=f"T-{n:04}",
                title=f"Task {n}",
                owner_team_id=teams[n % TEAMS].id,
                locked_fields=[],
                efforts=[
                    TaskEffort(skill_id=skills[(n + k) % SKILLS].id, person_days=Decimal(1 + k))
                    for k in range(2)
                ],
            )
            for n in range(TASKS)
        ]
        session.add_all([*members, *tasks])
        await session.flush()
        session.add_all(
            Assignment(
                task_id=tasks[n].id,
                skill_id=skills[n % SKILLS].id,
                member_id=members[n % MEMBERS].id,
                week_start=FIRST_WEEK + timedelta(weeks=n % 4),
                person_days=Decimal("1.5"),
            )
            for n in range(TASKS)
        )
        await session.commit()


async def test_the_planning_board_of_500_members_and_2000_tasks_loads_within_a_second(
    api: Api,
) -> None:
    await fill(api)
    admin = await api.admin()
    params = {"from": FIRST_WEEK.isoformat(), "weeks": 4}
    await admin.get(PLANNING, params=params)  # warm the connection pool and the caches

    started = time.perf_counter()
    response = await admin.get(PLANNING, params=params)
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    board = response.json()
    assert sum(len(t["members"]) for t in board["teams"]) == MEMBERS
    assert len(board["tasks"]) == TASKS
    assert elapsed < BUDGET_SECONDS, f"the board took {elapsed:.2f} s"


async def test_the_overview_of_the_same_installation_is_just_as_quick(api: Api) -> None:
    await fill(api)
    admin = await api.admin()
    params = {"from": FIRST_WEEK.isoformat(), "weeks": 4}
    await admin.get(OVERVIEW, params=params)

    started = time.perf_counter()
    response = await admin.get(OVERVIEW, params=params)
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    assert response.json()["kpis"]["demand"] > 0
    assert elapsed < BUDGET_SECONDS, f"the overview took {elapsed:.2f} s"
