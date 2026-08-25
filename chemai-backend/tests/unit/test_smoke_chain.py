"""7.3 全链路冒烟：题库CRUD → 考试生命周期 → 相似题检索（排除自身）→ 导出 Word/PDF。"""
import io

from docx import Document

from app.db.models import Class, Grade, Question, School, Student, StudentAnswer
from app.db.models.enums import Difficulty
from app.services.question.exam_bank import ExamBankService
from app.services.question.exam_service import ExamService
from app.services.question.export import export_paper_docx, generate_report_html, report_to_pdf
from app.services.question.vector import reload_vector, sync_question_to_vector


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
    return cls


def test_full_chain_smoke(db_session, tmp_path):
    cls = _org(db_session)

    # 1) 题库 CRUD：建文件夹集 + 导入题目
    bank_svc = ExamBankService(db_session)
    qs = bank_svc.create_set(name="高三化学", region="全国", year=2024, description="冒烟")
    q = Question(
        content="配平：H2+O2→H2O",
        answer="2H2+O2=2H2O",
        knowledge_points="氧化还原",
        difficulty=Difficulty.medium,
    )
    db_session.add(q)
    db_session.commit()
    assert bank_svc.import_questions(qs.id, [q.id]) == {"added": 1, "skipped": 0, "skipped_reasons": {}}
    assert len(bank_svc.get_set_questions(qs.id)) == 1

    # 2) 考试生命周期：创建→关联→发布→阅卷→完成→归档
    vs = reload_vector(str(tmp_path / "chroma"))
    sync_question_to_vector(q)
    exam_svc = ExamService(db_session)
    exam = exam_svc.create(class_id=cls.id, name="期中化学")
    assert exam_svc.add_questions(exam.id, [q.id])["added"] == 1
    exam_svc.publish(exam.id)
    exam_svc.start_grading(exam.id)
    stu = Student(class_id=cls.id, name="张三")
    db_session.add(stu)
    db_session.flush()
    db_session.add(StudentAnswer(student_id=stu.id, question_id=q.id, exam_id=exam.id, is_correct=True))
    db_session.commit()
    exam_svc.finalize(exam.id)
    exam_svc.archive(exam.id)
    assert exam.status.value == "archived"
    assert exam.stats["attended"] == 1

    # 3) 相似题检索（排除自身）
    q2 = Question(
        content="氧化还原反应的判断",
        answer="B",
        knowledge_points="氧化还原",
        difficulty=Difficulty.easy,
    )
    db_session.add(q2)
    db_session.commit()
    sync_question_to_vector(q2)
    ids = [
        r["question_id"]
        for r in vs.search("配平：H2+O2→H2O", knowledge_points=["氧化还原"], exclude_id=q.id)
    ]
    assert q.id not in ids  # 排除自身
    assert q2.id in ids  # 同知识点命中间接命中

    # 4) 导出 Word/PDF
    qdict = {
        "question_id": q.id,
        "content": q.content,
        "options": [],
        "answer": q.answer,
        "analysis": "",
        "knowledge_points": ["氧化还原"],
        "difficulty": "medium",
    }
    docx_bytes = export_paper_docx([qdict], title="期中化学", with_answers=True)
    assert len(Document(io.BytesIO(docx_bytes)).paragraphs) > 0
    html = generate_report_html(
        "期中化学",
        {"total_students": 1, "attended": 1, "question_count": 1, "wrong_counts": {q.id: 1}},
        [{"question_id": q.id, "content": q.content, "knowledge_points": ["氧化还原"]}],
        [{"student_id": stu.id, "name": "张三", "accuracy": 1.0}],
        include_analysis=True,
    )
    assert "TOP5" in html
    out = tmp_path / "report.pdf"
    assert report_to_pdf(html, str(out)) is True
    assert out.stat().st_size > 0
