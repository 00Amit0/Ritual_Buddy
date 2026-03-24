"""add pandit booking projection

Revision ID: 8f1c7d9b2e20
Revises: 6b9d3c2a4f11
Create Date: 2026-03-18 01:55:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "8f1c7d9b2e20"
down_revision: Union[str, None] = "6b9d3c2a4f11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pandit_booking_projection",
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_number", sa.String(length=20), nullable=False),
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_hrs", sa.Numeric(4, 1), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("pandit_payout", sa.Numeric(10, 2), nullable=False),
        sa.Column("payout_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("payout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("booking_id"),
    )
    op.create_index(
        "ix_pandit_booking_projection_pandit_status",
        "pandit_booking_projection",
        ["pandit_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_pandit_booking_projection_scheduled",
        "pandit_booking_projection",
        ["pandit_id", "scheduled_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pandit_booking_projection_scheduled", table_name="pandit_booking_projection")
    op.drop_index("ix_pandit_booking_projection_pandit_status", table_name="pandit_booking_projection")
    op.drop_table("pandit_booking_projection")
