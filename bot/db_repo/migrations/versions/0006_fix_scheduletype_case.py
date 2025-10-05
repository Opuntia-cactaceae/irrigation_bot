"""
Make ENUM scheduletype labels upper-case to match the Python Enum

Revision ID: 0006_fix_scheduletype_case
Revises: 0005_schedule_type_enum
Create Date: 2025-10-05 19:00:00.000000
"""
from alembic import op

# Идентификаторы ревизии
revision = "0006_fix_scheduletype_case"
down_revision = "0005_schedule_type_enum"
branch_labels = None
depends_on = None


def upgrade():
    # Переименуем значения ENUM, если они есть в нижнем регистре.
    # Это безопасно: меняется «метка» значения без трогания строк в таблицах.
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'scheduletype'
      ) THEN
        -- interval -> INTERVAL
        IF EXISTS (
          SELECT 1 FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
          WHERE t.typname = 'scheduletype' AND e.enumlabel = 'interval'
        ) AND NOT EXISTS (
          SELECT 1 FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
          WHERE t.typname = 'scheduletype' AND e.enumlabel = 'INTERVAL'
        ) THEN
          ALTER TYPE scheduletype RENAME VALUE 'interval' TO 'INTERVAL';
        END IF;

        -- weekly -> WEEKLY
        IF EXISTS (
          SELECT 1 FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
          WHERE t.typname = 'scheduletype' AND e.enumlabel = 'weekly'
        ) AND NOT EXISTS (
          SELECT 1 FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
          WHERE t.typname = 'scheduletype' AND e.enumlabel = 'WEEKLY'
        ) THEN
          ALTER TYPE scheduletype RENAME VALUE 'weekly' TO 'WEEKLY';
        END IF;
      END IF;
    END
    $$;
    """)


def downgrade():
    # Возвратим метки обратно в нижний регистр (если вдруг нужно откатить)
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'scheduletype'
      ) THEN
        IF EXISTS (
          SELECT 1 FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
          WHERE t.typname = 'scheduletype' AND e.enumlabel = 'INTERVAL'
        ) AND NOT EXISTS (
          SELECT 1 FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
          WHERE t.typname = 'scheduletype' AND e.enumlabel = 'interval'
        ) THEN
          ALTER TYPE scheduletype RENAME VALUE 'INTERVAL' TO 'interval';
        END IF;

        IF EXISTS (
          SELECT 1 FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
          WHERE t.typname = 'scheduletype' AND e.enumlabel = 'WEEKLY'
        ) AND NOT EXISTS (
          SELECT 1 FROM pg_enum e
          JOIN pg_type t ON t.oid = e.enumtypid
          WHERE t.typname = 'scheduletype' AND e.enumlabel = 'weekly'
        ) THEN
          ALTER TYPE scheduletype RENAME VALUE 'WEEKLY' TO 'weekly';
        END IF;
      END IF;
    END
    $$;
    """)