"""ledger user fk set null

Account deletion must anonymise the generation ledger, not destroy it: the
platform-wide cost ceiling counts these rows, and cascading them let a
create-generate-delete loop drain the budget invisibly. GDPR wants the person
gone, not the accounting.

Revision ID: 3793efbb78d8
Revises: 8cc694e50351
Create Date: 2026-08-03
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "3793efbb78d8"
down_revision: Union[str, None] = "8cc694e50351"
branch_labels = None
depends_on = None

# Postgres alters in place and needs the constraint's default name. SQLite
# cannot address unnamed constraints at all, so its path rebuilds the table
# from an explicit definition (copy_from) that simply omits the old FK.
_FK = "generation_events_user_id_fkey"


def _copy_from(user_fk_ondelete: str | None) -> sa.Table:
    """The generation_events table as SQLite should rebuild it.

    Mirrors models.GenerationEvent column-for-column, indexes included —
    a batch rebuild keeps only what is declared here.
    """
    args = [
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), nullable=user_fk_ondelete == "SET NULL"),
        sa.Column("story_id", sa.String(32), nullable=True),
        sa.Column("refunded", sa.Boolean, nullable=False),
        sa.Column("refund_reason", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("images", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"], ondelete="SET NULL"),
        sa.Index("ix_generation_events_user_id", "user_id"),
        sa.Index("ix_generation_events_story_id", "story_id"),
        sa.Index("ix_generation_events_created_at", "created_at"),
        sa.Index("ix_generation_events_user_created", "user_id", "created_at"),
    ]
    if user_fk_ondelete is not None:
        args.insert(
            10, sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete=user_fk_ondelete)
        )
    return sa.Table("generation_events", sa.MetaData(), *args)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "generation_events", "user_id", existing_type=sa.VARCHAR(length=32), nullable=True
        )
        op.drop_constraint(_FK, "generation_events", type_="foreignkey")
        op.create_foreign_key(
            _FK, "generation_events", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )
    else:
        # copy_from omits the user FK, so the rebuild drops it without naming
        # it; the batch op then adds the SET NULL version.
        with op.batch_alter_table(
            "generation_events", copy_from=_copy_from(None), recreate="always"
        ) as batch_op:
            batch_op.alter_column("user_id", existing_type=sa.VARCHAR(length=32), nullable=True)
            batch_op.create_foreign_key(
                "fk_generation_events_user_id", "users", ["user_id"], ["id"], ondelete="SET NULL"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(_FK, "generation_events", type_="foreignkey")
        op.create_foreign_key(
            _FK, "generation_events", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        op.alter_column(
            "generation_events", "user_id", existing_type=sa.VARCHAR(length=32), nullable=False
        )
    else:
        with op.batch_alter_table(
            "generation_events", copy_from=_copy_from(None), recreate="always"
        ) as batch_op:
            batch_op.alter_column("user_id", existing_type=sa.VARCHAR(length=32), nullable=False)
            batch_op.create_foreign_key(
                "fk_generation_events_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE"
            )
