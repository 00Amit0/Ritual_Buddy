"""add user account projection

Revision ID: e2a6b9d1184f
Revises: d91e7a34c5aa
Create Date: 2026-03-18 04:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e2a6b9d1184f"
down_revision: Union[str, None] = "d91e7a34c5aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_account_projection",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("preferred_language", sa.String(length=10), nullable=False),
        sa.Column("fcm_token", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_user_account_projection_phone",
        "user_account_projection",
        ["phone"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_account_projection_phone", table_name="user_account_projection")
    op.drop_table("user_account_projection")
