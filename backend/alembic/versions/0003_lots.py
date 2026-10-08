"""lots de documents, annulation, début de traitement

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-07 18:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('batches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('options', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('batches', schema=None) as batch_op:
        batch_op.create_index('ix_batches_user_created', ['user_id', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_batches_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('batch_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('started_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.create_index(batch_op.f('ix_documents_batch_id'), ['batch_id'], unique=False)
        batch_op.create_foreign_key('fk_documents_batch_id', 'batches', ['batch_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_constraint('fk_documents_batch_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_documents_batch_id'))
        batch_op.drop_column('cancel_requested')
        batch_op.drop_column('started_at')
        batch_op.drop_column('batch_id')
    with op.batch_alter_table('batches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_batches_user_id'))
        batch_op.drop_index('ix_batches_user_created')
    op.drop_table('batches')
