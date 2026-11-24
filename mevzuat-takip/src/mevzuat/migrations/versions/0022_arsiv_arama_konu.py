"""arsiv tam metin aramasi (madde madde) ve konu filtresi sonuclari

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0022'
down_revision: str | None = '0021'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('arsiv_arama',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('anahtar', sa.String(length=100), nullable=False),
    sa.Column('surum_no', sa.Integer(), nullable=False),
    sa.Column('sira', sa.Integer(), nullable=False),
    sa.Column('bolum', sa.String(length=300), nullable=False),
    sa.Column('metin', sa.Text(), nullable=False),
    sa.Column('ozgun', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['anahtar'], ['arsiv_mevzuat.anahtar'], name=op.f('fk_arsiv_arama_anahtar_arsiv_mevzuat')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_arsiv_arama'))
    )
    with op.batch_alter_table('arsiv_arama', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_arsiv_arama_anahtar'), ['anahtar'], unique=False)
    if op.get_bind().dialect.name == 'postgresql':
        op.create_index('ix_arsiv_arama_tsv', 'arsiv_arama', [sa.text("to_tsvector('simple', metin)")],
                        postgresql_using='gin')

    op.create_table('arsiv_konu',
    sa.Column('anahtar', sa.String(length=100), nullable=False),
    sa.Column('konu', sa.String(length=200), nullable=False),
    sa.Column('surum_no', sa.Integer(), nullable=False),
    sa.Column('puan', sa.Double(), nullable=False),
    sa.Column('ilgili', sa.Boolean(), nullable=False),
    sa.Column('kanit', sa.JSON(), nullable=False),
    sa.Column('hesaplandi', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['anahtar'], ['arsiv_mevzuat.anahtar'], name=op.f('fk_arsiv_konu_anahtar_arsiv_mevzuat')),
    sa.PrimaryKeyConstraint('anahtar', 'konu', name=op.f('pk_arsiv_konu'))
    )
    with op.batch_alter_table('arsiv_konu', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_arsiv_konu_ilgili'), ['ilgili'], unique=False)
        batch_op.create_index(batch_op.f('ix_arsiv_konu_konu'), ['konu'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('arsiv_konu', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_arsiv_konu_konu'))
        batch_op.drop_index(batch_op.f('ix_arsiv_konu_ilgili'))
    op.drop_table('arsiv_konu')
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_index('ix_arsiv_arama_tsv', table_name='arsiv_arama')
    with op.batch_alter_table('arsiv_arama', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_arsiv_arama_anahtar'))
    op.drop_table('arsiv_arama')
