"""Rename journey to trip and add trip fields.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("journey", "trip")
    op.alter_column("waypoint", "journey_id", new_column_name="trip_id")
    op.add_column("trip", sa.Column("cover", sa.Text(), nullable=True))
    op.add_column(
        "trip",
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_column("trip", "updated_at")
    op.drop_column("trip", "cover")
    op.alter_column("waypoint", "trip_id", new_column_name="journey_id")
    op.rename_table("trip", "journey")
