"""Add per-user Google Drive connections and photo export operations."""

import sqlalchemy as sa
from alembic import op

revision = "0006_google_drive"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "googledriveconnection",
        sa.Column("primary_key", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("permission_id", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("folder_id", sa.String(255), nullable=True),
        sa.Column("revocation_pending", sa.Boolean(), nullable=False),
        sa.Column("oauth_state_hash", sa.String(64), nullable=True),
        sa.Column("oauth_cookie_hash", sa.String(64), nullable=True),
        sa.Column("oauth_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("oauth_state_used", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("primary_key"),
        sa.ForeignKeyConstraint(["user_id"], ["user.primary_key"]),
        sa.UniqueConstraint("user_id"),
        sa.UniqueConstraint("oauth_state_hash"),
    )
    op.create_table(
        "googledriveexport",
        sa.Column("primary_key", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("connection_generation", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("mime_type", sa.String(32), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("md5", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("drive_file_id", sa.String(255), nullable=False),
        sa.Column("web_view_link", sa.Text(), nullable=True),
        sa.Column("error_reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("primary_key"),
        sa.ForeignKeyConstraint(["user_id"], ["user.primary_key"]),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["googledriveconnection.primary_key"]
        ),
        sa.UniqueConstraint("user_id", "idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("googledriveexport")
    op.drop_table("googledriveconnection")
