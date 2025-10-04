"""create action_logs table

Revision ID: 0003_create_action_logs
Revises: 0002_add_schedule_id_to_events
Create Date: 2025-10-04 03:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_create_action_logs"
down_revision = "0002_add_schedule_id_to_events"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    status_enum_create = postgresql.ENUM("done", "skipped", name="actionstatus")
    source_enum_create = postgresql.ENUM("schedule", "manual", name="actionsource")
    status_enum_create.create(bind, checkfirst=True)
    source_enum_create.create(bind, checkfirst=True)


    action_enum_ref = postgresql.ENUM(
        "watering", "fertilizing", "repotting",
        name="actiontype",
        create_type=False,
    )
    status_enum_ref = postgresql.ENUM(name="actionstatus", create_type=False)
    source_enum_ref = postgresql.ENUM(name="actionsource", create_type=False)

    # 3) Таблица
    op.create_table(
        "action_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("plant_id", sa.Integer(),
                  sa.ForeignKey("plants.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("schedule_id", sa.Integer(),
                  sa.ForeignKey("schedules.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("action", action_enum_ref, nullable=False),
        sa.Column("status", status_enum_ref, nullable=False),
        sa.Column("source", source_enum_ref,
                  nullable=False,
                  server_default=sa.text("'schedule'::actionsource")),
        sa.Column("done_at_utc", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("plant_name_at_time", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )

    op.create_index("ix_action_logs_user_id", "action_logs", ["user_id"])
    op.create_index("ix_action_logs_plant_id", "action_logs", ["plant_id"])
    op.create_index("ix_action_logs_schedule_id", "action_logs", ["schedule_id"])


def downgrade():
    op.drop_index("ix_action_logs_schedule_id", table_name="action_logs")
    op.drop_index("ix_action_logs_plant_id", table_name="action_logs")
    op.drop_index("ix_action_logs_user_id", table_name="action_logs")
    op.drop_table("action_logs")

    # удаляем ТОЛЬКО новые типы; actiontype не трогаем
    op.execute("DROP TYPE IF EXISTS actionstatus")
    op.execute("DROP TYPE IF EXISTS actionsource")