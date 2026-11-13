"""Fixtures for tests of the web interface: the real interface in front of the real API."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from tests.api.conftest import (  # noqa: F401 - the fixtures of the API tests
    api,
    app,
    imports_dir,
    sync_api,
    sync_app,
)
from tests.api.support import USER_PASSWORD, Api

from planner_client import PlannerApi
from planner_ui.config import Settings
from planner_ui.main import create_app

CSRF = re.compile(r"""hx-headers='\{"X-CSRF-Token": "([^"]+)"\}'""")
FORM_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')
LOGIN_TOKEN = re.compile(r'name="login_token" value="([^"]+)"')


class Browser:
    """A stand-in for one browser: its own cookie jar, and the CSRF token of the page it saw."""

    def __init__(self, application: Any, language: str | None = None) -> None:
        headers = {"Accept-Language": language} if language else {}
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://test",
            headers=headers,
            follow_redirects=False,
        )
        self.csrf: str | None = None

    async def close(self) -> None:
        await self.http.aclose()

    async def login(self, username: str, password: str = USER_PASSWORD) -> httpx.Response:
        page = await self.http.get("/login")
        token = LOGIN_TOKEN.search(page.text)
        assert token, page.text
        return await self.http.post(
            "/login", data={"username": username, "password": password, "login_token": token[1]}
        )

    async def sign_in(self, username: str, password: str = USER_PASSWORD) -> None:
        response = await self.login(username, password)
        assert response.status_code == 303, response.text
        await self.page("/account")

    async def page(self, url: str, **kwargs: Any) -> httpx.Response:
        """A full page request; remembers the CSRF token the page carries."""
        response = await self.http.get(url, **kwargs)
        found = CSRF.search(response.text) or FORM_CSRF.search(response.text)
        if found:
            self.csrf = found[1]
        return response

    def htmx(self, **extra: str) -> dict[str, str]:
        headers = {"HX-Request": "true", "HX-Current-URL": "http://test/teams", **extra}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        return headers

    async def fragment(self, url: str, target: str = "list") -> httpx.Response:
        return await self.http.get(url, headers=self.htmx(**{"HX-Target": target}))

    async def send(
        self, method: str, url: str, data: dict[str, Any] | None = None
    ) -> httpx.Response:
        """As htmx does: the fields of a GET or DELETE go in the address, the others in the body."""
        if method in {"GET", "DELETE"}:
            return await self.http.request(method, url, params=data, headers=self.htmx())
        return await self.http.request(method, url, data=data, headers=self.htmx())


@pytest.fixture
def ui_settings() -> Settings:
    return Settings(api_base_url="http://test", public_base_url="http://test", log_level="WARNING")


@pytest_asyncio.fixture
async def ui(api: Api, ui_settings: Settings) -> AsyncIterator[Any]:  # noqa: F811
    client = PlannerApi("http://test", transport=httpx.ASGITransport(app=api.app))
    application = create_app(ui_settings, api=client)
    try:
        yield application
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def sync_ui(sync_api: Api, ui_settings: Settings) -> AsyncIterator[Any]:  # noqa: F811
    """The interface in front of the API that has an import directory and extra providers."""
    client = PlannerApi("http://test", transport=httpx.ASGITransport(app=sync_api.app))
    application = create_app(ui_settings, api=client)
    try:
        yield application
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def sync_browser(sync_ui: Any) -> AsyncIterator[Browser]:
    instance = Browser(sync_ui)
    try:
        yield instance
    finally:
        await instance.close()


@pytest_asyncio.fixture
async def browser(ui: Any) -> AsyncIterator[Browser]:
    instance = Browser(ui)
    try:
        yield instance
    finally:
        await instance.close()


@pytest_asyncio.fixture
async def make_browser(ui: Any) -> AsyncIterator[Any]:
    created: list[Browser] = []

    def make(language: str | None = None) -> Browser:
        instance = Browser(ui, language)
        created.append(instance)
        return instance

    yield make
    for instance in created:
        await instance.close()
