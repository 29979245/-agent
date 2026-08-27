"""LLM 批改引擎单元测试（task 5.x）。

5.1 答案来源选择（题库>录入>自判）/ 5.2 标准化比较 / 5.3 自判安全模式 /
5.4 主观题 LLM 语义批改（mock LLM + 容错解析失败）。
"""
import pytest

from app.services.ocr.grading import (
    AUTO_MARKER,
    compare_answer,
    grade_submission,
    grade_subjective,
    normalize_answer,
    parse_subjective_result,
    select_answer_source,
)

ANSWERS = [
    {"question_no": 1, "answer": " c "},
    {"question_no": 2, "answer": "B"},
    {"question_no": 3, "answer": ""},
]
BANK = {1: "C", 2: "A", 3: "C"}
TEACHER = {1: "C", 2: "B", 3: "D"}


class _FakeLLM:
    def __init__(self, text):
        self.text = text

    def complete(self, messages):
        return self.text


# ---- 5.1 答案来源选择 ----


def test_select_bank_has_priority_over_teacher():
    source, standards = select_answer_source(True, BANK, TEACHER)
    assert source == "bank"
    assert standards == BANK


def test_select_teacher_when_no_exam():
    source, standards = select_answer_source(False, None, TEACHER)
    assert source == "teacher"
    assert standards == TEACHER


def test_select_self_when_no_bank_and_no_teacher():
    source, standards = select_answer_source(False, None, None)
    assert source == "self"
    assert standards == {}


def test_grade_submission_bank_mode():
    result = grade_submission(ANSWERS, has_exam=True, bank_answers=BANK)
    assert result["source"] == "bank"
    assert result["manual_review"] is False
    items = {i["question_no"]: i for i in result["items"]}
    assert items[1]["is_correct"] is True      # " c " vs "C" → 标准化相等
    assert items[1]["review_needed"] is False
    assert items[2]["is_correct"] is False     # B vs A
    assert items[3]["is_correct"] is False     # 空答案判错


def test_grade_submission_teacher_mode():
    result = grade_submission(ANSWERS, has_exam=False, teacher_answers=TEACHER)
    assert result["source"] == "teacher"
    items = {i["question_no"]: i for i in result["items"]}
    assert items[2]["is_correct"] is True      # B vs B
    assert items[1]["is_correct"] is True      # " c " vs "C"


def test_grade_submission_self_mode_marks_manual_review():
    result = grade_submission(ANSWERS, has_exam=False)
    assert result["source"] == "self"
    assert result["manual_review"] is True
    for item in result["items"]:
        assert item["review_needed"] is True
        assert item["is_correct"] is None      # 不自动判对错


def test_grade_submission_auto_standard_requires_review():
    answers = [{"question_no": 1, "answer": "A"}]
    result = grade_submission(answers, has_exam=True, bank_answers={1: AUTO_MARKER})
    assert result["items"][0]["review_needed"] is True
    assert result["items"][0]["is_correct"] is False


# ---- 5.2 标准化比较 ----


@pytest.mark.parametrize(
    ("student", "standard", "expected"),
    [
        (" c ", "C", True),     # 去首尾空白 + 大写
        ("a", "A", True),       # 大小写归一
        ("B", "B", True),
        ("", "C", False),       # 空答案判错
        ("A", "", False),       # 空标准答案判错
        ("A", "AUTO", False),   # 自判模式判错
        ("B", "A", False),      # 不相等判错
    ],
)
def test_compare_answer_cases(student, standard, expected):
    assert compare_answer(student, standard) is expected


def test_normalize_answer_strips_and_uppercases():
    assert normalize_answer("  c  ") == "C"
    assert normalize_answer(None) == ""


# ---- 5.4 主观题 LLM 语义批改 ----


def test_parse_subjective_result_success():
    parsed = parse_subjective_result(
        '{"is_correct": true, "review_needed": false, "reason": "要点齐全"}'
    )
    assert parsed == {"is_correct": True, "review_needed": False, "reason": "要点齐全"}


def test_parse_subjective_result_string_boolean():
    """LLM 返回字符串布尔（"false"）→ 解析为 False，避免 bool("false")==True 误判对。"""
    parsed = parse_subjective_result(
        '{"is_correct": "false", "review_needed": "true", "reason": "x"}'
    )
    assert parsed["is_correct"] is False
    assert parsed["review_needed"] is True


def test_parse_subjective_result_fenced_json():
    parsed = parse_subjective_result(
        '```json\n{"is_correct": false, "review_needed": true, "reason": "漏写"}'
        "\n```"
    )
    assert parsed["is_correct"] is False
    assert parsed["review_needed"] is True


@pytest.mark.asyncio
async def test_grade_subjective_llm_returns_result():
    llm = _FakeLLM('{"is_correct": true, "review_needed": false, "reason": "对"}')
    result = await grade_subjective("配平", "2H2+O2=2H2O", "2H2+O2=2H2O", client=llm)
    assert result["is_correct"] is True
    assert result["review_needed"] is False


@pytest.mark.asyncio
async def test_grade_subjective_unparseable_marks_review():
    llm = _FakeLLM("我无法判断")
    result = await grade_subjective("配平", "2H2+O2=2H2O", "2H2+O2=2H2O", client=llm)
    assert result["review_needed"] is True
    assert result["is_correct"] is False
    assert result["reason"]
