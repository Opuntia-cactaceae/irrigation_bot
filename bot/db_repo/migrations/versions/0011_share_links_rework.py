"""
Share Links: replace schedule shares/subscriptions with share links and members.

Revision ID: 0011_share_links
Revises: 0010_users_pk_is_tg_id
Create Date: 2025-10-13 21:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0011_share_links"
down_revision = "0010_users_pk_is_tg_id"
branch_labels = None
depends_on = None

def upgrade():
    # 1) Extend ActionSource enum type with new value 'SHARED'
    # 1) Идемпотентно добавляем 'SHARED' в enum actionsource
    with op.get_context().autocommit_block():
        op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname = 'actionsource'
                  AND e.enumlabel = 'SHARED'
            ) THEN
                ALTER TYPE actionsource ADD VALUE 'SHARED';
            END IF;
        END
        $$;
        """)

    # 2) Create new enum type for ShareMemberStatus
    share_member_status_enum = postgresql.ENUM('PENDING', 'ACTIVE', 'REMOVED', 'BLOCKED', name='sharememberstatus')
    share_member_status_enum.create(op.get_bind())

    # 3) Create table share_links (replaces schedule_shares)
    op.create_table(
        "share_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=True),
        sa.Column("note", sa.String(length=128), nullable=True),
        sa.Column("allow_complete_default", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("show_history_default", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("uses_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_share_links_code")
    )
    # Create indexes for share_links
    op.create_index("ix_share_links_code", "share_links", ["code"], unique=True)
    op.create_index("ix_share_links_owner_user_id", "share_links", ["owner_user_id"], unique=False)

    # 4) Create table share_link_schedules (association between share_links and schedules)
    op.create_table(
        "share_link_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("share_id", sa.Integer(), sa.ForeignKey("share_links.id", ondelete="CASCADE")),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("schedules.id", ondelete="CASCADE")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("share_id", "schedule_id", name="uq_share_schedule")
    )
    op.create_index("ix_share_link_schedules_share_id", "share_link_schedules", ["share_id"], unique=False)
    op.create_index("ix_share_link_schedules_schedule_id", "share_link_schedules", ["schedule_id"], unique=False)

    # 5) Create table share_members (replaces schedule_subscriptions)
    op.create_table(
        "share_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("share_id", sa.Integer(), sa.ForeignKey("share_links.id", ondelete="CASCADE")),
        sa.Column("subscriber_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("status", share_member_status_enum, nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("can_complete_override", sa.Boolean(), nullable=True),
        sa.Column("show_history_override", sa.Boolean(), nullable=True),
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("joined_at_utc", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("removed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("share_id", "subscriber_user_id", name="uq_share_member")
    )
    op.create_index("ix_share_members_share_id", "share_members", ["share_id"], unique=False)
    op.create_index("ix_share_members_subscriber_user_id", "share_members", ["subscriber_user_id"], unique=False)

    # 6) Add new columns to action_logs for share context
    op.add_column("action_logs", sa.Column("share_id", sa.Integer(), nullable=True))
    op.add_column("action_logs", sa.Column("share_member_id", sa.Integer(), nullable=True))
    # Create indexes on the new columns
    op.create_index("ix_action_logs_share_id", "action_logs", ["share_id"], unique=False)
    op.create_index("ix_action_logs_share_member_id", "action_logs", ["share_member_id"], unique=False)
    # Add foreign key constraints for new columns (with ON DELETE SET NULL)
    op.create_foreign_key("action_logs_share_id_fkey", "action_logs", "share_links", ["share_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("action_logs_share_member_id_fkey", "action_logs", "share_members", ["share_member_id"], ["id"], ondelete="SET NULL")

    # 7) Drop old tables schedule_shares and schedule_subscriptions
    op.drop_table("schedule_subscriptions")
    op.drop_table("schedule_shares")

def downgrade():
    raise NotImplementedError("Downgrade is not supported for 0011_share_links")