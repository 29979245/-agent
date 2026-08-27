"""OCRTask 调度（设计 D6）：APScheduler interval 5s 轮询 + 失败重试。

轮询 pending → processing → done/failed（成功置 progress 100、失败记 error）；
retry 端点 failed → pending + 清空错误信息与识别结果（幂等：非 failed 无副作用）。
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.db.models.enums import OCRTaskStatus
from app.db.models.ocr import OCRTask
from app.db.session import SessionLocal
from app.services.ocr.engines import OCRDocument, extract_document

INTERVAL_SECONDS = 5
POLL_LIMIT = 10
ERROR_MAX_LEN = 500


def retry_task(db, task: OCRTask) -> OCRTask:
    """失败任务重试：failed → pending，清空错误与识别结果（file_path 保留）。

    幂等：非 failed 任务原样返回，重复重试不产生副作用。
    """
    if task.status != OCRTaskStatus.failed:
        return task
    task.status = OCRTaskStatus.pending
    task.error = ""
    task.result = {}
    return task


async def process_task(db, task: OCRTask, *, http=None, llm_client=None) -> None:
    """处理单个任务：pending → processing → done/failed。http/llm_client 可注入 mock。"""
    if task.status != OCRTaskStatus.pending:
        return
    task.status = OCRTaskStatus.processing
    db.commit()  # 先提交 processing 并释放写锁（避免网络 I/O 期间占用 SQLite 锁/状态不可见）
    try:
        if not task.file_path:
            raise ValueError("任务缺少 file_path")
        result = await extract_document(
            OCRDocument(path=task.file_path), http=http, llm_client=llm_client
        )
        if result.provider == "none":  # 三引擎全败、无任何输出 → 标记失败供重试（部分结果仍算 done）
            raise ValueError("所有 OCR 引擎均未能识别")
        data = asdict(result)
        data["file_path"] = task.file_path
        task.result = data
        task.progress = 100
        task.status = OCRTaskStatus.done
    except Exception as e:  # 硬失败（文件缺失/引擎异常）记录错误，软降级部分结果仍算 done
        task.status = OCRTaskStatus.failed
        task.error = str(e)[:ERROR_MAX_LEN]
    db.commit()


async def run_poll_once(db, *, http=None, llm_client=None, limit: int = POLL_LIMIT) -> dict:
    """轮询一轮：拾取 pending 任务逐个处理，返回汇总（供测试直接 await 断言）。"""
    tasks = (
        db.query(OCRTask)
        .filter(OCRTask.status == OCRTaskStatus.pending)
        .limit(limit)
        .all()
    )
    summary = {"processed": len(tasks), "done": 0, "failed": 0}
    for task in tasks:
        await process_task(db, task, http=http, llm_client=llm_client)
        if task.status == OCRTaskStatus.done:
            summary["done"] += 1
        elif task.status == OCRTaskStatus.failed:
            summary["failed"] += 1
    return summary


def poll_job() -> dict:
    """调度入口（APScheduler job）：独立会话执行一轮轮询。"""
    db = SessionLocal()
    try:
        return asyncio.run(run_poll_once(db))
    finally:
        db.close()


def create_scheduler() -> BackgroundScheduler:
    """创建 OCR 轮询调度器（5s interval），main.py 在 enable_scheduler 时启动。"""
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        poll_job,
        IntervalTrigger(seconds=INTERVAL_SECONDS, timezone="UTC"),
        id="ocr_poll",
        name="OCR 任务轮询",
        replace_existing=True,
        misfire_grace_time=30,
    )
    return scheduler
