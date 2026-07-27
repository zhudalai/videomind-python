"""add user_config table

Revision ID: b1a7d2e3f901
Revises: ac2c0859ceb3
Create Date: 2026-07-26 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1a7d2e3f901'
down_revision: Union[str, None] = 'ac2c0859ceb3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_config',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('theme', sa.String(length=16), server_default=sa.text("'system'"), nullable=False),
        sa.Column('default_model', sa.String(length=128), nullable=True),
        sa.Column('language', sa.String(length=16), server_default=sa.text("'zh-CN'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', name='uq_user_config_user_id'),
    )
    op.create_index('ix_user_config_user_id', 'user_config', ['user_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_user_config_user_id', table_name='user_config')
    op.drop_table('user_config')
