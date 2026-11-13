"""Single sign-on through the development Keycloak (acceptance case 9).

Needs the stack with the profile ``dev-keycloak`` and the OIDC variables of ``.env.example``::

    docker compose -f docker-compose.yml -f docker/docker-compose.dev.yml \\
        --profile dev-keycloak up -d
    E2E_USER=admin E2E_PASSWORD=... E2E_KEYCLOAK_URL=http://localhost:8180 \\
    E2E_KEYCLOAK_PASSWORD=<KEYCLOAK_DEMO_PASSWORD> E2E_OIDC_CLIENT_SECRET=<OIDC_CLIENT_SECRET> \\
    uv run pytest tests/e2e/test_oidc.py

The tests skip themselves when those variables are missing.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, expect
from tests.e2e.conftest import BASE_URL, new_context

KEYCLOAK = os.environ.get("E2E_KEYCLOAK_URL", "")
DEMO_PASSWORD = os.environ.get("E2E_KEYCLOAK_PASSWORD", "")
CLIENT_SECRET = os.environ.get("E2E_OIDC_CLIENT_SECRET", "")

pytestmark = pytest.mark.skipif(
    not (KEYCLOAK and DEMO_PASSWORD and CLIENT_SECRET),
    reason="set E2E_KEYCLOAK_URL, E2E_KEYCLOAK_PASSWORD and E2E_OIDC_CLIENT_SECRET",
)

VIEWER = {
    "team:read",
    "skill:read",
    "member:read",
    "task:read",
    "assignment:read",
    "absence:read",
    "absence-type:read",
}
PLANNER = VIEWER | {"task:write", "assignment:write", "absence:write"}


@pytest.fixture
def anonymous(browser: Browser) -> Iterator[BrowserContext]:
    """A browser that has signed in nowhere (the other tests start from a signed-in state)."""
    ctx = new_context(browser)
    yield ctx
    ctx.close()


@pytest.fixture
def other_context(browser: Browser) -> Iterator[BrowserContext]:
    ctx = new_context(browser)
    yield ctx
    ctx.close()


def mappings_of(context: BrowserContext) -> list[dict[str, Any]]:
    response = context.request.get(f"{BASE_URL}/api/v1/role-mappings")
    assert response.ok, response.text()
    return [
        {"externalRole": m["externalRole"], "roleId": m["roleId"]}
        for m in response.json()["mappings"]
    ]


def put_mappings(context: BrowserContext, mappings: list[dict[str, Any]]) -> None:
    csrf = me(context)["csrfToken"]
    response = context.request.put(
        f"{BASE_URL}/api/v1/role-mappings",
        data={"mappings": mappings},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.ok, response.text()


def single_sign_on(page: Page, user: str) -> None:
    page.goto("/login")
    page.get_by_role("link", name="Sign in with single sign-on").click()
    page.locator("#username").fill(user)
    page.locator("#password").fill(DEMO_PASSWORD)
    page.locator("#kc-login").click()
    page.wait_for_url(lambda url: url.startswith(BASE_URL) and "/login" not in url)


def me(context: BrowserContext) -> dict[str, Any]:
    response = context.request.get(f"{BASE_URL}/api/v1/auth/me")
    assert response.ok, response.text()
    found: dict[str, Any] = response.json()
    return found


def status_of(page: Page, url: str) -> int:
    response = page.goto(url)
    assert response is not None
    return response.status


def keycloak_token(playwright: Playwright, user: str) -> str:
    request = playwright.request.new_context()
    try:
        response = request.post(
            f"{KEYCLOAK}/realms/planner/protocol/openid-connect/token",
            form={
                "grant_type": "password",
                "client_id": "resource-planner",
                "client_secret": CLIENT_SECRET,
                "username": user,
                "password": DEMO_PASSWORD,
            },
        )
        assert response.ok, response.text()
        return str(response.json()["access_token"])
    finally:
        request.dispose()


def test_a_planner_of_the_identity_provider_gets_exactly_the_planner_permissions(
    anonymous: BrowserContext,
) -> None:
    page = anonymous.new_page()
    single_sign_on(page, "planner-demo")
    expect(page.get_by_role("heading", name="Planning board")).to_be_visible()

    found = me(anonymous)
    assert set(found["permissions"]) == PLANNER
    assert found["authProvider"] == "oidc:oidc"
    assert found["displayName"] == "Paula Planner"


def test_a_viewer_of_the_identity_provider_can_look_but_not_manage(
    anonymous: BrowserContext,
) -> None:
    page = anonymous.new_page()
    single_sign_on(page, "viewer-demo")

    assert set(me(anonymous)["permissions"]) == VIEWER
    assert status_of(page, "/users") == 403
    assert status_of(page, "/api-tokens") == 403


def test_the_administrator_sees_the_accounts_that_signed_in_with_their_roles(
    browser: Browser, anonymous: BrowserContext
) -> None:
    for user in ("planner-demo", "viewer-demo"):
        ctx = new_context(browser)
        try:
            single_sign_on(ctx.new_page(), user)
        finally:
            ctx.close()

    page = anonymous.new_page()
    single_sign_on(page, "admin-demo")
    assert "user:admin" in me(anonymous)["permissions"]

    page.goto("/users?source=sso")
    row = page.get_by_role("row").filter(has_text="Paula Planner")
    expect(row).to_contain_text("rp-planner")
    expect(row).to_contain_text("Single sign-on")
    expect(page.get_by_role("row").filter(has_text="Viktor Viewer")).to_contain_text("rp-viewer")
    # The name and the e-mail belong to the identity provider.
    page.get_by_role("button", name="Edit Paula Planner").click()
    expect(page.locator("#f-display_name")).to_have_attribute("readonly", "")


def test_a_changed_role_mapping_applies_without_a_new_sign_in(
    anonymous: BrowserContext, other_context: BrowserContext
) -> None:
    single_sign_on(other_context.new_page(), "viewer-demo")
    assert set(me(other_context)["permissions"]) == VIEWER

    admin = anonymous.new_page()
    single_sign_on(admin, "admin-demo")
    original = mappings_of(anonymous)
    try:
        admin.goto("/roles")
        admin.get_by_role("button", name="Role mapping").click()
        dialog = admin.locator("#modal-body")
        row = dialog.locator("[data-row]").filter(has=admin.locator("input[value='rp-viewer']"))
        row.locator("select").select_option(label="Planner")
        dialog.get_by_role("button", name="Save").click()
        expect(admin.locator("#toasts")).to_contain_text("Saved.")
        expect(admin.locator("#modal")).not_to_be_visible()  # the dialog closed itself

        assert set(me(other_context)["permissions"]) == PLANNER
    finally:
        put_mappings(anonymous, original)  # the other tests expect the realm as it was imported
    assert set(me(other_context)["permissions"]) == VIEWER


def test_signing_out_ends_the_session_at_the_identity_provider_too(
    anonymous: BrowserContext,
) -> None:
    page = anonymous.new_page()
    single_sign_on(page, "planner-demo")

    page.get_by_role("button", name=re.compile("Account menu")).click()
    page.get_by_role("button", name="Sign out").click()
    page.wait_for_url(f"{BASE_URL}/login**")
    assert anonymous.request.get(f"{BASE_URL}/api/v1/auth/me").status == 401

    # Keycloak asks for the password again: its own session ended as well.
    page.get_by_role("link", name="Sign in with single sign-on").click()
    expect(page.locator("#username")).to_be_visible()


def test_a_token_of_the_identity_provider_works_as_a_bearer_token(
    playwright_instance: Playwright,
) -> None:
    token = keycloak_token(playwright_instance, "planner-demo")
    request = playwright_instance.request.new_context(base_url=BASE_URL)
    try:
        response = request.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.ok, response.text()
        found = response.json()
        assert found["via"] == "bearer" and set(found["permissions"]) == PLANNER

        refused = request.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-token"})
        assert refused.status == 401
    finally:
        request.dispose()
