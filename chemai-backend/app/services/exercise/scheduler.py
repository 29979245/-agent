"""APScheduler 接线（design.md D8）：每日 08:00 UTC 练习批量 + 00:00 UTC 预警检测
+ 周一 08:00 UTC 周报生成（parent-backend 2.8）。

各任务用主库独立会话执行，由 main.py lifespan 在 settings.enable_scheduler
开启时启动（测试默认关闭，避免后台线程）。
"""
from __future__ import annotations

import datetime
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.models import StudentParentBinding
from app.db.models.enums import ParentBindingStatus
from app.db.session import SessionLocal
from app.services.analytics.early_warning import EarlyWarningService
from app.services.analytics.weekly_report_service import (
    WeeklyReportError,
    get_or_generate_weekly_report,
)
from app.services.exercise.daily import DailyPracticeScheduler

logger = logging.getLogger(__name__)

DAILY_CRON = CronTrigger(hour=8, minute=0, timezone="UTC")
WARNING_CRON = CronTrigger(hour=0, minute=0, timezone="UTC")
WEEKLY_CRON = CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="UTC")


def run_daily_job() -> dict:
    """独立会话执行每日批量，返回汇总（供测试直接调用断言）。"""
    db = SessionLocal()
    try:
        summary = DailyPracticeScheduler(db).run_daily_batch()
        logger.info("[DailyPractice] 调度执行完成：%s", summary)
        return summary
    finally:
        db.close()


def run_warning_job() -> dict:
    """独立会话执行预警全量检测，返回汇总（供测试直接调用断言）。"""
    db = SessionLocal()
    try:
        summary = EarlyWarningService(db).check_all_warnings()
        logger.info("[Warning] 预警检测完成：%s", summary)
        return summary
    finally:
        db.close()


def _bound_student_ids(db) -> set[int]:
    """有至少一条 active 绑定的学生 ID 集合（周报仅面向已绑定家长的活跃学生）。"""
    rows = (
        db.query(StudentParentBinding.student_id)
        .filter(StudentParentBinding.status == ParentBindingStatus.active)
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def run_weekly_batch(
    db,
    today: datetime.date | None = None,
    client=None,
) -> dict:
    """为有 active 绑定家长的学生批量生成当周周报；无数据/失败计入汇总，不中断。"""
    ids = _bound_student_ids(db)
    generated = 0
    no_data = 0
    failed = 0
    for sid in sorted(ids):
        try:
            report = get_or_generate_weekly_report(db, sid, today=today, client=client)
            if report.get("no_data"):
                no_data += 1
            else:
                generated += 1
        except WeeklyReportError:
            failed += 1
    return {"total": len(ids), "generated": generated, "no_data": no_data, "failed": failed}


def run_weekly_job(client=None) -> dict:
    """独立会话执行周一周报批量，返回汇总（供测试直接调用断言）。"""
    db = SessionLocal()
    try:
        summary = run_weekly_batch(db, client=client)
        logger.info("[Weekly] 周报生成完成：%s", summary)
        return summary
    finally:
        db.close()


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_daily_job,
        DAILY_CRON,
        id="daily_practice",
        name="每日练习批量",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_warning_job,
        WARNING_CRON,
        id="warning_check",
        name="预警检测",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_weekly_job,
        WEEKLY_CRON,
        id="weekly_report",
        name="家长周报生成",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    return scheduler
