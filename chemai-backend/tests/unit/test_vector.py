"""向量检索服务测试（5.1-5.4）：初始化/降级、per-kp 索引、两段检索排除自身、append 同步。"""
import pytest

from app.services.question.vector import VectorSearch, _md5_pseudo_vector


def _index_sample(vs, questions):
    """questions: list[(id, content, kps)]，rebuild replace 模式写入。"""
    vs.rebuild([{"id": qid, "content": content, "knowledge_points": kps} for qid, content, kps in questions])


SAMPLE = [
    (1, "配平：H2+O2→H2O", ["氧化还原"]),
    (2, "某气体的摩尔质量为 44", ["物质的量"]),
    (3, "氧化还原反应的判断", ["氧化还原"]),
]


# ---- 5.1 初始化与降级 ----

def test_init_collection(tmp_path):
    vs = VectorSearch(persist_dir=str(tmp_path))
    assert vs.available is True
    assert vs.collection.name == "exam_questions"


def test_md5_fallback_deterministic_dim():
    v1 = _md5_pseudo_vector("配平：H2+O2→H2O")
    v2 = _md5_pseudo_vector("配平：H2+O2→H2O")
    v3 = _md5_pseudo_vector("另一题内容")
    assert v1 == v2
    assert v1 != v3
    assert len(v1) == 1024


# ---- 5.2 索引构建 ----

def test_rebuild_indexes_per_knowledge_point(tmp_path):
    vs = VectorSearch(persist_dir=str(tmp_path))
    _index_sample(vs, SAMPLE)
    # 每知识点一向量：题1/题3 各 1 个（氧化还原），题2 1 个 → 共 3 个向量
    assert vs.collection.count() == 3
    got = vs.collection.get()
    assert any("::kp-0" in i for i in got["ids"])


def test_incremental_index_adds_without_wiping(tmp_path):
    vs = VectorSearch(persist_dir=str(tmp_path))
    _index_sample(vs, SAMPLE)
    vs.index_question(4, "电解质的电离", ["电解质"])
    assert vs.collection.count() == 4


def test_dimension_mismatch_clears_and_rebuilds(tmp_path):
    """模拟旧 Embedding 模型构建的 64 维集合 → 检测到维度不一致后清库重建。"""
    import chromadb

    legacy = chromadb.PersistentClient(path=str(tmp_path))
    legacy.get_or_create_collection(name="exam_questions").add(
        ids=["999::kp-0"],
        embeddings=[[0.0] * 64],
        metadatas=[{"question_id": 999, "content": "x", "answer": "", "kp": "x"}],
    )
    vs = VectorSearch(persist_dir=str(tmp_path))
    assert vs.available
    vs.rebuild([{"id": 1, "content": "配平：H2+O2→H2O", "knowledge_points": ["氧化还原"]}])
    got = vs.collection.get(include=["embeddings"])
    assert len(vs._embeddings(got)[0]) == 1024  # 与当前 Embedding 维度一致
    assert vs.collection.count() == 1


def test_append_dimension_mismatch_skips_without_rebuild(tmp_path):
    """append 增量路径维度不一致时应跳过而非清库重建（防误删既有索引）。"""
    import chromadb

    legacy = chromadb.PersistentClient(path=str(tmp_path))
    legacy.get_or_create_collection(name="exam_questions").add(
        ids=["999::kp-0"],
        embeddings=[[0.0] * 64],
        metadatas=[{"question_id": 999, "content": "旧题", "answer": "", "kp": "旧"}],
    )
    vs = VectorSearch(persist_dir=str(tmp_path))
    assert vs.available
    # append 触发维度检测 → 不一致仅跳过，不删集合
    vs.index_question(4, "电解质的电离", ["电解质"])
    got = vs.collection.get()
    assert len(got["ids"]) == 1  # 既有 64 维数据仍在，未清库
    assert got["ids"] == ["999::kp-0"]
    # replace 重建路径仍能正常清库换维度
    vs.rebuild([{"id": 1, "content": "配平：H2+O2→H2O", "knowledge_points": ["氧化还原"]}])
    assert vs.collection.count() == 1
    assert len(vs._embeddings(vs.collection.get(include=["embeddings"]))[0]) == 1024


# ---- 5.3 两段检索 ----

def test_two_stage_search_sorted_and_excludes_self(tmp_path):
    vs = VectorSearch(persist_dir=str(tmp_path))
    _index_sample(vs, SAMPLE)
    results = vs.search("配平：H2+O2→H2O", knowledge_points=["氧化还原"], exclude_id=1, k=5)
    ids = [r["question_id"] for r in results]
    assert 1 not in ids  # 排除自身
    # 关键词初筛命中：题3（kp 氧化还原）；题2（物质的量）不含关键词不应出现
    assert 3 in ids
    assert 2 not in ids
    sims = [r["similarity"] for r in results]
    assert sims == sorted(sims, reverse=True)  # 相似度降序
    assert all(isinstance(s, float) for s in sims)


def test_search_returns_content_and_similarity(tmp_path):
    vs = VectorSearch(persist_dir=str(tmp_path))
    _index_sample(vs, SAMPLE)
    res = vs.search("氧化还原反应的判断", knowledge_points=["氧化还原"], exclude_id=3, k=5)
    assert len(res) >= 1
    item = res[0]
    assert {"question_id", "content", "answer", "similarity"} <= set(item.keys())


# ---- 5.4 增量同步 ----

def test_new_question_searchable_after_append(tmp_path):
    vs = VectorSearch(persist_dir=str(tmp_path))
    _index_sample(vs, SAMPLE[:1])  # 仅题1
    assert vs.search("配平：H2+O2→H2O", knowledge_points=["氧化还原"], exclude_id=1) == []
    # 增量 append 题3 后立即可检索
    vs.index_question(3, "氧化还原反应的判断", ["氧化还原"])
    res = vs.search("配平：H2+O2→H2O", knowledge_points=["氧化还原"], exclude_id=1)
    assert [r["question_id"] for r in res] == [3]


# ---- 5.4 保存入库同步 ----

def test_sync_question_to_vector(tmp_path):
    """sync_question_to_vector：DB 题目保存后 append 同步，立即可被检索。"""
    from app.services.question.vector import reload_vector, sync_question_to_vector

    vs = reload_vector(str(tmp_path))
    assert vs.available
    _index_sample(vs, SAMPLE)
    # 模拟保存入库的新题（含知识点逗号串），同步后检索命中
    class FakeQuestion:
        id = 7
        content = "配平：CO2+C→CO"
        knowledge_points = "氧化还原"
        answer = "CO2+C=2CO"

    sync_question_to_vector(FakeQuestion())
    res = vs.search("配平：CO2+C→CO", knowledge_points=["氧化还原"], exclude_id=7)
    ids = [r["question_id"] for r in res]
    assert 3 in ids  # 题3（氧化还原）被召回
    assert 7 not in ids  # 题7 自身排除
    assert 2 not in ids  # 题2（物质的量）无关键词命中


# ---- 降级路径 ----

def test_degraded_keyword_only_no_exception(tmp_path):
    vs = VectorSearch(persist_dir=str(tmp_path))
    _index_sample(vs, SAMPLE)
    vs.available = False  # 模拟 ChromaDB 不可用
    results = vs.search("配平：H2+O2→H2O", knowledge_points=["氧化还原"], exclude_id=1)
    assert 3 in [r["question_id"] for r in results]
    assert "similarity" not in results[0]  # 纯关键词无相似度
