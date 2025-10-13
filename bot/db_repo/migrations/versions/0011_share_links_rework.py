"""
Share Links: replace schedule shares/subscriptions with share links and members (idempotent).

Revision ID: 0011_share_links
Revises: 0010_users_pk_is_tg_id
Create Date: 2025-10-13 21:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0011_share_links"
down_revision = "0010_users_pk_is_tg_id"
branch_labels = None
depends_on = None


# ---------- helpers ----------

def _has_table(conn, table: str) -> bool:
    return bool(conn.execute(sa.text("""
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = :t
    """), {"t": table}).fetchone())

def _has_column(conn, table: str, column: str) -> bool:
    return bool(conn.execute(sa.text("""
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
    """), {"t": table, "c": column}).fetchone())

def _has_index(conn, index_name: str) -> bool:
    return bool(conn.execute(sa.text("""
        SELECT 1 FROM pg_indexes WHERE indexname = :n
    """), {"n": index_name}).fetchone())

def _has_constraint(conn, table: str, constraint_name: str) -> bool:
    return bool(conn.execute(sa.text("""
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_name = :t AND constraint_name = :n
    """), {"t": table, "n": constraint_name}).fetchone())

def _fk_exists(conn, table: str, fk_name: str) -> bool:
    return bool(conn.execute(sa.text("""
        SELECT 1
        FROM information_schema.table_constraints tc
        WHERE tc.table_name = :t
          AND tc.constraint_type = 'FOREIGN KEY'
          AND tc.constraint_name = :n
    """), {"t": table, "n": fk_name}).fetchone())


def upgrade():
    bind = op.get_bind()

    # 1) ActionSource: идемпотентно добавляем 'SHARED'
    with op.get_context().autocommit_block():
        op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname = 'actionsource'
                  AND e.enumlabel = 'SHARED'
            ) THEN
                ALTER TYPE actionsource ADD VALUE 'SHARED';
            END IF;
        END
        $$;
        """)

    # 2) sharememberstatus: идемпотентное создание TYPE
    with op.get_context().autocommit_block():
        op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = 'sharememberstatus'
            ) THEN
                CREATE TYPE sharememberstatus AS ENUM ('PENDING','ACTIVE','REMOVED','BLOCKED');
            END IF;
        END
        $$;
        """)

    # Объект enum для использования в колонках без автосоздания
    share_member_status_enum = postgresql.ENUM(
        'PENDING', 'ACTIVE', 'REMOVED', 'BLOCKED',
        name='sharememberstatus',
        create_type=False,
    )

    # 3) share_links (replaces schedule_shares)
    if not _has_table(bind, "share_links"):
        op.create_table(
            "share_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_user_id", sa.BigInteger(),
                      sa.ForeignKey("users.id", ondelete="CASCADE")),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=64), nullable=True),
            sa.Column("note", sa.String(length=128), nullable=True),
            sa.Column("allow_complete_default", sa.Boolean(),
                      nullable=False, server_default=sa.text("TRUE")),
            sa.Column("show_history_default", sa.Boolean(),
                      nullable=False, server_default=sa.text("TRUE")),
            sa.Column("is_active", sa.Boolean(),
                      nullable=False, server_default=sa.text("TRUE")),
            sa.Column("created_at_utc", sa.DateTime(timezone=True),
                      server_default=sa.text("NOW()"), nullable=False),
            sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("max_uses", sa.Integer(), nullable=True),
            sa.Column("uses_count", sa.Integer(),
                      nullable=False, server_default=sa.text("0")),
            sa.UniqueConstraint("code", name="uq_share_links_code"),
        )
    # индексы (если вдруг таблица уже была)
    if not _has_index(bind, "ix_share_links_owner_user_id"):
        op.create_index("ix_share_links_owner_user_id", "share_links", ["owner_user_id"], unique=False)

    # 4) share_link_schedules (association between share_links and schedules)
    if not _has_table(bind, "share_link_schedules"):
        op.create_table(
            "share_link_schedules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("share_id", sa.Integer(),
                      sa.ForeignKey("share_links.id", ondelete="CASCADE")),
            sa.Column("schedule_id", sa.Integer(),
                      sa.ForeignKey("schedules.id", ondelete="CASCADE")),
            sa.UniqueConstraint("share_id", "schedule_id", name="uq_share_schedule"),
        )
    if not _has_index(bind, "ix_share_link_schedules_share_id"):
        op.create_index("ix_share_link_schedules_share_id", "share_link_schedules", ["share_id"], unique=False)
    if not _has_index(bind, "ix_share_link_schedules_schedule_id"):
        op.create_index("ix_share_link_schedules_schedule_id", "share_link_schedules", ["schedule_id"], unique=False)

    # 5) share_members (replaces schedule_subscriptions)
    if not _has_table(bind, "share_members"):
        op.create_table(
            "share_members",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("share_id", sa.Integer(),
                      sa.ForeignKey("share_links.id", ondelete="CASCADE")),
            sa.Column("subscriber_user_id", sa.BigInteger(),
                      sa.ForeignKey("users.id", ondelete="CASCADE")),
            sa.Column("status", share_member_status_enum, nullable=False,
                      server_default=sa.text("'ACTIVE'")),
            sa.Column("can_complete_override", sa.Boolean(), nullable=True),
            sa.Column("show_history_override", sa.Boolean(), nullable=True),
            sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
            sa.Column("joined_at_utc", sa.DateTime(timezone=True),
                      server_default=sa.text("NOW()"), nullable=False),
            sa.Column("removed_at_utc", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("share_id", "subscriber_user_id", name="uq_share_member"),
        )
    if not _has_index(bind, "ix_share_members_share_id"):
        op.create_index("ix_share_members_share_id", "share_members", ["share_id"], unique=False)
    if not _has_index(bind, "ix_share_members_subscriber_user_id"):
        op.create_index("ix_share_members_subscriber_user_id", "share_members", ["subscriber_user_id"], unique=False)

    # 6) action_logs: новые колонки + индексы + FK (всё с проверками)
    if not _has_column(bind, "action_logs", "share_id"):
        op.add_column("action_logs", sa.Column("share_id", sa.Integer(), nullable=True))
    if not _has_column(bind, "action_logs", "share_member_id"):
        op.add_column("action_logs", sa.Column("share_member_id", sa.Integer(), nullable=True))

    if not _has_index(bind, "ix_action_logs_share_id"):
        op.create_index("ix_action_logs_share_id", "action_logs", ["share_id"], unique=False)
    if not _has_index(bind, "ix_action_logs_share_member_id"):
        op.create_index("ix_action_logs_share_member_id", "action_logs", ["share_member_id"], unique=False)

    if not _fk_exists(bind, "action_logs", "action_logs_share_id_fkey"):
        op.create_foreign_key(
            "action_logs_share_id_fkey", "action_logs", "share_links",
            ["share_id"], ["id"], ondelete="SET NULL"
        )
    if not _fk_exists(bind, "action_logs", "action_logs_share_member_id_fkey"):
        op.create_foreign_key(
            "action_logs_share_member_id_fkey", "action_logs", "share_members",
            ["share_member_id"], ["id"], ondelete="SET NULL"
        )

    # 7) Drop legacy tables (без ошибок, если их уже нет)
    op.execute("DROP TABLE IF EXISTS schedule_subscriptions CASCADE")
    op.execute("DROP TABLE IF EXISTS schedule_shares CASCADE")


def downgrade():
    raise NotImplementedError("Downgrade is not supported for 0011_share_links")