"""Absences, half days, ownership and the calendar (FR-ABS-01 to 07)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select, update
from tests.api.support import Api, Caller, absence_type_id
from tests.api.world import (
    ABSENCES,
    TYPES,
    audit_count,
    booked,
    day,
    member,
    skill,
    task,
    team,
    week,
)

from planner_api.models import Absence, AppUser, Member


async def vacation(api: Api) -> str:
    return await absence_type_id(api, "vacation")


def body(
    member_id: str | None, type_id: str, start: str, end: str | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "memberId": member_id,
        "absenceTypeId": type_id,
        "startDate": start,
        "endDate": end or start,
        **extra,
    }


async def link_user_to_member(api: Api, username: str, member_id: str) -> None:
    async with api.factory() as session:
        user = (await session.scalars(select(AppUser).where(AppUser.username == username))).one()
        await session.execute(
            update(Member).where(Member.id == uuid.UUID(member_id)).values(user_id=user.id)
        )
        await session.commit()


async def test_an_absence_is_created_listed_changed_and_deleted(api: Api) -> None:
    admin = await api.admin()
    ada = await member(admin, (await team(admin))["id"], "Ada")
    kind = await vacation(api)
    created = await admin.post(ABSENCES, body(ada["id"], kind, day(0), day(4), note="Alps"))
    assert created.status_code == 201, created.text
    row = created.json()
    assert created.headers["etag"] == '"1"'
    assert (row["memberName"], row["typeKey"], row["typeLetter"]) == ("Ada", "vacation", "V")
    assert (row["workingDays"], row["startPart"], row["endPart"]) == (5, "FULL", "FULL")
    assert row["writable"] is True and row["managedFields"] == []
    assert row["dryRun"] is False and row["overbookedMemberWeeks"] == []

    listing = (await admin.get(ABSENCES)).json()
    assert listing["total"] == 1 and listing["items"][0]["note"] == "Alps"

    changed = await admin.put(
        f"{ABSENCES}/{row['id']}",
        body(ada["id"], kind, day(0), day(1)),
        headers={"If-Match": '"1"'},
    )
    assert changed.status_code == 200
    assert (changed.json()["workingDays"], changed.json()["version"]) == (2, 2)
    stale = await admin.put(
        f"{ABSENCES}/{row['id']}", body(ada["id"], kind, day(0)), headers={"If-Match": '"1"'}
    )
    assert stale.status_code == 412

    gone = await admin.delete(f"{ABSENCES}/{row['id']}", params={"version": 2})
    assert gone.status_code == 200 and gone.json()["deletedId"] == row["id"]
    assert (await admin.get(f"{ABSENCES}/{row['id']}")).status_code == 404


async def test_working_days_count_only_workdays_and_half_days(api: Api) -> None:
    admin = await api.admin()
    ada = await member(admin, (await team(admin))["id"], "Ada")
    kind = await vacation(api)

    async def days(start: str, end: str, **parts: str) -> float:
        response = await admin.post(ABSENCES, body(ada["id"], kind, start, end, **parts))
        assert response.status_code == 201, response.text
        return float(response.json()["workingDays"])

    assert await days(day(0), day(6)) == 5, "a whole week with its weekend"
    assert await days(day(7), day(7), startPart="AM", endPart="AM") == 0.5
    assert await days(day(8), day(8), startPart="PM", endPart="PM") == 0.5
    # Friday afternoon to Monday morning: half a day on each side of the weekend
    assert await days(day(4), day(7), startPart="PM", endPart="AM") == 1.0
    assert await days(day(5), day(6)) == 0, "a weekend alone"


async def test_dates_and_parts_are_validated(api: Api) -> None:
    admin = await api.admin()
    ada = await member(admin, (await team(admin))["id"], "Ada")
    kind = await vacation(api)

    async def refused(payload: dict[str, Any]) -> dict[str, str]:
        response = await admin.post(ABSENCES, payload)
        assert response.status_code == 422, response.text
        return response.json()["errors"][0]  # type: ignore[no-any-return]

    assert (await refused(body(ada["id"], kind, day(3), day(1))))["code"] == "date_order"
    assert (await refused(body(ada["id"], kind, day(1), startPart="AM", endPart="PM")))[
        "field"
    ] == "endPart"
    assert (await refused(body(ada["id"], kind, day(1), day(2), startPart="AM")))[
        "field"
    ] == "startPart"
    assert (await refused(body(ada["id"], kind, day(1), day(2), endPart="PM")))[
        "field"
    ] == "endPart"
    assert (await refused(body(ada["id"], kind, day(1), "2045-01-01")))["code"] == "too_long"
    assert (await refused(body(str(uuid.uuid4()), kind, day(1))))["field"] == "memberId"
    assert (await refused(body(ada["id"], str(uuid.uuid4()), day(1))))["field"] == "absenceTypeId"
    assert (await refused(body(ada["id"], kind, day(1), startPart="NOON")))["code"]

    inactive = await admin.create(
        TYPES, {"key": "old", "name": "Old", "color": "#336699", "active": False}
    )
    assert (await refused(body(ada["id"], inactive["id"], day(1))))["code"] == "inactive_type"


async def test_an_absence_for_everybody_shows_for_every_member_and_team(api: Api) -> None:
    admin = await api.admin()
    alpha, beta = await team(admin, "Alpha"), await team(admin, "Beta")
    ada = await member(admin, alpha["id"], "Ada")
    bob = await member(admin, beta["id"], "Bob")
    kind = await absence_type_id(api, "public-holiday")
    await admin.create(ABSENCES, body(None, kind, day(3)))
    await admin.create(ABSENCES, body(bob["id"], await vacation(api), day(0)))

    def count(response: Any) -> int:
        return int(response.json()["total"])

    assert count(await admin.get(ABSENCES)) == 2
    assert count(await admin.get(ABSENCES, params={"memberId": ada["id"]})) == 1
    assert count(await admin.get(ABSENCES, params={"memberId": bob["id"]})) == 2
    assert count(await admin.get(ABSENCES, params={"teamId": alpha["id"]})) == 1
    assert count(await admin.get(ABSENCES, params={"teamId": beta["id"]})) == 2
    assert count(await admin.get(ABSENCES, params={"typeId": kind})) == 1
    assert count(await admin.get(ABSENCES, params={"from": day(2), "to": day(4)})) == 1
    assert count(await admin.get(ABSENCES, params={"source": "external"})) == 0


async def test_the_own_absence_permission_covers_only_the_linked_member(api: Api) -> None:
    admin = await api.admin()
    alpha = await team(admin, "Alpha")
    ada = await member(admin, alpha["id"], "Ada")
    bob = await member(admin, alpha["id"], "Bob")
    kind = await vacation(api)
    await api.add_user("ada", "member")
    await link_user_to_member(api, "ada", ada["id"])
    me = await api.sign_in("ada")

    mine = await me.post(ABSENCES, body(ada["id"], kind, day(0)))
    assert mine.status_code == 201
    assert mine.json()["writable"] is True
    theirs = await me.post(ABSENCES, body(bob["id"], kind, day(0)))
    assert theirs.status_code == 403
    assert theirs.json()["permission"] == "absence:write:own"
    everybody = await me.post(ABSENCES, body(None, kind, day(0)))
    assert everybody.status_code == 403

    bobs = await admin.create(ABSENCES, body(bob["id"], kind, day(1)))
    shown = {a["id"]: a["writable"] for a in (await me.get(ABSENCES)).json()["items"]}
    assert shown[bobs["id"]] is False
    assert (
        await me.put(
            f"{ABSENCES}/{bobs['id']}", body(bob["id"], kind, day(2)), headers={"If-Match": '"1"'}
        )
    ).status_code == 403
    assert (await me.delete(f"{ABSENCES}/{bobs['id']}", params={"version": 1})).status_code == 403
    # an own absence cannot be handed to somebody else
    reassign = await me.put(
        f"{ABSENCES}/{mine.json()['id']}",
        body(bob["id"], kind, day(0)),
        headers={"If-Match": '"1"'},
    )
    assert reassign.status_code == 403
    viewer = await api.as_role("viewer")
    assert (await viewer.post(ABSENCES, body(ada["id"], kind, day(0)))).status_code == 403


async def test_a_team_scoped_role_manages_its_teams_but_not_the_company(api: Api) -> None:
    admin = await api.admin()
    alpha, beta = await team(admin, "Alpha"), await team(admin, "Beta")
    ada = await member(admin, alpha["id"], "Ada")
    bob = await member(admin, beta["id"], "Bob")
    kind = await vacation(api)
    lead = await api.as_role("team-lead", teams=[uuid.UUID(alpha["id"])])
    assert (await lead.post(ABSENCES, body(ada["id"], kind, day(0)))).status_code == 201
    assert (await lead.post(ABSENCES, body(bob["id"], kind, day(0)))).status_code == 403
    assert (await lead.post(ABSENCES, body(None, kind, day(0)))).status_code == 403
    planner = await api.as_role("planner")
    assert (await planner.post(ABSENCES, body(None, kind, day(0)))).status_code == 201


async def test_an_absence_from_an_integration_is_read_only(api: Api) -> None:
    admin = await api.admin()
    ada = await member(admin, (await team(admin))["id"], "Ada")
    kind = await vacation(api)
    async with api.factory() as session:
        row = Absence(
            member_id=uuid.UUID(ada["id"]),
            absence_type_id=uuid.UUID(kind),
            start_date=date(2030, 1, 7),
            end_date=date(2030, 1, 8),
            external_source="sap",
            external_id="A-1",
        )
        session.add(row)
        await session.commit()
        absence_id = str(row.id)
    shown = (await admin.get(f"{ABSENCES}/{absence_id}")).json()
    assert shown["writable"] is False
    assert shown["externalSource"] == "sap" and shown["managedFields"]
    changed = await admin.put(
        f"{ABSENCES}/{absence_id}", body(ada["id"], kind, day(0)), headers={"If-Match": '"1"'}
    )
    assert changed.status_code == 409 and changed.json()["code"] == "MANAGED_BY_SOURCE"
    gone = await admin.delete(f"{ABSENCES}/{absence_id}", params={"version": 1})
    assert gone.status_code == 409 and gone.json()["code"] == "MANAGED_BY_SOURCE"
    assert (await admin.get(ABSENCES, params={"source": "sap"})).json()["total"] == 1


async def full_week_world(api: Api) -> tuple[Caller, dict[str, Any], dict[str, Any], str]:
    """Ada at 80 % has capacity 4 and four person-days booked in the first week."""
    admin = await api.admin()
    backend = await skill(admin)
    ada = await member(
        admin, (await team(admin))["id"], "Ada", (backend["id"],), allocationPercent=80
    )
    work = await task(admin, "T-1", {backend["id"]: "4"})
    await booked(admin, work["id"], backend["id"], ada["id"], week(), 4)
    return admin, ada, work, await vacation(api)


async def test_an_absence_reports_the_weeks_it_overbooks(api: Api) -> None:
    admin, ada, _, kind = await full_week_world(api)
    created = await admin.post(ABSENCES, body(ada["id"], kind, day(0)))
    assert created.status_code == 201
    weeks = created.json()["overbookedMemberWeeks"]
    assert [(w["memberId"], w["weekStart"], w["capacity"], w["booked"]) for w in weeks] == [
        (ada["id"], week(), 3.2, 4)
    ]
    deleted = await admin.delete(f"{ABSENCES}/{created.json()['id']}", params={"version": 1})
    assert deleted.json()["overbookedMemberWeeks"] == []


async def test_a_dry_run_absence_computes_the_effect_and_saves_nothing(api: Api) -> None:
    admin, ada, _, kind = await full_week_world(api)
    before = await audit_count(admin)
    probe = await admin.post(ABSENCES, body(ada["id"], kind, day(0)), params={"dryRun": "true"})
    assert probe.status_code == 201 and probe.json()["dryRun"] is True
    assert probe.json()["overbookedMemberWeeks"][0]["overbooked"] is True
    assert "location" not in probe.headers
    assert (await admin.get(ABSENCES)).json()["total"] == 0
    assert await audit_count(admin) == before

    real = await admin.create(ABSENCES, body(ada["id"], kind, day(1)))
    entries = await audit_count(admin)
    for call in (
        admin.put(
            f"{ABSENCES}/{real['id']}",
            body(ada["id"], kind, day(2)),
            headers={"If-Match": '"1"'},
            params={"dryRun": "true"},
        ),
        admin.delete(f"{ABSENCES}/{real['id']}", params={"version": 1, "dryRun": "true"}),
    ):
        response = await call
        assert response.status_code == 200 and response.json()["dryRun"] is True
    assert await audit_count(admin) == entries
    unchanged = (await admin.get(f"{ABSENCES}/{real['id']}")).json()
    assert (unchanged["startDate"], unchanged["version"]) == (day(1), 1)


async def test_the_calendar_shows_half_days_letters_and_the_company_holiday(api: Api) -> None:
    admin = await api.admin()
    alpha = await team(admin, "Alpha")
    ada, bob = await member(admin, alpha["id"], "Ada"), await member(admin, alpha["id"], "Bob")
    await member(admin, alpha["id"], "Off", active=False)
    kind = await vacation(api)
    holiday = await absence_type_id(api, "public-holiday")
    await admin.create(ABSENCES, body(ada["id"], kind, day(0), startPart="PM", endPart="PM"))
    await admin.create(ABSENCES, body(None, holiday, day(3)))
    calendar = await admin.get(ABSENCES + "/calendar", params={"from": week(), "weeks": 2})
    assert calendar.status_code == 200, calendar.text
    data = calendar.json()
    assert data["start"] == week()
    assert len(data["days"]) == 10 and data["days"][0] == day(0) and data["days"][5] == day(7)
    assert [m["displayName"] for m in data["members"]] == ["Ada", "Bob"]
    ada_days = {d["date"]: d for d in data["members"][0]["days"]}
    assert ada_days[day(0)]["am"] is None
    assert ada_days[day(0)]["pm"]["letter"] == "V"
    assert ada_days[day(3)]["am"]["letter"] == "H" and ada_days[day(3)]["pm"]["letter"] == "H"
    bob_days = {d["date"]: d for d in data["members"][1]["days"]}
    assert bob_days[day(0)]["am"] is None and bob_days[day(0)]["pm"] is None
    assert bob_days[day(3)]["am"]["reducesCapacity"] is True
    assert bob["id"] in {m["memberId"] for m in data["members"]}


async def test_a_slot_shows_the_absence_that_costs_capacity_first(api: Api) -> None:
    admin = await api.admin()
    ada = await member(admin, (await team(admin))["id"], "Ada")
    info = await admin.create(
        TYPES,
        {
            "key": "on-call",
            "name": "On-call",
            "color": "#5b6b7a",
            "letter": "O",
            "reducesCapacity": False,
            "sortOrder": 1,
        },
    )
    await admin.create(ABSENCES, body(ada["id"], info["id"], day(0)))
    await admin.create(ABSENCES, body(ada["id"], await vacation(api), day(0)))
    data = (await admin.get(ABSENCES + "/calendar", params={"from": week(), "weeks": 1})).json()
    first = data["members"][0]["days"][0]
    assert first["am"]["letter"] == "V" and first["pm"]["letter"] == "V"
