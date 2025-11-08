"""add owner_user_id to action_logs (with backfill)"""

from alembic import op
import sqlalchemy as sa

# Alembic identifiers
revision = "0014_add_owner_user_id"
down_revision = "0013_create_action_pendings"
branch_labels = None
depends_on = None


def upgrade():
    # 1) колонка (nullable, чтобы спокойно бэкфиллить)
    op.add_column(
        "action_logs",
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
    )

    # 2) бэкфилл от plant_id → plants.user_id
    op.execute("""
        UPDATE action_logs AS al
        SET owner_user_id = p.user_id
        FROM plants AS p
        WHERE al.plant_id = p.id
          AND al.owner_user_id IS NULL
    """)

    # 3) бэкфилл через schedule_id → schedules → plants.user_id (если plant_id нет)
    op.execute("""
        UPDATE action_logs AS al
        SET owner_user_id = p.user_id
        FROM schedules AS s
        JOIN plants AS p ON p.id = s.plant_id
        WHERE al.schedule_id = s.id
          AND al.owner_user_id IS NULL
    """)

    # 4) запасной вариант: ставим автора (user_id), если владелец всё ещё неизвестен
    op.execute("""
        UPDATE action_logs AS al
        SET owner_user_id = al.user_id
        WHERE al.owner_user_id IS NULL
    """)

    # 5) теперь NOT NULL
    op.alter_column("action_logs", "owner_user_id", nullable=False)

    # 6) FK и индексы
    op.create_foreign_key(
        "fk_action_logs_owner_user",
        "action_logs", "users",
        local_cols=["owner_user_id"], remote_cols=["id"],
        ondelete="RESTRICT"  # оставим историю даже если попытаться удалить пользователя
    )
    op.create_index("ix_action_logs_owner_user_id", "action_logs", ["owner_user_id"])
    op.create_index("ix_action_logs_owner_date", "action_logs", ["owner_user_id", "done_at_utc"])


def downgrade():
    op.drop_index("ix_action_logs_owner_date", table_name="action_logs")
    op.drop_index("ix_action_logs_owner_user_id", table_name="action_logs")
    op.drop_constraint("fk_action_logs_owner_user", "action_logs", type_="foreignkey")
    op.drop_column("action_logs", "owner_user_id")