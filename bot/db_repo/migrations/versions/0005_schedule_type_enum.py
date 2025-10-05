"""Convert schedules.type from VARCHAR to ENUM scheduletype

Revision ID: 0005_schedule_type_enum
Revises: 0004_shares_custom_action
Create Date: 2025-10-05 16:30:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Идентификаторы ревизии
revision = "0005_schedule_type_enum"
down_revision = "0004_shares_custom_action"
branch_labels = None
depends_on = None


def upgrade():
    # 1) Создаём новый PG ENUM с нужными значениями
    scheduletype = postgresql.ENUM("interval", "weekly", name="scheduletype")
    scheduletype.create(op.get_bind(), checkfirst=True)

    # 2) Приводим существующие данные к нижнему регистру, на всякий
    op.execute("UPDATE schedules SET type = LOWER(type)")

    # 3) Меняем тип колонки schedules.type на scheduletype
    #    USING — безопасное приведение, если уже 'interval'/'weekly'
    op.alter_column(
        "schedules",
        "type",
        type_=scheduletype,
        existing_type=sa.String(length=16),
        postgresql_using="type::scheduletype",
        nullable=False,   # если уверены, что пустых нет; иначе уберите эту строку
    )


def downgrade():
    # 1) Откатываем колонку назад в VARCHAR(16)
    op.alter_column(
        "schedules",
        "type",
        type_=sa.String(length=16),
        existing_type=postgresql.ENUM(name="scheduletype"),
        postgresql_using="type::text",
        nullable=True,   # подстрахуемся обратной совместимостью
    )

    # 2) Удаляем PG ENUM
    scheduletype = postgresql.ENUM("interval", "weekly", name="scheduletype")
    scheduletype.drop(op.get_bind(), checkfirst=True)