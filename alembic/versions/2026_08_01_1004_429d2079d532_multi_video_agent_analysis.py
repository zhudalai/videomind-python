"""multi video agent analysis

Revision ID: 429d2079d532
Revises: b1a7d2e3f901
Create Date: 2026-08-01 10:04:08.018081

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '429d2079d532'
down_revision: Union[str, None] = 'b1a7d2e3f901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure pgcrypto extension exists for digest/encode functions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # 1. 加列（先 nullable，回填后再 NOT NULL）
    op.add_column('analysis_task',
        sa.Column('media_ids_hash', sa.String(length=64), nullable=True))

    # 2. 回填旧行：单视频任务 media_ids_hash = sha256(lower(media_id::text))
    #    pgcrypto 在本库已启用（gen_random_uuid 依赖它）；digest/encode 来自 pgcrypto。
    op.execute(
        "UPDATE analysis_task "
        "SET media_ids_hash = encode(digest(lower(CAST(media_id AS text))::bytea, 'sha256'), 'hex')"
    )

    # 3. 改 NOT NULL
    op.alter_column('analysis_task', 'media_ids_hash',
        nullable=False, server_default='')

    # 4. 换唯一约束
    op.drop_constraint('uq_at_media_goal', 'analysis_task', type_='unique')
    op.create_unique_constraint(
        'uq_at_mediaids_goal', 'analysis_task', ['media_ids_hash', 'goal_hash'])

    # 5. 索引
    op.create_index('ix_analysis_task_media_ids_hash', 'analysis_task', ['media_ids_hash'])

    # 6. 关联表
    op.create_table(
        'analysis_task_media',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('media_id', sa.UUID(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['task_id'], ['analysis_task.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['media_id'], ['media_file.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('task_id', 'media_id', name='uq_atm_task_media'),
    )
    op.create_index('ix_analysis_task_media_task_id', 'analysis_task_media', ['task_id'])
    op.create_index('ix_analysis_task_media_media_id', 'analysis_task_media', ['media_id'])


def downgrade() -> None:
    op.drop_index('ix_analysis_task_media_media_id', table_name='analysis_task_media')
    op.drop_index('ix_analysis_task_media_task_id', table_name='analysis_task_media')
    op.drop_table('analysis_task_media')
    op.drop_index('ix_analysis_task_media_ids_hash', table_name='analysis_task')
    op.drop_constraint('uq_at_mediaids_goal', 'analysis_task', type_='unique')
    op.create_unique_constraint('uq_at_media_goal', 'analysis_task', ['media_id', 'goal_hash'])
    op.drop_column('analysis_task', 'media_ids_hash')
