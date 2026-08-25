"""配置与启动整合测试（7.1-7.2）：exam_bank_dir/chroma_dir 读取、启动加载真题库日志。"""
import logging

from app.config import settings
from app.main import startup_services


def test_config_exposes_bank_and_chroma_dirs():
    assert settings.exam_bank_dir.endswith("data" + __import__("os").sep + "exam_bank")
    assert settings.chroma_dir.endswith("data" + __import__("os").sep + "chroma")


def test_startup_loads_bank_and_logs(tmp_path, caplog):
    p = tmp_path / "全国卷" / "2024"
    p.mkdir(parents=True, exist_ok=True)
    (p / "高考化学.json").write_text(
        '{"title": "高考化学", "questions": ['
        '{"id": "q1", "content": "真题：离子方程式", "answer": "B",'
        ' "knowledge_points": ["离子反应"], "difficulty": "medium"}]}',
        encoding="utf-8",
    )
    with caplog.at_level(logging.INFO):
        stats = startup_services(exam_bank_dir=str(tmp_path), chroma_dir=str(tmp_path / "chroma"))
    assert stats["loaded_files"] == 1
    assert stats["loaded_questions"] == 1
    assert any("[ExamBank] 从 1 个文件加载了 1 道真题" in r.message for r in caplog.records)


def test_startup_missing_dir_loads_empty(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        stats = startup_services(exam_bank_dir=str(tmp_path / "nope"), chroma_dir=str(tmp_path / "chroma"))
    assert stats == {"loaded_files": 0, "loaded_questions": 0}
