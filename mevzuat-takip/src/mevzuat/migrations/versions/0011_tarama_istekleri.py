"""tarama istekleri

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tarama_istekleri',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('isteyen_id', sa.Integer(), nullable=True),
    sa.Column('istendi', sa.DateTime(), nullable=False),
    sa.Column('basladi', sa.DateTime(), nullable=True),
    sa.Column('bitti', sa.DateTime(), nullable=True),
    sa.Column('durum', sa.String(length=20), nullable=False),
    sa.Column('calisma_id', sa.Integer(), nullable=True),
    sa.Column('rapor_id', sa.Integer(), nullable=True),
    sa.Column('hata', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['calisma_id'], ['calismalar.id'], name=op.f('fk_tarama_istekleri_calisma_id_calismalar')),
    sa.ForeignKeyConstraint(['isteyen_id'], ['kullanicilar.id'], name=op.f('fk_tarama_istekleri_isteyen_id_kullanicilar')),
    sa.ForeignKeyConstraint(['rapor_id'], ['raporlar.id'], name=op.f('fk_tarama_istekleri_rapor_id_raporlar')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tarama_istekleri'))
    )
    with op.batch_alter_table('tarama_istekleri', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tarama_istekleri_durum'), ['durum'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('tarama_istekleri', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tarama_istekleri_durum'))
    op.drop_table('tarama_istekleri')
