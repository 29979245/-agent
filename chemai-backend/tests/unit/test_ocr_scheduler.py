"""OCRTask 调度单元测试（task 6.x）：5s 轮询状态流转 / 失败重试幂等。"""
import datetime

import httpx
import pytest

from app.config import settings
from app.db.models import OCRTask, UploadSession
from app.db.models.enums import OCRTaskStatus
from app.services.ocr.scheduler import (
    INTERVAL_SECONDS,
    create_scheduler,
    process_task,
    retry_task,
    run_poll_once,
)
from app.services.ocr.token import reset_token_cache

API_KEY = "ak_sched"
SECRET = "sk_sched"

WORDS = ["姓名：张三", "学号：2023001234", "1 A", "2 B"]


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setattr(settings, "baidu_ocr_api_key", API_KEY)
    monkeypatch.setattr(settings, "baidu_ocr_secret_key", SECRET)
    reset_token_cache()
    yield
    reset_token_cache()


def _baidu_client(words):
    def handler(request: httpx.Request):
        if "/oauth/2.0/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "tk", "expires_in": 2592000})
        return httpx.Response(200, json={"words_result": [{"words": w} for w in words]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://aip.baidubce.com")


def _session(db):
    s = UploadSession()
    db.add(s)
    db.flush()
    return s


def _task(db, path="", status=OCRTaskStatus.pending):
    s = _session(db)
    t = OCRTask(session_id=s.id, file_path=path, status=status)
    db.add(t)
    db.flush()
    return t


def _write_image(tmp_path, name="card.png"):
    p = tmp_path / name
    p.write_bytes(b"fake-image-bytes")
    return str(p)


# ---- 6.1 轮询状态流转 ----


@pytest.mark.asyncio
async def test_poll_marks_done_with_progress_and_result(db_session, tmp_path):
    path = _write_image(tmp_path)
    task = _task(db_session, path=path)
    async with _baidu_client(WORDS) as client:
        summary = await run_poll_once(db_session, http=client)
    db_session.refresh(task)
    assert summary == {"processed": 1, "done": 1, "failed": 0}
    assert task.status == OCRTaskStatus.done
    assert task.progress == 100
    assert task.result["student_no"] == "2023001234"
    assert task.result["file_path"] == path


@pytest.mark.asyncio
async def test_poll_hard_failure_records_error(db_session, tmp_path):
    task = _task(db_session, path=str(tmp_path / "missing.png"))
    async with _baidu_client(WORDS) as client:
        summary = await run_poll_once(db_session, http=client)
    db_session.refresh(task)
    assert summary == {"processed": 1, "done": 0, "failed": 1}
    assert task.status == OCRTaskStatus.failed
    assert task.error


@pytest.mark.asyncio
async def test_process_task_skips_non_pending(db_session):
    task = _task(db_session, path="x.png", status=OCRTaskStatus.done)
    await process_task(db_session, task)
    assert task.status == OCRTaskStatus.done
    assert task.progress == 0  # 未被打断重跑


@pytest.mark.asyncio
async def test_poll_processes_pending_only(db_session, tmp_path):
    done_task = _task(db_session, path="x.png", status=OCRTaskStatus.done)
    pending_task = _task(db_session, path=_write_image(tmp_path))
    async with _baidu_client(WORDS) as client:
        summary = await run_poll_once(db_session, http=client)
    assert summary["processed"] == 1
    db_session.refresh(pending_task)
    db_session.refresh(done_task)
    assert pending_task.status == OCRTaskStatus.done
    assert done_task.status == OCRTaskStatus.done


# ---- 6.2 失败重试（幂等） ----


def test_retry_failed_task_resets_to_pending(db_session):
    task = _task(db_session, path="card.png")
    task.status = OCRTaskStatus.failed
    task.error = "OCR engine timeout"
    task.result = {"student_no": "2023001234"}
    db_session.commit()
    retry_task(db_session, task)
    assert task.status == OCRTaskStatus.pending
    assert task.error == ""
    assert task.result == {}
    assert task.file_path == "card.png"  # 文件路径保留，供重跑识别


def test_retry_is_idempotent(db_session):
    task = _task(db_session, path="card.png", status=OCRTaskStatus.pending)
    db_session.commit()
    first = retry_task(db_session, task)
    second = retry_task(db_session, task)
    assert first is task
    assert second is task
    assert task.status == OCRTaskStatus.pending
    assert task.error == ""


def test_retry_on_done_is_noop(db_session):
    task = _task(db_session, path="card.png", status=OCRTaskStatus.done)
    db_session.commit()
    retry_task(db_session, task)
    assert task.status == OCRTaskStatus.done


# ---- 6.1 调度器注册 ----


def test_create_scheduler_registers_ocr_poll_job():
    scheduler = create_scheduler()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "ocr_poll"
    assert job.name == "OCR 任务轮询"
    assert job.trigger.interval == datetime.timedelta(seconds=INTERVAL_SECONDS)
    assert "UTC" in str(job.trigger.timezone)
