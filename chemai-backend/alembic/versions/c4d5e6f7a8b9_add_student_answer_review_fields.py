"""teacher-grading: 主观题人工复核落库——StudentAnswer 补 review_needed/review_reason/review_comment/reviewed_at

Revision ID: c4d5e6f7a8b9
Revises: b1c2d3e4f5a6
Create Date: 2026-09-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("student_answer") as batch_op:
        batch_op.add_column(sa.Column("review_needed", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("review_reason", sa.String(length=500), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("review_comment", sa.String(length=500), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("student_answer") as batch_op:
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("review_comment")
        batch_op.drop_column("review_reason")
        batch_op.drop_column("review_needed")
