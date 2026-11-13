"""Integrations in a real browser: set up, test, preview, sync, runs and removal (acceptance 10/11).

The CSV test puts its file into the import directory of the running API container with the Docker
command line; the Keycloak test needs the development Keycloak of the stack and the secret of its
import client (``E2E_KEYCLOAK_SYNC_SECRET``). Both skip themselves when that is missing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from tests.e2e.test_planning import call

API_CONTAINER = os.environ.get("E2E_API_CONTAINER", "resource-planner-api-1")
KEYCLOAK_SECRET = os.environ.get("E2E_KEYCLOAK_SYNC_SECRET", "")
KEYCLOAK_URL = os.environ.get("E2E_KEYCLOAK_URL", "")


def container_runs() -> bool:
    if not shutil.which("docker"):
        return False
    found = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", API_CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    return found.stdout.strip() == "true"


@pytest.fixture
def import_file() -> Iterator[Any]:
    """Put a file into the import directory of the running API; removed again afterwards."""
    if not container_runs():
        pytest.skip(f"the container {API_CONTAINER} is not running")
    written: list[str] = []

    def put(name: str, text: str) -> None:
        subprocess.run(
            ["docker", "exec", "-i", API_CONTAINER, "sh", "-c", f"cat > /data/imports/{name}"],
            input=text.encode("utf-8"),
            check=True,
        )
        written.append(name)

    yield put
    for name in written:
        subprocess.run(
            ["docker", "exec", API_CONTAINER, "rm", "-f", f"/data/imports/{name}"], check=False
        )


def items(page: Page, path: str, query: str = "") -> list[dict[str, Any]]:
    found = call(page, "GET", f"{path}?{query}&pageSize=200")
    return list(found["items"])


def forget_integration(page: Page, name: str) -> None:
    for row in items(page, "/integrations"):
        if row["name"] == name:
            call(page, "DELETE", f"/integrations/{row['id']}?version={row['version']}&detach=true")


def forget_tasks(page: Page, prefix: str) -> None:
    for task in items(page, "/tasks", f"q={prefix}"):
        if task["key"].startswith(prefix):
            call(page, "DELETE", f"/tasks/{task['id']}?version={task['version']}")


@pytest.mark.expects_refusal
def test_a_csv_integration_is_set_up_previewed_synced_and_removed(
    signed_in: Page, unique: str, import_file: Any
) -> None:
    page = signed_in
    name = f"E2E CSV {unique}"
    prefix = f"E2E-{unique}"
    import_file(f"{unique}.csv", f"Key;Title\n{prefix}-1;First\n{prefix}-2;Second\n")
    dialog = page.locator("#modal")
    try:
        page.goto("/integrations")
        expect(page.get_by_role("heading", name="Integrations")).to_be_visible()

        # set up
        page.get_by_role("button", name="New integration").click()
        dialog.get_by_role("button", name="CSV file (tasks)").click()
        dialog.locator("#f-name").fill(name)
        dialog.locator("#f-config-path").fill(f"{unique}.csv")
        dialog.get_by_role("button", name="Create").click()
        expect(dialog).not_to_be_visible()
        row = page.locator("tr", has_text=name)
        expect(row).to_contain_text("CSV file (tasks)")
        expect(row).to_contain_text("never")

        # test the connection
        row.get_by_role("button", name=f"Test the connection of {name}").click()
        expect(page.locator("#toasts")).to_contain_text("The file can be read (2 rows).")

        # a preview saves nothing
        row.get_by_role("button", name=f"Sync {name} now").click()
        dialog.get_by_role("button", name="Preview").click()
        expect(dialog).to_contain_text("This was a preview. Nothing was saved.")
        dialog.get_by_role("button", name="Done").click()
        assert [
            t for t in items(page, "/tasks", f"q={prefix}") if t["key"].startswith(prefix)
        ] == []

        # the real sync
        row.get_by_role("button", name=f"Sync {name} now").click()
        dialog.get_by_role("button", name="Sync now").click()
        expect(dialog).to_contain_text("Succeeded")
        expect(dialog.locator("dl")).to_contain_text("2")
        dialog.get_by_role("button", name="Done").click()
        expect(row).to_contain_text("Succeeded")
        made = [t for t in items(page, "/tasks", f"q={prefix}") if t["key"].startswith(prefix)]
        assert len(made) == 2 and all(t["externalSource"] for t in made)

        # what the source owns cannot be edited here
        page.goto(f"/tasks?q={prefix}-1")
        page.get_by_role("button", name=f"Edit {prefix}-1").click()
        expect(dialog.locator("#f-title")).to_have_attribute("readonly", "")
        dialog.get_by_role("button", name="Cancel").click()

        # runs and their log
        page.goto("/integrations")
        row = page.locator("tr", has_text=name)
        row.get_by_role("link", name=f"Runs of {name}").click()
        expect(page.get_by_role("heading", name=f"Runs of {name}")).to_be_visible()
        expect(page.locator("tbody tr")).to_have_count(2)
        page.locator("tbody tr").first.get_by_role("button").click()
        expect(dialog).to_contain_text("Created")
        dialog.locator("#modal-body").get_by_role("button", name="Close").click()

        # delete: what it wrote must be kept or removed first
        page.goto("/integrations")
        row = page.locator("tr", has_text=name)
        row.get_by_role("button", name=f"Delete {name}").click()
        detach = dialog.get_by_label("Keep what it brought in as manual data")
        expect(detach).not_to_be_checked()
        dialog.get_by_role("button", name="Delete").click()
        expect(dialog).to_contain_text("2 tasks")
        expect(detach).to_be_checked()
        dialog.get_by_role("button", name="Delete").click()
        expect(dialog).not_to_be_visible()
        expect(page.locator("tr", has_text=name)).to_have_count(0)
        kept = [t for t in items(page, "/tasks", f"q={prefix}") if t["key"].startswith(prefix)]
        assert len(kept) == 2 and all(t["externalSource"] is None for t in kept)
    finally:
        forget_integration(page, name)
        forget_tasks(page, prefix)


@pytest.mark.skipif(
    not (KEYCLOAK_SECRET and KEYCLOAK_URL),
    reason="set E2E_KEYCLOAK_URL and E2E_KEYCLOAK_SYNC_SECRET",
)
def test_the_people_of_the_development_keycloak_are_imported(signed_in: Page, unique: str) -> None:
    page = signed_in
    name = f"E2E Keycloak {unique}"
    teams = {"Backend": f"Backend {unique}", "Frontend": f"Frontend {unique}"}
    default = f"Unassigned {unique}"
    call(page, "POST", "/teams", {"name": default, "color": "#336699"})
    dialog = page.locator("#modal")
    try:
        page.goto("/integrations")
        page.get_by_role("button", name="New integration").click()
        dialog.get_by_role("button", name="Keycloak").click()
        dialog.locator("#f-name").fill(name)
        dialog.locator("#f-config-baseUrl").fill("http://keycloak:8080")
        dialog.locator("#f-config-realm").fill("planner")
        dialog.locator("#f-config-clientId").fill("resource-planner-sync")
        dialog.locator("#f-config-clientSecret").fill(KEYCLOAK_SECRET)
        dialog.locator("#f-mapping-groupTeams").fill(
            "\n".join(f"{group}={team}" for group, team in teams.items())
        )
        dialog.locator("#f-mapping-createTeams").check()
        dialog.locator("#f-mapping-defaultTeam").fill(default)
        dialog.get_by_role("button", name="Create").click()
        expect(dialog).not_to_be_visible()
        row = page.locator("tr", has_text=name)

        row.get_by_role("button", name=f"Test the connection of {name}").click()
        expect(page.locator("#toasts")).to_contain_text("planner")

        row.get_by_role("button", name=f"Sync {name} now").click()
        dialog.get_by_role("button", name="Sync now").click()
        expect(dialog).to_contain_text("Succeeded")
        dialog.get_by_role("button", name="Done").click()

        page.goto("/members?q=Planner")
        expect(page.locator("tr", has_text="Paula Planner")).to_contain_text(teams["Backend"])
        page.goto("/members?q=Viewer")
        expect(page.locator("tr", has_text="Viktor Viewer")).to_contain_text(teams["Frontend"])

        # the second run finds nothing to change
        page.goto("/integrations")
        row = page.locator("tr", has_text=name)
        row.get_by_role("button", name=f"Sync {name} now").click()
        dialog.get_by_role("button", name="Sync now").click()
        expect(dialog).to_contain_text("Succeeded")
        expect(dialog.locator("dl div").nth(0)).to_contain_text("0")
        expect(dialog.locator("dl div").nth(2)).to_contain_text("3")
        dialog.get_by_role("button", name="Done").click()
    finally:
        forget_integration(page, name)
        for member in items(page, "/members"):
            mine = member["teamName"] in {*teams.values(), default}
            if mine and re.search(
                r"(Paula Planner|Viktor Viewer|Ada Admin)", member["displayName"]
            ):
                call(page, "DELETE", f"/members/{member['id']}?version={member['version']}")
        for team in items(page, "/teams"):
            if team["name"] in {*teams.values(), default}:
                call(page, "DELETE", f"/teams/{team['id']}?version={team['version']}")
