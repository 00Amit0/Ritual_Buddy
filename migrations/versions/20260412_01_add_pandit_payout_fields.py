"""add pandit payout fields

Revision ID: 20260412_01
Revises:
Create Date: 2026-04-12 19:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260412_01"
down_revision = None
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    if not _has_column("pandit_profiles", "bank_account_number"):
        op.add_column(
            "pandit_profiles",
            sa.Column("bank_account_number", sa.String(length=34), nullable=True),
        )

    if not _has_column("pandit_profiles", "bank_ifsc"):
        op.add_column(
            "pandit_profiles",
            sa.Column("bank_ifsc", sa.String(length=20), nullable=True),
        )


def downgrade() -> None:
    if _has_column("pandit_profiles", "bank_ifsc"):
        op.drop_column("pandit_profiles", "bank_ifsc")

    if _has_column("pandit_profiles", "bank_account_number"):
        op.drop_column("pandit_profiles", "bank_account_number")
