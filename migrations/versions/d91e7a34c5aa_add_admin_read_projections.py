"""add admin read projections

Revision ID: d91e7a34c5aa
Revises: c8f4a21d9b77
Create Date: 2026-03-18 03:35:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d91e7a34c5aa"
down_revision: Union[str, None] = "c8f4a21d9b77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_user_projection",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_admin_user_projection_role_active",
        "admin_user_projection",
        ["role", "is_active"],
        unique=False,
    )

    op.create_table(
        "admin_booking_projection",
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_number", sa.String(length=20), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pooja_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("platform_fee", sa.Numeric(10, 2), nullable=False),
        sa.Column("pandit_payout", sa.Numeric(10, 2), nullable=False),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("booking_id"),
    )
    op.create_index(
        "ix_admin_booking_projection_status_created",
        "admin_booking_projection",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_booking_projection_user",
        "admin_booking_projection",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_booking_projection_pandit",
        "admin_booking_projection",
        ["pandit_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "admin_payment_projection",
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("payment_id"),
    )
    op.create_index(
        "ix_admin_payment_projection_status_captured",
        "admin_payment_projection",
        ["status", "captured_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_payment_projection_booking",
        "admin_payment_projection",
        ["booking_id"],
        unique=False,
    )

    op.create_table(
        "admin_review_projection",
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("review_id"),
    )
    op.create_index(
        "ix_admin_review_projection_visible",
        "admin_review_projection",
        ["is_visible", "pandit_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_review_projection_visible", table_name="admin_review_projection")
    op.drop_table("admin_review_projection")

    op.drop_index("ix_admin_payment_projection_booking", table_name="admin_payment_projection")
    op.drop_index("ix_admin_payment_projection_status_captured", table_name="admin_payment_projection")
    op.drop_table("admin_payment_projection")

    op.drop_index("ix_admin_booking_projection_pandit", table_name="admin_booking_projection")
    op.drop_index("ix_admin_booking_projection_user", table_name="admin_booking_projection")
    op.drop_index("ix_admin_booking_projection_status_created", table_name="admin_booking_projection")
    op.drop_table("admin_booking_projection")

    op.drop_index("ix_admin_user_projection_role_active", table_name="admin_user_projection")
    op.drop_table("admin_user_projection")
