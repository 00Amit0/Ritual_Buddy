"""add user pandit projection

Revision ID: b4d2e6f1c903
Revises: a31e9c5d4f02
Create Date: 2026-03-18 02:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b4d2e6f1c903"
down_revision: Union[str, None] = "a31e9c5d4f02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_pandit_projection",
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("rating_avg", sa.Numeric(3, 2), nullable=False),
        sa.Column("rating_count", sa.Integer(), nullable=False),
        sa.Column("base_fee", sa.Numeric(10, 2), nullable=False),
        sa.Column("verification_status", sa.String(length=40), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("pandit_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_user_pandit_projection_name", "user_pandit_projection", ["name"], unique=False)
    op.create_index(
        "ix_user_pandit_projection_status",
        "user_pandit_projection",
        ["verification_status", "is_available"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_pandit_projection_status", table_name="user_pandit_projection")
    op.drop_index("ix_user_pandit_projection_name", table_name="user_pandit_projection")
    op.drop_table("user_pandit_projection")
