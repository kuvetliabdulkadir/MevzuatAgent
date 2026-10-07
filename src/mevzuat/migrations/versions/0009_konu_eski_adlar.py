"""konu eski adlar

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('konular', schema=None) as batch_op:
        batch_op.add_column(sa.Column('eski_adlar', sa.JSON(), server_default='[]', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('konular', schema=None) as batch_op:
        batch_op.drop_column('eski_adlar')
