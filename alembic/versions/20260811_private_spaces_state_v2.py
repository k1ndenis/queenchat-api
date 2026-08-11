"""Make pair-space activation explicit and keep existing spaces intact."""
from alembic import op
import sqlalchemy as sa

revision = "20260811_private_spaces_state_v2"
down_revision = "20260811_private_spaces_v1"
branch_labels = None
depends_on = None


def upgrade():
    # Existing rows were the old product's explicit enabled spaces.  Preserve
    # them as active; no chats without a row are backfilled.
    op.add_column("private_space_settings", sa.Column("status", sa.String(length=16), nullable=False, server_default="active"))
    op.add_column("private_space_invites", sa.Column("recipient_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("space_dates", sa.Column("emoji", sa.String(length=16), nullable=False, server_default="❤️"))
    op.add_column("space_dates", sa.Column("repeats_yearly", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_index("ix_private_space_invites_recipient_user_id", "private_space_invites", ["recipient_user_id"])


def downgrade():
    op.drop_index("ix_private_space_invites_recipient_user_id", table_name="private_space_invites")
    op.drop_column("private_space_invites", "recipient_user_id")
    op.drop_column("space_dates", "repeats_yearly")
    op.drop_column("space_dates", "emoji")
    op.drop_column("private_space_settings", "status")
