"""Fixtures for tests that call the real application on a real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.api.oidc_support import (
    API_AUDIENCE,
    CLIENT_ID,
    CLIENT_SECRET,
    DISCOVERY_URL,
    ISSUER,
    FakeIdp,
)
from tests.api.support import BOOTSTRAP_PASSWORD, BOOTSTRAP_USER, Api

from planner_api import bootstrap
from planner_api.auth.oidc import OidcProvider
from planner_api.auth.ratelimit import RateLimiter
from planner_api.config import Settings
from planner_api.main import create_app
from planner_api.models import Base
from planner_api.providers.registry import GROUP, default_registry, load_entry_points


@pytest_asyncio.fixture(scope="session")
async def app(database_url: str) -> AsyncIterator[FastAPI]:
    settings = Settings(
        database_url=database_url,
        bootstrap_admin_user=BOOTSTRAP_USER,
        bootstrap_admin_password=BOOTSTRAP_PASSWORD,
        login_max_attempts=3,
        login_lockout_minutes=15,
        scheduler_enabled=False,
        enable_demo_seed=True,
        log_level="WARNING",
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def api(app: FastAPI) -> AsyncIterator[Api]:
    """A clean database with the start-up data, and a factory for callers."""
    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with factory() as session:
        await session.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        await session.commit()
    app.state.login_limiter = RateLimiter(limit=20, window_seconds=300)
    await bootstrap.reconcile(factory, app.state.settings)
    harness = Api(app, factory, app.state.settings)
    try:
        yield harness
    finally:
        await harness.close()


@pytest_asyncio.fixture(scope="session")
async def idp() -> FakeIdp:
    return FakeIdp()


@pytest_asyncio.fixture(scope="session")
async def oidc_app(database_url: str, idp: FakeIdp) -> AsyncIterator[FastAPI]:
    """The application with single sign-on and bearer tokens on, against a fake provider."""
    settings = Settings(
        database_url=database_url,
        bootstrap_admin_user=BOOTSTRAP_USER,
        bootstrap_admin_password=BOOTSTRAP_PASSWORD,
        auth_oidc_enabled=True,
        oidc_issuer=ISSUER,
        oidc_discovery_url=DISCOVERY_URL,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
        oidc_api_audiences=API_AUDIENCE,
        public_base_url="http://localhost:8080",
        scheduler_enabled=False,
        log_level="WARNING",
    )
    application = create_app(settings)
    application.state.oidc = OidcProvider(settings, transport=idp.transport())
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def oidc_api(oidc_app: FastAPI, idp: FakeIdp) -> AsyncIterator[Api]:
    """Like ``api`` for the application with single sign-on; the fake provider starts fresh."""
    factory: async_sessionmaker[AsyncSession] = oidc_app.state.session_factory
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with factory() as session:
        await session.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        await session.commit()
    oidc_app.state.login_limiter = RateLimiter(limit=20, window_seconds=300)
    oidc_app.state.oidc = OidcProvider(oidc_app.state.settings, transport=idp.transport())
    idp.reset()
    await bootstrap.reconcile(factory, oidc_app.state.settings)
    harness = Api(oidc_app, factory, oidc_app.state.settings)
    try:
        yield harness
    finally:
        await harness.close()


def _empty(directory: Path) -> None:
    for leftover in directory.iterdir():
        leftover.unlink()


@pytest.fixture(scope="session")
def imports_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("imports")


@pytest_asyncio.fixture(scope="session")
async def sync_app(database_url: str, imports_dir: Path) -> AsyncIterator[FastAPI]:
    """The application with an import directory, an encryption key and one more provider."""
    settings = Settings(
        database_url=database_url,
        bootstrap_admin_user=BOOTSTRAP_USER,
        bootstrap_admin_password=BOOTSTRAP_PASSWORD,
        import_dir=imports_dir,
        app_encryption_key="a key for the tests only",
        scheduler_enabled=False,
        log_level="WARNING",
    )
    registry = default_registry()
    for added in load_entry_points(
        [EntryPoint("fake", "tests.api.sync_support:FakeTaskSource", GROUP)]
    ).all():
        registry.add(added)
    application = create_app(settings, registry)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def sync_api(sync_app: FastAPI, imports_dir: Path) -> AsyncIterator[Api]:
    """Like ``api`` for the application of ``sync_app``; the import directory starts empty."""
    factory: async_sessionmaker[AsyncSession] = sync_app.state.session_factory
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with factory() as session:
        await session.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        await session.commit()
    _empty(imports_dir)
    sync_app.state.login_limiter = RateLimiter(limit=20, window_seconds=300)
    await bootstrap.reconcile(factory, sync_app.state.settings)
    harness = Api(sync_app, factory, sync_app.state.settings)
    try:
        yield harness
    finally:
        await harness.close()
