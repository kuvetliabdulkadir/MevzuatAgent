"""istek imzasi, api anahtarinin sifreli hali ve tek seferlik numaralar

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0019'
down_revision: str | None = '0018'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('api_anahtarlari', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gizli', sa.Text(), nullable=True))
    op.create_table(
        'istek_numaralari',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('anahtar_id', sa.Integer(), nullable=False),
        sa.Column('numara', sa.String(length=64), nullable=False),
        sa.Column('zaman', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['anahtar_id'], ['api_anahtarlari.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('anahtar_id', 'numara'),
    )
    with op.batch_alter_table('istek_numaralari', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_istek_numaralari_zaman'), ['zaman'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('istek_numaralari', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_istek_numaralari_zaman'))
    op.drop_table('istek_numaralari')
    with op.batch_alter_table('api_anahtarlari', schema=None) as batch_op:
        batch_op.drop_column('gizli')
