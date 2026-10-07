"""sistem ayarlari

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('sistem_ayarlari',
    sa.Column('anahtar', sa.String(length=50), nullable=False),
    sa.Column('deger', sa.JSON(), nullable=False),
    sa.Column('surum', sa.Integer(), nullable=False),
    sa.Column('guncelleme', sa.DateTime(), nullable=False),
    sa.Column('guncelleyen_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['guncelleyen_id'], ['kullanicilar.id'], name=op.f('fk_sistem_ayarlari_guncelleyen_id_kullanicilar')),
    sa.PrimaryKeyConstraint('anahtar', name=op.f('pk_sistem_ayarlari'))
    )


def downgrade() -> None:
    op.drop_table('sistem_ayarlari')
