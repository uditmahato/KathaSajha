"""user token version

Revision ID: 2e8f49dd67b9
Revises: 75a4e5ce30bf
Create Date: 2026-08-02 14:43:40.143847
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2e8f49dd67b9'
down_revision: Union[str, None] = '75a4e5ce30bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Access tokens carry the version they were issued with; bumping this column
    # on a password change or reset retires every session at once. The server
    # default backfills existing rows at 0, and tokens minted before this column
    # existed carry no version at all, so they are rejected on the next request.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('token_version', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('token_version')
