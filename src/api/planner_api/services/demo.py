"""Demo data (§6): three teams, twelve members, eight skills, thirteen tasks, some assignments
and absences, all dates relative to the week of ``today``."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_api import clock
from planner_api.auth.permissions import SETTINGS_ADMIN
from planner_api.domain.calendar import monday_of
from planner_api.errors import ErrorCode, ProblemError
from planner_api.models import (
    Absence,
    AbsenceType,
    Assignment,
    Member,
    MemberSkill,
    Skill,
    Task,
    TaskEffort,
    Team,
)
from planner_api.schemas.planning import DemoSeedResult
from planner_api.services.context import Ctx

SKILLS = (
    ("Backend", "Backend development"),
    ("Frontend", "Frontend development"),
    ("Mobile", "Mobile apps"),
    ("DevOps", "Operations and delivery"),
    ("QA", "Quality assurance"),
    ("Design", "UX and design"),
    ("Data", "Data engineering"),
    ("Security", "Security engineering"),
)

TEAMS = (("Platform", "#1b5e8c"), ("Apps", "#2a7f62"), ("Quality", "#b5651d"))

# name, team, allocation %, skills
MEMBERS = (
    ("Anna Berger", "Platform", 80, ("Backend", "DevOps")),
    ("Ben Costa", "Platform", 80, ("Backend",)),
    ("Chen Wu", "Platform", 60, ("Backend", "Data")),
    ("Dana Fox", "Platform", 80, ("DevOps", "Security")),
    ("Emil Novak", "Platform", 80, ("Data",)),
    ("Fatima Haddad", "Apps", 80, ("Frontend", "Design")),
    ("Gabor Kiss", "Apps", 80, ("Frontend",)),
    ("Hana Sato", "Apps", 80, ("Mobile", "Frontend")),
    ("Ivan Petrov", "Apps", 60, ("Mobile",)),
    ("Julia Meyer", "Quality", 80, ("QA",)),
    ("Kofi Mensah", "Quality", 80, ("QA", "Security")),
    ("Lena Ortiz", "Quality", 100, ("QA",)),
)

# key, title, owner team, status, efforts
TASKS = (
    ("SHOP-101", "Product search", "Platform", "open", {"Backend": "6", "Frontend": "4"}),
    ("SHOP-102", "Order history API", "Platform", "open", {"Backend": "5", "QA": "2"}),
    (
        "SHOP-103",
        "Payment provider switch",
        "Platform",
        "open",
        {"Backend": "8", "Security": "3", "QA": "3"},
    ),
    ("SHOP-104", "CI pipeline speed-up", "Platform", "open", {"DevOps": "5"}),
    ("SHOP-105", "Data export for finance", "Platform", "open", {"Data": "6", "Backend": "2"}),
    ("SHOP-106", "Push notifications", "Apps", "open", {"Mobile": "7", "Backend": "3"}),
    ("SHOP-107", "Wishlist screen", "Apps", "open", {"Frontend": "5", "Design": "2"}),
    (
        "SHOP-108",
        "Checkout redesign",
        "Apps",
        "open",
        {"Frontend": "8", "Design": "4", "Backend": "6", "QA": "4", "Security": "2"},
    ),
    ("SHOP-109", "Accessibility fixes", "Apps", "open", {"Frontend": "4", "QA": "2"}),
    ("SHOP-110", "Login hardening", "Platform", "open", {"Security": "4", "Backend": "2"}),
    ("SHOP-111", "Regression test suite", "Quality", "open", {"QA": "8"}),
    ("SHOP-112", "Offline mode", "Apps", "open", {"Mobile": "6", "Backend": "2"}),
    ("SHOP-113", "Release checklist", "Quality", "done", {"QA": "3"}),
)

# task, skill, member, week offset, person-days
ASSIGNMENTS = (
    ("SHOP-101", "Backend", "Ben Costa", 0, "3"),
    ("SHOP-101", "Backend", "Anna Berger", 1, "2"),
    ("SHOP-101", "Frontend", "Gabor Kiss", 1, "4"),
    ("SHOP-102", "Backend", "Ben Costa", 1, "3"),
    ("SHOP-103", "Backend", "Ben Costa", 1, "2"),  # Ben is overbooked in week 1
    ("SHOP-103", "Security", "Dana Fox", 2, "3"),
    ("SHOP-104", "DevOps", "Dana Fox", 0, "3"),
    ("SHOP-104", "DevOps", "Anna Berger", 2, "2"),
    ("SHOP-105", "Data", "Emil Novak", 0, "4"),
    ("SHOP-105", "Data", "Chen Wu", 1, "1"),
    ("SHOP-106", "Mobile", "Hana Sato", 0, "3"),
    ("SHOP-106", "Mobile", "Ivan Petrov", 1, "2"),
    ("SHOP-107", "Frontend", "Fatima Haddad", 0, "3"),
    ("SHOP-107", "Design", "Fatima Haddad", 1, "2"),
    ("SHOP-108", "Frontend", "Fatima Haddad", 2, "2"),
    ("SHOP-108", "Design", "Fatima Haddad", 2, "1"),
    ("SHOP-108", "Backend", "Anna Berger", 3, "3"),
    ("SHOP-108", "QA", "Julia Meyer", 3, "2"),
    ("SHOP-109", "QA", "Julia Meyer", 2, "2"),
    ("SHOP-110", "Security", "Kofi Mensah", 1, "2"),
    ("SHOP-110", "Backend", "Anna Berger", 1, "1"),
    ("SHOP-111", "QA", "Julia Meyer", 0, "4"),
    ("SHOP-111", "QA", "Lena Ortiz", 1, "5"),
    ("SHOP-111", "QA", "Kofi Mensah", 2, "3"),
    ("SHOP-112", "Mobile", "Hana Sato", 2, "3"),
    ("SHOP-112", "Backend", "Ben Costa", 3, "2"),
    ("SHOP-113", "QA", "Julia Meyer", -1, "3"),
)

ON_CALL = ("on-call", "On-call", "#5b6b7a", "O", 50)


async def is_empty(session: AsyncSession) -> bool:
    for model in (Team, Skill, Member, Task):
        if (await session.execute(select(func.count()).select_from(model))).scalar_one():
            return False
    return True


async def _type(session: AsyncSession, key: str) -> AbsenceType:
    found = (await session.scalars(select(AbsenceType).where(AbsenceType.key == key))).one_or_none()
    if found is not None:
        return found
    defaults = {
        "vacation": ("Vacation", "#2a78d6", "V", 10),
        "sick": ("Sick", "#c53c3c", "S", 20),
        "training": ("Training", "#7a4fc4", "T", 30),
        "public-holiday": ("Public holiday", "#a87820", "H", 40),
    }
    name, color, letter, order = defaults[key]
    created = AbsenceType(key=key, name=name, color=color, letter=letter, sort_order=order)
    session.add(created)
    await session.flush()
    return created


async def seed_demo(session: AsyncSession, today: date) -> DemoSeedResult:
    """Insert the demo data into an empty database; the caller commits."""
    if not await is_empty(session):
        raise ProblemError(
            409,
            ErrorCode.DEMO_SEED_NOT_EMPTY,
            "Demo data can only be loaded into an installation without teams, skills, "
            "members and tasks.",
        )
    monday = monday_of(today)

    def week(offset: int) -> date:
        return monday + timedelta(weeks=offset)

    skills = {key: Skill(key=key, name=name) for key, name in SKILLS}
    teams = {name: Team(name=name, color=color) for name, color in TEAMS}
    session.add_all([*skills.values(), *teams.values()])
    await session.flush()

    members = {}
    for name, team, percent, own in MEMBERS:
        first, _, last = name.partition(" ")
        member = Member(
            team_id=teams[team].id,
            display_name=name,
            email=f"{first}.{last}@example.test".lower(),
            allocation_percent=percent,
            skill_links=[MemberSkill(skill_id=skills[s].id) for s in own],
        )
        members[name] = member
        session.add(member)

    tasks = {}
    for key, title, team, status, efforts in TASKS:
        task = Task(
            key=key,
            title=title,
            owner_team_id=teams[team].id,
            status=status,
            locked_fields=[],
            efforts=[
                TaskEffort(skill_id=skills[s].id, person_days=Decimal(d))
                for s, d in efforts.items()
            ],
        )
        tasks[key] = task
        session.add(task)
    await session.flush()

    for key, skill, member_name, offset, days in ASSIGNMENTS:
        session.add(
            Assignment(
                task_id=tasks[key].id,
                skill_id=skills[skill].id,
                member_id=members[member_name].id,
                week_start=week(offset),
                person_days=Decimal(days),
            )
        )

    vacation = await _type(session, "vacation")
    sick = await _type(session, "sick")
    training = await _type(session, "training")
    holiday = await _type(session, "public-holiday")
    on_call = AbsenceType(
        key=ON_CALL[0],
        name=ON_CALL[1],
        color=ON_CALL[2],
        letter=ON_CALL[3],
        sort_order=ON_CALL[4],
        reduces_capacity=False,
    )
    session.add(on_call)
    await session.flush()

    def absence(
        member: str | None,
        kind: AbsenceType,
        start: date,
        end: date,
        start_part: str = "FULL",
        end_part: str = "FULL",
        note: str | None = None,
    ) -> Absence:
        return Absence(
            member_id=members[member].id if member else None,
            absence_type_id=kind.id,
            start_date=start,
            end_date=end,
            start_part=start_part,
            end_part=end_part,
            note=note,
        )

    absences = [
        absence("Chen Wu", vacation, week(1), week(1) + timedelta(days=2)),
        absence("Dana Fox", sick, week(0) + timedelta(days=1), week(0) + timedelta(days=1)),
        absence(
            "Fatima Haddad",
            training,
            week(2) + timedelta(days=3),
            week(2) + timedelta(days=3),
            "AM",
            "AM",
            "Accessibility workshop",
        ),
        absence("Anna Berger", on_call, week(1), week(1) + timedelta(days=4)),
        absence(None, holiday, week(2) + timedelta(days=3), week(2) + timedelta(days=3)),
        absence("Hana Sato", vacation, week(3), week(3) + timedelta(days=4)),
        absence(
            "Gabor Kiss",
            vacation,
            week(2) + timedelta(days=4),
            week(3),
            "PM",
            "AM",
        ),
    ]
    session.add_all(absences)
    await session.flush()
    return DemoSeedResult(
        teams=len(teams),
        skills=len(skills),
        members=len(members),
        tasks=len(tasks),
        assignments=len(ASSIGNMENTS),
        absences=len(absences),
    )


async def load(ctx: Ctx) -> DemoSeedResult:
    """Load the demo data on behalf of an administrator."""
    ctx.principal.require(SETTINGS_ADMIN)
    if not ctx.settings.enable_demo_seed:
        raise ProblemError(
            403, ErrorCode.DEMO_SEED_DISABLED, "Demo data is switched off (ENABLE_DEMO_SEED)."
        )
    result = await seed_demo(ctx.session, clock.today())
    ctx.audit("seed-demo", "system", "demo", after=result.model_dump())
    await ctx.uow.finish()
    return result
