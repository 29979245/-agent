"""APScheduler 接线（design.md D8）：每日 08:00 UTC 练习批量 + 00:00 UTC 预警检测。

两个任务各用主库独立会话执行，由 main.py lifespan 在 settings.enable_scheduler
开启时启动（测试默认关闭，避免后台线程）。
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import SessionLocal
from app.services.analytics.early_warning import EarlyWarningService
from app.services.exercise.daily import DailyPracticeScheduler

logger = logging.getLogger(__name__)

DAILY_CRON = CronTrigger(hour=8, minute=0, timezone="UTC")
WARNING_CRON = CronTrigger(hour=0, minute=0, timezone="UTC")


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
    return scheduler
