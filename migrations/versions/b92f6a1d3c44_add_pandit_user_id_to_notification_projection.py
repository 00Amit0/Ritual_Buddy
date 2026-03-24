"""add pandit user id to notification booking projection

Revision ID: b92f6a1d3c44
Revises: a7c52d8e41fe
Create Date: 2026-03-18 05:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b92f6a1d3c44"
down_revision: Union[str, None] = "a7c52d8e41fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_booking_projection",
        sa.Column("pandit_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_booking_projection", "pandit_user_id")
