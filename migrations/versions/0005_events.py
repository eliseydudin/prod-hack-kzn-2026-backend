"""Add trip events.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event",
        sa.Column("primary_key", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("booking_url", sa.Text(), nullable=True),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("waypoint_primary_key", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["trip_id"], ["trip.primary_key"]),
        sa.ForeignKeyConstraint(
            ["waypoint_primary_key"], ["waypoint.primary_key"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("primary_key"),
    )


def downgrade() -> None:
    op.drop_table("event")
