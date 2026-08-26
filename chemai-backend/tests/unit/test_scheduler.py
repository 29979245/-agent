"""调度接线单元测试（task 9.1 / 6.1）：create_scheduler 注册每日 08:00 UTC 练习
与 00:00 UTC 预警检测任务。

run_daily_job → run_daily_batch / run_warning_job → check_all_warnings 的行为已由
test_daily_practice / test_early_warning_service 覆盖。
"""
from app.services.exercise.scheduler import create_scheduler


def test_create_scheduler_registers_daily_job():
    scheduler = create_scheduler()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 2
    daily = {j.id: j for j in jobs}["daily_practice"]
    assert daily.name == "每日练习批量"
    fields = {f.name: f for f in daily.trigger.fields}
    assert [str(e) for e in fields["hour"].expressions] == ["8"]
    assert [str(e) for e in fields["minute"].expressions] == ["0"]
    assert daily.trigger.timezone is not None and "UTC" in str(daily.trigger.timezone)


def test_create_scheduler_registers_warning_job():
    scheduler = create_scheduler()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 2
    warning = {j.id: j for j in jobs}["warning_check"]
    assert warning.name == "预警检测"
    fields = {f.name: f for f in warning.trigger.fields}
    assert [str(e) for e in fields["hour"].expressions] == ["0"]
    assert [str(e) for e in fields["minute"].expressions] == ["0"]
    assert warning.trigger.timezone is not None and "UTC" in str(warning.trigger.timezone)


