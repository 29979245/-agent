"""Alembic 迁移测试（10.1-10.2）：临时文件库升级 + 19 表 + 索引 + 幂等 + 往返。"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db.session import SessionLocal
from app.db.models import Account, School, Teacher
from app.db.models.enums import AccountRole

EXPECTED_TABLES = {
    "account",
    "school",
    "grade",
    "class",
    "teacher",
    "student",
    "exam_record",
    "question",
    "student_answer",
    "review_task",
    "review_history",
    "parent",
    "student_parent_binding",
    "parent_notification",
    "upload_session",
    "ocr_task",
    "student_submission",
    "question_set",
    "question_set_item",
}

EXPECTED_INDEXES = {
    "ix_student_class_id",
    "ix_student_answer_student_id",
    "ix_student_answer_question_id",
    "ix_student_answer_exam_id",
    "ix_review_task_student_id",
    "ix_ocr_task_session_id",
    "ix_student_submission_exam_id",
    "ix_student_parent_binding_parent_id",
    "ix_student_parent_binding_student_id",
    "ix_question_set_item_set_id",
    "ix_question_set_item_question_id",
    "ix_question_record_id",
}


def _run_upgrade(tmp_path: Path) -> str:
    db_path = tmp_path / "migration_test.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", "alembic")
    command.upgrade(cfg, "head")
    return str(db_path)


def test_migration_creates_19_tables_and_indexes(tmp_path):
    db_path = _run_upgrade(tmp_path)
    insp = inspect(create_engine(f"sqlite:///{db_path}"))
    assert EXPECTED_TABLES <= set(insp.get_table_names())
    all_indexes = set()
    for table in (
        "student",
        "student_answer",
        "review_task",
        "ocr_task",
        "student_submission",
        "student_parent_binding",
        "question_set_item",
        "question",
    ):
        all_indexes |= {ix["name"] for ix in insp.get_indexes(table)}
    assert EXPECTED_INDEXES <= all_indexes, f"缺失索引: {EXPECTED_INDEXES - all_indexes}"


def test_migration_idempotent(tmp_path):
    db_path = _run_upgrade(tmp_path)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", "alembic")
    command.upgrade(cfg, "head")  # 第二次升级应无操作
    insp = inspect(create_engine(f"sqlite:///{db_path}"))
    assert EXPECTED_TABLES <= set(insp.get_table_names())


def test_migrated_db_insert_delete_roundtrip(tmp_path):
    db_path = _run_upgrade(tmp_path)
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    db = Session()
    school = School(name="冒烟学校")
    db.add(school)
    db.flush()
    teacher = Teacher(school_id=school.id, name="王老师", phone="13900000000")
    db.add(teacher)
    db.flush()
    db.add(Account(username="mig_t", password_hash="x", role=AccountRole.teacher, role_id=teacher.id))
    db.commit()
    assert db.query(School).count() == 1
    db.delete(teacher)
    db.flush()
    db.delete(school)
    db.commit()
    assert db.query(School).count() == 0
    db.close()
