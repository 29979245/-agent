"""种子脚本：创建演示学校 + 已入驻教师 + 学生账号（供前端工作台登录验证）。

用法（在 chemai-backend/ 下）：
    python scripts/seed_teacher.py

生成账号：
    teacher_demo / demo123  → 教师（status=approved，可登录出题工作台）
    student_demo / demo123  → 学生（可登录，但被前端角色门控拒于工作台外）

脚本幂等：账号已存在则跳过创建，重复执行安全。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password
from app.db.models import Account, Class as ClassModel, Grade, School, Student, Teacher
from app.db.models.enums import AccountRole, TeacherStatus, TeacherSubRole
from app.db.session import SessionLocal


def _get_or_create(db, model, **kwargs):
    """按唯一键（kwargs 除 defaults 外）查，命中返回 (obj, False)，否则创建 (obj, True)。"""
    defaults = kwargs.pop("defaults", {})
    obj = db.query(model).filter_by(**kwargs).first()
    if obj is not None:
        return obj, False
    obj = model(**kwargs, **defaults)
    db.add(obj)
    db.flush()
    return obj, True


def main() -> None:
    db = SessionLocal()
    try:
        school, school_created = _get_or_create(
            db, School, name="智辅化学演示学校", defaults={
                "region": "本地演示", "phone": "010-00000000",
                "current_semester": "2026-2027 学年",
            }
        )

        teacher, teacher_created = _get_or_create(
            db, Teacher,
            phone="13800000001",
            defaults={
                "school_id": school.id, "name": "演示教师",
                "status": TeacherStatus.approved,
                "sub_role": TeacherSubRole.teacher,
            },
        )
        account_t, account_t_created = _get_or_create(
            db, Account,
            username="teacher_demo",
            defaults={
                "password_hash": hash_password("demo123"),
                "role": AccountRole.teacher,
                "role_id": teacher.id,
            },
        )

        grade, grade_created = _get_or_create(
            db, Grade, school_id=school.id, name="高一", defaults={"academic_year": "2026-2027"}
        )
        cls, cls_created = _get_or_create(
            db, ClassModel,
            grade_id=grade.id, name="高一（3）班",
            defaults={"subject": "化学", "head_teacher": teacher.name},
        )
        student, student_created = _get_or_create(
            db, Student,
            class_id=cls.id, name="演示学生",
            defaults={"bind_code": "000000"},
        )
        account_s, account_s_created = _get_or_create(
            db, Account,
            username="student_demo",
            defaults={
                "password_hash": hash_password("demo123"),
                "role": AccountRole.student,
                "role_id": student.id,
            },
        )

        db.commit()
        print("种子数据就绪：")
        print("  教师   teacher_demo / demo123  (status=approved, school=%s)" % school.name)
        print("  学生   student_demo / demo123  (可登录但无工作台权限)")
        print("  学校   %s / 年级 高一 / 班级 高一（3）班" % school.name)
        created = [n for n, c in [("school", school_created), ("teacher", teacher_created),
                                  ("account_teacher", account_t_created), ("grade", grade_created),
                                  ("class", cls_created), ("student", student_created),
                                  ("account_student", account_s_created)] if c]
        print("  新建:", ", ".join(created) if created else "无（全部已存在，幂等）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
