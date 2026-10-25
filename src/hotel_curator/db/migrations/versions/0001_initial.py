"""Initial schema: hotels, attribute scores, dataset info.

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the hotels, attribute_scores and dataset_info tables."""
    op.create_table(
        "hotels",
        sa.Column("hotel_id", sa.String(16), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_lower", sa.String(200), nullable=False),
        sa.Column("city", sa.String(80), nullable=False),
        sa.Column("country", sa.String(80), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("tier_rank", sa.Integer(), nullable=False),
        sa.Column("sort_score", sa.Float(), nullable=False),
        sa.Column("reviews_eligible", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_hotels_name_lower", "hotels", ["name_lower"])
    op.create_index("ix_hotels_city", "hotels", ["city"])
    op.create_index("ix_hotels_tier", "hotels", ["tier"])
    op.create_index("ix_hotels_tier_rank", "hotels", ["tier_rank"])
    op.create_index(
        "ix_hotels_rank_score", "hotels", ["tier_rank", "sort_score"]
    )
    op.create_table(
        "attribute_scores",
        sa.Column(
            "hotel_id",
            sa.String(16),
            sa.ForeignKey("hotels.hotel_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("attribute", sa.String(32), primary_key=True),
        sa.Column("grade", sa.String(16), nullable=False),
        sa.Column("observed", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_attribute_grade", "attribute_scores", ["attribute", "grade"]
    )
    op.create_table(
        "dataset_info",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("results_sha256", sa.String(64), nullable=False),
        sa.Column("params_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("hotels", sa.Integer(), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    """Drop everything ``upgrade`` created."""
    op.drop_table("dataset_info")
    op.drop_index("ix_attribute_grade", table_name="attribute_scores")
    op.drop_table("attribute_scores")
    for name in (
        "ix_hotels_rank_score",
        "ix_hotels_tier_rank",
        "ix_hotels_tier",
        "ix_hotels_city",
        "ix_hotels_name_lower",
    ):
        op.drop_index(name, table_name="hotels")
    op.drop_table("hotels")
