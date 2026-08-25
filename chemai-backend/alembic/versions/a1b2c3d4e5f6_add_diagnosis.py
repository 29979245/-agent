"""diagnosis: barrier_config + barrier_override_log + student 三列 + student_answer 平铺诊断列

Revision ID: a1b2c3d4e5f6
Revises: 9c4e2f1a8b3d
Create Date: 2026-08-25 14:20:00.000000

评审 1.2a 说明：
- 迁移不触碰既有 barrier_profile 数据；对 NULL/缺键/畸形 JSON 的防御性归一化在
  聚合读路径（app/services/diagnosis/aggregation.py）实现，迁移本身幂等。
- downgrade 会 DROP student_answer 平铺诊断列，丢失 fused_conf/rule_conf/llm_conf/
  diagnosis_flag/diagnosis_version/diagnosis_source/diagnosis_detail 明细数据
  （非破坏性字段恢复：barrier_type 主判与 barrier_profile 画像仍在）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9c4e2f1a8b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('barrier_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.Column('consecutive_error_threshold', sa.Integer(), nullable=False),
        sa.Column('consecutive_correct_threshold', sa.Integer(), nullable=False),
        sa.Column('low_score_threshold', sa.Integer(), nullable=False),
        sa.Column('warning_threshold', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['teacher_id'], ['teacher.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('teacher_id')
    )
    op.create_table('barrier_override_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('teacher_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('before', sa.JSON(), nullable=False),
        sa.Column('after', sa.JSON(), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['teacher_id'], ['teacher.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    # SQLite 不支持 ALTER 加非空默认列，用 batch 重建表带出默认值
    with op.batch_alter_table('student') as batch_op:
        batch_op.add_column(sa.Column('barrier_last_updated', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('barrier_frozen', sa.Boolean(), nullable=False, server_default='0'))
    with op.batch_alter_table('student_answer') as batch_op:
        batch_op.add_column(sa.Column('fused_conf', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('rule_conf', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('llm_conf', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('diagnosis_flag', sa.String(length=20), nullable=False, server_default='none'))
        batch_op.add_column(sa.Column('diagnosis_version', sa.String(length=20), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('diagnosis_source', sa.String(length=10), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('diagnosis_detail', sa.JSON(), nullable=False, server_default='{}'))
    op.create_index('ix_student_answer_barrier_flag', 'student_answer', ['diagnosis_flag'], unique=False)


def downgrade() -> None:
    # 数据丢失语义：drop 平铺诊断列，丢失诊断明细（fused/rule/llm 置信度与 detail）
    op.drop_index('ix_student_answer_barrier_flag', table_name='student_answer')
    with op.batch_alter_table('student_answer') as batch_op:
        batch_op.drop_column('diagnosis_detail')
        batch_op.drop_column('diagnosis_source')
        batch_op.drop_column('diagnosis_version')
        batch_op.drop_column('diagnosis_flag')
        batch_op.drop_column('llm_conf')
        batch_op.drop_column('rule_conf')
        batch_op.drop_column('fused_conf')
    with op.batch_alter_table('student') as batch_op:
        batch_op.drop_column('barrier_frozen')
        batch_op.drop_column('barrier_last_updated')
    op.drop_table('barrier_override_log')
    op.drop_table('barrier_config')
