"""
Users: switch PK to Telegram ID (BIGINT) and update all FKs.

Revision ID: 0010_users_pk_is_tg_id
Revises: 0009_drop_events_table
Create Date: 2025-10-08 00:05:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_users_pk_is_tg_id"
down_revision = "0009_drop_events_table"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1) users: добавляем id_new BIGINT и копируем в него tg_user_id
    op.add_column("users", sa.Column("id_new", sa.BigInteger(), nullable=True))
    op.execute("UPDATE users SET id_new = tg_user_id")

    # sanity-check
    nulls = conn.execute(sa.text("SELECT COUNT(*) FROM users WHERE id_new IS NULL")).scalar()
    if nulls and int(nulls) > 0:
        raise RuntimeError("users.id_new содержит NULL — есть пользователи без tg_user_id")

    # 2) добавляем временные *_new BIGINT-колонки в таблицах с FK на users
    # species.user_id
    op.add_column("species", sa.Column("user_id_new", sa.BigInteger(), nullable=True))
    op.execute("""
        UPDATE species s
        SET user_id_new = u.id_new
        FROM users u
        WHERE s.user_id = u.id
    """)

    # plants.user_id
    op.add_column("plants", sa.Column("user_id_new", sa.BigInteger(), nullable=True))
    op.execute("""
        UPDATE plants p
        SET user_id_new = u.id_new
        FROM users u
        WHERE p.user_id = u.id
    """)

    # action_logs.user_id
    op.add_column("action_logs", sa.Column("user_id_new", sa.BigInteger(), nullable=True))
    op.execute("""
        UPDATE action_logs a
        SET user_id_new = u.id_new
        FROM users u
        WHERE a.user_id = u.id
    """)

    # schedule_shares.owner_user_id
    op.add_column("schedule_shares", sa.Column("owner_user_id_new", sa.BigInteger(), nullable=True))
    op.execute("""
        UPDATE schedule_shares ss
        SET owner_user_id_new = u.id_new
        FROM users u
        WHERE ss.owner_user_id = u.id
    """)

    # schedule_subscriptions.subscriber_user_id
    op.add_column("schedule_subscriptions", sa.Column("subscriber_user_id_new", sa.BigInteger(), nullable=True))
    op.execute("""
        UPDATE schedule_subscriptions ssub
        SET subscriber_user_id_new = u.id_new
        FROM users u
        WHERE ssub.subscriber_user_id = u.id
    """)

    # 3) перекидываем колонки и пересоздаём ограничения

    # species
    op.execute("ALTER TABLE species DROP CONSTRAINT IF EXISTS species_user_id_fkey")
    op.drop_constraint("uq_species_user_name", "species", type_="unique")
    op.drop_column("species", "user_id")
    op.alter_column("species", "user_id_new", new_column_name="user_id", existing_type=sa.BigInteger())
    op.create_unique_constraint("uq_species_user_name", "species", ["user_id", "name"])

    # plants
    op.execute("ALTER TABLE plants DROP CONSTRAINT IF EXISTS plants_user_id_fkey")
    op.drop_column("plants", "user_id")
    op.alter_column("plants", "user_id_new", new_column_name="user_id", existing_type=sa.BigInteger())

    # action_logs
    op.execute("ALTER TABLE action_logs DROP CONSTRAINT IF EXISTS action_logs_user_id_fkey")
    # старый индекс на user_id можно не знать по имени — создадим новый позже
    op.drop_column("action_logs", "user_id")
    op.alter_column("action_logs", "user_id_new", new_column_name="user_id", existing_type=sa.BigInteger())
    op.create_index("ix_action_logs_user_id", "action_logs", ["user_id"], unique=False)

    # schedule_shares
    op.execute("ALTER TABLE schedule_shares DROP CONSTRAINT IF EXISTS schedule_shares_owner_user_id_fkey")
    op.drop_column("schedule_shares", "owner_user_id")
    op.alter_column("schedule_shares", "owner_user_id_new", new_column_name="owner_user_id", existing_type=sa.BigInteger())

    # schedule_subscriptions
    op.execute("ALTER TABLE schedule_subscriptions DROP CONSTRAINT IF EXISTS schedule_subscriptions_subscriber_user_id_fkey")
    op.drop_constraint("uq_schedule_subscriber", "schedule_subscriptions", type_="unique")
    op.drop_column("schedule_subscriptions", "subscriber_user_id")
    op.alter_column("schedule_subscriptions", "subscriber_user_id_new", new_column_name="subscriber_user_id", existing_type=sa.BigInteger())
    op.create_unique_constraint("uq_schedule_subscriber", "schedule_subscriptions", ["schedule_id", "subscriber_user_id"])

    # 4) users: переключаем PK на id_new и выбрасываем tg_user_id/old id
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_pkey")
    op.execute("ALTER TABLE users RENAME COLUMN id TO old_id")
    op.execute("ALTER TABLE users RENAME COLUMN id_new TO id")
    op.execute("ALTER TABLE users ADD PRIMARY KEY (id)")

    # убрать индексы/уники по tg_user_id, затем сам столбец
    op.execute("DROP INDEX IF EXISTS ix_users_tg_user_id")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_tg_user_id_key")
    with op.batch_alter_table("users") as b:
        if has_column(conn, "users", "tg_user_id"):
            b.drop_column("tg_user_id")
        if has_column(conn, "users", "old_id"):
            b.drop_column("old_id")

    # 5) вернуть FK на users(id)
    op.execute("""
        ALTER TABLE species
        ADD CONSTRAINT species_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    """)
    op.execute("""
        ALTER TABLE plants
        ADD CONSTRAINT plants_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    """)
    op.execute("""
        ALTER TABLE action_logs
        ADD CONSTRAINT action_logs_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    """)
    op.execute("""
        ALTER TABLE schedule_shares
        ADD CONSTRAINT schedule_shares_owner_user_id_fkey
        FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
    """)
    op.execute("""
        ALTER TABLE schedule_subscriptions
        ADD CONSTRAINT schedule_subscriptions_subscriber_user_id_fkey
        FOREIGN KEY (subscriber_user_id) REFERENCES users(id) ON DELETE CASCADE
    """)


def has_column(conn, table_name: str, column_name: str) -> bool:
    res = conn.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = :t
              AND column_name = :c
            """
        ),
        {"t": table_name, "c": column_name},
    ).fetchone()
    return bool(res)


def downgrade():
    raise NotImplementedError("Downgrade is not supported for 0010_users_pk_is_tg_id")