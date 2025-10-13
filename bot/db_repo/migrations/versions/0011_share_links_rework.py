"""
Share Links v2: replace schedule_shares/subscriptions with share_links/*,
add SHARED to actionsource, wire action_logs to shares.

Revision ID: 0011_share_links_rework
Revises: 0010_users_pk_is_tg_id
Create Date: 2025-10-13 12:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as psql

revision = "0011_share_links_rework"
down_revision = "0010_users_pk_is_tg_id"
branch_labels = None
depends_on = None

# --- config ---
ACTIONSOURCE_TYPE = "actionsource"             # имя PG ENUM для ActionSource
SHAREMEMBERSTATUS_TYPE = "sharememberstatus"   # имя PG ENUM для ShareMember.status


def upgrade():
    conn = op.get_bind()

    # 0) Добавить значение 'SHARED' в ENUM actionsource (если ещё нет)
    # В DO $$ ... $$ нельзя использовать bind-параметры под asyncpg — подставляем литералы.
    conn.execute(sa.text(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname = '{ACTIONSOURCE_TYPE}' AND e.enumlabel = 'SHARED'
            ) THEN
                ALTER TYPE {ACTIONSOURCE_TYPE} ADD VALUE 'SHARED';
            END IF;
        END$$;
    """))

    # 1) Новый ENUM sharememberstatus (идемпотентно)
    op.execute(f"""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_type t WHERE t.typname = '{SHAREMEMBERSTATUS_TYPE}'
        ) THEN
            CREATE TYPE {SHAREMEMBERSTATUS_TYPE} AS ENUM ('PENDING','ACTIVE','REMOVED','BLOCKED');
        END IF;
    END$$;
    """)

    # 2) Новые таблицы
    op.create_table(
        "share_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False, unique=True),
        sa.Column("title", sa.String(length=64), nullable=True),
        sa.Column("note", sa.String(length=128), nullable=True),
        sa.Column("allow_complete_default", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("show_history_default", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("uses_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_share_links_owner_user_id", "share_links", ["owner_user_id"])
    # ВАЖНО: отдельный индекс по code не нужен — есть unique-индекс
    # op.create_index("ix_share_links_code", "share_links", ["code"])

    op.create_table(
        "share_link_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("share_id", sa.Integer(), sa.ForeignKey("share_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("share_id", "schedule_id", name="uq_share_schedule"),
    )
    op.create_index("ix_share_link_schedules_share_id", "share_link_schedules", ["share_id"])
    op.create_index("ix_share_link_schedules_schedule_id", "share_link_schedules", ["schedule_id"])

    op.create_table(
        "share_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("share_id", sa.Integer(), sa.ForeignKey("share_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subscriber_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "status",
            # используем PG-диалект и НЕ создаём тип заново (мы уже создали DDL-ом выше)
            psql.ENUM("PENDING", "ACTIVE", "REMOVED", "BLOCKED",
                      name=SHAREMEMBERSTATUS_TYPE, create_type=False),
            nullable=False,
            server_default=sa.text("'ACTIVE'::sharememberstatus"),
        ),
        sa.Column("can_complete_override", sa.Boolean(), nullable=True),
        sa.Column("show_history_override", sa.Boolean(), nullable=True),
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("joined_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("removed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("share_id", "subscriber_user_id", name="uq_share_member"),
    )
    op.create_index("ix_share_members_share_id", "share_members", ["share_id"])
    op.create_index("ix_share_members_subscriber_user_id", "share_members", ["subscriber_user_id"])

    # 3) action_logs: новые nullable-колонки + индексы + FK
    op.add_column("action_logs", sa.Column("share_id", sa.Integer(), nullable=True))
    op.add_column("action_logs", sa.Column("share_member_id", sa.Integer(), nullable=True))
    op.create_index("ix_action_logs_share_id", "action_logs", ["share_id"], unique=False)
    op.create_index("ix_action_logs_share_member_id", "action_logs", ["share_member_id"], unique=False)
    op.create_foreign_key(
        "action_logs_share_id_fkey", "action_logs", "share_links",
        ["share_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "action_logs_share_member_id_fkey", "action_logs", "share_members",
        ["share_member_id"], ["id"], ondelete="SET NULL"
    )

    # 4) Перенос данных из старых таблиц (если они существуют)
    if table_exists(conn, "schedule_shares"):
        op.execute("""
            INSERT INTO share_links
                (id, owner_user_id, code, title, note,
                 allow_complete_default, show_history_default,
                 is_active, created_at_utc, expires_at_utc, max_uses, uses_count)
            SELECT
                ss.id, ss.owner_user_id, ss.code, NULL, ss.note,
                COALESCE(ss.allow_complete_by_subscribers, TRUE),
                TRUE,
                COALESCE(ss.is_active, TRUE),
                ss.created_at_utc, ss.expires_at_utc, NULL, 0
            FROM schedule_shares ss
            ON CONFLICT (id) DO NOTHING
        """)

        op.execute("""
            INSERT INTO share_link_schedules (share_id, schedule_id)
            SELECT ss.id AS share_id, ss.schedule_id
            FROM schedule_shares ss
            ON CONFLICT DO NOTHING
        """)

        if table_exists(conn, "schedule_subscriptions"):
            op.execute(f"""
                INSERT INTO share_members
                    (share_id, subscriber_user_id, status,
                     can_complete_override, show_history_override,
                     muted, joined_at_utc, removed_at_utc)
                SELECT
                    ss.id AS share_id,
                    ssub.subscriber_user_id,
                    'ACTIVE'::{SHAREMEMBERSTATUS_TYPE},
                    ssub.can_complete,
                    NULL,
                    COALESCE(ssub.muted, FALSE),
                    ssub.accepted_at_utc,
                    NULL
                FROM schedule_subscriptions ssub
                JOIN schedule_shares ss ON ss.schedule_id = ssub.schedule_id
                ON CONFLICT (share_id, subscriber_user_id) DO NOTHING
            """)

        bump_seq(conn, "share_links_id_seq", "share_links")
        bump_seq(conn, "share_link_schedules_id_seq", "share_link_schedules")
        bump_seq(conn, "share_members_id_seq", "share_members")

    # 5) Сносим старые таблицы
    if table_exists(conn, "schedule_subscriptions"):
        op.drop_constraint("schedule_subscriptions_subscriber_user_id_fkey", "schedule_subscriptions", type_="foreignkey")
        op.drop_constraint("uq_schedule_subscriber", "schedule_subscriptions", type_="unique")
        op.drop_table("schedule_subscriptions")

    if table_exists(conn, "schedule_shares"):
        op.drop_constraint("schedule_shares_owner_user_id_fkey", "schedule_shares", type_="foreignkey")
        op.drop_table("schedule_shares")


def downgrade():
    # Обратная миграция не поддержана.
    raise NotImplementedError("Downgrade is not supported for 0011_share_links_rework")


# --- helpers ---

def table_exists(conn, table_name: str) -> bool:
    res = conn.execute(
        sa.text("""
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :t
        """),
        {"t": table_name},
    ).fetchone()
    return bool(res)

def bump_seq(conn, seq_name: str, table_name: str, pk: str = "id"):
    """Поднять значение sequence до max(id) в таблице, если sequence существует."""
    seq = conn.execute(
        sa.text("""
            SELECT 1
            FROM pg_class
            WHERE relkind = 'S' AND relname = :s
        """),
        {"s": seq_name},
    ).fetchone()
    if not seq:
        return
    max_id = conn.execute(sa.text(f"SELECT COALESCE(MAX({pk}), 0) FROM {table_name}")).scalar() or 0
    # важная правка: приводим к regclass
    conn.execute(sa.text("SELECT setval(:s::regclass, :v)"), {"s": seq_name, "v": max_id + 1})