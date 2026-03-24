"""add booking projections

Revision ID: 6b9d3c2a4f11
Revises: 2ab44b7273cf
Create Date: 2026-03-18 01:25:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "6b9d3c2a4f11"
down_revision: Union[str, None] = "2ab44b7273cf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "booking_pandit_projection",
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("verification_status", sa.String(length=40), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("base_fee", sa.Numeric(10, 2), nullable=False),
        sa.Column("pooja_fees", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("profile_complete", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("pandit_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_booking_pandit_projection_user",
        "booking_pandit_projection",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_booking_pandit_projection_status",
        "booking_pandit_projection",
        ["verification_status", "is_available"],
        unique=False,
    )

    op.create_table(
        "booking_availability_projection",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_time", sa.String(length=8), nullable=False),
        sa.Column("end_time", sa.String(length=8), nullable=False),
        sa.Column("is_booked", sa.Boolean(), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("blocked_reason", sa.String(length=255), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_booking_availability_pandit_date",
        "booking_availability_projection",
        ["pandit_id", "date"],
        unique=False,
    )
    op.create_index(
        "ix_booking_availability_booking",
        "booking_availability_projection",
        ["booking_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_booking_availability_booking", table_name="booking_availability_projection")
    op.drop_index("ix_booking_availability_pandit_date", table_name="booking_availability_projection")
    op.drop_table("booking_availability_projection")
    op.drop_index("ix_booking_pandit_projection_status", table_name="booking_pandit_projection")
    op.drop_index("ix_booking_pandit_projection_user", table_name="booking_pandit_projection")
    op.drop_table("booking_pandit_projection")
