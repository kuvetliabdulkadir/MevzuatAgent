"""kullanici silme

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-07
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('kullanicilar', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pasif_tarihi', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('silindi', sa.DateTime(), nullable=True))
    # Hâlihazırda pasif olanların süresi bugünden başlar.
    op.execute(sa.text("UPDATE kullanicilar SET pasif_tarihi = :simdi WHERE aktif = :pasif")
               .bindparams(simdi=datetime.now(), pasif=False))


def downgrade() -> None:
    with op.batch_alter_table('kullanicilar', schema=None) as batch_op:
        batch_op.drop_column('silindi')
        batch_op.drop_column('pasif_tarihi')
