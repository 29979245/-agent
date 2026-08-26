"""exercise: 练习管线模型扩展

- exam_record：新增 student_id（per-student 练习/训练/每日练习归属，ADR-0002），class_id 改可空
- student_answer：新增 answered_at 作答时间戳
- review_task：新增 next_review_at/first_studied_at/completed_at/consecutive_correct/consecutive_error
- review_task.status：四态（pending/in_progress/completed/archived）→ 三态（pending/overdue/done）

枚举迁移语义（design.md D8 迁移兼容）：
- completed/archived → done（已掌握终态）
- in_progress → pending
- pending 保持 pending

SQLite 无法就地修改 CHECK 约束，须经 batch 重建表。status 改动先放宽为
VARCHAR → 原地映射数据 → 收紧为三态枚举，避免数据 COPY 撞旧 CHECK。
Revision ID: b5f6a2c3d4e7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-26 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5f6a2c3d4e7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STATUS = sa.Enum('pending', 'in_progress', 'completed', 'archived',
                      name='reviewtaskstatus', native_enum=False, create_constraint=True)
_NEW_STATUS = sa.Enum('pending', 'overdue', 'done',
                      name='reviewtaskstatus', native_enum=False, create_constraint=True)


def upgrade() -> None:
    # ---- exam_record：student_id 新增 + class_id 放宽可空（ADR-0002） ----
    with op.batch_alter_table('exam_record') as batch_op:
        batch_op.add_column(sa.Column('student_id', sa.Integer(), nullable=True))
        batch_op.alter_column('class_id', existing_type=sa.Integer(), nullable=True)

    # ---- student_answer：answered_at 作答时间戳 ----
    with op.batch_alter_table('student_answer') as batch_op:
        batch_op.add_column(sa.Column('answered_at', sa.DateTime(), nullable=True))

    # ---- review_task：新字段 + 枚举三态迁移 ----
    # ① 加列 + status 放宽为 VARCHAR（重建表，旧值原样拷贝）
    with op.batch_alter_table('review_task') as batch_op:
        batch_op.add_column(sa.Column('next_review_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('first_studied_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('completed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('consecutive_correct', sa.Integer(),
                                      nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('consecutive_error', sa.Integer(),
                                      nullable=False, server_default='0'))
        batch_op.alter_column('status', existing_type=_OLD_STATUS, type_=sa.String(20),
                              existing_nullable=False)
    # ② 原地映射数据：四态 → 三态（此时无 CHECK 约束）
    op.execute("UPDATE review_task SET status = 'done' WHERE status IN ('completed', 'archived')")
    op.execute("UPDATE review_task SET status = 'pending' WHERE status = 'in_progress'")
    # ③ 收紧为三态枚举 CHECK（数据均为合法取值）
    with op.batch_alter_table('review_task') as batch_op:
        batch_op.alter_column('status', existing_type=sa.String(20), type_=_NEW_STATUS,
                              existing_nullable=False)


def downgrade() -> None:
    # 反向映射：done → completed（终态语义），overdue/pending → pending，随后恢复四态 CHECK
    with op.batch_alter_table('review_task') as batch_op:
        batch_op.alter_column('status', existing_type=_NEW_STATUS, type_=sa.String(20),
                              existing_nullable=False)
    op.execute("UPDATE review_task SET status = 'completed' WHERE status = 'done'")
    op.execute("UPDATE review_task SET status = 'pending' WHERE status IN ('pending', 'overdue')")
    with op.batch_alter_table('review_task') as batch_op:
        batch_op.alter_column('status', existing_type=sa.String(20), type_=_OLD_STATUS,
                              existing_nullable=False)

    with op.batch_alter_table('review_task') as batch_op:
        batch_op.drop_column('consecutive_error')
        batch_op.drop_column('consecutive_correct')
        batch_op.drop_column('completed_at')
        batch_op.drop_column('first_studied_at')
        batch_op.drop_column('next_review_at')

    with op.batch_alter_table('student_answer') as batch_op:
        batch_op.drop_column('answered_at')

    with op.batch_alter_table('exam_record') as batch_op:
        batch_op.drop_column('student_id')
        batch_op.alter_column('class_id', existing_type=sa.Integer(), nullable=False)
