"""§4 rules 5 to 10, acceptance case 6, DM-05 and DM-06."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from planner_api.domain.planning import (
    BOTTLENECK,
    STATUS_OK,
    TIGHT,
    AssignmentIn,
    MemberIn,
    Overview,
    TaskIn,
    booked_index,
    bottleneck_status,
    build_availability,
    build_overview,
    floor_half,
    member_week,
    planned_percent,
    suggest_person_days,
    task_skill_status,
)

W1 = date(2026, 3, 2)
W2 = date(2026, 3, 9)
TEAM = uuid.uuid4()
BACKEND = uuid.uuid4()
QA = uuid.uuid4()


def person(
    name: str, skills: set[uuid.UUID], *, percent: int = 100, active: bool = True
) -> MemberIn:
    return MemberIn(uuid.uuid4(), TEAM, name, percent, active, frozenset(skills))


def booking(
    task: uuid.UUID, skill: uuid.UUID, member: MemberIn, week: date, pd: str, status: str = "open"
) -> AssignmentIn:
    return AssignmentIn(uuid.uuid4(), task, skill, member.id, week, Decimal(pd), status)


def test_acceptance_6_overbooking_is_flagged_not_blocked() -> None:
    m = person("Anna", {BACKEND}, percent=80)  # capacity 4.0
    task = uuid.uuid4()
    booked = booked_index([booking(task, BACKEND, m, W1, "5")])
    week = member_week(m, W1, [], booked[(m.id, W1)])
    assert week.capacity == Decimal("4.00")
    assert week.booked == Decimal("5.00")
    assert week.overbooked is True
    assert week.free == Decimal("-1.00")


def test_full_but_not_overbooked() -> None:
    m = person("Anna", {BACKEND}, percent=80)
    week = member_week(m, W1, [], Decimal("4"))
    assert week.full is True
    assert week.overbooked is False


def test_zero_capacity_week_is_not_full() -> None:
    m = person("Anna", {BACKEND})
    week = member_week(m, W1, [], Decimal(0))
    assert week.capacity == Decimal("5.00")
    assert week.full is False


def test_cancelled_work_does_not_count_as_booked() -> None:
    m = person("Anna", {BACKEND})
    task = uuid.uuid4()
    rows = [
        booking(task, BACKEND, m, W1, "2"),
        booking(uuid.uuid4(), BACKEND, m, W1, "3", status="cancelled"),
        booking(uuid.uuid4(), BACKEND, m, W1, "1", status="done"),
    ]
    assert booked_index(rows)[(m.id, W1)] == Decimal("3.00")


def test_task_skill_open_and_overplanned() -> None:
    open_part = task_skill_status(BACKEND, Decimal("6"), Decimal("2.5"))
    assert open_part.open == Decimal("3.50")
    assert open_part.overplanned is False
    over = task_skill_status(BACKEND, Decimal("2"), Decimal("3"))
    assert over.open == Decimal("-1.00")
    assert over.overplanned is True


def test_floor_half() -> None:
    assert floor_half(Decimal("2.9")) == Decimal("2.5")
    assert floor_half(Decimal("3")) == Decimal("3.0")
    assert floor_half(Decimal("0.4")) == Decimal("0.0")
    assert floor_half(Decimal("-1.2")) == Decimal("-1.0")


def test_suggestion_follows_free_capacity_then_one_day() -> None:
    assert suggest_person_days(Decimal("6"), Decimal("2.9")) == Decimal("2.50")
    assert suggest_person_days(Decimal("1.5"), Decimal("4")) == Decimal("1.50")
    # No free capacity: one day, or less if less is open. The overbooking is shown separately.
    assert suggest_person_days(Decimal("6"), Decimal("0")) == Decimal("1.00")
    assert suggest_person_days(Decimal("0.5"), Decimal("-2")) == Decimal("0.50")
    assert suggest_person_days(Decimal("0"), Decimal("4")) == Decimal("0.50")


def test_bottleneck_thresholds() -> None:
    assert bottleneck_status(Decimal("10"), Decimal("9")) == BOTTLENECK
    assert bottleneck_status(Decimal("9"), Decimal("10")) == TIGHT
    assert bottleneck_status(Decimal("8"), Decimal("10")) == STATUS_OK
    assert bottleneck_status(Decimal("0"), Decimal("-3")) == STATUS_OK


def test_overview_counts_a_multi_skilled_member_in_every_pool() -> None:
    both = person("Both", {BACKEND, QA})
    only_qa = person("QA", {QA})
    task = TaskIn(uuid.uuid4(), "open", {BACKEND: Decimal("4"), QA: Decimal("2")})
    overview = build_overview(
        members=[both, only_qa],
        team_ids=[TEAM],
        skill_ids=[BACKEND, QA],
        tasks=[task],
        assignments=[booking(task.id, BACKEND, both, W1, "3")],
        spans=[],
        weeks=[W1, W2],
    )
    rows = {r.skill_id: r for r in overview.skills}
    assert rows[BACKEND].pool == Decimal("10.00")
    assert rows[QA].pool == Decimal("20.00")
    # The three booked days of the shared member reduce both pools.
    assert rows[BACKEND].pool_free == Decimal("7.00")
    assert rows[QA].pool_free == Decimal("17.00")
    assert rows[BACKEND].assigned == Decimal("3.00")
    assert rows[BACKEND].open == Decimal("1.00")
    assert overview.kpis.demand == Decimal("6.00")
    assert overview.kpis.assigned == Decimal("3.00")
    assert overview.kpis.open == Decimal("3.00")


def test_overview_ignores_closed_tasks_and_inactive_members() -> None:
    gone = person("Gone", {BACKEND}, active=False)
    done = TaskIn(uuid.uuid4(), "done", {BACKEND: Decimal("5")})
    overview = build_overview(
        members=[gone],
        team_ids=[TEAM],
        skill_ids=[BACKEND],
        tasks=[done],
        assignments=[],
        spans=[],
        weeks=[W1],
    )
    assert overview.skills == []
    assert overview.teams[0].member_count == 0
    assert overview.teams[0].capacity == Decimal("0.00")
    assert overview.teams[0].utilisation == Decimal("0")


def test_overview_over_planned_effort_is_not_clipped_into_assigned() -> None:
    m = person("Anna", {BACKEND})
    task = TaskIn(uuid.uuid4(), "open", {BACKEND: Decimal("2")})
    overview = build_overview(
        members=[m],
        team_ids=[TEAM],
        skill_ids=[BACKEND],
        tasks=[task],
        assignments=[booking(task.id, BACKEND, m, W1, "3")],
        spans=[],
        weeks=[W1],
    )
    row = overview.skills[0]
    assert row.assigned == Decimal("2.00")  # capped at the effort for the progress figure
    assert row.open == Decimal("0.00")


def test_overbooked_member_weeks_kpi() -> None:
    m = person("Anna", {BACKEND}, percent=80)
    task = TaskIn(uuid.uuid4(), "open", {BACKEND: Decimal("9")})
    overview = build_overview(
        members=[m],
        team_ids=[TEAM],
        skill_ids=[BACKEND],
        tasks=[task],
        assignments=[booking(task.id, BACKEND, m, W1, "5"), booking(task.id, BACKEND, m, W2, "4")],
        spans=[],
        weeks=[W1, W2],
    )
    assert overview.kpis.overbooked_member_weeks == 1  # 5 > 4, but 4 == 4 is only full


def test_availability_orders_by_free_capacity_and_skips_others() -> None:
    busy = person("Busy", {BACKEND})
    free = person("Free", {BACKEND})
    other = person("Other", {QA})
    gone = person("Gone", {BACKEND}, active=False)
    task = uuid.uuid4()
    rows = build_availability(
        members=[busy, free, other, gone],
        skill_id=BACKEND,
        weeks=[W1, W2],
        spans=[],
        assignments=[booking(task, BACKEND, busy, W1, "4")],
    )
    assert [r.member_id for r in rows] == [free.id, busy.id]
    assert rows[0].total_free == Decimal("10.00")
    assert rows[1].total_free == Decimal("6.00")
    assert rows[1].weeks[0].free == Decimal("1.00")


def test_planned_percent_counts_each_skill_at_most_up_to_its_effort() -> None:
    d = Decimal
    assert planned_percent([]) == 0.0
    assert planned_percent([(d("0"), d("3"))]) == 0.0, "no effort at all"
    assert planned_percent([(d("4"), d("0")), (d("2"), d("0"))]) == 0.0
    assert planned_percent([(d("4"), d("3")), (d("2"), d("0"))]) == 50.0
    assert planned_percent([(d("4"), d("9")), (d("4"), d("0"))]) == 50.0, "over-planning is capped"
    assert planned_percent([(d("3"), d("1"))]) == 33.3
    assert planned_percent([(d("2"), d("2"))]) == 100.0


def test_overview_counts_what_is_assigned_over_all_weeks_when_totals_are_given() -> None:
    ada = person("Ada", {BACKEND})
    task = TaskIn(uuid.uuid4(), "open", {BACKEND: Decimal("4")})
    in_horizon = booking(task.id, BACKEND, ada, W1, "1")

    def run(totals: dict[tuple[uuid.UUID, uuid.UUID], Decimal] | None) -> Overview:
        return build_overview(
            members=[ada],
            team_ids=[TEAM],
            skill_ids=[BACKEND],
            tasks=[task],
            assignments=[in_horizon],
            spans=[],
            weeks=[W1],
            assigned_totals=totals,
        )

    horizon_only = run(None).skills[0]
    assert (horizon_only.assigned, horizon_only.open) == (Decimal("1.00"), Decimal("3.00"))
    # three more person-days sit in a week outside the horizon
    whole = run({(task.id, BACKEND): Decimal("4")})
    assert (whole.skills[0].assigned, whole.skills[0].open) == (Decimal("4.00"), Decimal("0.00"))
    assert whole.skills[0].status == STATUS_OK
    assert whole.teams[0].booked == Decimal("1.00"), "members are booked from the horizon only"
    over = run({(task.id, BACKEND): Decimal("9")})
    assert (over.skills[0].assigned, over.kpis.open) == (Decimal("4.00"), Decimal("0.00"))
