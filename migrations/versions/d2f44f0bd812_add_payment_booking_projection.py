"""add_payment_booking_projection

Revision ID: d2f44f0bd812
Revises: 9c1e7e5f3b21
Create Date: 2026-03-06 16:42:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2f44f0bd812"
down_revision: Union[str, None] = "9c1e7e5f3b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_booking_projection",
        sa.Column("booking_id", sa.UUID(), nullable=False),
        sa.Column("booking_number", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("pandit_id", sa.UUID(), nullable=False),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("platform_fee", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("booking_id"),
    )
    op.create_index(
        "ix_payment_projection_user_status",
        "payment_booking_projection",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_payment_projection_user_status", table_name="payment_booking_projection")
    op.drop_table("payment_booking_projection")
