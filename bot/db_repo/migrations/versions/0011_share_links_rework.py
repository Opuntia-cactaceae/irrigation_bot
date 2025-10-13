"""
Share Links v2: replace schedule_shares/subscriptions with share_links/*,
add SHARED to actionsource, wire action_logs to shares.

Revision ID: 0011_share_links_rework
Revises: 0010_users_pk_is_tg_id
Create Date: 2025-10-13 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_share_links_rework"
down_revision = "0010_users_pk_is_tg_id"
branch_labels = None
depends_on = None

# --- config ---
ACTIONSOURCE_TYPE = "actionsource"        # имя PG ENUM для ActionSource
SHAREMEMBERSTATUS_TYPE = "sharememberstatus"  # новое имя PG ENUM для ShareMember.status


def upgrade():
    conn = op.get_bind()

    # 0) Добавить значение 'SHARED' в ENUM actionsource (если ещё нет)
    op.execute(
        sa.text(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname = :typ AND e.enumlabel = 'SHARED'
            ) THEN
                ALTER TYPE {ACTIONSOURCE_TYPE} ADD VALUE 'SHARED';
            END IF;
        END$$;
        """),
        {"typ": ACTIONSOURCE_TYPE},
    )

    # 1) Новый ENUM sharememberstatus
    op.execute(f"CREATE TYPE {SHAREMEMBERSTATUS_TYPE} AS ENUM ('PENDING','ACTIVE','REMOVED','BLOCKED')")

    # 2) Новые таблицы
    op.create_table(
        "share_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False, index=True, unique=True),
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

    op.create_table(
        "share_link_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("share_id", sa.Integer(), sa.ForeignKey("share_links.id", ondelete="CASCADE"), index=True, nullable=False),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("schedules.id", ondelete="CASCADE"), index=True, nullable=False),
        sa.UniqueConstraint("share_id", "schedule_id", name="uq_share_schedule"),
    )

    op.create_table(
        "share_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("share_id", sa.Integer(), sa.ForeignKey("share_links.id", ondelete="CASCADE"), index=True, nullable=False),
        sa.Column("subscriber_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False),
        sa.Column("status", sa.Enum(name=SHAREMEMBERSTATUS_TYPE), nullable=False, server_default="ACTIVE"),
        sa.Column("can_complete_override", sa.Boolean(), nullable=True),
        sa.Column("show_history_override", sa.Boolean(), nullable=True),
        sa.Column("muted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("joined_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("removed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("share_id", "subscriber_user_id", name="uq_share_member"),
    )

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
    # Старые таблицы:
    # - schedule_shares(id, owner_user_id, schedule_id, code, note, created_at_utc, expires_at_utc, is_active, allow_complete_by_subscribers)
    # - schedule_subscriptions(id, schedule_id, subscriber_user_id, can_complete, muted, accepted_at_utc, uq(schedule_id, subscriber_user_id))
    #
    # Маппинг:
    #   schedule_shares → share_links (1:1), title=NULL, allow_complete_default = allow_complete_by_subscribers,
    #                     show_history_default=TRUE, uses_count=0, max_uses=NULL
    #   schedule_shares(schedule_id) → share_link_schedules(share_id=share_links.id, schedule_id)
    #   schedule_subscriptions → share_members через join по schedule_id к share_links,
    #                     status=ACTIVE, can_complete_override = can_complete,
    #                     show_history_override = NULL, muted, joined_at_utc = accepted_at_utc
    #
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

        # schedules
        op.execute("""
            INSERT INTO share_link_schedules (share_id, schedule_id)
            SELECT ss.id AS share_id, ss.schedule_id
            FROM schedule_shares ss
            ON CONFLICT DO NOTHING
        """)

        # members
        if table_exists(conn, "schedule_subscriptions"):
            op.execute("""
                INSERT INTO share_members
                    (share_id, subscriber_user_id, status,
                     can_complete_override, show_history_override,
                     muted, joined_at_utc, removed_at_utc)
                SELECT
                    ss.id AS share_id,
                    ssub.subscriber_user_id,
                    'ACTIVE'::sharememberstatus,
                    ssub.can_complete,
                    NULL,
                    COALESCE(ssub.muted, FALSE),
                    ssub.accepted_at_utc,
                    NULL
                FROM schedule_subscriptions ssub
                JOIN schedule_shares ss ON ss.schedule_id = ssub.schedule_id
                ON CONFLICT (share_id, subscriber_user_id) DO NOTHING
            """)

        # выровнять последовательности id у новых таблиц (на случай явной вставки id)
        bump_seq(conn, "share_links_id_seq", "share_links")
        bump_seq(conn, "share_link_schedules_id_seq", "share_link_schedules")
        bump_seq(conn, "share_members_id_seq", "share_members")

    # 5) Удаляем старые таблицы (если были)
    if table_exists(conn, "schedule_subscriptions"):
        op.drop_constraint("schedule_subscriptions_subscriber_user_id_fkey", "schedule_subscriptions", type_="foreignkey")
        op.drop_constraint("uq_schedule_subscriber", "schedule_subscriptions", type_="unique")
        op.drop_table("schedule_subscriptions")

    if table_exists(conn, "schedule_shares"):
        op.drop_constraint("schedule_shares_owner_user_id_fkey", "schedule_shares", type_="foreignkey")
        # FK на schedules был через index, дропа не требуется отдельно
        op.drop_table("schedule_shares")


def downgrade():
    # Возврат к старой схеме не поддержан (сложная обратная миграция отношений).
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
    # проверить, есть ли sequence
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
    conn.execute(sa.text(f"SELECT setval(:s, :v)"), {"s": seq_name, "v": max_id + 1})