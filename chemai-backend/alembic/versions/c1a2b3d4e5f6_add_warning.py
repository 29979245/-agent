"""early-warning: 预警引擎模型扩展

- student：新增 created_at 注册时间（no_login 从未作答分支依赖，design.md D2），
  既有行回填 COALESCE(max(answered_at), now)——取最近作答时间，无作答取迁移时刻
- warning_log：新增预警记录表（design.md D1）

Revision ID: c1a2b3d4e5f6
Revises: b5f6a2c3d4e7
Create Date: 2026-08-26 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, None] = 'b5f6a2c3d4e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- student：created_at 注册时间 + 既有行回填（D2） ----
    with op.batch_alter_table('student') as batch_op:
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))
    op.execute(
        """
        UPDATE student
        SET created_at = COALESCE(
            (SELECT MAX(sa.answered_at) FROM student_answer sa
             WHERE sa.student_id = student.id),
            CURRENT_TIMESTAMP
        )
        WHERE created_at IS NULL
        """
    )

    # ---- warning_log：预警记录表（D1） ----
    op.create_table(
        'warning_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column(
            'warning_type',
            sa.Enum('no_login', 'score_drop', 'high_error_rate',
                    name='warningtype', native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column(
            'level',
            sa.Enum('info', 'warning', 'critical',
                    name='warninglevel', native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content', sa.String(length=2000), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('pending', 'processed', 'ignored',
                    name='warningstatus', native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column('processed_by', sa.Integer(), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('processed_note', sa.String(length=500), nullable=True),
        sa.Column('notified_teacher', sa.Boolean(), nullable=False),
        sa.Column('notified_parent', sa.Boolean(), nullable=False),
        sa.Column('notified_student', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['student_id'], ['student.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_warning_log_student_id', 'warning_log', ['student_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_warning_log_student_id', table_name='warning_log')
    op.drop_table('warning_log')
    with op.batch_alter_table('student') as batch_op:
        batch_op.drop_column('created_at')
