"""mevzuat arsivi, arsiv istekleri ve menude Arsiv sayfasi

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0020'
down_revision: str | None = '0019'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Aynı anda en fazla bir aktif arşiv işi (db.ArsivIstegi ile aynı koşul).
AKTIF = sa.text("durum IN ('BEKLIYOR', 'CALISIYOR', 'DURDURULUYOR')")


def upgrade() -> None:
    op.create_table('arsiv_mevzuat',
    sa.Column('anahtar', sa.String(length=100), nullable=False),
    sa.Column('tur', sa.String(length=60), nullable=False),
    sa.Column('ad', sa.Text(), nullable=False),
    sa.Column('rg_tarihi', sa.Date(), nullable=True),
    sa.Column('rg_sayisi', sa.Integer(), nullable=True),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('bicim', sa.String(length=10), nullable=False),
    sa.Column('mulga', sa.Boolean(), nullable=False),
    sa.Column('ilk_gorulme', sa.DateTime(), nullable=False),
    sa.Column('son_gorulme', sa.DateTime(), nullable=False),
    sa.Column('kayip_sayisi', sa.Integer(), nullable=False),
    sa.Column('kalkti', sa.DateTime(), nullable=True),
    sa.Column('durum', sa.String(length=20), nullable=False),
    sa.Column('deneme', sa.Integer(), nullable=False),
    sa.Column('son_hata', sa.Text(), nullable=True),
    sa.Column('son_cekim', sa.DateTime(), nullable=True),
    sa.Column('surum_sayisi', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('anahtar', name=op.f('pk_arsiv_mevzuat'))
    )
    with op.batch_alter_table('arsiv_mevzuat', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_arsiv_mevzuat_durum'), ['durum'], unique=False)
        batch_op.create_index(batch_op.f('ix_arsiv_mevzuat_tur'), ['tur'], unique=False)

    op.create_table('arsiv_istekleri',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('isteyen_id', sa.Integer(), nullable=True),
    sa.Column('istendi', sa.DateTime(), nullable=False),
    sa.Column('basladi', sa.DateTime(), nullable=True),
    sa.Column('bitti', sa.DateTime(), nullable=True),
    sa.Column('durum', sa.String(length=20), nullable=False),
    sa.Column('secenekler', sa.JSON(), nullable=False),
    sa.Column('ilerleme', sa.JSON(), nullable=True),
    sa.Column('hata', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['isteyen_id'], ['kullanicilar.id'], name=op.f('fk_arsiv_istekleri_isteyen_id_kullanicilar')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_arsiv_istekleri'))
    )
    with op.batch_alter_table('arsiv_istekleri', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_arsiv_istekleri_durum'), ['durum'], unique=False)
    op.create_index('uq_arsiv_istekleri_tek_aktif', 'arsiv_istekleri', [sa.text('(1)')], unique=True,
                    postgresql_where=AKTIF, sqlite_where=AKTIF)

    op.create_table('arsiv_surumleri',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('anahtar', sa.String(length=100), nullable=False),
    sa.Column('surum_no', sa.Integer(), nullable=False),
    sa.Column('cekildi', sa.DateTime(), nullable=False),
    sa.Column('ozet', sa.String(length=64), nullable=False),
    sa.Column('metin', sa.Text(), nullable=False),
    sa.Column('okuma', sa.String(length=20), nullable=False),
    sa.Column('pdf_yolu', sa.String(length=500), nullable=True),
    sa.Column('ekler', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['anahtar'], ['arsiv_mevzuat.anahtar'], name=op.f('fk_arsiv_surumleri_anahtar_arsiv_mevzuat')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_arsiv_surumleri')),
    sa.UniqueConstraint('anahtar', 'surum_no', name=op.f('uq_arsiv_surumleri_anahtar'))
    )
    with op.batch_alter_table('arsiv_surumleri', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_arsiv_surumleri_anahtar'), ['anahtar'], unique=False)

    # Menüye "Arşiv" sayfası, Tarama'nın hemen altına. Panel kullanıcılarının hepsi görür.
    menu = sa.table('menu_ogeleri', sa.column('anahtar', sa.String), sa.column('etiket', sa.String),
                    sa.column('aciklama', sa.String), sa.column('ikon', sa.String), sa.column('yetki', sa.String),
                    sa.column('adres', sa.String), sa.column('sira', sa.Integer), sa.column('aktif', sa.Boolean))
    tarama = op.get_bind().execute(sa.text("SELECT sira FROM menu_ogeleri WHERE anahtar = 'tarama'")).scalar()
    sira = (tarama if tarama is not None else 4) + 1
    op.execute(sa.text("UPDATE menu_ogeleri SET sira = sira + 1 WHERE sira >= :s").bindparams(s=sira))
    op.bulk_insert(menu, [{'anahtar': 'arsiv', 'etiket': 'Arşiv', 'aciklama': 'Yürürlükteki bütün mevzuat',
                           'ikon': 'Archive', 'yetki': 'panel', 'adres': None, 'sira': sira, 'aktif': True}])


def downgrade() -> None:
    sira = op.get_bind().execute(sa.text("SELECT sira FROM menu_ogeleri WHERE anahtar = 'arsiv'")).scalar()
    op.execute(sa.text("DELETE FROM menu_ogeleri WHERE anahtar = 'arsiv'"))
    if sira is not None:
        op.execute(sa.text("UPDATE menu_ogeleri SET sira = sira - 1 WHERE sira > :s").bindparams(s=sira))
    with op.batch_alter_table('arsiv_surumleri', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_arsiv_surumleri_anahtar'))
    op.drop_table('arsiv_surumleri')
    op.drop_index('uq_arsiv_istekleri_tek_aktif', table_name='arsiv_istekleri')
    with op.batch_alter_table('arsiv_istekleri', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_arsiv_istekleri_durum'))
    op.drop_table('arsiv_istekleri')
    with op.batch_alter_table('arsiv_mevzuat', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_arsiv_mevzuat_tur'))
        batch_op.drop_index(batch_op.f('ix_arsiv_mevzuat_durum'))
    op.drop_table('arsiv_mevzuat')
