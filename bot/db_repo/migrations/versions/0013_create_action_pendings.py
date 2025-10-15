"""create action_pendings and action_pending_messages tables"""

from alembic import op
import sqlalchemy as sa

revision = "0013_create_action_pendings"
down_revision = "0012_add_tg_username_to_users"
branch_labels = None
depends_on = None

ActionTypeEnum = sa.Enum(name="actiontype", create_type=False)
ActionStatusEnum = sa.Enum(name="actionstatus", create_type=False)
ActionSourceEnum = sa.Enum(name="actionsource", create_type=False)


def upgrade():
    # action_pendings
    op.create_table(
        "action_pendings",
        sa.Column("id", sa.Integer(), primary_key=True),

        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plant_id", sa.Integer(), sa.ForeignKey("plants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),

        sa.Column("action", ActionTypeEnum, nullable=False),
        sa.Column("planned_run_at_utc", sa.DateTime(timezone=True), nullable=False),

        sa.Column("resolved_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_by_log_id", sa.Integer(), sa.ForeignKey("action_logs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_status", ActionStatusEnum, nullable=True),
        sa.Column("resolved_source", ActionSourceEnum, nullable=True),

        sa.UniqueConstraint("schedule_id", "planned_run_at_utc", name="uq_pending_sched_run_at"),
    )

    # индексы для action_pendings
    op.create_index("ix_action_pendings_schedule_id", "action_pendings", ["schedule_id"])
    op.create_index("ix_action_pendings_plant_id", "action_pendings", ["plant_id"])
    op.create_index("ix_action_pendings_owner_user_id", "action_pendings", ["owner_user_id"])
    op.create_index("ix_action_pendings_planned_run_at_utc", "action_pendings", ["planned_run_at_utc"])
    op.create_index("ix_action_pendings_resolved_by_user_id", "action_pendings", ["resolved_by_user_id"])
    op.create_index("ix_action_pendings_resolved_by_log_id", "action_pendings", ["resolved_by_log_id"])

    # action_pending_messages
    op.create_table(
        "action_pending_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pending_id", sa.Integer(), sa.ForeignKey("action_pendings.id", ondelete="CASCADE"), nullable=False),

        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),

        sa.Column("share_id", sa.Integer(), sa.ForeignKey("share_links.id", ondelete="SET NULL"), nullable=True),
        sa.Column("share_member_id", sa.Integer(), sa.ForeignKey("share_members.id", ondelete="SET NULL"), nullable=True),
    )

    # индексы для action_pending_messages
    op.create_index("ix_action_pending_messages_pending_id", "action_pending_messages", ["pending_id"])
    op.create_index("ix_action_pending_messages_chat_id", "action_pending_messages", ["chat_id"])
    op.create_index("ix_action_pending_messages_share_id", "action_pending_messages", ["share_id"])
    op.create_index("ix_action_pending_messages_share_member_id", "action_pending_messages", ["share_member_id"])


def downgrade():
    op.drop_index("ix_action_pending_messages_share_member_id", table_name="action_pending_messages")
    op.drop_index("ix_action_pending_messages_share_id", table_name="action_pending_messages")
    op.drop_index("ix_action_pending_messages_chat_id", table_name="action_pending_messages")
    op.drop_index("ix_action_pending_messages_pending_id", table_name="action_pending_messages")
    op.drop_table("action_pending_messages")

    op.drop_index("ix_action_pendings_resolved_by_log_id", table_name="action_pendings")
    op.drop_index("ix_action_pendings_resolved_by_user_id", table_name="action_pendings")
    op.drop_index("ix_action_pendings_planned_run_at_utc", table_name="action_pendings")
    op.drop_index("ix_action_pendings_owner_user_id", table_name="action_pendings")
    op.drop_index("ix_action_pendings_plant_id", table_name="action_pendings")
    op.drop_index("ix_action_pendings_schedule_id", table_name="action_pendings")
    op.drop_table("action_pendings")