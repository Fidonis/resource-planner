"""Shared pytest setup: event loop choice and a throw-away PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

if sys.platform == "win32":
    # pytest-asyncio rejects an implementation of this hook that returns nothing, so it is only
    # defined where it is needed; elsewhere the default loop is used.
    def pytest_asyncio_loop_factories(config: Any, item: Any) -> dict[str, Any]:
        """psycopg's async driver cannot run on the Proactor loop that Windows uses by default."""
        return {"selector": asyncio.SelectorEventLoop}


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """SQLAlchemy URL (psycopg 3) of an empty PostgreSQL 16 database.

    ``TEST_DATABASE_URL`` points at an existing server whose database may be emptied; without
    it a container is started. The host is spelled 127.0.0.1 because ``localhost`` resolves to
    ::1 first on Docker Desktop and the connection then stalls before falling back to IPv4.
    """
    existing = os.environ.get("TEST_DATABASE_URL")
    if existing:
        yield existing
        return

    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        host = pg.get_container_host_ip()
        if host == "localhost":
            host = "127.0.0.1"
        port = pg.get_exposed_port(5432)
        yield f"postgresql+psycopg://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}"


@pytest.fixture
def scratch_database_url(database_url: str) -> Iterator[str]:
    """An empty database next to the shared one, for tests that drop or rebuild the schema."""
    base = make_url(database_url)
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
