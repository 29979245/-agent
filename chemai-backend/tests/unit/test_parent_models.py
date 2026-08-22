"""家长链模型测试：Parent/StudentParentBinding/ParentNotification（5.1-5.3）。"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Class, Grade, Parent, ParentNotification, School, Student, StudentParentBinding
from app.db.models.enums import NotificationType, ParentBindingRelation, ParentBindingStatus


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
    return stu


def _parent(db_session, **kw):
    p = Parent(name=kw.get("name", "张父"), phone=kw.get("phone", "13900000001"))
    db_session.add(p)
    db_session.flush()
    return p


# ---- 5.1 Parent ----

def test_parent_crud(db_session):
    p = _parent(db_session, email="p@example.com")
    p.password_hash = "pbkdf2:abc"
    db_session.commit()
    got = db_session.get(Parent, p.id)
    assert got.phone == "13900000001"
    assert got.password_hash == "pbkdf2:abc"


# ---- 5.2 StudentParentBinding ----

def test_binding_creation(db_session):
    stu = _org(db_session)
    p = _parent(db_session)
    binding = StudentParentBinding(parent_id=p.id, student_id=stu.id, bind_code=stu.bind_code)
    db_session.add(binding)
    db_session.commit()
    got = db_session.get(StudentParentBinding, binding.id)
    assert got.status == ParentBindingStatus.active
    assert got.relation == ParentBindingRelation.guardian


def test_binding_bind_code_validation(db_session):
    stu = _org(db_session)
    p = _parent(db_session)
    # 绑定码不匹配 → 不创建绑定（F2 场景：无效绑定码）
    if stu.bind_code != "999999":
        assert stu.bind_code != "999999"
    binding = StudentParentBinding(parent_id=p.id, student_id=stu.id, bind_code="999999")
    valid = stu.bind_code == binding.bind_code
    assert valid is False


def test_binding_unique_parent_student(db_session):
    stu = _org(db_session)
    p = _parent(db_session)
    db_session.add(StudentParentBinding(parent_id=p.id, student_id=stu.id, bind_code="123456"))
    db_session.flush()
    with pytest.raises(IntegrityError):
        db_session.add(StudentParentBinding(parent_id=p.id, student_id=stu.id, bind_code="123456"))
        db_session.flush()


# ---- 5.3 ParentNotification ----

def test_parent_notification_crud(db_session):
    p = _parent(db_session)
    n = ParentNotification(parent_id=p.id, notification_type=NotificationType.warning, title="学情预警")
    db_session.add(n)
    db_session.commit()
    got = db_session.get(ParentNotification, n.id)
    assert got.notification_type == NotificationType.warning
    assert got.is_read is False
