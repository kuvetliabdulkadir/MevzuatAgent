"""arsiv okuyucu 2: surum turu, kalite, kaynak ozeti, gorunum, sayfalar, resimler, sitenin PDF'i, RG notlari, dosyalar

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0021'
down_revision: str | None = '0020'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('arsiv_mevzuat', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kalite', sa.String(length=20), nullable=True))
        batch_op.create_index(batch_op.f('ix_arsiv_mevzuat_kalite'), ['kalite'], unique=False)

    with op.batch_alter_table('arsiv_surumleri', schema=None) as batch_op:
        batch_op.add_column(sa.Column('okuyucu_surumu', sa.Integer(), server_default=sa.text('1'), nullable=False))
        batch_op.add_column(sa.Column('surum_turu', sa.String(length=20), server_default='ilk', nullable=False))
        batch_op.add_column(sa.Column('kalite', sa.String(length=20), server_default='temiz', nullable=False))
        batch_op.add_column(sa.Column('kalite_notlari', sa.JSON(), server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('kaynak_ozeti', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('gorunum', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('arama_metni', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('sayfalar', sa.JSON(), server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('resimler', sa.JSON(), server_default='[]', nullable=False))
        batch_op.add_column(sa.Column('site_pdf_yolu', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('site_pdf_turu', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('rg_notlari', sa.JSON(), server_default='[]', nullable=False))

    # Okuyucu 1'in ikinci sürümleri (v2) çoğunlukla okuma düzeltmesiydi (2026-11-22 otopsisi), ama eski kayıtta kaynak
    # özeti yok, ayırt edilemez; "degisiklik" sayılır. Bu sürümler teslimden önce katlanır (karar 19).
    op.execute(sa.text("UPDATE arsiv_surumleri SET surum_turu = 'degisiklik' WHERE surum_no > 1"))
    op.execute(sa.text("UPDATE arsiv_mevzuat SET durum = 'SITEDE_YOK' WHERE durum = 'BOS'"))

    op.create_table('arsiv_dosyalari',
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('ad', sa.String(length=500), nullable=False),
    sa.Column('bicim', sa.String(length=20), nullable=False),
    sa.Column('boyut', sa.Integer(), nullable=False),
    sa.Column('yol', sa.String(length=500), nullable=False),
    sa.Column('durum', sa.String(length=20), nullable=False),
    sa.Column('metin', sa.Text(), nullable=True),
    sa.Column('gorunum', sa.Text(), nullable=True),
    sa.Column('notlar', sa.JSON(), nullable=False),
    sa.Column('ic', sa.JSON(), nullable=False),
    sa.Column('sayfalar', sa.JSON(), nullable=False),
    sa.Column('okuyucu_surumu', sa.Integer(), nullable=False),
    sa.Column('islendi', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('sha256', name=op.f('pk_arsiv_dosyalari'))
    )


def downgrade() -> None:
    op.drop_table('arsiv_dosyalari')
    op.execute(sa.text("UPDATE arsiv_mevzuat SET durum = 'BOS' WHERE durum = 'SITEDE_YOK'"))
    with op.batch_alter_table('arsiv_surumleri', schema=None) as batch_op:
        for ad in ('rg_notlari', 'site_pdf_turu', 'site_pdf_yolu', 'resimler', 'sayfalar', 'arama_metni', 'gorunum',
                   'kaynak_ozeti', 'kalite_notlari', 'kalite', 'surum_turu', 'okuyucu_surumu'):
            batch_op.drop_column(ad)
    with op.batch_alter_table('arsiv_mevzuat', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_arsiv_mevzuat_kalite'))
        batch_op.drop_column('kalite')
