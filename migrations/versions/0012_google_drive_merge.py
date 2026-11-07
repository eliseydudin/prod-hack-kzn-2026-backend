"""Join the published Google Drive and current master migration branches."""

revision = "0012_google_drive_merge"
down_revision = ("0011", "0006_google_drive")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
