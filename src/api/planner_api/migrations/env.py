"""Alembic environment. Runs on the blocking psycopg driver, so it needs no event loop."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool, text

from planner_api.models import Base

config = context.config
target_metadata = Base.metadata

# Two API containers starting together must not migrate at the same time.
MIGRATION_LOCK = 727274


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    assert url, "sqlalchemy.url is not set"
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=False,
        )
        with context.begin_transaction():
            connection.execute(text(f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK})"))
            context.run_migrations()
    engine.dispose()


run_migrations_online()
