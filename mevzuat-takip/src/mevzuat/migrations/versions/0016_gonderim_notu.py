"""gonderim notu

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0016'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('gonderimler', schema=None) as batch_op:
        batch_op.add_column(sa.Column('notu', sa.Text(), nullable=True))
    # Eski gönderimler raporun karar notunu taşır, mail eskisi gibi hazırlanır.
    op.execute(sa.text("UPDATE gonderimler SET notu = (SELECT karar_notu FROM raporlar WHERE raporlar.id = gonderimler.rapor_id)"))


def downgrade() -> None:
    with op.batch_alter_table('gonderimler', schema=None) as batch_op:
        batch_op.drop_column('notu')
