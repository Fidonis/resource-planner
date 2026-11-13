"""The administration pages: accounts, roles, the role mapping, API tokens, audit log, settings."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from tests.api.support import Api
from tests.ui.conftest import Browser

from planner_api.models import AppUser
from planner_ui.texts import LOGIN_ERRORS

ID = r"([0-9a-f-]{36})"


def events(response: Any) -> dict[str, Any]:
    return json.loads(response.headers["hx-trigger"])  # type: ignore[no-any-return]


async def sign_in_admin(api: Api, browser: Browser, name: str = "zoe") -> None:
    await api.add_user(name, "admin")
    await browser.sign_in(name)


async def role_ids(browser: Browser) -> dict[str, str]:
    """Role key to ID, read from the dialog of a new user (the checkbox names carry the ID)."""
    html = (await browser.fragment("/users/new", target="modal-body")).text
    found = re.findall(rf'name="role-{ID}"[^>]*>\s*<span class="label-text">([^<]+)</span>', html)
    return {name: ident for ident, name in found}


async def version_in(browser: Browser, url: str) -> str:
    """The version a dialog carries in its hidden field."""
    found = re.search(
        r'name="version" value="(\d+)"', (await browser.fragment(url, "modal-body")).text
    )
    assert found, url
    return found[1]


async def drop_other_users(api: Api, keep: str) -> None:
    """Leave one account behind, so that it is the only local administrator."""
    async with api.factory() as session:
        for user in (await session.scalars(select(AppUser))).all():
            if user.username != keep:
                await session.delete(user)
        await session.commit()


async def user_id(browser: Browser, username: str) -> str:
    html = (await browser.page(f"/users?q={username}")).text
    found = re.search(rf'hx-get="/users/{ID}/edit"', html)
    assert found, html
    return found[1]


# --- users ----------------------------------------------------------------------------------


async def test_the_pages_of_the_administration_are_in_the_navigation(
    api: Api, browser: Browser
) -> None:
    await sign_in_admin(api, browser)
    page = await browser.page("/users")
    for href in ("/users", "/roles", "/api-tokens", "/audit", "/settings"):
        assert f'href="{href}"' in page.text


async def test_somebody_without_the_rights_sees_neither_the_pages_nor_the_links(
    api: Api, browser: Browser
) -> None:
    await api.add_user("vera", "viewer")
    await browser.sign_in("vera")
    page = await browser.page("/planning")
    for href in ("/users", "/roles", "/api-tokens", "/audit", "/settings"):
        assert f'href="{href}"' not in page.text
        assert (await browser.page(href)).status_code == 403


async def test_a_user_is_created_with_a_generated_password_that_is_shown_once(
    api: Api, browser: Browser, make_browser: Any
) -> None:
    await sign_in_admin(api, browser)
    roles = await role_ids(browser)
    assert {"Viewer", "Planner", "Administrator"} <= set(roles)

    created = await browser.send(
        "POST",
        "/users",
        {
            "username": "nina",
            "display_name": "Nina Neu",
            "email": "nina@example.test",
            "password": "",
            "member_id": "",
            "active": "on",
            f"role-{roles['Viewer']}": "on",
            "version": "",
        },
    )
    assert created.status_code == 200
    assert created.headers["hx-retarget"] == "#modal-body"
    assert events(created)["refreshList"] is True
    assert "Temporary password" in created.text
    shown = re.search(r'id="secret-value"[^>]*value="([^"]+)"', created.text)
    assert shown and len(shown[1]) >= 12

    page = await browser.page("/users")
    assert "Nina Neu" in page.text and "Must choose a password" in page.text
    assert shown[1] not in page.text  # it was shown once and is not kept anywhere

    # The generated password works, and the account then has to choose its own.
    response = await make_browser().login("nina", shown[1])
    assert response.status_code == 303 and response.headers["location"] == "/change-password"


async def test_a_password_typed_by_the_administrator_is_not_shown_again(
    api: Api, browser: Browser
) -> None:
    await sign_in_admin(api, browser)
    created = await browser.send(
        "POST",
        "/users",
        {
            "username": "paul",
            "display_name": "Paul Pass",
            "password": "Typed-By-The-Admin-77",
            "active": "on",
            "version": "",
        },
    )
    assert created.status_code == 200 and events(created)["modalClose"] is True
    assert "Typed-By-The-Admin-77" not in created.text and "Paul Pass" in created.text


async def test_the_form_of_a_user_keeps_the_input_and_names_the_field(
    api: Api, browser: Browser
) -> None:
    await sign_in_admin(api, browser)
    await browser.send(
        "POST",
        "/users",
        {"username": "dora", "display_name": "Dora", "active": "on", "version": ""},
    )
    duplicate = await browser.send(
        "POST",
        "/users",
        {
            "username": "dora",
            "display_name": "Another Dora",
            "password": "Secret-Typed-In-123",
            "active": "on",
            "version": "",
        },
    )
    assert duplicate.status_code == 422
    assert duplicate.headers["hx-retarget"] == "#modal-body"
    assert "Already in use." in duplicate.text
    assert 'value="Another Dora"' in duplicate.text
    assert "Secret-Typed-In-123" not in duplicate.text  # a password is never sent back


async def test_roles_and_teams_of_a_user_are_chosen_in_the_form(api: Api, browser: Browser) -> None:
    await sign_in_admin(api, browser)
    team = await (await api.admin()).create("/api/v1/teams", {"name": "Alpha", "color": "#336699"})
    roles = await role_ids(browser)
    form = (await browser.fragment("/users/new", target="modal-body")).text
    # Only a role that has a team-bound permission offers the choice of teams.
    assert f'name="teams-{roles["Team lead"]}"' in form
    assert f'name="teams-{roles["Viewer"]}"' not in form

    await browser.send(
        "POST",
        "/users",
        {
            "username": "tom",
            "display_name": "Tom Lead",
            "active": "on",
            f"role-{roles['Team lead']}": "on",
            f"teams-{roles['Team lead']}": team["id"],
            "version": "",
        },
    )
    ident = await user_id(browser, "tom")
    edit = (await browser.fragment(f"/users/{ident}/edit", target="modal-body")).text
    assert f'name="role-{roles["Team lead"]}" class="checkbox checkbox-sm" checked' in edit
    assert f'<option value="{team["id"]}" selected>' in edit
    assert 'name="username"' not in edit  # the user name cannot be changed


async def test_the_last_account_that_manages_users_cannot_be_taken_away(
    api: Api, browser: Browser
) -> None:
    await sign_in_admin(api, browser)
    roles = await role_ids(browser)
    ident = await user_id(browser, "zoe")
    version = await version_in(browser, f"/users/{ident}/edit")
    await drop_other_users(api, keep="zoe")  # the harness has a bootstrap administrator too

    refused = await browser.send(
        "PUT",
        f"/users/{ident}",
        {
            "display_name": "Zoe",
            "email": "zoe@example.test",
            "active": "on",
            f"role-{roles['Viewer']}": "on",
            "version": version,
        },
    )
    assert refused.status_code == 422
    assert "no active local account that can manage users" in refused.text


async def test_the_own_account_has_no_delete_button(api: Api, browser: Browser) -> None:
    await sign_in_admin(api, browser)
    await api.add_user("other", "viewer")
    page = (await browser.page("/users")).text
    own = await user_id(browser, "zoe")
    other = await user_id(browser, "other")
    assert f'hx-get="/users/{other}/delete"' in page
    assert f'hx-get="/users/{own}/delete"' not in page


async def test_the_password_of_an_account_is_reset(api: Api, browser: Browser) -> None:
    await sign_in_admin(api, browser)
    await api.add_user("rita", "viewer")
    ident = await user_id(browser, "rita")

    dialog = await browser.fragment(f"/users/{ident}/reset-password", target="modal-body")
    assert dialog.status_code == 200 and f'hx-post="/users/{ident}/reset-password"' in dialog.text

    generated = await browser.send("POST", f"/users/{ident}/reset-password", {"new_password": ""})
    assert generated.status_code == 200 and "Temporary password" in generated.text
    assert events(generated)["refreshList"] is True

    weak = await browser.send("POST", f"/users/{ident}/reset-password", {"new_password": "short"})
    assert weak.status_code == 422 and 'role="alert"' in weak.text

    typed = await browser.send(
        "POST", f"/users/{ident}/reset-password", {"new_password": "Typed-By-The-Admin-77"}
    )
    assert typed.status_code == 200 and typed.headers["hx-reswap"] == "none"
    assert events(typed)["toast"]["kind"] == "success" and events(typed)["modalClose"] is True


# --- roles ----------------------------------------------------------------------------------


async def test_a_custom_role_is_created_changed_and_deleted(api: Api, browser: Browser) -> None:
    await sign_in_admin(api, browser)
    page = await browser.page("/roles")
    assert "Built-in" in page.text and "Administrator" in page.text
    assert 'hx-get="/roles/mappings"' not in page.text  # single sign-on is off in the tests

    dialog = await browser.fragment("/roles/new", target="modal-body")
    assert 'value="ingest:write"' in dialog.text and "Use the machine ingest API" in dialog.text

    created = await browser.send(
        "POST",
        "/roles",
        {
            "key": "ingest",
            "name": "Ingest",
            "description": "For imports",
            "permissions": ["ingest:write", "member:read"],
            "version": "",
        },
    )
    assert created.status_code == 200 and "Ingest" in created.text and "Custom" in created.text

    ident = re.search(rf'hx-get="/roles/{ID}/edit"', created.text)
    assert ident
    edit = await browser.fragment(f"/roles/{ident[1]}/edit", target="modal-body")
    assert 'name="key"' not in edit.text  # the key cannot be changed
    version = await version_in(browser, f"/roles/{ident[1]}/edit")
    saved = await browser.send(
        "PUT",
        f"/roles/{ident[1]}",
        {"name": "Ingest 2", "permissions": ["ingest:write"], "version": version},
    )
    assert saved.status_code == 200 and "Ingest 2" in saved.text

    version = await version_in(browser, f"/roles/{ident[1]}/delete")
    removed = await browser.send("DELETE", f"/roles/{ident[1]}", {"version": version})
    assert removed.status_code == 200 and "Ingest 2" not in removed.text


async def test_a_built_in_role_has_no_buttons_and_a_used_role_cannot_be_deleted(
    api: Api, browser: Browser
) -> None:
    await sign_in_admin(api, browser)
    roles = await role_ids(browser)
    page = (await browser.page("/roles")).text
    assert f'hx-get="/roles/{roles["Viewer"]}/edit"' not in page

    caller = await api.sign_in("zoe")
    made = await caller.create(
        "/api/v1/roles", {"key": "held", "name": "Held", "permissions": ["team:read"]}
    )
    await caller.create(
        "/api/v1/users",
        {
            "username": "holder",
            "displayName": "Holder",
            "roles": [{"roleId": made["id"]}],
        },
    )
    confirm = await browser.fragment(f"/roles/{made['id']}/delete", target="modal-body")
    assert confirm.status_code == 200
    refused = await browser.send(
        "DELETE", f"/roles/{made['id']}", {"version": str(made["version"])}
    )
    assert refused.status_code == 412
    assert "1 accounts" in refused.text


async def test_the_role_mapping_is_edited_in_a_dialog(
    api: Api, browser: Browser, make_browser: Any
) -> None:
    await sign_in_admin(api, browser)
    roles = await role_ids(browser)

    dialog = await browser.fragment("/roles/mappings", target="modal-body")
    assert dialog.status_code == 200 and 'hx-put="/roles/mappings"' in dialog.text
    row = await browser.fragment("/roles/mappings/row", target="mapping-rows")
    assert row.status_code == 200 and 'name="external_role"' in row.text and "data-row" in row.text

    saved = await browser.send(
        "PUT",
        "/roles/mappings",
        {
            "external_role": ["rp-planner", "", "rp-viewer"],
            "role_id": [roles["Planner"], roles["Viewer"], roles["Viewer"]],
        },
    )
    assert saved.status_code == 200 and saved.headers["hx-reswap"] == "none"
    assert events(saved)["modalClose"] is True

    again = await browser.fragment("/roles/mappings", target="modal-body")
    assert 'value="rp-planner"' in again.text and 'value="rp-viewer"' in again.text
    assert again.text.count('name="external_role"') == 2  # the empty row was dropped


# --- API tokens -----------------------------------------------------------------------------


async def test_an_api_token_is_shown_once_and_can_be_revoked(api: Api, browser: Browser) -> None:
    await sign_in_admin(api, browser)
    dialog = (await browser.fragment("/api-tokens/new", target="modal-body")).text
    assert "zoe" in dialog.lower() and 'value="ingest:write"' in dialog
    owner = await user_id(browser, "zoe")

    created = await browser.send(
        "POST",
        "/api-tokens",
        {
            "name": "nightly import",
            "user_id": owner,
            "permissions": ["ingest:write"],
            "expires_at": "2031-01-31",
            "version": "",
        },
    )
    assert created.status_code == 200 and events(created)["refreshList"] is True
    token = re.search(r'id="secret-value"[^>]*value="(rpt_[^"]+)"', created.text)
    assert token

    page = (await browser.page("/api-tokens")).text
    assert "nightly import" in page and token[1] not in page  # only the start of it is kept
    assert token[1][:8] in page

    ident = re.search(rf'hx-get="/api-tokens/{ID}/delete"', page)
    assert ident
    confirm = await browser.fragment(f"/api-tokens/{ident[1]}/delete", target="modal-body")
    assert "Revoke" in confirm.text
    revoked = await browser.send("DELETE", f"/api-tokens/{ident[1]}", {"version": ""})
    assert revoked.status_code == 200 and events(revoked)["toast"]["message"] == "Revoked."
    assert "nightly import" not in revoked.text  # revoked tokens are hidden by default

    with_revoked = (await browser.page("/api-tokens?revoked=yes")).text
    assert "nightly import" in with_revoked and "Revoked" in with_revoked


async def test_a_token_form_refuses_a_wrong_date_and_has_no_edit(
    api: Api, browser: Browser
) -> None:
    await sign_in_admin(api, browser)
    owner = await user_id(browser, "zoe")
    wrong = await browser.send(
        "POST",
        "/api-tokens",
        {
            "name": "x",
            "user_id": owner,
            "permissions": ["ingest:write"],
            "expires_at": "31.01.2031",
            "version": "",
        },
    )
    assert wrong.status_code == 422 and "Not a valid date." in wrong.text

    assert (
        await browser.fragment(f"/api-tokens/{owner}/edit", target="modal-body")
    ).status_code in {
        404,
        405,
    }


# --- audit log and settings -----------------------------------------------------------------


async def test_the_audit_log_lists_changes_with_their_details_and_filters(
    api: Api, browser: Browser
) -> None:
    await sign_in_admin(api, browser)
    await browser.send("POST", "/teams", {"name": "Audited", "color": "#336699", "version": ""})

    page = await browser.page("/audit")
    assert page.status_code == 200 and "Audited" in page.text and "planner-ui" in page.text

    filtered = await browser.fragment("/audit?entity_type=team&action=create")
    assert filtered.status_code == 200 and "Audited" in filtered.text
    assert 'id="list"' in filtered.text and "<html" not in filtered.text

    none = await browser.fragment("/audit?entity_type=skill")
    assert "No entries found." in none.text

    dated = await browser.fragment("/audit?since=2999-01-01")
    assert "No entries found." in dated.text


async def test_the_settings_are_changed_and_checked(api: Api, browser: Browser) -> None:
    await sign_in_admin(api, browser)
    page = await browser.page("/settings")
    assert page.status_code == 200 and 'hx-put="/settings"' in page.text
    assert "Set by the installation" in page.text

    saved = await browser.send(
        "PUT",
        "/settings",
        {"default_allocation_percent": "80", "planning_horizon_weeks": "6", "locale": "de"},
    )
    assert saved.status_code == 200 and events(saved)["toast"]["kind"] == "success"
    assert 'value="80"' in saved.text and 'value="6"' in saved.text

    bad = await browser.send(
        "PUT",
        "/settings",
        {"default_allocation_percent": "5", "planning_horizon_weeks": "6", "locale": "de"},
    )
    assert bad.status_code == 422 and "Too small." in bad.text
    assert 'value="5"' in bad.text

    settings = (await (await api.sign_in("zoe")).get("/api/v1/settings")).json()
    assert settings["defaultAllocationPercent"] == 80 and settings["planningHorizonWeeks"] == 6


# --- sign-in page --------------------------------------------------------------------------


async def test_the_sign_in_page_explains_why_single_sign_on_sent_the_browser_back(
    browser: Browser,
) -> None:
    for reason in LOGIN_ERRORS:
        page = await browser.http.get("/login", params={"error": reason})
        assert page.status_code == 200 and 'role="alert"' in page.text, reason

    unknown = await browser.http.get("/login", params={"error": "<script>alert(1)</script>"})
    assert "<script>alert" not in unknown.text and 'role="alert"' not in unknown.text
