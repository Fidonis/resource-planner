"""The test database is reachable through the async driver on every platform."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def test_async_driver_reaches_postgres(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("select version()"))).scalar_one()
    finally:
        await engine.dispose()
    assert version.startswith("PostgreSQL 16")
