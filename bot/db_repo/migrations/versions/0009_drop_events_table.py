"""
Drop obsolete 'events' table (migrated to action_logs).

Revision ID: 0009_drop_events_table
Revises: 0008_events_source_to_enum
Create Date: 2025-10-07 22:15:00
"""
from alembic import op


revision = "0009_drop_events_table"
down_revision = "0008_events_source_to_enum"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP TABLE IF EXISTS events CASCADE;")


def downgrade():
    op.execute(
        """
    CREATE TABLE events (
        id SERIAL PRIMARY KEY,
        plant_id INTEGER NOT NULL
            REFERENCES plants (id) ON DELETE CASCADE,
        schedule_id INTEGER NULL
            REFERENCES schedules (id) ON DELETE SET NULL,
        action actiontype NOT NULL,
        done_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        source actionsource NOT NULL
    );
    CREATE INDEX ix_events_schedule_id ON events (schedule_id);
        """
    )