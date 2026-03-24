"""add_outbox_events

Revision ID: 9c1e7e5f3b21
Revises: f7a83a716172
Create Date: 2026-03-06 16:05:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "9c1e7e5f3b21"
down_revision: Union[str, None] = "f7a83a716172"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("topic", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="NEW"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_status_available", "outbox_events", ["status", "available_at"], unique=False)
    op.create_index("ix_outbox_event_key", "outbox_events", ["event_key"], unique=False)
    op.create_index("ix_outbox_topic", "outbox_events", ["topic"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_outbox_topic", table_name="outbox_events")
    op.drop_index("ix_outbox_event_key", table_name="outbox_events")
    op.drop_index("ix_outbox_status_available", table_name="outbox_events")
    op.drop_table("outbox_events")
