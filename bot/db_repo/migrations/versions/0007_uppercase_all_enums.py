"""
Uppercase all enum labels to match Python Enums

Revision ID: 0007_uppercase_all_enums
Revises: 0006_fix_scheduletype_case
Create Date: 2025-10-05 23:59:00.000000
"""
from alembic import op

# Идентификаторы ревизии
revision = "0007_uppercase_all_enums"
down_revision = "0006_fix_scheduletype_case"
branch_labels = None
depends_on = None


def upgrade():
    # Для каждого enum-типа: если есть lowercase-лейбл и нет uppercase — переименуем.
    op.execute(
        """
DO $$
BEGIN
  -- scheduletype: interval -> INTERVAL, weekly -> WEEKLY
  IF EXISTS (SELECT 1 FROM pg_type t WHERE t.typname = 'scheduletype') THEN
    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'scheduletype' AND e.enumlabel = 'interval')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'scheduletype' AND e.enumlabel = 'INTERVAL') THEN
      ALTER TYPE scheduletype RENAME VALUE 'interval' TO 'INTERVAL';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'scheduletype' AND e.enumlabel = 'weekly')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'scheduletype' AND e.enumlabel = 'WEEKLY') THEN
      ALTER TYPE scheduletype RENAME VALUE 'weekly' TO 'WEEKLY';
    END IF;
  END IF;

  -- actiontype: watering -> WATERING, fertilizing -> FERTILIZING, repotting -> REPOTTING, custom -> CUSTOM
  IF EXISTS (SELECT 1 FROM pg_type t WHERE t.typname = 'actiontype') THEN
    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actiontype' AND e.enumlabel = 'watering')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actiontype' AND e.enumlabel = 'WATERING') THEN
      ALTER TYPE actiontype RENAME VALUE 'watering' TO 'WATERING';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actiontype' AND e.enumlabel = 'fertilizing')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actiontype' AND e.enumlabel = 'FERTILIZING') THEN
      ALTER TYPE actiontype RENAME VALUE 'fertilizing' TO 'FERTILIZING';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actiontype' AND e.enumlabel = 'repotting')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actiontype' AND e.enumlabel = 'REPOTTING') THEN
      ALTER TYPE actiontype RENAME VALUE 'repotting' TO 'REPOTTING';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actiontype' AND e.enumlabel = 'custom')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actiontype' AND e.enumlabel = 'CUSTOM') THEN
      ALTER TYPE actiontype RENAME VALUE 'custom' TO 'CUSTOM';
    END IF;
  END IF;

  -- actionstatus: done -> DONE, skipped -> SKIPPED
  IF EXISTS (SELECT 1 FROM pg_type t WHERE t.typname = 'actionstatus') THEN
    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actionstatus' AND e.enumlabel = 'done')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actionstatus' AND e.enumlabel = 'DONE') THEN
      ALTER TYPE actionstatus RENAME VALUE 'done' TO 'DONE';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actionstatus' AND e.enumlabel = 'skipped')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actionstatus' AND e.enumlabel = 'SKIPPED') THEN
      ALTER TYPE actionstatus RENAME VALUE 'skipped' TO 'SKIPPED';
    END IF;
  END IF;

  -- actionsource: schedule -> SCHEDULE, manual -> MANUAL
  IF EXISTS (SELECT 1 FROM pg_type t WHERE t.typname = 'actionsource') THEN
    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actionsource' AND e.enumlabel = 'schedule')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actionsource' AND e.enumlabel = 'SCHEDULE') THEN
      ALTER TYPE actionsource RENAME VALUE 'schedule' TO 'SCHEDULE';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actionsource' AND e.enumlabel = 'manual')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actionsource' AND e.enumlabel = 'MANUAL') THEN
      ALTER TYPE actionsource RENAME VALUE 'manual' TO 'MANUAL';
    END IF;
  END IF;

END
$$;
        """
    )


def downgrade():
    # Возврат меток к lowercase на случай отката.
    op.execute(
        """
DO $$
BEGIN
  -- scheduletype
  IF EXISTS (SELECT 1 FROM pg_type t WHERE t.typname = 'scheduletype') THEN
    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'scheduletype' AND e.enumlabel = 'INTERVAL')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'scheduletype' AND e.enumlabel = 'interval') THEN
      ALTER TYPE scheduletype RENAME VALUE 'INTERVAL' TO 'interval';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'scheduletype' AND e.enumlabel = 'WEEKLY')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'scheduletype' AND e.enumlabel = 'weekly') THEN
      ALTER TYPE scheduletype RENAME VALUE 'WEEKLY' TO 'weekly';
    END IF;
  END IF;

  -- actiontype
  IF EXISTS (SELECT 1 FROM pg_type t WHERE t.typname = 'actiontype') THEN
    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actiontype' AND e.enumlabel = 'WATERING')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actiontype' AND e.enumlabel = 'watering') THEN
      ALTER TYPE actiontype RENAME VALUE 'WATERING' TO 'watering';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actiontype' AND e.enumlabel = 'FERTILIZING')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actiontype' AND e.enumlabel = 'fertilizing') THEN
      ALTER TYPE actiontype RENAME VALUE 'FERTILIZING' TO 'fertilizing';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actiontype' AND e.enumlabel = 'REPOTTING')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actiontype' AND e.enumlabel = 'repotting') THEN
      ALTER TYPE actiontype RENAME VALUE 'REPOTTING' TO 'repotting';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actiontype' AND e.enumlabel = 'CUSTOM')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actiontype' AND e.enumlabel = 'custom') THEN
      ALTER TYPE actiontype RENAME VALUE 'CUSTOM' TO 'custom';
    END IF;
  END IF;

  -- actionstatus
  IF EXISTS (SELECT 1 FROM pg_type t WHERE t.typname = 'actionstatus') THEN
    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actionstatus' AND e.enumlabel = 'DONE')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actionstatus' AND e.enumlabel = 'done') THEN
      ALTER TYPE actionstatus RENAME VALUE 'DONE' TO 'done';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actionstatus' AND e.enumlabel = 'SKIPPED')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actionstatus' AND e.enumlabel = 'skipped') THEN
      ALTER TYPE actionstatus RENAME VALUE 'SKIPPED' TO 'skipped';
    END IF;
  END IF;

  -- actionsource
  IF EXISTS (SELECT 1 FROM pg_type t WHERE t.typname = 'actionsource') THEN
    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actionsource' AND e.enumlabel = 'SCHEDULE')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actionsource' AND e.enumlabel = 'schedule') THEN
      ALTER TYPE actionsource RENAME VALUE 'SCHEDULE' TO 'schedule';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
               WHERE t.typname = 'actionsource' AND e.enumlabel = 'MANUAL')
       AND NOT EXISTS (SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                       WHERE t.typname = 'actionsource' AND e.enumlabel = 'manual') THEN
      ALTER TYPE actionsource RENAME VALUE 'MANUAL' TO 'manual';
    END IF;
  END IF;

END
$$;
        """
    )