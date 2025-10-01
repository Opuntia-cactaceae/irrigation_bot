from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_add_schedule_id_to_events"
down_revision = "0001_initial_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("schedule_id", sa.Integer(), nullable=True))


    op.create_index("ix_events_schedule_id", "events", ["schedule_id"], unique=False)

    op.create_foreign_key(
        "fk_events_schedule",
        source_table="events",
        referent_table="schedules",
        local_cols=["schedule_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_events_schedule", "events", type_="foreignkey")
    op.drop_index("ix_events_schedule_id", table_name="events")
    op.drop_column("events", "schedule_id")