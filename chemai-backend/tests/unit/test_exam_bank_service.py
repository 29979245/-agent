"""题库管理服务测试（3.1 + 3.4）：QuestionSet CRUD、批量导入/移除、预设禁删、真题复制。"""
import pytest
from fastapi import HTTPException

from app.core.exceptions import NotFoundError
from app.db.models import Question, QuestionSet, QuestionSetItem
from app.db.models.enums import AuditStatus, Difficulty, QuestionSource
from app.services.question.exam_bank import ExamBankService
from app.services.question.historical import HistoricalBank


def _bank(tmp_path):
    """构造含一道真题的样例真题库。"""
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "高考化学.json").write_text(
        '{"title": "高考化学", "questions": ['
        '{"id": "q1", "content": "配平：H2+O2→H2O", "answer": "2H2+O2=2H2O",'
        ' "knowledge_points": ["氧化还原"], "difficulty": "easy"}]}',
        encoding="utf-8",
    )
    return HistoricalBank().load_from(tmp_path)


# ---- 3.1 QuestionSet CRUD ----

def test_create_and_list_set(db_session):
    svc = ExamBankService(db_session)
    qs = svc.create_set(name="2024 高考真题", teacher_id=3, region="全国卷", year=2024)
    db_session.commit()
    res = svc.list_sets(region="全国卷", year=2024)
    assert res["total"] == 1
    item = res["items"][0]
    assert item["id"] == qs.id
    assert item["name"] == "2024 高考真题"
    assert item["question_count"] == 0
    assert item["is_preset"] is False


def test_get_set_detail_with_questions(db_session):
    svc = ExamBankService(db_session)
    qs = svc.create_set(name="基础训练")
    q1 = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    q2 = Question(content="题B", answer="b", difficulty=Difficulty.medium)
    db_session.add_all([q1, q2])
    db_session.flush()
    svc.import_questions(qs.id, [q1.id, q2.id])
    db_session.commit()
    questions = svc.get_set_questions(qs.id)
    assert len(questions) == 2
    assert {q["content"] for q in questions} == {"题A", "题B"}


def test_get_set_not_found(db_session):
    svc = ExamBankService(db_session)
    with pytest.raises(NotFoundError):
        svc.get_set(9999)


def test_list_sets_filters_by_teacher_id(db_session):
    svc = ExamBankService(db_session)
    svc.create_set(name="甲老师文件夹", teacher_id=1)
    svc.create_set(name="乙老师文件夹", teacher_id=2)
    db_session.commit()
    res = svc.list_sets(teacher_id=1)
    assert res["total"] == 1
    assert res["items"][0]["name"] == "甲老师文件夹"


def test_list_sets_unions_preset_and_own(db_session):
    svc = ExamBankService(db_session)
    preset = QuestionSet(name="系统预设", teacher_id=0, is_preset=True)
    own = QuestionSet(name="我的文件夹", teacher_id=5)
    db_session.add_all([preset, own])
    db_session.commit()
    res = svc.list_sets(teacher_id=5)
    assert res["total"] == 2
    names = [item["name"] for item in res["items"]]
    assert "系统预设" in names and "我的文件夹" in names
    assert res["items"][0]["is_preset"] is True  # 预设置顶


# ---- 批量导入 / 移除 ----

def test_import_and_remove_question(db_session):
    svc = ExamBankService(db_session)
    qs = svc.create_set(name="题库")
    q1 = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    q2 = Question(content="题B", answer="b", difficulty=Difficulty.medium)
    db_session.add_all([q1, q2])
    db_session.flush()
    res = svc.import_questions(qs.id, [q1.id, q2.id])
    assert res == {"added": 2, "skipped": 0, "skipped_reasons": {}}
    db_session.commit()
    assert svc.list_sets()["items"][0]["question_count"] == 2

    svc.remove_question(qs.id, q1.id)
    db_session.commit()
    assert svc.list_sets()["items"][0]["question_count"] == 1
    # 题目实体保留，仅解除关联
    assert db_session.get(Question, q1.id) is not None
    assert db_session.query(QuestionSetItem).filter_by(set_id=qs.id, question_id=q1.id).count() == 0


def test_import_skips_missing_and_duplicate(db_session):
    svc = ExamBankService(db_session)
    qs = svc.create_set(name="题库")
    q1 = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    db_session.add(q1)
    db_session.flush()
    svc.import_questions(qs.id, [q1.id])
    res = svc.import_questions(qs.id, [q1.id, 9999])  # 重复 + 不存在
    assert res == {"added": 0, "skipped": 2, "skipped_reasons": {
        str(q1.id): "已在文件夹中",
        "9999": "题目不存在",
    }}


def test_import_skips_blocked_questions(db_session):
    svc = ExamBankService(db_session)
    qs = svc.create_set(name="题库")
    ok = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    bad = Question(
        content="题B", answer="b", difficulty=Difficulty.easy,
        audit_status=AuditStatus.blocked,
    )
    db_session.add_all([ok, bad])
    db_session.flush()
    res = svc.import_questions(qs.id, [ok.id, bad.id])
    assert res == {"added": 1, "skipped": 1, "skipped_reasons": {
        str(bad.id): "审核未通过（blocked 不可入库）",
    }}
    db_session.commit()
    assert db_session.query(QuestionSetItem).filter_by(
        set_id=qs.id, question_id=bad.id
    ).count() == 0


# ---- 删除：非预设保留题目 / 预设禁删 ----

def test_delete_set_keeps_questions(db_session):
    svc = ExamBankService(db_session)
    qs = svc.create_set(name="待删")
    q1 = Question(content="题A", answer="a", difficulty=Difficulty.easy)
    db_session.add(q1)
    db_session.flush()
    svc.import_questions(qs.id, [q1.id])
    qs_id, q1_id = qs.id, q1.id
    db_session.commit()

    svc.delete_set(qs_id)
    db_session.commit()
    assert db_session.get(QuestionSet, qs_id) is None
    assert db_session.get(Question, q1_id) is not None


def test_delete_preset_set_forbidden(db_session):
    svc = ExamBankService(db_session)
    preset = QuestionSet(name="系统预设", is_preset=True)
    db_session.add(preset)
    db_session.flush()
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        svc.delete_set(preset.id)
    assert exc.value.status_code == 400
    assert db_session.get(QuestionSet, preset.id) is not None


# ---- 3.4 渠道二：真题复制入库 ----

def test_import_historical_question(db_session, tmp_path):
    bank = _bank(tmp_path)
    svc = ExamBankService(db_session)
    q = svc.import_historical(bank, "全国卷/2024/高考化学#q1")
    db_session.commit()
    assert q.id is not None
    got = db_session.get(Question, q.id)
    assert got.content == "配平：H2+O2→H2O"
    assert got.answer == "2H2+O2=2H2O"
    assert got.knowledge_points == "氧化还原"
    assert got.difficulty == Difficulty.easy
    assert got.source == QuestionSource.manual  # 真题复制入库视为手动录入
    assert got.audit_status.value == "passed"


def test_import_historical_missing(db_session, tmp_path):
    bank = _bank(tmp_path)
    svc = ExamBankService(db_session)
    with pytest.raises(NotFoundError):
        svc.import_historical(bank, "全国卷/2024/高考化学#nope")


def test_copied_historical_question_links_into_folder(db_session, tmp_path):
    """3.4 双渠道前置闭环：真题复制入库 → 可批量导入题库文件夹关联。"""
    bank = _bank(tmp_path)
    svc = ExamBankService(db_session)
    q = svc.import_historical(bank, "全国卷/2024/高考化学#q1")
    qs = svc.create_set(name="真题集")
    db_session.flush()
    res = svc.import_questions(qs.id, [q.id])
    assert res == {"added": 1, "skipped": 0, "skipped_reasons": {}}
    db_session.commit()
    assert len(svc.get_set_questions(qs.id)) == 1
    got = svc.get_set_questions(qs.id)[0]
    assert got["answer"] == "2H2+O2=2H2O"
    assert got["audit_status"] == "passed"  # question_dict 含审核状态
