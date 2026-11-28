"""rapora giren belgelerin tablo/goruntuleri ve PDF kopyasi

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0023'
down_revision: str | None = '0022'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('kayit_gorselleri',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kayit_id', sa.Integer(), nullable=False),
    sa.Column('sira', sa.Integer(), nullable=False),
    sa.Column('sayfa', sa.Integer(), nullable=False),
    sa.Column('tur', sa.String(length=20), nullable=False),
    sa.Column('aciklama', sa.String(length=200), nullable=False),
    sa.Column('tablo', sa.JSON(), nullable=True),
    sa.Column('png', sa.LargeBinary(), nullable=True),
    sa.Column('genislik', sa.Integer(), nullable=True),
    sa.Column('yukseklik', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['kayit_id'], ['kayitlar.id'], name=op.f('fk_kayit_gorselleri_kayit_id_kayitlar')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kayit_gorselleri'))
    )
    with op.batch_alter_table('kayit_gorselleri', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_kayit_gorselleri_kayit_id'), ['kayit_id'], unique=False)

    op.create_table('kayit_belgeleri',
    sa.Column('kayit_id', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('mime', sa.String(length=100), nullable=False),
    sa.Column('boyut', sa.Integer(), nullable=False),
    sa.Column('veri', sa.LargeBinary(), nullable=False),
    sa.Column('alindi', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['kayit_id'], ['kayitlar.id'], name=op.f('fk_kayit_belgeleri_kayit_id_kayitlar')),
    sa.PrimaryKeyConstraint('kayit_id', name=op.f('pk_kayit_belgeleri'))
    )


def downgrade() -> None:
    op.drop_table('kayit_belgeleri')
    with op.batch_alter_table('kayit_gorselleri', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_kayit_gorselleri_kayit_id'))
    op.drop_table('kayit_gorselleri')
