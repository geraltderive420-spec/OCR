"""mesures typographiques des blocs

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07 10:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('blocks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('style', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('blocks', schema=None) as batch_op:
        batch_op.drop_column('style')
