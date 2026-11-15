"""task source attributes

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("task", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column(
        "task",
        sa.Column("original_estimate_hours", sa.Numeric(precision=8, scale=2), nullable=True),
    )
    op.add_column(
        "task", sa.Column("story_points", sa.Numeric(precision=8, scale=2), nullable=True)
    )
    op.add_column("task", sa.Column("priority", sa.String(length=64), nullable=True))
    op.add_column("task", sa.Column("assignee", sa.String(length=160), nullable=True))
    op.add_column("task", sa.Column("reporter", sa.String(length=160), nullable=True))


def downgrade() -> None:
    op.drop_column("task", "reporter")
    op.drop_column("task", "assignee")
    op.drop_column("task", "priority")
    op.drop_column("task", "story_points")
    op.drop_column("task", "original_estimate_hours")
    op.drop_column("task", "start_date")
    op.drop_column("task", "due_date")
