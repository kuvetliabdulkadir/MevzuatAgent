"""tek aktif tarama istegi

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KOSUL = sa.text("durum IN ('BEKLIYOR', 'CALISIYOR')")


def upgrade() -> None:
    # Eski bir yarıştan kalmış fazla aktif istek varsa indeks kurulamaz: en eskisi dışındakileri kapat.
    op.execute("""
        UPDATE tarama_istekleri SET durum = 'HATALI', hata = 'Aynı anda açılmış fazla istek; kapatıldı.'
        WHERE durum IN ('BEKLIYOR', 'CALISIYOR')
          AND id > (SELECT MIN(id) FROM tarama_istekleri WHERE durum IN ('BEKLIYOR', 'CALISIYOR'))
    """)
    op.create_index('uq_tarama_istekleri_tek_aktif', 'tarama_istekleri', [sa.text('(1)')], unique=True,
                    postgresql_where=KOSUL, sqlite_where=KOSUL)


def downgrade() -> None:
    op.drop_index('uq_tarama_istekleri_tek_aktif', table_name='tarama_istekleri')
