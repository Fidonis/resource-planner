"""The planning pages in a real browser: board dialogs, keyboard, conflicts and the list pages."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest
from playwright.sync_api import Browser, Locator, Page, StorageState, expect
from tests.e2e.conftest import new_context


def this_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


@dataclass
class World:
    page: Page
    unique: str
    team: dict[str, Any]
    skill: dict[str, Any]
    member: dict[str, Any]
    task: dict[str, Any]

    @property
    def board(self) -> str:
        query = f"from={this_monday()}&weeks=2&team={self.team['id']}&skill={self.skill['id']}"
        return f"/planning?{query}"

    @property
    def row(self) -> Locator:
        return self.page.locator(f'tr[data-member="{self.member["displayName"]}"]')

    @property
    def dialog(self) -> Locator:
        return self.page.locator("#modal")


def call(page: Page, method: str, path: str, body: Any = None) -> Any:
    """The API through the same address and session as the browser."""
    csrf = page.request.get("/api/v1/auth/me").json()["csrfToken"]
    response = page.request.fetch(
        f"/api/v1{path}", method=method, data=body, headers={"X-CSRF-Token": csrf}
    )
    assert response.ok, response.text()
    return response.json() if response.body() else None


def tidy(w: World) -> None:
    """Remove what a test made, so the lists the next run looks at stay on one page."""
    page = w.page

    def rows(path: str, query: str) -> list[dict[str, Any]]:
        found = call(page, "GET", f"{path}?{query}&pageSize=200")
        return list(found["items"])

    def drop(path: str, row: dict[str, Any]) -> None:
        call(page, "DELETE", f"{path}/{row['id']}?version={row['version']}")

    for row in rows("/users", f"q={w.unique}"):
        drop("/users", row)
    member = w.member["id"]
    for path in ("/assignments", "/absences"):
        for row in rows(path, f"memberId={member}"):
            drop(path, row)
    for row in rows("/tasks", f"q={w.unique}"):
        drop("/tasks", row)
    for path, ident in (
        ("/members", member),
        ("/skills", w.skill["id"]),
        ("/teams", w.team["id"]),
    ):
        drop(path, call(page, "GET", f"{path}/{ident}"))


@pytest.fixture
def world(signed_in: Page, unique: str) -> Iterator[World]:
    page = signed_in
    team = call(page, "POST", "/teams", {"name": f"Team {unique}", "color": "#336699"})
    skill = call(page, "POST", "/skills", {"key": f"Sk{unique}", "name": f"Skill {unique}"})
    member = call(
        page,
        "POST",
        "/members",
        {
            "teamId": team["id"],
            "displayName": f"Ada {unique}",
            "skillIds": [skill["id"]],
            "allocationPercent": 100,
        },
    )
    task = call(
        page,
        "POST",
        "/tasks",
        {
            "key": f"E2E-{unique}",
            "title": f"Task {unique}",
            "efforts": [{"skillId": skill["id"], "personDays": 4}],
        },
    )
    made = World(page, unique, team, skill, member, task)
    yield made
    tidy(made)


def assign_from_pool(w: World) -> None:
    w.page.goto(w.board)
    pool = w.page.locator(f'[data-task="{w.task["key"]}"]')
    pool.get_by_role("button", name=re.compile("^Assign .* work of")).click()
    expect(w.dialog).to_be_visible()
    expect(w.dialog.get_by_role("heading", name="Assign work")).to_be_visible()


def test_work_is_assigned_changed_and_removed_through_dialogs(world: World) -> None:
    w, page = world, world.page
    assign_from_pool(w)
    # the dialog proposes the member and the open amount
    expect(w.dialog.get_by_label("Member")).to_contain_text(w.member["displayName"])
    expect(w.dialog.get_by_label("Person-days")).to_have_value("4")
    w.dialog.get_by_role("button", name="Assign", exact=True).click()
    expect(w.dialog).not_to_be_visible()
    expect(page.locator("#toasts")).to_contain_text("Saved.")
    expect(w.row).to_contain_text("4 / 5")
    chip = w.row.locator("[data-chip]")
    expect(chip).to_have_count(1)
    expect(chip).to_contain_text(w.task["key"])

    chip.click()
    expect(w.dialog.get_by_role("heading", name="Change assignment")).to_be_visible()
    w.dialog.get_by_label("Person-days").fill("1,5")
    w.dialog.get_by_role("button", name="Save").click()
    expect(w.dialog).not_to_be_visible()
    expect(w.row).to_contain_text("1.5 / 5")

    w.row.locator("[data-chip]").click()
    w.dialog.get_by_role("button", name="Remove").click()
    expect(w.dialog).not_to_be_visible()
    expect(w.row.locator("[data-chip]")).to_have_count(0)
    expect(page.locator("#toasts")).to_contain_text("Removed.")


def test_an_amount_beyond_the_free_capacity_warns_and_is_shown_as_overbooked(world: World) -> None:
    w, page = world, world.page
    assign_from_pool(w)
    days = w.dialog.get_by_label("Person-days")
    days.fill("9")
    days.press("Tab")  # the dialog reloads and compares the amount with the free days
    expect(w.dialog.locator("[data-overbook-warning]")).to_be_visible()
    w.dialog.get_by_role("button", name="Assign", exact=True).click()
    expect(w.dialog).not_to_be_visible()
    expect(page.locator("#toasts")).to_contain_text("overbooked")
    expect(w.row).to_contain_text("Overbooked")
    expect(w.row).to_contain_text("9 / 5")


def test_the_dialog_can_be_used_with_the_keyboard_alone(world: World) -> None:
    w, page = world, world.page
    page.goto(w.board)
    button = page.locator(f'[data-task="{w.task["key"]}"]').get_by_role(
        "button", name=re.compile("^Assign .* work of")
    )
    button.focus()
    page.keyboard.press("Enter")
    expect(w.dialog).to_be_visible()
    # focus moves into the dialog and Escape returns to the page
    expect(w.dialog.locator(":focus")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(w.dialog).not_to_be_visible()
    # the plus button of a cell opens the same dialog for that member and week
    w.row.get_by_role("button", name=re.compile("^Assign work to")).first.focus()
    page.keyboard.press("Enter")
    expect(w.dialog.get_by_label("Member")).to_contain_text(w.member["displayName"])
    page.keyboard.press("Tab")
    page.keyboard.press("Escape")
    expect(w.dialog).not_to_be_visible()


@pytest.fixture
def other_page(browser: Browser, session_state: StorageState) -> Iterator[Page]:
    """A second browser of the same account, for changes made meanwhile."""
    context = new_context(browser, session_state)
    yield context.new_page()
    context.close()


def test_a_change_by_somebody_else_shows_a_notice_and_the_current_plan(
    world: World, other_page: Page
) -> None:
    w, page = world, world.page
    assign_from_pool(w)
    w.dialog.get_by_role("button", name="Assign", exact=True).click()
    expect(w.row).to_contain_text("4 / 5")

    # the first person opens the chip, the second one changes it meanwhile
    w.row.locator("[data-chip]").click()
    expect(w.dialog.get_by_label("Person-days")).to_be_visible()

    other = World(other_page, w.unique, w.team, w.skill, w.member, w.task)
    other_page.goto(w.board)
    other.row.locator("[data-chip]").click()
    other.dialog.get_by_label("Person-days").fill("3")
    other.dialog.get_by_role("button", name="Save").click()
    expect(other.row).to_contain_text("3 / 5")

    w.dialog.get_by_label("Person-days").fill("1")
    w.dialog.get_by_role("button", name="Save").click()
    expect(page.locator("#toasts")).to_contain_text("changed by somebody else")
    expect(w.dialog).not_to_be_visible()
    expect(w.row).to_contain_text("3 / 5")


def drag(page: Page, source: Locator, target: Locator, *, release: bool = True) -> None:
    """Drag with the mouse in steps, as a person does; the board reacts to the moves."""
    start, end = source.bounding_box(), target.bounding_box()
    assert start and end
    x, y = start["x"] + start["width"] / 2, start["y"] + start["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 12, y + 12, steps=4)
    page.mouse.move(end["x"] + end["width"] / 2, end["y"] + end["height"] / 2, steps=12)
    if release:
        page.mouse.up()


def roomy(page: Page) -> None:
    """A window wide enough to show the pool next to the grid while dragging."""
    page.set_viewport_size({"width": 1600, "height": 1000})


def cell_of(w: World, row: Locator, offset: int = 0) -> Locator:
    """The drop area of a member's cell in the week `offset` weeks after this one."""
    when = this_monday() + timedelta(weeks=offset)
    return row.locator(f'td[data-week="{when}"] [data-drop]')


def pool_item(w: World) -> Locator:
    return w.page.locator(f'[data-task="{w.task["key"]}"] li[data-drag] > span').first


def test_a_skill_dragged_from_the_pool_onto_a_week_books_the_open_work(world: World) -> None:
    w, page = world, world.page
    roomy(page)
    page.goto(w.board)
    drag(page, pool_item(w), cell_of(w, w.row))
    expect(page.locator("#toasts")).to_contain_text("Saved.")
    expect(w.row).to_contain_text("4 / 5")
    expect(w.row.locator("[data-chip]")).to_have_count(1)
    rows = call(page, "GET", f"/assignments?memberId={w.member['id']}")["items"]
    assert [(r["taskKey"], r["personDays"]) for r in rows] == [(w.task["key"], 4)]


def test_the_weeks_that_take_the_item_are_framed_while_it_is_dragged(world: World) -> None:
    w, page = world, world.page
    unskilled = call(
        page,
        "POST",
        "/members",
        {
            "teamId": w.team["id"],
            "displayName": f"Zed {w.unique}",
            "skillIds": [],
            "allocationPercent": 100,
        },
    )
    try:
        roomy(page)
        page.goto(w.board)
        zed = page.locator(f'tr[data-member="{unskilled["displayName"]}"]')
        drag(page, pool_item(w), cell_of(w, w.row, 1), release=False)
        expect(page.locator("#board")).to_have_attribute("data-dragging", "")
        expect(w.row.locator('td[data-target="valid"]')).to_have_count(2)
        expect(zed.locator('td[data-target="invalid"]')).to_have_count(2)
        # let go outside of every week: nothing happens, the frames go away
        page.mouse.move(5, 5, steps=6)
        page.mouse.up()
        expect(page.locator("#board")).not_to_have_attribute("data-dragging", "")
        assert call(page, "GET", f"/assignments?taskId={w.task['id']}")["total"] == 0
        # a member without the skill refuses the drop: nothing is booked
        drag(page, pool_item(w), cell_of(w, zed))
        expect(page.locator("#board")).not_to_have_attribute("data-dragging", "")
        assert call(page, "GET", f"/assignments?taskId={w.task['id']}")["total"] == 0
        expect(page.locator("#toasts")).not_to_contain_text("Saved.")
    finally:
        call(page, "DELETE", f"/members/{unskilled['id']}?version={unskilled['version']}")


def test_a_chip_dragged_to_another_week_moves_the_booking(world: World) -> None:
    w, page = world, world.page
    made = call(
        page,
        "POST",
        "/assignments",
        {
            "taskId": w.task["id"],
            "skillId": w.skill["id"],
            "memberId": w.member["id"],
            "weekStart": str(this_monday()),
            "personDays": 2,
        },
    )
    roomy(page)
    page.goto(w.board)
    drag(page, w.row.locator("[data-chip]"), cell_of(w, w.row, 1))
    expect(page.locator("#toasts")).to_contain_text("Saved.")
    moved = call(page, "GET", f"/assignments/{made['id']}")
    assert (moved["weekStart"], moved["personDays"]) == (str(this_monday() + timedelta(weeks=1)), 2)
    expect(cell_of(w, w.row, 1).locator("[data-chip]")).to_have_count(1)
    expect(cell_of(w, w.row, 0).locator("[data-chip]")).to_have_count(0)


def test_the_overview_and_the_calendar_open_without_errors(world: World) -> None:
    page = world.page
    assign_from_pool(world)
    world.dialog.get_by_role("button", name="Assign", exact=True).click()
    expect(world.row).to_contain_text("4 / 5")

    page.goto(f"/overview?from={this_monday()}&weeks=2")
    expect(page.get_by_role("heading", name="Overview", level=1)).to_be_visible()
    skill_row = page.locator(f'tr[data-skill="Sk{world.unique}"]')
    expect(skill_row).to_contain_text("OK")
    expect(page.locator("[data-kpi=demand]")).to_be_visible()

    page.goto(f"/absences/calendar?from={this_monday()}&weeks=2&team={world.team['id']}")
    expect(page.locator(f'tr[data-member="{world.member["displayName"]}"]')).to_be_visible()


def test_a_task_and_an_absence_are_created_in_their_dialogs(world: World) -> None:
    w, page = world, world.page
    key = f"UI-{w.unique}"
    page.goto("/tasks")
    page.get_by_role("button", name="New task").click()
    dialog = w.dialog
    expect(dialog).to_be_visible()
    dialog.get_by_label("Key").fill(key)
    dialog.get_by_label("Title").fill("Made in the dialog")
    dialog.get_by_label(f"Skill {w.unique}").fill("2,5")
    dialog.get_by_role("button", name="Create").click()
    expect(dialog).not_to_be_visible()
    row = page.get_by_role("row").filter(has_text=key)
    expect(row).to_contain_text("Made in the dialog")
    expect(row).to_contain_text(f"Sk{w.unique} 0/2.5")

    page.goto("/absences")
    page.get_by_role("button", name="New absence").click()
    dialog.get_by_label("Member").select_option(
        label=f"{w.member['displayName']} ({w.team['name']})"
    )
    dialog.get_by_label("Type").select_option(label="Vacation")
    start = this_monday()
    dialog.get_by_label("From").fill(start.isoformat())
    dialog.get_by_label("To (inclusive)").fill((start + timedelta(days=1)).isoformat())
    dialog.get_by_role("button", name="Create").click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role("row").filter(has_text=w.member["displayName"]).first).to_contain_text(
        "Vacation"
    )


ACCOUNT_PASSWORD = "Correct-Horse-Battery-42"


def account_page(browser: Browser, admin: Page, name: str, role: str, team_ids: list[str]) -> Page:
    """A page of a new local account with one role (bound to teams), past the password step."""
    roles = call(admin, "GET", "/roles?pageSize=100")["items"]
    role_id = next(r["id"] for r in roles if r["key"] == role)
    call(
        admin,
        "POST",
        "/users",
        {
            "username": name,
            "displayName": name,
            "password": "Temporary-Passw0rd-1",
            "roles": [{"roleId": role_id, "teamIds": team_ids}],
        },
    )
    context = new_context(browser)
    login = context.request.post(
        "/api/v1/auth/login", data={"username": name, "password": "Temporary-Passw0rd-1"}
    )
    assert login.ok, login.text()
    csrf = context.request.get("/api/v1/auth/me").json()["csrfToken"]
    changed = context.request.post(
        "/api/v1/auth/change-password",
        data={"currentPassword": "Temporary-Passw0rd-1", "newPassword": ACCOUNT_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.ok, changed.text()
    return context.new_page()


def test_a_viewer_only_looks_and_a_team_bound_planner_changes_only_their_team(
    world: World, browser: Browser
) -> None:
    w = world
    other_team = call(w.page, "POST", "/teams", {"name": f"Other {w.unique}", "color": "#993366"})
    call(
        w.page,
        "POST",
        "/members",
        {
            "teamId": other_team["id"],
            "displayName": f"Bob {w.unique}",
            "skillIds": [w.skill["id"]],
            "allocationPercent": 100,
        },
    )
    board = f"/planning?from={this_monday()}&weeks=2&skill={w.skill['id']}"
    assign = re.compile("^Assign work to ")

    viewer = account_page(browser, w.page, f"viewer-{w.unique}", "viewer", [])
    scoped = account_page(browser, w.page, f"scoped-{w.unique}", "planner", [w.team["id"]])
    try:
        viewer.goto(board)
        expect(viewer.get_by_role("row").filter(has_text=w.member["displayName"])).to_be_visible()
        expect(viewer.get_by_role("button", name=assign)).to_have_count(0)
        expect(viewer.get_by_role("button", name=re.compile("^Assign .* work of"))).to_have_count(0)

        scoped.goto(board)
        mine = scoped.locator(f'tr[data-member="{w.member["displayName"]}"]')
        theirs = scoped.locator(f'tr[data-member="Bob {w.unique}"]')
        expect(theirs).to_be_visible()
        expect(mine.get_by_role("button", name=assign)).to_have_count(2)
        expect(theirs.get_by_role("button", name=assign)).to_have_count(0)
    finally:
        viewer.context.close()
        scoped.context.close()
        for member in call(w.page, "GET", f"/members?q=Bob {w.unique}")["items"]:
            call(w.page, "DELETE", f"/members/{member['id']}?version={member['version']}")
        team = call(w.page, "GET", f"/teams/{other_team['id']}")
        call(w.page, "DELETE", f"/teams/{team['id']}?version={team['version']}")
