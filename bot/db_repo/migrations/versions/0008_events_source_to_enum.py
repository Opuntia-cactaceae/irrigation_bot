"""
Make events.source use enum 'actionsource' (UPPERCASE)
- Normalize existing values to UPPERCASE
- Cast VARCHAR -> actionsource enum

Revision ID: 0008_events_source_to_enum
Revises: 0007_uppercase_all_enums
Create Date: 2025-10-06 00:45:00
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = "0008_events_source_to_enum"
down_revision = "0007_uppercase_all_enums"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
DO $$
BEGIN
  -- 1) Создаём тип, если вдруг ещё нет (в большинстве случаев уже есть из других таблиц)
  IF NOT EXISTS (SELECT 1 FROM pg_type t WHERE t.typname = 'actionsource') THEN
    CREATE TYPE actionsource AS ENUM ('SCHEDULE', 'MANUAL');
  END IF;

  -- 2) Нормализуем значения в таблице events к UPPERCASE и валидным меткам
  --    Всё неизвестное осторожно маппим в 'MANUAL' (чтобы не упасть на касте)
  UPDATE events
     SET source = CASE
                    WHEN UPPER(source) IN ('SCHEDULE','MANUAL') THEN UPPER(source)
                    ELSE 'MANUAL'
                  END;

  -- 3) Меняем тип колонки с varchar -> enum actionsource
  ALTER TABLE events
    ALTER COLUMN source TYPE actionsource USING (source::actionsource);
END
$$;
        """
    )


def downgrade():
    op.execute(
        """
DO $$
BEGIN
  -- Возвращаемся к строке
  ALTER TABLE events
    ALTER COLUMN source TYPE VARCHAR(16) USING (source::text);

  -- Тип actionsource оставляем, т.к. он может использоваться другими таблицами (например, action_logs).
  -- Если ТЫ уверен, что он больше не нужен, можно раскомментировать:
  -- DROP TYPE IF EXISTS actionsource;
END
$$;
        """
    )