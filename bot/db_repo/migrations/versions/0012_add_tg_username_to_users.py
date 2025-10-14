"""add tg_username to users

Revision ID: 0012_add_tg_username_to_users
Revises: 0011_share_links
Create Date: 2025-10-15 00:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0012_add_tg_username_to_users"
down_revision = "0011_share_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("tg_username", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "tg_username")