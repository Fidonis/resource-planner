"""The master data pages: lists, dialogs, conflicts and field errors."""

from __future__ import annotations

import json
from typing import Any

from tests.api.support import Api
from tests.ui.conftest import Browser


def events(response: Any) -> dict[str, Any]:
    return json.loads(response.headers["hx-trigger"])  # type: ignore[no-any-return]


async def admin(api: Api, browser: Browser, name: str = "zoe") -> None:
    await api.add_user(name, "admin")
    await browser.sign_in(name)


async def test_a_team_is_created_listed_renamed_and_deleted(api: Api, browser: Browser) -> None:
    await admin(api, browser)

    dialog = await browser.fragment("/teams/new", target="modal-body")
    assert dialog.status_code == 200 and 'hx-post="/teams"' in dialog.text

    created = await browser.send(
        "POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""}
    )
    assert created.status_code == 200
    assert "Alpha" in created.text and 'id="list"' in created.text
    assert events(created)["toast"]["kind"] == "success"
    assert events(created)["modalClose"] is True

    page = await browser.page("/teams")
    assert "Alpha" in page.text and "background-color: #336699" in page.text

    teams = await _teams(browser)
    ident, version = teams[0]
    edit = await browser.fragment(f"/teams/{ident}/edit", target="modal-body")
    assert (
        f'hx-put="/teams/{ident}"' in edit.text and f'name="version" value="{version}"' in edit.text
    )

    renamed = await browser.send(
        "PUT", f"/teams/{ident}", {"name": "Beta", "color": "#336699", "version": str(version)}
    )
    assert renamed.status_code == 200 and "Beta" in renamed.text and "Alpha" not in renamed.text

    confirm = await browser.fragment(f"/teams/{ident}/delete", target="modal-body")
    assert "This cannot be undone." in confirm.text
    deleted = await browser.send("DELETE", f"/teams/{ident}", {"version": str(version + 1)})
    assert deleted.status_code == 200 and "No teams yet." in deleted.text


async def _teams(browser: Browser) -> list[tuple[str, int]]:
    import re

    html = (await browser.page("/teams")).text
    ids = re.findall(r'hx-get="/teams/([0-9a-f-]{36})/edit"', html)
    out = []
    for ident in ids:
        edit = await browser.fragment(f"/teams/{ident}/edit", target="modal-body")
        version = re.search(r'name="version" value="(\d+)"', edit.text)
        assert version
        out.append((ident, int(version[1])))
    return out


async def test_field_errors_stay_with_the_dialog(api: Api, browser: Browser) -> None:
    await admin(api, browser)
    await browser.send("POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""})

    duplicate = await browser.send(
        "POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""}
    )
    assert duplicate.status_code == 422
    assert duplicate.headers["hx-retarget"] == "#modal-body"
    assert "Already in use." in duplicate.text
    assert 'value="Alpha"' in duplicate.text  # the input is kept

    bad_colour = await browser.send(
        "POST", "/teams", {"name": "Beta", "color": "blue", "version": ""}
    )
    assert bad_colour.status_code == 422 and "Not in the expected format." in bad_colour.text

    blank = await browser.send("POST", "/teams", {"name": "  ", "color": "#336699", "version": ""})
    assert blank.status_code == 422


async def test_a_stale_version_is_reported_not_overwritten(api: Api, make_browser: Any) -> None:
    await api.add_user("zoe", "admin")
    await api.add_user("yan", "admin")
    first, second = make_browser(), make_browser()
    await first.sign_in("zoe")
    await second.sign_in("yan")
    await first.send("POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""})
    [(ident, version)] = await _teams(first)

    won = await first.send(
        "PUT", f"/teams/{ident}", {"name": "Mine", "color": "#336699", "version": str(version)}
    )
    assert won.status_code == 200
    lost = await second.send(
        "PUT", f"/teams/{ident}", {"name": "Theirs", "color": "#336699", "version": str(version)}
    )
    assert lost.status_code == 412
    assert "Somebody else changed this entry" in lost.text
    assert "Load current values" in lost.text
    assert 'value="Theirs"' in lost.text
    assert "Mine" in (await second.page("/teams")).text


async def test_delete_is_blocked_while_the_team_has_members(api: Api, browser: Browser) -> None:
    await admin(api, browser)
    await browser.send("POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""})
    [(team_id, version)] = await _teams(browser)
    await browser.send(
        "POST",
        "/members",
        {
            "display_name": "Ada",
            "team_id": team_id,
            "allocation_percent": "80",
            "active": "on",
            "version": "",
        },
    )
    blocked = await browser.send("DELETE", f"/teams/{team_id}", {"version": str(version)})
    assert blocked.status_code == 412
    assert "still used by: 1 members" in blocked.text
    assert "btn-error" not in blocked.text  # nothing left to confirm


async def test_members_with_skills_filters_and_conflicting_edits(
    api: Api, browser: Browser
) -> None:
    await admin(api, browser)
    await browser.send("POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""})
    await browser.send("POST", "/teams", {"name": "Beta", "color": "#996633", "version": ""})
    await browser.send(
        "POST", "/skills", {"key": "Backend", "name": "Backend development", "version": ""}
    )
    teams = dict(zip(["Alpha", "Beta"], [t[0] for t in await _teams(browser)], strict=False))
    html = (await browser.page("/members/new")).text
    assert "Availability for planned work" in html

    # the skills page lists what we made, then the member form offers it
    form = await browser.fragment("/members/new", target="modal-body")
    import re

    skill_id = re.search(r'name="skill_ids" value="([0-9a-f-]{36})"', form.text)
    assert skill_id
    created = await browser.send(
        "POST",
        "/members",
        {
            "display_name": "Ada Lovelace",
            "email": "ada@example.org",
            "team_id": teams["Alpha"],
            "allocation_percent": "80",
            "skill_ids": skill_id[1],
            "active": "on",
            "note": "",
            "version": "",
        },
    )
    assert created.status_code == 200
    assert "Ada Lovelace" in created.text and "Backend development" in created.text

    filtered = await browser.fragment(f"/members?team={teams['Beta']}")
    assert "No members found." in filtered.text
    by_skill = await browser.fragment(f"/members?skill={skill_id[1]}&active=yes")
    assert "Ada Lovelace" in by_skill.text
    searched = await browser.fragment("/members?q=lovelace")
    assert "Ada Lovelace" in searched.text

    bad = await browser.send(
        "POST",
        "/members",
        {
            "display_name": "Bo",
            "team_id": teams["Alpha"],
            "allocation_percent": "5",
            "active": "on",
            "version": "",
        },
    )
    assert bad.status_code == 422 and "allocation_percent" in bad.text

    text = await browser.send(
        "POST",
        "/members",
        {
            "display_name": "Bo",
            "team_id": teams["Alpha"],
            "allocation_percent": "many",
            "active": "on",
            "version": "",
        },
    )
    assert text.status_code == 422 and "Not a whole number." in text.text


async def test_a_viewer_can_read_but_not_change(api: Api, make_browser: Any) -> None:
    await api.add_user("vera", "viewer")
    await api.add_user("zoe", "admin")
    admin_browser, viewer = make_browser(), make_browser()
    await admin_browser.sign_in("zoe")
    await viewer.sign_in("vera")
    await admin_browser.send("POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""})

    page = (await viewer.page("/teams")).text
    assert "Alpha" in page and "New team" not in page and "/edit" not in page

    refused = await viewer.send(
        "POST", "/teams", {"name": "Beta", "color": "#336699", "version": ""}
    )
    assert refused.status_code == 403
    assert "You do not have the permission for this." in events(refused)["toast"]["message"]
    assert (await viewer.fragment("/teams/new", target="modal-body")).status_code == 403


async def test_a_team_lead_sees_edit_buttons_only_for_the_own_team(
    api: Api, make_browser: Any
) -> None:
    await api.add_user("zoe", "admin")
    admin_browser = make_browser()
    await admin_browser.sign_in("zoe")
    await admin_browser.send("POST", "/teams", {"name": "Alpha", "color": "#336699", "version": ""})
    await admin_browser.send("POST", "/teams", {"name": "Beta", "color": "#996633", "version": ""})
    teams = [t[0] for t in await _teams(admin_browser)]
    for team_id, name in zip(teams, ["Alpha", "Beta"], strict=False):
        await admin_browser.send(
            "POST",
            "/members",
            {
                "display_name": f"Member {name}",
                "team_id": team_id,
                "allocation_percent": "80",
                "active": "on",
                "version": "",
            },
        )
    import uuid

    await api.add_user("lead", "team-lead", teams=[uuid.UUID(teams[0])])
    lead = make_browser()
    await lead.sign_in("lead")
    html = (await lead.page("/members")).text
    assert html.count("/edit") == 1
