"""Add independent invitations to start a private QueenChat conversation."""
from alembic import op
import sqlalchemy as sa

revision = "20260811_private_chat_invites_v1"
down_revision = "20260811_private_spaces_state_v2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_chat_invites",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("creator_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.Integer(), nullable=True),
        sa.Column("accepted_by_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
    )
    op.create_index("ix_private_chat_invites_token_hash", "private_chat_invites", ["token_hash"], unique=True)
    op.create_index("ix_private_chat_invites_creator_user_id", "private_chat_invites", ["creator_user_id"])
    op.create_index("ix_private_chat_invites_expires_at", "private_chat_invites", ["expires_at"])


def downgrade():
    op.drop_table("private_chat_invites")
