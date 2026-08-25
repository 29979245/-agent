"""真题库加载器测试（2.1-2.3）：三层目录加载、容错、分页查询、ref_id 解析。"""
import json

from app.services.question.historical import HistoricalBank, ExamPaper, HistoricalQuestion


def _write_paper(base, region, year, name, questions):
    path = base / region / str(year)
    path.mkdir(parents=True, exist_ok=True)
    with open(path / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump({"title": name, "questions": questions}, f, ensure_ascii=False)


def _mk_sample(tmp_path):
    _write_paper(tmp_path, "全国卷", 2024, "高考化学", [
        {"id": "q1", "content": "配平：H2+O2→H2O", "answer": "2H2+O2=2H2O",
         "knowledge_points": ["氧化还原"], "difficulty": "easy"},
        {"id": "q2", "content": "下列物质属于电解质的是", "answer": "NaCl",
         "knowledge_points": ["电解质"], "difficulty": "medium"},
    ])
    _write_paper(tmp_path, "全国卷", 2024, "模拟卷A", [
        {"id": "s1", "content": "某气体的摩尔质量为 44", "answer": "CO2",
         "knowledge_points": ["物质的量"], "difficulty": "hard"},
    ])
    _write_paper(tmp_path, "全国卷", 2023, "高考化学", [
        {"id": "q1", "content": "氧化还原反应的判断", "answer": "A",
         "knowledge_points": ["氧化还原"], "difficulty": "medium"},
    ])
    _write_paper(tmp_path, "北京卷", 2024, "高考化学", [
        {"id": "q1", "content": "电解质与非电解质", "answer": "C",
         "knowledge_points": ["电解质"], "difficulty": "easy"},
    ])
    return tmp_path


# ---- 2.1 三层目录加载 ----

def test_load_bank_structure(tmp_path):
    base = _mk_sample(tmp_path)
    bank = HistoricalBank().load_from(base)
    assert bank.loaded_files == 4
    assert bank.loaded_questions == 5
    assert len(bank.papers) == 4

    tree = bank.paper_tree()
    assert len(tree) == 2  # 全国卷 / 北京卷
    regions = {r["region"] for r in tree}
    assert regions == {"全国卷", "北京卷"}
    gj = next(r for r in tree if r["region"] == "全国卷")
    years = {y["year"] for y in gj["years"]}
    assert years == {2024, 2023}


def test_loaded_paper_fields(tmp_path):
    base = _mk_sample(tmp_path)
    bank = HistoricalBank().load_from(base)
    paper = next(p for p in bank.papers if p.region == "全国卷" and p.year == 2024 and p.name == "高考化学")
    assert isinstance(paper, ExamPaper)
    assert len(paper.questions) == 2
    q = paper.questions[0]
    assert isinstance(q, HistoricalQuestion)
    assert q.content == "配平：H2+O2→H2O"
    assert q.answer == "2H2+O2=2H2O"
    assert q.knowledge_points == ["氧化还原"]


def test_missing_dir_loads_empty(tmp_path):
    bank = HistoricalBank().load_from(tmp_path / "not_exists")
    assert bank.loaded_files == 0
    assert bank.loaded_questions == 0
    assert bank.paper_tree() == []


# ---- 2.2 容错 ----

def test_load_skips_bad_json(tmp_path):
    base = _mk_sample(tmp_path)
    bad = base / "全国卷" / "2022"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "坏卷.json").write_text("{ not valid json !!!", encoding="utf-8")
    # 缺 questions 字段的合法 JSON 也应跳过
    (bad / "缺结构.json").write_text(json.dumps({"title": "no questions"}), encoding="utf-8")

    bank = HistoricalBank().load_from(base)
    # 4 个好文件照常加载，2 个坏文件被跳过
    assert bank.loaded_files == 4
    assert bank.loaded_questions == 5
    assert bank.loaded_skipped == 2
    assert len(bank.papers) == 4


# ---- 2.3 查询 ----

def test_search_all_paginated(tmp_path):
    base = _mk_sample(tmp_path)
    bank = HistoricalBank().load_from(base)
    res = bank.search(page=1, page_size=2)
    assert res["total"] == 5
    assert len(res["items"]) == 2
    assert res["page"] == 1
    res2 = bank.search(page=3, page_size=2)
    assert len(res2["items"]) == 1  # 第 5 条落在第 3 页


def test_search_filters(tmp_path):
    base = _mk_sample(tmp_path)
    bank = HistoricalBank().load_from(base)
    by_kw = bank.search(keyword="电解质")
    assert by_kw["total"] == 2  # 全国卷/北京卷 各一
    by_region = bank.search(region="北京卷")
    assert by_region["total"] == 1
    by_year = bank.search(year=2023)
    assert by_year["total"] == 1
    combo = bank.search(region="全国卷", year=2024, keyword="气体")
    assert combo["total"] == 1
    assert combo["items"][0]["answer"] == "CO2"


def test_search_result_shape(tmp_path):
    base = _mk_sample(tmp_path)
    bank = HistoricalBank().load_from(base)
    item = bank.search(keyword="气体")["items"][0]
    assert "ref_id" in item
    assert "content" in item
    assert "answer" in item
    assert "knowledge_points" in item
    assert "difficulty" in item
    assert "region" in item
    assert "year" in item
    assert "paper" in item


# ---- ref_id 解析（渠道二复制前置） ----

def test_get_question_by_ref_id(tmp_path):
    base = _mk_sample(tmp_path)
    bank = HistoricalBank().load_from(base)
    q = bank.get_question("全国卷/2024/高考化学#q1")
    assert q is not None
    assert q.answer == "2H2+O2=2H2O"
    assert bank.get_question("全国卷/2024/高考化学#nope") is None
    assert bank.get_question("不存在的卷/2024/x#q1") is None


def test_paper_id_roundtrip(tmp_path):
    """ref_id 由 paper_id + 题号唯一确定，可反向解析。"""
    base = _mk_sample(tmp_path)
    bank = HistoricalBank().load_from(base)
    paper = bank.papers[0]
    q = paper.questions[0]
    ref = f"{paper.paper_id}#{q.id}"
    assert bank.get_question(ref) is q
