"""ocr: UploadSession.exam_id 批改考试绑定（run 时写入、save 校验，防 run/save exam_id 漂移）

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-27 00:00:00.000000

背景：/grading/run 与 /grading/save 各自接收 exam_id，若客户端两次传入不同考试，
批改依据与落库考试会错位（跨考试静默污染）。在批次上持久化 run 时绑定，save 校验一致性。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('upload_session') as batch_op:
        batch_op.add_column(sa.Column('exam_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_upload_session_exam_id', 'exam_record', ['exam_id'], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    with op.batch_alter_table('upload_session') as batch_op:
        batch_op.drop_constraint('fk_upload_session_exam_id', type_='foreignkey')
        batch_op.drop_column('exam_id')
