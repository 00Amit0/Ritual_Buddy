"""add_notification_records

Revision ID: e6d8af3a8b41
Revises: d2f44f0bd812
Create Date: 2026-03-06 17:05:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e6d8af3a8b41"
down_revision: Union[str, None] = "d2f44f0bd812"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_records",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("booking_id", sa.UUID(), nullable=True),
        sa.Column(
            "type",
            sa.Enum(
                "BOOKING_CREATED",
                "BOOKING_CONFIRMED",
                "BOOKING_DECLINED",
                "BOOKING_CANCELLED",
                "BOOKING_COMPLETED",
                "PAYMENT_SUCCESS",
                "PAYMENT_FAILED",
                "REVIEW_RECEIVED",
                "PAYOUT_SENT",
                "ACCOUNT_VERIFIED",
                name="notificationtype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_push", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sent_sms", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sent_email", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_records_user_id_read",
        "notification_records",
        ["user_id", "is_read"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notification_records_user_id_read", table_name="notification_records")
    op.drop_table("notification_records")
