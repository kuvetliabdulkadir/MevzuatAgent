"""parola linkleri

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('parola_linkleri',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kullanici_id', sa.Integer(), nullable=False),
    sa.Column('ozet', sa.String(length=64), nullable=False),
    sa.Column('tur', sa.String(length=20), nullable=False),
    sa.Column('olusturuldu', sa.DateTime(), nullable=False),
    sa.Column('son_gecerlilik', sa.DateTime(), nullable=False),
    sa.Column('kullanildi', sa.DateTime(), nullable=True),
    sa.Column('isteyen_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['isteyen_id'], ['kullanicilar.id'], name=op.f('fk_parola_linkleri_isteyen_id_kullanicilar')),
    sa.ForeignKeyConstraint(['kullanici_id'], ['kullanicilar.id'], name=op.f('fk_parola_linkleri_kullanici_id_kullanicilar')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_parola_linkleri')),
    sa.UniqueConstraint('ozet', name=op.f('uq_parola_linkleri_ozet'))
    )
    with op.batch_alter_table('parola_linkleri', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_parola_linkleri_kullanici_id'), ['kullanici_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('parola_linkleri', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_parola_linkleri_kullanici_id'))
    op.drop_table('parola_linkleri')
