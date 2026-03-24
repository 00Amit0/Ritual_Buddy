"""add_review_booking_projection

Revision ID: 2ab44b7273cf
Revises: e6d8af3a8b41
Create Date: 2026-03-06 18:05:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2ab44b7273cf"
down_revision: Union[str, None] = "e6d8af3a8b41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_booking_projection",
        sa.Column("booking_id", sa.UUID(), nullable=False),
        sa.Column("booking_number", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("pandit_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("booking_id"),
    )
    op.create_index(
        "ix_review_projection_user_status",
        "review_booking_projection",
        ["user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_review_projection_pandit",
        "review_booking_projection",
        ["pandit_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_review_projection_pandit", table_name="review_booking_projection")
    op.drop_index("ix_review_projection_user_status", table_name="review_booking_projection")
    op.drop_table("review_booking_projection")
