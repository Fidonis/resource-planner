"""The migration scripts and the ORM models describe the same schema."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from planner_api import migrate
from planner_api.models import Base


def test_upgrade_head_matches_models_and_downgrades_cleanly(scratch_database_url: str) -> None:
    url = scratch_database_url
    engine = create_engine(url)
    try:
        migrate.upgrade(url)
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == [], f"models and migrations differ: {diff}"

        migrate.downgrade(url, "base")
        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text(
                        "select table_name from information_schema.tables "
                        "where table_schema = 'public'"
                    )
                )
            }
        assert tables <= {"alembic_version"}

        migrate.upgrade(url)
    finally:
        engine.dispose()
