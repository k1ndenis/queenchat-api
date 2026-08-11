"""Add administrative roles, account block state, and audit log."""
from alembic import op
import sqlalchemy as sa

revision = "20260810_admin_panel_v1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("role", sa.String(length=16), nullable=False, server_default="user"))
    op.add_column("users", sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("blocked_at", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("blocked_reason", sa.Text(), nullable=True))
    op.create_check_constraint("ck_users_role", "users", "role IN ('user', 'admin')")
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_is_blocked", "users", ["is_blocked"])
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("admin_user_id", sa.String(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_admin_audit_log_admin_user_id", "admin_audit_log", ["admin_user_id"])
    op.create_index("ix_admin_audit_log_action", "admin_audit_log", ["action"])
    op.create_index("ix_admin_audit_log_target_type", "admin_audit_log", ["target_type"])
    op.create_index("ix_admin_audit_log_target_id", "admin_audit_log", ["target_id"])
    op.create_index("ix_admin_audit_log_created_at", "admin_audit_log", ["created_at"])


def downgrade():
    for name in ("ix_admin_audit_log_created_at", "ix_admin_audit_log_target_id", "ix_admin_audit_log_target_type", "ix_admin_audit_log_action", "ix_admin_audit_log_admin_user_id"):
        op.drop_index(name, table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    op.drop_index("ix_users_is_blocked", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "blocked_reason")
    op.drop_column("users", "blocked_at")
    op.drop_column("users", "is_blocked")
    op.drop_column("users", "role")
