"""Add private pair spaces and one-time invite storage."""
from alembic import op
import sqlalchemy as sa

revision = "20260811_private_spaces_v1"
down_revision = "20260810_admin_panel_v1"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "private_space_settings",
        sa.Column("chat_id", sa.String(), sa.ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("theme", sa.String(length=24), nullable=False, server_default="queen"),
        sa.Column("accent", sa.String(length=24), nullable=True),
        sa.Column("cover_image", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "private_space_invites",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("creator_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.String(), sa.ForeignKey("chats.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.Integer(), nullable=True),
        sa.Column("accepted_by_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
    )
    op.create_index("ix_private_space_invites_token_hash", "private_space_invites", ["token_hash"], unique=True)
    op.create_index("ix_private_space_invites_creator_user_id", "private_space_invites", ["creator_user_id"])
    op.create_index("ix_private_space_invites_chat_id", "private_space_invites", ["chat_id"])
    op.create_index("ix_private_space_invites_expires_at", "private_space_invites", ["expires_at"])
    op.create_table(
        "space_memories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("chat_id", sa.String(), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.String(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("saved_by_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_space_memories_chat_message"),
    )
    op.create_index("ix_space_memories_chat_id", "space_memories", ["chat_id"])
    op.create_index("ix_space_memories_message_id", "space_memories", ["message_id"])
    op.create_table(
        "space_dates",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("chat_id", sa.String(), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("event_date", sa.String(length=10), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_space_dates_chat_id", "space_dates", ["chat_id"])
    op.create_table(
        "space_notes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("chat_id", sa.String(), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("note_type", sa.String(length=12), nullable=False, server_default="note"),
        sa.Column("due_date", sa.String(length=10), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_space_notes_chat_id", "space_notes", ["chat_id"])

def downgrade():
    op.drop_index("ix_space_notes_chat_id", table_name="space_notes")
    op.drop_table("space_notes")
    op.drop_index("ix_space_dates_chat_id", table_name="space_dates")
    op.drop_table("space_dates")
    op.drop_index("ix_space_memories_message_id", table_name="space_memories")
    op.drop_index("ix_space_memories_chat_id", table_name="space_memories")
    op.drop_table("space_memories")
    op.drop_index("ix_private_space_invites_expires_at", table_name="private_space_invites")
    op.drop_index("ix_private_space_invites_chat_id", table_name="private_space_invites")
    op.drop_index("ix_private_space_invites_creator_user_id", table_name="private_space_invites")
    op.drop_index("ix_private_space_invites_token_hash", table_name="private_space_invites")
    op.drop_table("private_space_invites")
    op.drop_table("private_space_settings")
