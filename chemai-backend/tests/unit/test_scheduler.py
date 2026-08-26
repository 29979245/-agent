"""调度接线单元测试（task 9.1）：create_scheduler 注册每日 08:00 UTC 任务。

run_daily_job → run_daily_batch 的行为已由 test_daily_practice 覆盖。
"""
from app.services.exercise.scheduler import create_scheduler


def test_create_scheduler_registers_daily_job():
    scheduler = create_scheduler()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "daily_practice"
    assert job.name == "每日练习批量"
    trigger = job.trigger
    fields = {f.name: f for f in trigger.fields}
    assert [str(e) for e in fields["hour"].expressions] == ["8"]
    assert [str(e) for e in fields["minute"].expressions] == ["0"]
    assert trigger.timezone is not None and "UTC" in str(trigger.timezone)


