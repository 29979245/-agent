"""exam-bank: 题库两级表 + ExamRecord 三列 + Question.record_id

Revision ID: 9c4e2f1a8b3d
Revises: dff337e274e7
Create Date: 2026-08-24 09:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c4e2f1a8b3d'
down_revision: Union[str, None] = 'dff337e274e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 题库文件夹
    op.create_table('question_set',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('teacher_id', sa.Integer(), nullable=False),
    sa.Column('region', sa.String(length=100), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('question_count', sa.Integer(), nullable=False),
    sa.Column('is_preset', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    # 题库条目（题库文件夹 <-> 题目 多对多关联）
    op.create_table('question_set_item',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('set_id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['question.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['set_id'], ['question_set.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    # ExamRecord 扩展：name / status（六态）/ question_stats（发布元数据）
    # SQLite 不支持 ALTER 加 CHECK 约束，用 batch 重建表以带出 status 枚举校验
    with op.batch_alter_table('exam_record') as batch_op:
        batch_op.add_column(sa.Column('name', sa.String(length=100), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('status', sa.Enum('draft', 'published', 'in_progress', 'grading', 'completed', 'archived', name='examstatus', native_enum=False, create_constraint=True), nullable=False, server_default='draft'))
        batch_op.add_column(sa.Column('question_stats', sa.JSON(), nullable=False, server_default='{}'))
    # Question 考试关联（渠道一直接关联 / 渠道二复制后关联）：
    # SQLite 不支持 ALTER 加约束，用 batch 重建表以带出 record_id 外键
    with op.batch_alter_table('question') as batch_op:
        batch_op.add_column(sa.Column('record_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_question_record_id', 'exam_record', ['record_id'], ['id'], ondelete='SET NULL')
    # 热 FK 列索引（D10）：SQLite 不自动为外键建索引
    op.create_index('ix_question_set_item_set_id', 'question_set_item', ['set_id'], unique=False)
    op.create_index('ix_question_set_item_question_id', 'question_set_item', ['question_id'], unique=False)
    op.create_index('ix_question_record_id', 'question', ['record_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_question_record_id', table_name='question')
    op.drop_index('ix_question_set_item_question_id', table_name='question_set_item')
    op.drop_index('ix_question_set_item_set_id', table_name='question_set_item')
    # SQLite 不支持原生 DROP COLUMN / DROP 约束，需 batch 重建表
    with op.batch_alter_table('question') as batch_op:
        batch_op.drop_constraint('fk_question_record_id', type_='foreignkey')
        batch_op.drop_column('record_id')
    with op.batch_alter_table('exam_record') as batch_op:
        # 先摘掉引用 status 列的 CHECK 约束，避免重建表时残留失效约束
        batch_op.drop_constraint('examstatus', type_='check')
        batch_op.drop_column('question_stats')
        batch_op.drop_column('status')
        batch_op.drop_column('name')
    op.drop_table('question_set_item')
    op.drop_table('question_set')
    # ### end Alembic commands ###
