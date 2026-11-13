"""The planning pages: board, dialogs, tasks, absences, overview and the CSV transfer."""

from __future__ import annotations

import json
import re
from typing import Any

from tests.api.support import Api
from tests.api.world import (
    ABSENCES,
    ASSIGNMENTS,
    TASKS,
    booked,
    member,
    skill,
    task,
    team,
    week,
)
from tests.ui.conftest import Browser

BOARD = f"/planning?from={week()}&weeks=2"


def events(response: Any) -> dict[str, Any]:
    return json.loads(response.headers["hx-trigger"])  # type: ignore[no-any-return]


async def planner_world(api: Api, browser: Browser, role: str = "admin") -> dict[str, Any]:
    """Alpha with Ada (Backend, 100 %) and one task needing 4 person-days of Backend."""
    admin = await api.admin()
    backend = await skill(admin, "Backend")
    alpha = await team(admin, "Alpha")
    ada = await member(admin, alpha["id"], "Ada", (backend["id"],), allocationPercent=100)
    work = await task(admin, "T-1", {backend["id"]: "4"})
    await api.add_user("zoe", role)
    await browser.sign_in("zoe")
    return {"admin": admin, "backend": backend, "alpha": alpha, "ada": ada, "task": work}


def form(world: dict[str, Any], **extra: str) -> dict[str, str]:
    return {
        "task": world["task"]["id"],
        "skill": world["backend"]["id"],
        "member": world["ada"]["id"],
        "week": week(),
        "days": "2",
        "from": week(),
        "weeks": "2",
        **extra,
    }


async def test_the_planning_pages_are_in_the_navigation_and_home_opens_the_board(
    api: Api, browser: Browser
) -> None:
    await planner_world(api, browser)
    home = await browser.http.get("/")
    assert home.status_code == 303 and home.headers["location"] == "/planning"
    page = await browser.page(BOARD)
    assert page.status_code == 200
    for target in ("/planning", "/overview", "/tasks", "/absences"):
        assert f'href="{target}"' in page.text


async def test_the_board_shows_capacity_chips_and_the_pool(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser)
    await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    page = await browser.page(BOARD)
    assert 'data-member="Ada"' in page.text and "2 / 5" in page.text
    assert "data-chip" in page.text and "T-1" in page.text
    assert 'data-task="T-1"' in page.text and "Assign…" in page.text
    assert "Overbooked" not in page.text

    await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 4)
    over = await browser.page(BOARD)
    assert "Overbooked" in over.text and "6 / 5" in over.text


async def test_a_board_fragment_is_just_the_board(api: Api, browser: Browser) -> None:
    await planner_world(api, browser)
    fragment = await browser.fragment(BOARD, target="board")
    assert fragment.status_code == 200
    assert fragment.text.lstrip().startswith('<div id="board"') and "<html" not in fragment.text


async def test_assigning_through_the_dialog_books_the_member_and_redraws_the_board(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    await browser.page(BOARD)
    dialog = await browser.fragment(
        f"/planning/assign?task={w['task']['id']}&skill={w['backend']['id']}&from={week()}&weeks=2",
        target="modal-body",
    )
    assert dialog.status_code == 200
    assert 'name="member"' in dialog.text and "Ada" in dialog.text and "5 free" in dialog.text
    assert 'hx-post="/planning/assignments"' in dialog.text

    created = await browser.send("POST", "/planning/assignments", form(w))
    assert created.status_code == 200
    assert 'id="board"' in created.text and "T-1" in created.text and "2 / 5" in created.text
    assert events(created)["toast"]["kind"] == "success" and events(created)["modalClose"] is True
    rows = (await w["admin"].get(ASSIGNMENTS)).json()["items"]
    assert [(r["taskKey"], r["memberName"], r["personDays"]) for r in rows] == [("T-1", "Ada", 2)]


async def test_the_plus_of_a_cell_offers_work_the_member_can_do(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser)
    admin = w["admin"]
    frontend = await skill(admin, "Frontend")
    await member(admin, w["alpha"]["id"], "Bob", (frontend["id"],), allocationPercent=100)
    # sorts before T-1, but Ada has no Frontend skill
    await task(admin, "A-0", {frontend["id"]: "3"})
    await browser.page(BOARD)
    opened = await browser.fragment(
        f"/planning/assign?member={w['ada']['id']}&week={week()}&from={week()}&weeks=2",
        target="modal-body",
    )
    assert opened.status_code == 200
    chosen = re.search(r'<option value="([^"]+)" selected>T-1 ', opened.text)
    assert chosen and chosen.group(1) == w["task"]["id"]
    assert f'<option value="{w["ada"]["id"]}" selected>' in opened.text

    # with a skill filter on the board, work of that skill comes first
    filtered = await browser.fragment(
        f"/planning/assign?member={w['ada']['id']}&filter_skill={w['backend']['id']}"
        f"&from={week()}&weeks=2",
        target="modal-body",
    )
    assert f'<option value="{w["ada"]["id"]}" selected>' in filtered.text


async def test_an_assignment_that_overbooks_is_saved_and_the_toast_says_so(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    await browser.page(BOARD)
    warned = await browser.fragment(
        f"/planning/assign?task={w['task']['id']}&skill={w['backend']['id']}"
        f"&member={w['ada']['id']}&week={week()}&days=9&from={week()}&weeks=2",
        target="modal-body",
    )
    assert "data-overbook-warning" in warned.text
    created = await browser.send("POST", "/planning/assignments", form(w, days="9"))
    toast = events(created)["toast"]
    assert toast["kind"] == "warning" and "overbooked" in toast["message"]
    assert "Overbooked" in created.text


async def test_the_dialog_refuses_a_bad_amount_and_keeps_what_was_chosen(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    await browser.page(BOARD)
    bad = await browser.send("POST", "/planning/assignments", form(w, days="lots"))
    assert bad.status_code == 422
    assert bad.headers["hx-retarget"] == "#modal-body"
    assert 'role="alert"' in bad.text and 'name="member"' in bad.text
    assert (await w["admin"].get(ASSIGNMENTS)).json()["total"] == 0


async def test_a_closed_task_is_refused_with_its_reason(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser)
    await browser.page(BOARD)
    closed = await w["admin"].put(
        f"{TASKS}/{w['task']['id']}",
        {"key": "T-1", "title": "x", "status": "done"},
        headers={"If-Match": '"1"'},
    )
    assert closed.status_code == 200
    refused = await browser.send("POST", "/planning/assignments", form(w))
    assert refused.status_code == 422 and "closed" in refused.text


async def test_a_chip_can_be_moved_changed_and_removed(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser)
    chip = await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    await browser.page(BOARD)
    ident = chip["id"]

    dialog = await browser.fragment(
        f"/planning/assignments/{ident}/edit?from={week()}&weeks=2", target="modal-body"
    )
    assert dialog.status_code == 200 and f'hx-put="/planning/assignments/{ident}"' in dialog.text
    assert 'name="version" value="1"' in dialog.text and "hx-delete" in dialog.text

    moved = await browser.send(
        "PUT",
        f"/planning/assignments/{ident}",
        form(w, days="1,5", week=week(1), version="1"),
    )
    assert moved.status_code == 200 and events(moved)["toast"]["kind"] == "success"
    row = (await w["admin"].get(f"{ASSIGNMENTS}/{ident}")).json()
    assert (row["weekStart"], row["personDays"], row["version"]) == (week(1), 1.5, 2)

    gone = await browser.send(
        "DELETE", f"/planning/assignments/{ident}", {"version": "2", "from": week(), "weeks": "2"}
    )
    assert gone.status_code == 200 and events(gone)["toast"]["message"] == "Removed."
    assert (await w["admin"].get(ASSIGNMENTS)).json()["total"] == 0


def drop_form(world: dict[str, Any], **extra: str) -> dict[str, str]:
    """What the board sends when a skill of the pool is dropped on a member's week."""
    return {
        "task": world["task"]["id"],
        "skill": world["backend"]["id"],
        "member": world["ada"]["id"],
        "week": week(),
        "from": week(),
        "weeks": "2",
        **extra,
    }


async def test_the_board_marks_what_can_be_dragged_and_where_it_can_be_dropped(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    page = await browser.page(BOARD)
    assert "data-drag-enabled" in page.text and "data-pool-list" in page.text
    pool_item = re.search(r'<li[^>]*data-effort="Backend"[^>]*>', page.text)
    assert pool_item and "data-drag " in pool_item[0]
    assert f'data-task-id="{w["task"]["id"]}"' in pool_item[0]
    assert f'data-skill-id="{w["backend"]["id"]}"' in pool_item[0]
    cell = re.search(r"<div[^>]*data-drop[^>]*>", page.text)
    assert cell and f'data-member-id="{w["ada"]["id"]}"' in cell[0]
    assert f'data-skills="{w["backend"]["id"]}"' in cell[0] and f'data-week="{week()}"' in cell[0]
    chip = re.search(r"<button[^>]*data-assignment[^>]*>", page.text)
    assert chip and 'data-version="1"' in chip[0]

    # what is fully planned cannot be dragged any more
    await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(1), 2)
    done = await browser.page(BOARD)
    done_item = re.search(r'<li[^>]*data-effort="Backend"[^>]*>', done.text)
    assert done_item is None or "data-drag " not in done_item[0]


async def test_dropping_a_skill_books_what_is_open_and_redraws_the_board(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    await browser.page(BOARD)
    dropped = await browser.send("POST", "/planning/drops", drop_form(w))
    assert dropped.status_code == 200 and 'id="board"' in dropped.text
    assert events(dropped)["toast"]["kind"] == "success"
    rows = (await w["admin"].get(ASSIGNMENTS)).json()["items"]
    assert [(r["taskKey"], r["memberName"], r["personDays"]) for r in rows] == [("T-1", "Ada", 4)]
    assert "4 / 5" in dropped.text


async def test_a_drop_proposes_no_more_than_the_week_has_free(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser)
    other = await task(w["admin"], "T-3", {w["backend"]["id"]: "5"})
    await booked(w["admin"], other["id"], w["backend"]["id"], w["ada"]["id"], week(), 4.5)
    await browser.page(BOARD)
    await browser.send("POST", "/planning/drops", drop_form(w))
    rows = (await w["admin"].get(ASSIGNMENTS, params={"taskId": w["task"]["id"]})).json()["items"]
    assert [r["personDays"] for r in rows] == [0.5], "4 are open, but the week has half a day free"


async def test_a_drop_the_api_refuses_shows_the_reason_and_the_board_as_it_was(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    frontend = await skill(w["admin"], "Frontend")
    design = await task(w["admin"], "T-2", {frontend["id"]: "2"})
    await browser.page(BOARD)
    refused = await browser.send(
        "POST",
        "/planning/drops",
        drop_form(w, task=design["id"], skill=frontend["id"]),
    )
    assert refused.status_code == 200 and 'id="board"' in refused.text
    assert events(refused)["toast"]["kind"] == "error"
    assert (await w["admin"].get(ASSIGNMENTS)).json()["total"] == 0


async def test_a_chip_dropped_on_another_week_moves_and_keeps_its_amount(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    beta = await team(w["admin"], "Beta")
    bob = await member(w["admin"], beta["id"], "Bob", (w["backend"]["id"],), allocationPercent=100)
    chip = await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    await browser.page(BOARD)
    ident = chip["id"]

    later = await browser.send(
        "PUT",
        f"/planning/assignments/{ident}/move",
        {"member": w["ada"]["id"], "week": week(1), "version": "1", "from": week(), "weeks": "2"},
    )
    assert later.status_code == 200 and events(later)["toast"]["kind"] == "success"
    row = (await w["admin"].get(f"{ASSIGNMENTS}/{ident}")).json()
    assert (row["weekStart"], row["personDays"], row["version"]) == (week(1), 2, 2)

    other = await browser.send(
        "PUT",
        f"/planning/assignments/{ident}/move",
        {"member": bob["id"], "week": week(1), "version": "2", "from": week(), "weeks": "2"},
    )
    assert other.status_code == 200 and events(other)["toast"]["kind"] == "success"
    row = (await w["admin"].get(f"{ASSIGNMENTS}/{ident}")).json()
    assert (row["memberName"], row["personDays"]) == ("Bob", 2)


async def test_moving_a_chip_to_a_member_without_the_skill_is_refused(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    frontend = await skill(w["admin"], "Frontend")
    beta = await team(w["admin"], "Beta")
    eve = await member(w["admin"], beta["id"], "Eve", (frontend["id"],), allocationPercent=100)
    chip = await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    await browser.page(BOARD)
    refused = await browser.send(
        "PUT",
        f"/planning/assignments/{chip['id']}/move",
        {"member": eve["id"], "week": week(), "version": "1", "from": week(), "weeks": "2"},
    )
    assert refused.status_code == 200 and events(refused)["toast"]["kind"] == "error"
    row = (await w["admin"].get(f"{ASSIGNMENTS}/{chip['id']}")).json()
    assert (row["memberName"], row["version"]) == ("Ada", 1)


async def test_a_move_of_an_outdated_chip_reloads_the_board_with_a_notice(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    chip = await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    await browser.page(BOARD)
    changed = await w["admin"].put(
        f"{ASSIGNMENTS}/{chip['id']}",
        {"memberId": w["ada"]["id"], "weekStart": week(), "personDays": 3},
        headers={"If-Match": '"1"'},
    )
    assert changed.status_code == 200
    stale = await browser.send(
        "PUT",
        f"/planning/assignments/{chip['id']}/move",
        {
            "member": w["ada"]["id"],
            "week": week(1),
            "version": "1",
            "from": week(),
            "weeks": "2",
        },
    )
    assert stale.status_code == 200 and 'id="board"' in stale.text
    assert events(stale)["toast"]["kind"] == "warning"
    row = (await w["admin"].get(f"{ASSIGNMENTS}/{chip['id']}")).json()
    assert (row["weekStart"], row["personDays"]) == (week(), 3), "the other person's change stays"


async def test_a_viewer_cannot_drop_and_sees_no_drag_marks(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser, role="viewer")
    chip = await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    page = await browser.page(BOARD)
    for mark in ("data-drag", "data-drop", "data-pool-list", "data-assignment"):
        assert mark not in page.text, mark
    assert (await browser.send("POST", "/planning/drops", drop_form(w))).status_code == 403
    moved = await browser.send(
        "PUT",
        f"/planning/assignments/{chip['id']}/move",
        {"member": w["ada"]["id"], "week": week(1), "version": "1", "from": week(), "weeks": "2"},
    )
    assert moved.status_code == 403


async def test_a_change_made_meanwhile_reloads_the_board_with_a_notice(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    chip = await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    await browser.page(BOARD)
    # somebody else changes it first
    other = await w["admin"].put(
        f"{ASSIGNMENTS}/{chip['id']}",
        {"memberId": w["ada"]["id"], "weekStart": week(), "personDays": 3},
        headers={"If-Match": '"1"'},
    )
    assert other.status_code == 200
    stale = await browser.send(
        "PUT", f"/planning/assignments/{chip['id']}", form(w, days="1", version="1")
    )
    assert stale.status_code == 200 and 'id="board"' in stale.text
    toast = events(stale)["toast"]
    assert toast["kind"] == "warning" and "somebody else" in toast["message"]
    assert "3 / 5" in stale.text, "the board shows what the other person saved"


async def test_a_viewer_sees_the_board_without_the_means_to_change_it(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser, role="viewer")
    await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    page = await browser.page(BOARD)
    assert page.status_code == 200 and "T-1" in page.text
    assert "Assign…" not in page.text and 'hx-get="/planning/assign' not in page.text
    assert 'hx-get="/planning/assignments' not in page.text
    denied = await browser.send("POST", "/planning/assignments", form(w))
    assert denied.status_code == 403
    dialog = await browser.fragment(f"/planning/assign?task={w['task']['id']}", target="modal-body")
    assert dialog.status_code == 403


async def test_a_team_scoped_user_may_only_change_the_own_team(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser, role="viewer")
    beta = await team(w["admin"], "Beta")
    bob = await member(w["admin"], beta["id"], "Bob", (w["backend"]["id"],), allocationPercent=100)
    import uuid

    await api.add_user("lena", "team-lead", teams=[uuid.UUID(w["alpha"]["id"])])
    scoped = Browser(browser.http._transport.app)  # type: ignore[attr-defined]
    try:
        await scoped.sign_in("lena")
        page = await scoped.page(BOARD)
        ada_row = re.search(r'data-member="Ada".*?</tr>', page.text, re.S)
        bob_row = re.search(r'data-member="Bob".*?</tr>', page.text, re.S)
        assert ada_row and 'hx-get="/planning/assign' in ada_row[0]
        assert bob_row and 'hx-get="/planning/assign' not in bob_row[0]
        refused = await scoped.send("POST", "/planning/assignments", form(w, member=bob["id"]))
        assert refused.status_code in (403, 422)
        assert (await w["admin"].get(ASSIGNMENTS)).json()["total"] == 0
    finally:
        await scoped.close()


async def test_the_overview_shows_demand_status_and_utilisation(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser)
    await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 2)
    page = await browser.page(f"/overview?from={week()}&weeks=1")
    assert page.status_code == 200
    assert 'data-kpi="demand"' in page.text and 'data-skill="Backend"' in page.text
    assert 'data-team="Alpha"' in page.text
    csv = await browser.http.get(f"/overview/export.csv?table=skills&from={week()}&weeks=1")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("Skill;Demand")


# --- tasks -------------------------------------------------------------------------------


async def test_a_task_is_created_with_efforts_listed_changed_and_deleted(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    qa = await skill(w["admin"], "QA")

    dialog = await browser.fragment("/tasks/new", target="modal-body")
    assert f'name="effort-{w["backend"]["id"]}"' in dialog.text
    assert f'name="effort-{qa["id"]}"' in dialog.text

    created = await browser.send(
        "POST",
        "/tasks",
        {
            "key": "T-2",
            "title": "Second",
            "status": "open",
            "owner_team_id": w["alpha"]["id"],
            f"effort-{w['backend']['id']}": "2,5",
            f"effort-{qa['id']}": "",
            "version": "",
        },
    )
    assert created.status_code == 200, created.text
    assert "T-2" in created.text and "Backend 0/2.5" in created.text
    page = await browser.page("/tasks")
    assert "Second" in page.text and "Alpha" in page.text

    listing = (await w["admin"].get(TASKS, params={"q": "T-2"})).json()["items"][0]
    edit = await browser.fragment(f"/tasks/{listing['id']}/edit", target="modal-body")
    assert f'hx-put="/tasks/{listing["id"]}"' in edit.text and 'value="2.5"' in edit.text

    changed = await browser.send(
        "PUT",
        f"/tasks/{listing['id']}",
        {
            "key": "T-2",
            "title": "Second, renamed",
            "status": "open",
            f"effort-{w['backend']['id']}": "3",
            f"effort-{qa['id']}": "1",
            "version": str(listing["version"]),
        },
    )
    assert changed.status_code == 200 and "Second, renamed" in changed.text
    assert "Backend 0/3" in changed.text and "QA 0/1" in changed.text

    confirm = await browser.fragment(f"/tasks/{listing['id']}/delete", target="modal-body")
    assert confirm.status_code == 200
    current = (await w["admin"].get(f"{TASKS}/{listing['id']}")).json()["version"]
    removed = await browser.send("DELETE", f"/tasks/{listing['id']}", {"version": str(current)})
    assert removed.status_code == 200 and "Second, renamed" not in removed.text


async def test_an_effort_below_the_planned_work_is_refused_unless_forced(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    await booked(w["admin"], w["task"]["id"], w["backend"]["id"], w["ada"]["id"], week(), 3)
    base = {
        "key": "T-1",
        "title": "Task T-1",
        "status": "open",
        f"effort-{w['backend']['id']}": "1",
        "version": "1",
    }
    refused = await browser.send("PUT", f"/tasks/{w['task']['id']}", base)
    assert refused.status_code == 422
    assert "lower than the work already planned" in refused.text
    forced = await browser.send("PUT", f"/tasks/{w['task']['id']}", {**base, "force": "on"})
    assert forced.status_code == 200
    rows = (await w["admin"].get(ASSIGNMENTS)).json()["items"]
    assert [r["personDays"] for r in rows] == [1]


async def test_a_bad_effort_is_reported_next_to_the_field(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser)
    response = await browser.send(
        "POST",
        "/tasks",
        {"key": "T-9", "title": "x", "status": "open", f"effort-{w['backend']['id']}": "many"},
    )
    assert response.status_code == 422 and "person-days" in response.text


# --- absences ----------------------------------------------------------------------------


async def test_an_absence_is_created_listed_and_shown_in_the_calendar(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    types = (await w["admin"].get("/api/v1/absence-types")).json()["items"]
    vacation = next(t for t in types if t["key"] == "vacation")

    dialog = await browser.fragment("/absences/new", target="modal-body")
    assert 'type="date"' in dialog.text and 'name="start_part"' in dialog.text

    created = await browser.send(
        "POST",
        "/absences",
        {
            "member_id": w["ada"]["id"],
            "absence_type_id": vacation["id"],
            "start_date": "2030-01-07",
            "start_part": "FULL",
            "end_date": "2030-01-09",
            "end_part": "FULL",
            "note": "Alps",
            "version": "",
        },
    )
    assert created.status_code == 200, created.text
    assert "Ada" in created.text and "Alps" in created.text and "Vacation" in created.text
    assert (await w["admin"].get(ABSENCES)).json()["total"] == 1

    calendar = await browser.page(f"/absences/calendar?from={week()}&weeks=1")
    assert calendar.status_code == 200
    assert 'data-member="Ada"' in calendar.text and 'data-half="AM"' in calendar.text
    board = await browser.page(BOARD)
    assert "3 off" in board.text, "three working days off show on the board"


async def test_the_absence_list_refuses_an_end_before_the_start(api: Api, browser: Browser) -> None:
    w = await planner_world(api, browser)
    types = (await w["admin"].get("/api/v1/absence-types")).json()["items"]
    response = await browser.send(
        "POST",
        "/absences",
        {
            "member_id": w["ada"]["id"],
            "absence_type_id": types[0]["id"],
            "start_date": "2030-01-09",
            "start_part": "FULL",
            "end_date": "2030-01-07",
            "end_part": "FULL",
        },
    )
    assert response.status_code == 422 and 'role="alert"' in response.text


async def test_a_member_account_may_open_the_dialog_for_own_absences(
    api: Api, browser: Browser
) -> None:
    await planner_world(api, browser, role="member")
    assert (await browser.fragment("/absences/new", target="modal-body")).status_code == 200
    assert (await browser.fragment("/tasks/new", target="modal-body")).status_code == 403


# --- CSV transfer ------------------------------------------------------------------------


async def test_tasks_are_exported_and_imported_through_the_dialog(
    api: Api, browser: Browser
) -> None:
    w = await planner_world(api, browser)
    export = await browser.http.get("/tasks/export.csv")
    assert export.status_code == 200 and "Key;Title;Team;Efforts;Status" in export.text
    assert 'filename="tasks.csv"' in export.headers["content-disposition"]

    dialog = await browser.fragment("/tasks/import", target="modal-body")
    assert 'type="file"' in dialog.text and 'name="create_skills"' in dialog.text

    text = "Key;Title;Team;Efforts;Status\nI-1;Imported;;Backend=2;\nI-2;Bad;;Nothing=1;\n"

    async def post(mode: str, **data: str) -> Any:
        return await browser.http.post(
            "/tasks/import",
            data={"mode": mode, **data},
            files={"file": ("tasks.csv", text.encode(), "text/csv")},
            headers=browser.htmx(),
        )

    checked = await post("check")
    assert checked.status_code == 200 and "Checked, nothing was saved yet." in checked.text
    assert "Nothing" in checked.text
    assert (await w["admin"].get(TASKS, params={"q": "I-1"})).json()["total"] == 0

    done = await post("import")
    assert "Imported." in done.text
    assert (await w["admin"].get(TASKS, params={"q": "I-1"})).json()["total"] == 1

    none = await browser.http.post("/tasks/import", data={"mode": "import"}, headers=browser.htmx())
    assert "Choose a CSV file first." in none.text


async def test_a_file_that_is_not_utf8_is_explained(api: Api, browser: Browser) -> None:
    await planner_world(api, browser)
    response = await browser.http.post(
        "/tasks/import",
        data={"mode": "import"},
        files={"file": ("tasks.csv", "Key;Title\nA;Größe\n".encode("latin-1"), "text/csv")},
        headers=browser.htmx(),
    )
    assert response.status_code == 200 and "UTF-8" in response.text


async def test_absences_are_exported_through_the_page(api: Api, browser: Browser) -> None:
    await planner_world(api, browser)
    export = await browser.http.get("/absences/export.csv")
    assert export.status_code == 200
    assert export.text.splitlines()[0] == "Name;From;To;Type;Note;StartPart;EndPart"
