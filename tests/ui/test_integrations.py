"""The integration pages: forms from a provider's schema, test, sync, runs and removal."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text
from tests.api.support import Api
from tests.api.sync_support import write
from tests.ui.conftest import Browser

from planner_ui.provider_texts import BUILT_IN_TEXTS

ID = r"([0-9a-f-]{36})"
INTEGRATIONS = "/api/v1/integrations"
TASKS = "/api/v1/tasks"


def events(response: Any) -> dict[str, Any]:
    return json.loads(response.headers["hx-trigger"])  # type: ignore[no-any-return]


async def sign_in_admin(api: Api, browser: Browser, name: str = "zoe") -> None:
    await api.add_user(name, "admin")
    await browser.sign_in(name)


def task_form(
    name: str = "Tasks from CSV", path: str = "tasks.csv", **extra: str
) -> dict[str, str]:
    return {
        "type": "task_source",
        "provider_key": "csv",
        "name": name,
        "schedule": "",
        "config.path": path,
        "mapping.effortStrategy": "MANUAL",
        "mapping.unitFactor": "1",
        **extra,
    }


async def make(browser: Browser, **form: str) -> str:
    """Create a CSV task integration through the form; returns its ID."""
    answer = await browser.send("POST", "/integrations", task_form(**form))
    assert answer.status_code == 200, answer.text
    found = re.search(rf'hx-get="/integrations/{ID}/edit"', answer.text)
    assert found, answer.text
    return found[1]


async def tasks_of(admin: Any) -> list[dict[str, Any]]:
    answer = await admin.get(TASKS, params={"pageSize": 100})
    return answer.json()["items"]  # type: ignore[no-any-return]


# --- who sees the page -----------------------------------------------------------------------


async def test_only_administrators_of_integrations_get_the_page(
    sync_api: Api, sync_browser: Browser, make_browser: Any
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    page = await sync_browser.page("/integrations")
    assert page.status_code == 200 and 'href="/integrations"' in page.text
    assert "No integrations yet." in page.text

    await sync_api.add_user("paula", "planner")
    planner = Browser(sync_browser.http._transport.app)  # type: ignore[attr-defined]
    try:
        await planner.sign_in("paula")
        refused = await planner.page("/integrations")
        assert refused.status_code == 403
        assert 'href="/integrations"' not in (await planner.page("/account")).text
    finally:
        await planner.close()


# --- create ----------------------------------------------------------------------------------


async def test_a_provider_is_chosen_and_its_form_is_drawn_from_the_schema(
    sync_api: Api, sync_browser: Browser
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    chooser = (await sync_browser.fragment("/integrations/new", "modal-body")).text
    for title in ("CSV file (tasks)", "CSV file (people)", "Keycloak", "Fake tracker"):
        assert title in chooser
    assert 'hx-get="/integrations/new/directory/keycloak"' in chooser

    csv = (await sync_browser.fragment("/integrations/new/task_source/csv", "modal-body")).text
    assert 'name="config.path"' in csv and "required" in csv
    assert 'name="provider_key" value="csv"' in csv
    assert 'name="mapping.effortStrategy"' in csv
    assert '<option value="EVEN_SPLIT"' in csv
    assert 'name="mapping.statusMap"' in csv and "name=value" in csv
    assert "<textarea" in csv

    keycloak = (
        await sync_browser.fragment("/integrations/new/directory/keycloak", "modal-body")
    ).text
    assert 'type="password"' in keycloak and 'name="config.clientSecret"' in keycloak
    assert 'name="config.verifyTls"' in keycloak and "checked" in keycloak
    assert 'name="config.pageSize"' in keycloak and 'min="10"' in keycloak

    unknown = await sync_browser.fragment("/integrations/new/task_source/nothing", "modal-body")
    assert unknown.status_code == 409
    assert "not installed" in unknown.text or events(unknown)["toast"]["kind"] == "error"


async def test_an_integration_is_created_from_the_form(
    sync_api: Api, sync_browser: Browser
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    answer = await sync_browser.send(
        "POST",
        "/integrations",
        task_form(
            schedule="0 6 * * 1-5",
            enabled="on",
            **{
                "mapping.statusMap": "Ready=open\nShipped = done",
                "mapping.effortStrategy": "EVEN_SPLIT",
                "mapping.unitFactor": "0,125",
            },
        ),
    )
    assert answer.status_code == 200, answer.text
    assert events(answer)["toast"]["message"] == "Created."
    assert events(answer)["modalClose"] is True
    assert "Tasks from CSV" in answer.text and "0 6 * * 1-5" in answer.text
    assert "Enabled" in answer.text

    admin = await sync_api.admin()
    stored = (await admin.get(INTEGRATIONS)).json()["items"][0]
    assert stored["enabled"] is True and stored["schedule"] == "0 6 * * 1-5"
    assert stored["config"] == {"path": "tasks.csv"}
    assert stored["mapping"]["effortStrategy"] == "EVEN_SPLIT"
    assert stored["mapping"]["unitFactor"] == 0.125
    assert stored["mapping"]["statusMap"] == {"Ready": "open", "Shipped": "done"}


async def test_a_form_with_mistakes_comes_back_with_the_input_and_a_message_at_each_field(
    sync_api: Api, sync_browser: Browser
) -> None:
    await sign_in_admin(sync_api, sync_browser)

    missing = await sync_browser.send("POST", "/integrations", task_form(path=""))
    assert missing.status_code == 422
    assert missing.headers["hx-retarget"] == "#modal-body"
    assert 'id="f-config-path-err"' in missing.text and "Required." in missing.text
    assert 'value="Tasks from CSV"' in missing.text  # what was typed is still there

    cron = await sync_browser.send("POST", "/integrations", task_form(schedule="soon"))
    assert cron.status_code == 422 and 'id="f-schedule-err"' in cron.text

    number = await sync_browser.send(
        "POST", "/integrations", task_form(**{"mapping.unitFactor": "lots"})
    )
    assert number.status_code == 422 and "Not a number." in number.text
    assert 'value="lots"' in number.text

    pairs = await sync_browser.send(
        "POST", "/integrations", task_form(**{"mapping.teamMap": "no separator"})
    )
    assert pairs.status_code == 422 and "name=value" in pairs.text

    pattern = await sync_browser.send(
        "POST", "/integrations", task_form(**{"mapping.labelPattern": "("})
    )
    assert pattern.status_code == 422 and 'id="f-mapping-labelPattern-err"' in pattern.text

    admin = await sync_api.admin()
    assert (await admin.get(INTEGRATIONS)).json()["total"] == 0


async def test_a_secret_is_never_shown_and_an_empty_input_keeps_it(
    sync_api: Api, sync_browser: Browser
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    keycloak = {
        "type": "directory",
        "provider_key": "keycloak",
        "name": "Keycloak",
        "schedule": "",
        "config.baseUrl": "http://keycloak.test",
        "config.realm": "planner",
        "config.clientId": "c",
        "config.clientSecret": "a-secret-nobody-may-see",
        "config.verifyTls": "on",
        "config.pageSize": "100",
    }
    answer = await sync_browser.send("POST", "/integrations", keycloak)
    assert answer.status_code == 200, answer.text
    assert "a-secret-nobody-may-see" not in answer.text
    ident = re.search(rf'hx-get="/integrations/{ID}/edit"', answer.text)
    assert ident

    edit = (await sync_browser.fragment(f"/integrations/{ident[1]}/edit", "modal-body")).text
    assert "a-secret-nobody-may-see" not in edit
    assert "Set. Leave empty to keep it." in edit
    version = re.search(r'name="version" value="(\d+)"', edit)
    assert version

    changed = {
        **keycloak,
        "config.clientSecret": "",
        "config.realm": "other",
        "version": version[1],
    }
    changed.pop("type"), changed.pop("provider_key")
    saved = await sync_browser.send("PUT", f"/integrations/{ident[1]}", changed)
    assert saved.status_code == 200, saved.text

    admin = await sync_api.admin()
    stored = (await admin.get(f"{INTEGRATIONS}/{ident[1]}")).json()
    assert stored["config"]["realm"] == "other"
    assert stored["secretsSet"] == ["clientSecret"]
    async with sync_api.factory() as session:
        raw = (await session.execute(text("SELECT config FROM integration_config"))).scalar_one()
    assert raw["provider"]["clientSecret"].startswith("enc:v1:")


async def test_a_mistake_in_a_number_of_the_provider_is_shown_at_its_input(
    sync_api: Api, sync_browser: Browser
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    form = {
        "type": "directory",
        "provider_key": "keycloak",
        "name": "Keycloak",
        "config.baseUrl": "http://keycloak.test",
        "config.realm": "planner",
        "config.clientId": "c",
        "config.clientSecret": "s",
        "config.pageSize": "5",  # the schema asks for at least 10
    }
    answer = await sync_browser.send("POST", "/integrations", form)
    assert answer.status_code == 422
    assert 'id="f-config-pageSize-err"' in answer.text and "Too small." in answer.text


# --- change ----------------------------------------------------------------------------------


async def test_an_entry_that_somebody_else_changed_is_not_overwritten(
    sync_api: Api, sync_browser: Browser
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    ident = await make(sync_browser)
    edit = (await sync_browser.fragment(f"/integrations/{ident}/edit", "modal-body")).text
    version = re.search(r'name="version" value="(\d+)"', edit)
    assert version and 'value="tasks.csv"' in edit

    admin = await sync_api.admin()
    current = (await admin.get(f"{INTEGRATIONS}/{ident}")).json()
    renamed = await admin.put(
        f"{INTEGRATIONS}/{ident}",
        {"version": current["version"], "name": "Renamed", "config": {"path": "tasks.csv"}},
    )
    assert renamed.status_code == 200

    stale = await sync_browser.send(
        "PUT", f"/integrations/{ident}", {**task_form(name="Mine"), "version": version[1]}
    )
    assert stale.status_code == 412
    assert "Somebody else changed this entry" in stale.text
    assert (await admin.get(f"{INTEGRATIONS}/{ident}")).json()["name"] == "Renamed"


async def test_an_integration_whose_provider_is_gone_can_still_be_deleted(
    sync_api: Api, sync_browser: Browser
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    ident = await make(sync_browser)
    async with sync_api.factory() as session:
        await session.execute(text("UPDATE integration_config SET provider_key = 'gone'"))
        await session.commit()
    page = await sync_browser.page("/integrations")
    assert "gone" in page.text and "not installed" in page.text
    edit = await sync_browser.fragment(f"/integrations/{ident}/edit", "modal-body")
    assert edit.status_code == 409 and events(edit)["toast"]["kind"] == "error"
    dialog = await sync_browser.fragment(f"/integrations/{ident}/delete", "modal-body")
    assert dialog.status_code == 200
    version = re.search(r'name="version" value="(\d+)"', dialog.text)
    assert version
    gone = await sync_browser.send("DELETE", f"/integrations/{ident}", {"version": version[1]})
    assert gone.status_code == 200 and "No integrations yet." in gone.text


# --- test, sync, runs ------------------------------------------------------------------------


async def test_the_connection_is_tested_from_the_list(
    sync_api: Api, sync_browser: Browser, imports_dir: Path
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    write(imports_dir, "tasks.csv", "Key;Title\nA-1;One\n")
    good = await make(sync_browser)
    answer = await sync_browser.send("POST", f"/integrations/{good}/test")
    assert answer.status_code == 200 and answer.headers["hx-reswap"] == "none"
    assert events(answer)["toast"]["kind"] == "success"

    bad = await make(sync_browser, name="Missing file", path="nowhere.csv")
    answer = await sync_browser.send("POST", f"/integrations/{bad}/test")
    assert events(answer)["toast"]["kind"] == "error"


async def test_a_preview_changes_nothing_and_a_sync_does(
    sync_api: Api, sync_browser: Browser, imports_dir: Path
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    write(imports_dir, "tasks.csv", "Key;Title\nA-1;One\nA-2;Two\n")
    ident = await make(sync_browser)
    admin = await sync_api.admin()

    dialog = (await sync_browser.fragment(f"/integrations/{ident}/sync", "modal-body")).text
    assert 'name="mode" value="dry"' in dialog and 'name="mode" value="run"' in dialog

    preview = await sync_browser.send("POST", f"/integrations/{ident}/sync", {"mode": "dry"})
    assert preview.status_code == 200, preview.text
    assert "This was a preview. Nothing was saved." in preview.text
    assert "Preview" in preview.text
    assert await tasks_of(admin) == []
    assert events(preview)["refreshList"] is True

    real = await sync_browser.send("POST", f"/integrations/{ident}/sync", {"mode": "run"})
    assert real.status_code == 200 and "This was a preview" not in real.text
    assert re.search(r">2</dd>", real.text)  # two tasks created
    assert "Succeeded" in real.text
    assert {t["key"] for t in await tasks_of(admin)} == {"A-1", "A-2"}

    page = await sync_browser.page(f"/integrations/{ident}/runs")
    assert page.status_code == 200 and page.text.count("<tr>") == 3  # head and two runs
    runs = re.findall(rf'hx-get="/integrations/{ident}/runs/{ID}"', page.text)
    assert len(runs) == 2
    detail = await sync_browser.fragment(f"/integrations/{ident}/runs/{runs[0]}", "modal-body")
    assert detail.status_code == 200 and "Close" in detail.text

    listed = (await sync_browser.page("/integrations")).text
    assert "Succeeded" in listed and "never" not in listed


async def test_a_source_that_cannot_be_read_is_reported_as_failed(
    sync_api: Api, sync_browser: Browser
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    ident = await make(sync_browser, path="nowhere.csv")
    answer = await sync_browser.send("POST", f"/integrations/{ident}/sync", {"mode": "run"})
    assert answer.status_code == 200
    assert "Failed" in answer.text and "alert-triangle" not in answer.text.split("Log")[0]
    assert "circle-x" in answer.text or "text-error" in answer.text


# --- delete ----------------------------------------------------------------------------------


async def test_deleting_an_integration_asks_what_happens_to_what_it_brought_in(
    sync_api: Api, sync_browser: Browser, imports_dir: Path
) -> None:
    await sign_in_admin(sync_api, sync_browser)
    write(imports_dir, "tasks.csv", "Key;Title\nA-1;One\nA-2;Two\n")
    ident = await make(sync_browser)
    await sync_browser.send("POST", f"/integrations/{ident}/sync", {"mode": "run"})

    dialog = (await sync_browser.fragment(f"/integrations/{ident}/delete", "modal-body")).text
    version = re.search(r'name="version" value="(\d+)"', dialog)
    assert version and 'name="detach"' in dialog

    blocked = await sync_browser.send("DELETE", f"/integrations/{ident}", {"version": version[1]})
    assert blocked.status_code == 412
    assert "2 tasks" in blocked.text and blocked.headers["hx-retarget"] == "#modal-body"

    gone = await sync_browser.send(
        "DELETE", f"/integrations/{ident}", {"version": version[1], "detach": "on"}
    )
    assert gone.status_code == 200 and "No integrations yet." in gone.text
    admin = await sync_api.admin()
    kept = await tasks_of(admin)
    assert len(kept) == 2 and all(t["externalSource"] is None for t in kept)


# --- language --------------------------------------------------------------------------------


async def test_the_pages_and_the_texts_of_the_built_in_providers_come_in_german(
    sync_api: Api, make_browser: Any
) -> None:
    german = make_browser("de")
    await sync_api.add_user("zoe", "admin")
    await german.sign_in("zoe")
    page = await german.page("/integrations")
    assert "Anbindungen" in page.text and "Noch keine Anbindungen." in page.text

    form = (await german.fragment("/integrations/new/directory/keycloak", "modal-body")).text
    assert "Keycloak-Adresse" in form and "Client-Geheimnis" in form
    assert "Zertifikat prüfen" in form and "Standardteam" in form
    assert "Ein Eintrag pro Zeile" in form

    refused = await german.send("POST", "/integrations", task_form(path=""))
    assert refused.status_code == 422 and "Pflichtfeld." in refused.text


async def test_every_text_of_the_built_in_providers_is_listed_for_translation(
    sync_api: Api,
) -> None:
    admin = await sync_api.admin()
    providers = (await admin.get(f"{INTEGRATIONS}/providers")).json()["items"]
    built_in = [p for p in providers if p["key"] != "fake"]
    assert {p["key"] for p in built_in} == {"csv", "keycloak"}
    wanted: set[str] = set()
    for provider in built_in:
        wanted.add(provider["title"])
        for schema in (provider["configSchema"], provider["mappingSchema"]):
            for spec in schema["properties"].values():
                wanted |= {spec[k] for k in ("title", "description") if spec.get(k)}
    assert wanted <= set(BUILT_IN_TEXTS), sorted(wanted - set(BUILT_IN_TEXTS))
