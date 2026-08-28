"""parent-backend: 通知类型 5 类 + 通知时间戳 + 学生周报列

- parent_notification.notification_type：枚举 3 类 → 5 类，存量值映射
  message→daily_report、warning→score_alert、report→weekly_report
- parent_notification：新增 created_at（非空默认 now，index）
- student：新增 weekly_report(JSON) + weekly_report_week(date)

Revision ID: a3b4c5d6e7f8
Revises: f6a7b8c9d0e1
Create Date: 2026-08-28 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- parent_notification.notification_type：换 CHECK 约束 + 存量值映射 ----
    with op.batch_alter_table("parent_notification") as batch_op:
        batch_op.drop_constraint("notificationtype", type_="check")
    op.execute(
        "UPDATE parent_notification SET notification_type='daily_report' "
        "WHERE notification_type='message'"
    )
    op.execute(
        "UPDATE parent_notification SET notification_type='score_alert' "
        "WHERE notification_type='warning'"
    )
    op.execute(
        "UPDATE parent_notification SET notification_type='weekly_report' "
        "WHERE notification_type='report'"
    )
    with op.batch_alter_table("parent_notification") as batch_op:
        batch_op.create_check_constraint(
            "notificationtype",
            "notification_type IN ('weekly_report', 'score_alert', "
            "'learning_plan', 'reminder', 'daily_report')",
        )

    # ---- parent_notification.created_at：非空默认 now + index ----
    with op.batch_alter_table("parent_notification") as batch_op:
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch_op.create_index(
            "ix_parent_notification_created_at", ["created_at"], unique=False
        )

    # ---- student：weekly_report + weekly_report_week ----
    with op.batch_alter_table("student") as batch_op:
        batch_op.add_column(sa.Column("weekly_report", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("weekly_report_week", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("student") as batch_op:
        batch_op.drop_column("weekly_report_week")
        batch_op.drop_column("weekly_report")
    with op.batch_alter_table("parent_notification") as batch_op:
        batch_op.drop_index("ix_parent_notification_created_at")
        batch_op.drop_column("created_at")
    # 回滚枚举：还原 3 类约束（存量值不回切旧值——回滚仅还原约束结构）
    with op.batch_alter_table("parent_notification") as batch_op:
        batch_op.drop_constraint("notificationtype", type_="check")
    with op.batch_alter_table("parent_notification") as batch_op:
        batch_op.create_check_constraint(
            "notificationtype",
            "notification_type IN ('report', 'warning', 'message')",
        )
