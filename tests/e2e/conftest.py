"""Browser tests against a running stack.

Start the stack and give the tests an account that may change master data::

    docker compose -f docker-compose.yml -f docker/docker-compose.dev.yml up -d --build
    E2E_USER=admin E2E_PASSWORD=... uv run pytest tests/e2e

``E2E_BASE_URL`` defaults to http://localhost:8080. ``E2E_BROWSER`` is a Playwright channel
(``msedge``, ``chrome``) or ``chromium`` for the browser that ``playwright install`` fetches.
Every test fails on a Content-Security-Policy violation or a script error of the page.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator

import pytest
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    StorageState,
    sync_playwright,
)

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
USER = os.environ.get("E2E_USER", "")
PASSWORD = os.environ.get("E2E_PASSWORD", "")

# What the browser logs for a response with a 4xx status, which the tests that provoke one expect.
REFUSED = re.compile(r"Failed to load resource: the server responded with a status of 4\d\d")

COLLECT = """
window.__problems = [];
document.addEventListener('securitypolicyviolation', (e) => {
  window.__problems.push('CSP: ' + e.violatedDirective + ' ' + e.blockedURI);
});
window.addEventListener('error', (e) => window.__problems.push('error: ' + e.message));
"""


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if USER and PASSWORD:
        return
    skip = pytest.mark.skip(reason="set E2E_USER and E2E_PASSWORD to run the browser tests")
    for item in items:
        if "tests/e2e" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def playwright_instance() -> Iterator[Playwright]:
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright) -> Iterator[Browser]:
    choice = os.environ.get("E2E_BROWSER", "msedge")
    if choice == "chromium":
        instance = playwright_instance.chromium.launch()
    else:
        instance = playwright_instance.chromium.launch(channel=choice)
    yield instance
    instance.close()


def new_context(browser: Browser, state: StorageState | None = None) -> BrowserContext:
    ctx = browser.new_context(
        base_url=BASE_URL,
        locale="en-US",
        viewport={"width": 1280, "height": 800},
        storage_state=state,
    )
    ctx.add_init_script(COLLECT)
    return ctx


@pytest.fixture(scope="session")
def session_state(browser: Browser) -> StorageState:
    """Sign in once; the sign-in endpoint limits attempts, and every test starts from this state."""
    ctx = new_context(browser)
    page = ctx.new_page()
    sign_in(page)
    state = ctx.storage_state()
    ctx.close()
    return state


@pytest.fixture
def context(browser: Browser, session_state: StorageState) -> Iterator[BrowserContext]:
    ctx = new_context(browser, session_state)
    yield ctx
    ctx.close()


@pytest.fixture
def page(context: BrowserContext, request: pytest.FixtureRequest) -> Iterator[Page]:
    page = context.new_page()
    expect_refusal = request.node.get_closest_marker("expects_refusal") is not None
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on(
        "console",
        lambda msg: (
            errors.append(f"console: {msg.text}")
            if msg.type == "error" and not (expect_refusal and REFUSED.search(msg.text))
            else None
        ),
    )
    yield page
    problems = page.evaluate("window.__problems || []") if not page.is_closed() else []
    assert not problems, problems
    assert not errors, errors


def sign_in(page: Page, user: str = USER, password: str = PASSWORD) -> None:
    page.goto("/login")
    page.get_by_label("User name").fill(user)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(lambda url: "/login" not in url)


@pytest.fixture
def signed_in(page: Page) -> Page:
    return page


@pytest.fixture
def unique() -> str:
    return uuid.uuid4().hex[:8]
