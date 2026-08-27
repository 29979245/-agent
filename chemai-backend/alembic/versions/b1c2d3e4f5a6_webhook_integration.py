"""parent-backend 3.1: Webhook 注册 + 集成配置表

- 新建 webhook_registration（event_type 枚举 7 类 + url + secret + enabled + created_at）
- 新建 integration_config（key 唯一 + JSON value）

Revision ID: b1c2d3e4f5a6
Revises: a3b4c5d6e7f8
Create Date: 2026-08-28 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_registration",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "practice.assigned",
                "practice.completed",
                "exam.created",
                "exam.graded",
                "warning.triggered",
                "student.login",
                "review.due",
                name="webhookeventtype",
            ),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("secret", sa.String(length=256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_delivery_status", sa.String(length=32), nullable=False),
        sa.Column("last_delivered_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_webhook_registration_event_type",
        "webhook_registration",
        ["event_type"],
        unique=False,
    )
    op.create_table(
        "integration_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_integration_config_key"),
    )


def downgrade() -> None:
    op.drop_table("integration_config")
    op.drop_index("ix_webhook_registration_event_type", table_name="webhook_registration")
    op.drop_table("webhook_registration")
