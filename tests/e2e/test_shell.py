"""The shell and the master data dialogs in a real browser."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.expects_refusal
def test_a_team_is_created_renamed_and_deleted(signed_in: Page, unique: str) -> None:
    page = signed_in
    name = f"Team {unique}"
    renamed = f"Renamed {unique}"
    page.goto("/teams")
    page.get_by_role("button", name="New team").click()
    dialog = page.locator("#modal")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_label("Name")).to_be_focused()
    dialog.get_by_label("Name").fill(name)
    dialog.get_by_role("button", name="Create").click()

    expect(dialog).not_to_be_visible()
    row = page.get_by_role("row").filter(has_text=name)
    expect(row).to_be_visible()
    expect(page.locator("#toasts")).to_contain_text("Created.")

    # a second team with the same name stays in the dialog and says why
    page.get_by_role("button", name="New team").click()
    dialog.get_by_label("Name").fill(name)
    dialog.get_by_role("button", name="Create").click()
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Already in use.")
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()

    row.get_by_role("button", name=f"Edit {name}").click()
    dialog.get_by_label("Name").fill(renamed)
    dialog.get_by_role("button", name="Save").click()
    expect(page.get_by_role("row").filter(has_text=renamed)).to_be_visible()
    expect(page.get_by_role("row").filter(has_text=name)).to_have_count(0)

    page.get_by_role("row").filter(has_text=renamed).get_by_role(
        "button", name=f"Delete {renamed}"
    ).click()
    expect(dialog).to_contain_text("This cannot be undone.")
    dialog.get_by_role("button", name="Delete").click()
    expect(page.get_by_role("row").filter(has_text=renamed)).to_have_count(0)


@pytest.mark.expects_refusal
def test_an_expired_session_sends_a_dialog_request_to_the_login_page(signed_in: Page) -> None:
    page = signed_in
    page.goto("/teams")
    page.context.clear_cookies()
    page.get_by_role("button", name="New team").click()
    page.wait_for_url("**/login?next=%2Fteams")
    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()


def test_theme_and_sidebar_survive_a_reload(signed_in: Page) -> None:
    page = signed_in
    page.goto("/teams")
    html = page.locator("html")
    before = html.get_attribute("data-theme")
    page.get_by_role(
        "button", name="Dark mode" if before == "fidonis-light" else "Light mode"
    ).first.click()
    after = html.get_attribute("data-theme")
    assert after != before
    page.get_by_role("button", name="Toggle sidebar").click()
    expect(html).to_have_attribute("data-sidebar", "collapsed")
    page.reload()
    expect(html).to_have_attribute("data-theme", after or "")
    expect(html).to_have_attribute("data-sidebar", "collapsed")
    # leave the browser profile as we found it
    page.get_by_role("button", name="Toggle sidebar").click()


def test_the_phone_layout_does_not_scroll_sideways(signed_in: Page) -> None:
    page = signed_in
    page.set_viewport_size({"width": 375, "height": 812})
    for path in ("/teams", "/members", "/skills", "/absence-types", "/account"):
        page.goto(path)
        widths = page.evaluate(
            "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
        )
        assert widths[0] <= widths[1], (path, widths)


def test_the_language_switch_persists(signed_in: Page) -> None:
    page = signed_in
    page.goto("/teams")
    page.get_by_role("button", name="Account menu for").click()
    page.get_by_role("button", name="Deutsch").click()
    expect(page.get_by_role("heading", name="Teams")).to_be_visible()
    expect(page.get_by_role("link", name="Fähigkeiten")).to_be_visible()
    page.get_by_role("button", name="Kontomenü für").click()
    page.get_by_role("button", name="English").click()
    expect(page.get_by_role("link", name="Skills")).to_be_visible()
