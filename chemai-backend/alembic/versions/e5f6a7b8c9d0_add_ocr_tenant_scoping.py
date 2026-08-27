"""ocr: 数据隔离与查询索引——UploadSession.school_id + 批改/查询列索引

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-27 00:00:00.000000

背景：OCR 批改跨校 IDOR 修复（/review 8.3）——批次归属学校、教师按校隔离；
并为调度轮询/查询热列补索引。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('upload_session') as batch_op:
        batch_op.add_column(sa.Column('school_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_upload_session_school_id', 'school', ['school_id'], ['id'], ondelete='RESTRICT')
        batch_op.create_index('ix_upload_session_school_id', ['school_id'])
        batch_op.create_index('ix_upload_session_created_at', ['created_at'])

    with op.batch_alter_table('ocr_task') as batch_op:
        batch_op.create_index('ix_ocr_task_status', ['status'])

    with op.batch_alter_table('student_submission') as batch_op:
        batch_op.create_index('ix_student_submission_session_id', ['session_id'])

    with op.batch_alter_table('student') as batch_op:
        batch_op.create_index('ix_student_student_no', ['student_no'])


def downgrade() -> None:
    with op.batch_alter_table('student') as batch_op:
        batch_op.drop_index('ix_student_student_no')

    with op.batch_alter_table('student_submission') as batch_op:
        batch_op.drop_index('ix_student_submission_session_id')

    with op.batch_alter_table('ocr_task') as batch_op:
        batch_op.drop_index('ix_ocr_task_status')

    with op.batch_alter_table('upload_session') as batch_op:
        batch_op.drop_index('ix_upload_session_created_at')
        batch_op.drop_index('ix_upload_session_school_id')
        batch_op.drop_constraint('fk_upload_session_school_id', type_='foreignkey')
        batch_op.drop_column('school_id')
