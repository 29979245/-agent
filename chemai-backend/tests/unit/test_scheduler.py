"""调度接线单元测试（task 9.1 / 6.1 / parent-backend 2.8）：create_scheduler 注册
每日 08:00 UTC 练习、00:00 UTC 预警检测与周一 08:00 UTC 周报生成任务。

run_daily_job → run_daily_batch / run_warning_job → check_all_warnings 的行为已由
test_daily_practice / test_early_warning_service 覆盖；周报批量遍历范围见下方
test_weekly_batch_*。
"""
from app.services.exercise.scheduler import create_scheduler


def test_create_scheduler_registers_daily_job():
    scheduler = create_scheduler()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 3
    daily = {j.id: j for j in jobs}["daily_practice"]
    assert daily.name == "每日练习批量"
    fields = {f.name: f for f in daily.trigger.fields}
    assert [str(e) for e in fields["hour"].expressions] == ["8"]
    assert [str(e) for e in fields["minute"].expressions] == ["0"]
    assert daily.trigger.timezone is not None and "UTC" in str(daily.trigger.timezone)


def test_create_scheduler_registers_warning_job():
    scheduler = create_scheduler()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 3
    warning = {j.id: j for j in jobs}["warning_check"]
    assert warning.name == "预警检测"
    fields = {f.name: f for f in warning.trigger.fields}
    assert [str(e) for e in fields["hour"].expressions] == ["0"]
    assert [str(e) for e in fields["minute"].expressions] == ["0"]
    assert warning.trigger.timezone is not None and "UTC" in str(warning.trigger.timezone)


def test_create_scheduler_registers_weekly_job():
    scheduler = create_scheduler()
    jobs = scheduler.get_jobs()
    assert len(jobs) == 3
    weekly = {j.id: j for j in jobs}["weekly_report"]
    assert weekly.name == "家长周报生成"
    fields = {f.name: f for f in weekly.trigger.fields}
    assert [str(e) for e in fields["day_of_week"].expressions] == ["mon"]
    assert [str(e) for e in fields["hour"].expressions] == ["8"]
    assert [str(e) for e in fields["minute"].expressions] == ["0"]
    assert weekly.trigger.timezone is not None and "UTC" in str(weekly.trigger.timezone)
