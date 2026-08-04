"""error codes on stories and generation jobs

Revision ID: a4b21c9de07f
Revises: f8549124ef00
Create Date: 2026-08-04 16:20:11.004512

Adds a machine-readable name beside the English failure sentence. `error` is not
touched and is still written on every failure: it is what any already-deployed
client renders, and it is the only thing every existing row has. Deliberately
no backfill — there is no reliable mapping from a frozen English sentence back
to a code, and the fallback already renders those rows correctly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4b21c9de07f"
down_revision: Union[str, None] = "f8549124ef00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stories",
        sa.Column("error_code", sa.String(length=40), server_default="", nullable=False),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("error_code", sa.String(length=40), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("generation_jobs", "error_code")
    op.drop_column("stories", "error_code")
