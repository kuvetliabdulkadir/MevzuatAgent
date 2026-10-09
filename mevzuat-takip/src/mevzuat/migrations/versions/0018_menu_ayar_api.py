"""menu, panel ayarlari, api anahtarlari

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Panelin menüsü. Yetki, herkes (API kullanıcısı dahil), panel (admin ve onaylayıcı), grup (alıcı grubu yönetimi),
# ayar (kaynak/konu), kurtarma (admin), dokuman (admin ve API kullanıcısı, onaylayıcı API dokümanını görmez).
MENU = [
    ("raporlar", "Onay Kuyruğu", "Mevzuat raporları", "FileCheck2", "panel", None),
    ("gruplar", "Alıcı Grupları", "Kime, hangi iş kolu", "Mail", "grup", None),
    ("kaynaklar", "Kaynaklar", "Taranan siteler", "Globe", "ayar", None),
    ("konular", "Konular", "Anahtar kelimeler", "Layers", "ayar", None),
    ("tarama", "Tarama", "Saatler ve durum", "Clock", "panel", None),
    ("kullanicilar", "Kullanıcılar", "Ekle, parola, pasifleştir", "Users", "kurtarma", None),
    ("denetim", "Denetim Kaydı", "Kim, ne zaman, ne yaptı", "History", "kurtarma", None),
    ("ayarlar", "Ayarlar", "Mail, panel adresi, süreler", "Settings", "kurtarma", None),
    ("api_anahtarlari", "API Anahtarları", "Dış sistemlerin erişimi", "KeyRound", "kurtarma", None),
    ("api_dokumani", "API Dokümanı", "Swagger, uç noktalar", "BookOpen", "dokuman", "/api/dokuman"),
]


def upgrade() -> None:
    tablo = op.create_table(
        'menu_ogeleri',
        sa.Column('anahtar', sa.String(length=50), nullable=False),
        sa.Column('etiket', sa.String(length=100), nullable=False),
        sa.Column('aciklama', sa.String(length=200), nullable=False),
        sa.Column('ikon', sa.String(length=50), nullable=False),
        sa.Column('yetki', sa.String(length=20), nullable=False),
        sa.Column('adres', sa.String(length=200), nullable=True),
        sa.Column('sira', sa.Integer(), nullable=False),
        sa.Column('aktif', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('anahtar'),
    )
    op.bulk_insert(tablo, [
        {'anahtar': a, 'etiket': e, 'aciklama': d, 'ikon': i, 'yetki': y, 'adres': adres, 'sira': sira, 'aktif': True}
        for sira, (a, e, d, i, y, adres) in enumerate(MENU)
    ])
    with op.batch_alter_table('kullanicilar', schema=None) as batch_op:
        batch_op.add_column(sa.Column('api_hesabi', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        'api_anahtarlari',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ad', sa.String(length=100), nullable=False),
        sa.Column('kullanici_id', sa.Integer(), nullable=False),
        sa.Column('on_ek', sa.String(length=12), nullable=False),
        sa.Column('ozet', sa.String(length=64), nullable=False),
        sa.Column('olusturan_id', sa.Integer(), nullable=True),
        sa.Column('olusturuldu', sa.DateTime(), nullable=False),
        sa.Column('son_kullanma', sa.DateTime(), nullable=True),
        sa.Column('son_kullanim', sa.DateTime(), nullable=True),
        sa.Column('iptal', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['kullanici_id'], ['kullanicilar.id']),
        sa.ForeignKeyConstraint(['olusturan_id'], ['kullanicilar.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ozet'),
    )


def downgrade() -> None:
    op.drop_table('api_anahtarlari')
    with op.batch_alter_table('kullanicilar', schema=None) as batch_op:
        batch_op.drop_column('api_hesabi')
    op.drop_table('menu_ogeleri')
