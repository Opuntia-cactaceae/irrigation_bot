"""share schedules + custom action

Revision ID: 0004_share_schedules_and_custom_action
Revises: 0003_create_action_logs
Create Date: 2025-10-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0004_share_schedules_and_custom_action"
down_revision = "0003_create_action_logs"
branch_labels = None
depends_on = None


def upgrade():
    # 1) Добавляем значение 'custom' в ENUM actiontype (идемпотентно)
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'custom'")

    # 2) Кастомные поля в schedules
    op.add_column("schedules", sa.Column("custom_title", sa.String(length=64), nullable=True))
    op.add_column("schedules", sa.Column("custom_note_template", sa.String(length=256), nullable=True))

    # 3) Таблица schedule_shares (без index=True на колонках!)
    op.create_table(
        "schedule_shares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("note", sa.String(length=128), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("allow_complete_by_subscribers", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
    )
    # Индексы — идемпотентно
    op.execute("CREATE INDEX IF NOT EXISTS ix_schedule_shares_owner_user_id ON schedule_shares (owner_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_schedule_shares_schedule_id ON schedule_shares (schedule_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_schedule_shares_code ON schedule_shares (code)")

    # 4) Таблица schedule_subscriptions
    op.create_table(
        "schedule_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscriber_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("can_complete", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("muted", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("accepted_at_utc", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("schedule_id", "subscriber_user_id", name="uq_schedule_subscriber"),
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_schedule_subscriptions_schedule_id ON schedule_subscriptions (schedule_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_schedule_subscriptions_subscriber_user_id ON schedule_subscriptions (subscriber_user_id)")


def downgrade():
    # Подписки
    op.execute("DROP INDEX IF EXISTS ix_schedule_subscriptions_subscriber_user_id")
    op.execute("DROP INDEX IF EXISTS ix_schedule_subscriptions_schedule_id")
    op.drop_table("schedule_subscriptions")

    # Шаринги
    op.execute("DROP INDEX IF EXISTS ix_schedule_shares_code")
    op.execute("DROP INDEX IF EXISTS ix_schedule_shares_schedule_id")
    op.execute("DROP INDEX IF EXISTS ix_schedule_shares_owner_user_id")
    op.drop_table("schedule_shares")

    # Поля в schedules
    op.drop_column("schedules", "custom_note_template")
    op.drop_column("schedules", "custom_title")

    # Откат ENUM: создаём временный тип без 'custom' и пересобираем ссылки
    tmp_enum = postgresql.ENUM("watering", "fertilizing", "repotting", name="actiontype_tmp")
    tmp_enum.create(op.get_bind(), checkfirst=False)

    targets = [("schedules", "action"), ("events", "action"), ("action_logs", "action")]
    for table, column in targets:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE actiontype_tmp USING {column}::text::actiontype_tmp"
        )

    op.execute("DROP TYPE actiontype")
    op.execute("ALTER TYPE actiontype_tmp RENAME TO actiontype")