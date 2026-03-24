"""add search projections

Revision ID: a31e9c5d4f02
Revises: 8f1c7d9b2e20
Create Date: 2026-03-18 02:15:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a31e9c5d4f02"
down_revision: Union[str, None] = "8f1c7d9b2e20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_pandit_projection",
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("languages", postgresql.ARRAY(sa.String(length=50)), nullable=False),
        sa.Column("poojas_offered", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("rating_avg", sa.Numeric(3, 2), nullable=False),
        sa.Column("rating_count", sa.Integer(), nullable=False),
        sa.Column("experience_years", sa.Integer(), nullable=False),
        sa.Column("base_fee", sa.Numeric(10, 2), nullable=False),
        sa.Column("service_radius_km", sa.Numeric(6, 2), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("verification_status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("pandit_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_search_pandit_projection_name", "search_pandit_projection", ["name"], unique=False)
    op.create_index(
        "ix_search_pandit_projection_status",
        "search_pandit_projection",
        ["verification_status", "is_available"],
        unique=False,
    )
    op.create_index("ix_search_pandit_projection_city", "search_pandit_projection", ["city"], unique=False)

    op.create_table(
        "search_pandit_availability_projection",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_time", sa.String(length=8), nullable=False),
        sa.Column("end_time", sa.String(length=8), nullable=False),
        sa.Column("is_booked", sa.Boolean(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_pandit_availability_pandit_date",
        "search_pandit_availability_projection",
        ["pandit_id", "date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_search_pandit_availability_pandit_date", table_name="search_pandit_availability_projection")
    op.drop_table("search_pandit_availability_projection")
    op.drop_index("ix_search_pandit_projection_city", table_name="search_pandit_projection")
    op.drop_index("ix_search_pandit_projection_status", table_name="search_pandit_projection")
    op.drop_index("ix_search_pandit_projection_name", table_name="search_pandit_projection")
    op.drop_table("search_pandit_projection")
