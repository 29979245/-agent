"""ocr: 批改判卷落库字段——Student 补 student_no（模式1 学号匹配）+ StudentSubmission.exam_id 可空（模式2/3 无考试）

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('student') as batch_op:
        batch_op.add_column(sa.Column('student_no', sa.String(length=20), nullable=False, server_default=''))

    with op.batch_alter_table('student_submission') as batch_op:
        batch_op.alter_column('exam_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('student_submission') as batch_op:
        batch_op.alter_column('exam_id', existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table('student') as batch_op:
        batch_op.drop_column('student_no')
