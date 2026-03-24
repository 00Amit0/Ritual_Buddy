"""add admin pandit review projection

Revision ID: c8f4a21d9b77
Revises: b4d2e6f1c903
Create Date: 2026-03-18 03:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c8f4a21d9b77"
down_revision: Union[str, None] = "b4d2e6f1c903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_pandit_review_projection",
        sa.Column("pandit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("experience_years", sa.Integer(), nullable=False),
        sa.Column("languages", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("poojas_offered", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("documents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("verification_status", sa.String(length=40), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("profile_complete", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("pandit_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_admin_pandit_review_status",
        "admin_pandit_review_projection",
        ["verification_status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_pandit_review_status", table_name="admin_pandit_review_projection")
    op.drop_table("admin_pandit_review_projection")
