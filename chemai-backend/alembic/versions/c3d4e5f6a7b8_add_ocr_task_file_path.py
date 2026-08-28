"""ocr: OCRTask 补充 file_path 列（批量上传写入待识别文件路径）

Revision ID: c3d4e5f6a7b8
Revises: c1a2b3d4e5f6
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'c1a2b3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('ocr_task') as batch_op:
        batch_op.add_column(sa.Column('file_path', sa.String(length=500), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('ocr_task') as batch_op:
        batch_op.drop_column('file_path')
