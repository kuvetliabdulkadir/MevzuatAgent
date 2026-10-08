"""mevzuat gov kaynagi

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-08
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TURLER = ["Kanun", "CumhurbaskaniKararnameleri", "CumhurbaskaniKararlari", "CumhurbaskanligiVeBakanlarKuruluYonetmelik",
          "KurumVeKurulusYonetmeligi", "Teblig", "CumhurbaskanligiGenelgeleri"]


def upgrade() -> None:
    # Kurulu sistemde kaynaklar veritabanından okunur, yeni kaynak buraya eklenir.
    # Tablo boşsa ilk kurulumdur, kaynak config/kaynaklar.toml dosyasından gelir.
    baglanti = op.get_bind()
    kaynaklar = sa.table('kaynaklar', sa.column('ad'), sa.column('etiket'), sa.column('tip'), sa.column('ayarlar', sa.JSON),
                         sa.column('varsayilan_konular', sa.JSON), sa.column('sira'), sa.column('aktif'),
                         sa.column('kaldirildi'), sa.column('surum'), sa.column('guncelleme'))
    adlar = set(baglanti.scalars(sa.select(kaynaklar.c.ad)))
    if not adlar or 'mevzuat_gov_yeni' in adlar:
        return
    sira = baglanti.scalar(sa.select(sa.func.max(kaynaklar.c.sira))) or 0
    op.bulk_insert(kaynaklar, [{
        'ad': 'mevzuat_gov_yeni', 'etiket': 'Mevzuat Bilgi Sistemi', 'tip': 'mevzuat_gov', 'ayarlar': {'turler': TURLER},
        'varsayilan_konular': [], 'sira': sira + 1, 'aktif': True, 'kaldirildi': False, 'surum': 1,
        'guncelleme': datetime.now(),
    }])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM kaynaklar WHERE ad = 'mevzuat_gov_yeni'"))
