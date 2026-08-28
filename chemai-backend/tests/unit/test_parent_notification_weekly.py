"""change parent-backend 2.1：通知类型 5 类 + created_at + Student 周报列。

- 模型字段：NotificationType 取值、ParentNotification.created_at 默认/索引、
  Student.weekly_report / weekly_report_week
- 迁移存量值映射：message→daily_report、warning→score_alert、report→weekly_report
"""
from datetime import datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Class, Grade, Parent, ParentNotification, School, Student
from app.db.models.enums import NotificationType

# 旧枚举值 → 新枚举值（迁移映射表）
MIGRATION_MAP = {
    "message": NotificationType.daily_report,
    "warning": NotificationType.score_alert,
    "report": NotificationType.weekly_report,
}


def _org(db_session):
    school = School(name="S")
    db_session.add(school)
    db_session.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db_session.add(grade)
    db_session.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db_session.add(cls)
    db_session.flush()
    stu = Student(class_id=cls.id, name="张三", bind_code="123456")
    db_session.add(stu)
    db_session.flush()
    return cls, stu


def _parent(db_session, phone="13900000001"):
    p = Parent(name="张父", phone=phone)
    db_session.add(p)
    db_session.flush()
    return p


# ---- 模型字段 ----

def test_notification_type_has_5_values(db_session):
    values = {e.value for e in NotificationType}
    assert values == {
        "weekly_report",
        "score_alert",
        "learning_plan",
        "reminder",
        "daily_report",
    }


def test_parent_notification_created_at_default(db_session):
    cls, _ = _org(db_session)
    p = _parent(db_session)
    n = ParentNotification(
        parent_id=p.id, notification_type=NotificationType.daily_report, title="今日练习"
    )
    db_session.add(n)
    db_session.commit()
    db_session.refresh(n)
    assert isinstance(n.created_at, datetime)


def test_parent_notification_created_at_indexed(db_session):
    import sqlalchemy as sa
    from app.db.models.parent import ParentNotification as PN

    # 模型声明 index=True → metadata 生成 ix_parent_notification_created_at
    table = PN.__table__
    idx = next((i for i in table.indexes if i.name == "ix_parent_notification_created_at"), None)
    assert idx is not None, "created_at 应声明 index=True"


def test_student_weekly_columns(db_session):
    cls, stu = _org(db_session)
    stu.weekly_report = {"summary": "本周练习完成", "no_data": False}
    stu.weekly_report_week = datetime(2026, 8, 24).date()
    db_session.commit()
    db_session.refresh(stu)
    assert stu.weekly_report["summary"] == "本周练习完成"
    assert stu.weekly_report_week == datetime(2026, 8, 24).date()


# ---- 迁移存量值映射 ----

def _upgrade_to(db_path: str, revision: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", "alembic")
    command.upgrade(cfg, revision)


def test_migration_maps_legacy_notification_values(tmp_path):
    db_path = tmp_path / "weekly_migrate.db"
    # 1) 升到迁移前一版（旧 CHECK 仍允许 message/warning/report）
    _upgrade_to(str(db_path), "f6a7b8c9d0e1")
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    db = Session()
    school = School(name="S")
    db.add(school)
    db.flush()
    grade = Grade(school_id=school.id, name="高一", academic_year="2026")
    db.add(grade)
    db.flush()
    cls = Class(grade_id=grade.id, name="1班")
    db.add(cls)
    db.flush()
    # 旧版 schema 无 weekly_report 列：学生用原始 SQL 插入（显式列避开 ORM 新字段）
    db.execute(
        text(
            "INSERT INTO student (id, class_id, name, student_no, barrier_profile, "
            "barrier_last_updated, barrier_frozen, learning_plan, bind_code, created_at) "
            "VALUES (1, :cid, '张三', '', '{}', NULL, 0, '{}', '123456', NULL)"
        ),
        {"cid": cls.id},
    )
    p = Parent(name="张父", phone="13900000001")
    db.add(p)
    db.flush()
    db.commit()

    # 直接以旧枚举值字符串写库（旧 CHECK 约束允许）
    for i, (old, _new) in enumerate(MIGRATION_MAP.items()):
        db.execute(
            text(
                "INSERT INTO parent_notification (parent_id, notification_type, title, content, is_read) "
                "VALUES (:pid, :nt, :title, '', 0)"
            ),
            {"pid": p.id, "nt": old, "title": f"旧通知{i}"},
        )
    db.commit()
    db.close()

    # 2) 升到 head → 存量值映射到新枚举
    _upgrade_to(str(db_path), "head")
    db = Session()
    rows = db.execute(
        text("SELECT notification_type, title FROM parent_notification ORDER BY title")
    ).fetchall()
    db.close()
    by_title = {title: nt for nt, title in rows}
    for old, new in MIGRATION_MAP.items():
        assert by_title[f"旧通知{list(MIGRATION_MAP).index(old)}"] == new.value

    # 新 CHECK 约束存在且含 5 类
    insp = inspect(engine)
    checks = insp.get_check_constraints("parent_notification")
    assert any("weekly_report" in c["sqltext"] for c in checks), "CHECK 应含新 5 类"
