"""add notification booking projection

Revision ID: a7c52d8e41fe
Revises: f3c1d4a8b902
Create Date: 2026-03-18 05:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a7c52d8e41fe"
down_revision: Union[str, None] = "f3c1d4a8b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_booking_projection",
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_number", sa.String(length=20), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_request_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("booking_id"),
    )
    op.create_index(
        "ix_notification_booking_projection_status_scheduled",
        "notification_booking_projection",
        ["status", "scheduled_at"],
        unique=False,
    )
    op.create_index(
        "ix_notification_booking_projection_status_completed",
        "notification_booking_projection",
        ["status", "completed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_booking_projection_status_completed",
        table_name="notification_booking_projection",
    )
    op.drop_index(
        "ix_notification_booking_projection_status_scheduled",
        table_name="notification_booking_projection",
    )
    op.drop_table("notification_booking_projection")
