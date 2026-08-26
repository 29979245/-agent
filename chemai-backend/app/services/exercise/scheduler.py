"""APScheduler 接线（design.md D8）：每天 08:00 UTC 触发每日练习批量任务。

主库独立会话执行每日批量（生成练习 → 家长通知 → 超期标记），由 main.py
lifespan 在 settings.enable_scheduler 开启时启动（测试默认关闭，避免后台线程）。
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import SessionLocal
from app.services.exercise.daily import DailyPracticeScheduler

logger = logging.getLogger(__name__)

DAILY_CRON = CronTrigger(hour=8, minute=0, timezone="UTC")


def run_daily_job() -> dict:
    """独立会话执行每日批量，返回汇总（供测试直接调用断言）。"""
    db = SessionLocal()
    try:
        summary = DailyPracticeScheduler(db).run_daily_batch()
        logger.info("[DailyPractice] 调度执行完成：%s", summary)
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
    return scheduler
