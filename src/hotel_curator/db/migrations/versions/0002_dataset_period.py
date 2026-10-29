"""Dataset info: the first and last month of the usable reviews.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two months, empty until the next seed fills them."""
    op.add_column(
        "dataset_info", sa.Column("first_month", sa.String(7), nullable=True)
    )
    op.add_column(
        "dataset_info", sa.Column("last_month", sa.String(7), nullable=True)
    )


def downgrade() -> None:
    """Drop the two months (batch mode, which SQLite needs to drop columns)."""
    with op.batch_alter_table("dataset_info") as batch:
        batch.drop_column("last_month")
        batch.drop_column("first_month")
