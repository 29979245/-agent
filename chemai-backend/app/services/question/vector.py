"""向量检索服务（doc 47，设计 §8/§15）：每知识点一向量 + 两段检索。

- ChromaDB PersistentClient（collection `exam_questions`，cosine+HNSW）；不可用时降级纯关键词。
- Embedding：DashScope text-embedding-v3（1024 维），不可用回退 MD5 伪向量（确定性）。
- 每知识点生成独立向量（ID `{question_id}::kp-{i}`）；维度不匹配自动清库重建。
- 两段检索：关键词初筛 Top-20 → 候选内向量精筛 Top-K，排除自身，相似度降序。
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from pathlib import Path
from typing import Iterable, Optional

from app.services.question.serializers import split_knowledge_points

# ChromaDB 匿名遥测会尝试 PostHog 并打印报错，静默之
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

logger = logging.getLogger(__name__)

EMBED_DIM = 1024
COLLECTION = "exam_questions"
KEYWORD_LIMIT = 20


def _md5_pseudo_vector(text: str, dim: int = EMBED_DIM) -> list[float]:
    """确定性伪向量：MD5 链扩展填充 dim 维，Embedding 服务不可用时的兜底。"""
    seed = hashlib.md5(text.encode("utf-8")).digest()
    vec: list[float] = []
    while len(vec) < dim:
        seed = hashlib.md5(seed).digest()
        vec.extend(b / 255.0 for b in seed)
    return vec[:dim]


def _embed(text: str) -> list[float]:
    """DashScope text-embedding-v3 → 1024 维；失败回退 MD5 伪向量。"""
    try:
        import dashscope  # noqa: F401

        resp = dashscope.TextEmbedding.call(
            model="text-embedding-v3", input=text, dimension=EMBED_DIM
        )
        emb = resp["output"]["embeddings"][0]["embedding"]
        return list(emb)
    except Exception:  # noqa: BLE001 —— 网络/未配置一律走伪向量兜底
        return _md5_pseudo_vector(text)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _tokenize(text: str) -> set[str]:
    """分词：按非词字符切分，保留长度 ≥2 的片段（中文短语/化学式/英文词）。"""
    return {t for t in re.split(r"[^\w一-鿿]+", text) if len(t) >= 2}


class VectorSearch:
    """相似题检索：构建/增量同步索引，两段检索，ChromaDB 不可用降级关键词。"""

    def __init__(self, persist_dir: str | None = None):
        self.available = False
        self.collection = None
        self._client = None
        self._content_index: dict[int, dict] = {}  # question_id → {content, answer, kps}
        self._vector_ids: dict[int, list[str]] = {}  # question_id → [vector ids]
        try:
            import chromadb

            self._client = chromadb.PersistentClient(path=persist_dir or _default_persist_dir())
            self.collection = self._client.get_or_create_collection(
                name=COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            self.available = True
            logger.info("[VectorSearch] ChromaDB 已连接，collection=%s", COLLECTION)
        except Exception as exc:  # noqa: BLE001 —— ChromaDB 不可用降级纯关键词
            self.available = False
            logger.warning("[VectorSearch] ChromaDB 不可用，降级纯关键词检索：%s", exc)

    # ---- 索引构建 ----

    def _clear_collection(self) -> None:
        """清空集合（ChromaDB 1.x delete 需显式 ids/where）。"""
        got = self.collection.get()
        if got["ids"]:
            self.collection.delete(ids=got["ids"])

    def rebuild(self, questions: Iterable[dict]) -> None:
        """replace 模式：维度检测 + 清库后按每知识点一向量重建全部题目。"""
        if not self.available:
            return
        self._ensure_dimension()  # 先检测，维度不一致则删集合重建（再清空无残留）
        self._clear_collection()
        self._content_index.clear()
        self._vector_ids.clear()
        for q in questions:
            self.index_question(
                q["id"],
                q["content"],
                q.get("knowledge_points"),
                q.get("answer", ""),
            )

    def index_question(
        self,
        question_id: int,
        content: str,
        knowledge_points: Optional[Iterable[str]] = None,
        answer: str = "",
    ) -> None:
        """增量同步单题：每知识点一向量（ID `{qid}::kp-{i}`）。"""
        if not self.available:
            return
        kp_list = [kp for kp in (knowledge_points or []) if kp] or ([content] if content else ["未分类"])
        self._content_index[question_id] = {
            "question_id": question_id,
            "content": content,
            "answer": answer,
            "kps": kp_list,
        }
        # append 增量路径不触发清库重建：维度不一致时仅跳过该题，避免误删既有索引
        if not self._dimension_matches():
            logger.warning(
                "[VectorSearch] 维度不匹配，append 跳过题 %s（请用 replace 全量重建）",
                question_id,
            )
            return
        ids, embs, metas, docs = [], [], [], []
        for i, kp in enumerate(kp_list):
            ids.append(f"{question_id}::kp-{i}")
            embs.append(_embed(f"{kp}：{content}"))
            metas.append({"question_id": question_id, "content": content, "answer": answer, "kp": kp})
            docs.append(content)
        self.collection.upsert(ids=ids, embeddings=embs, metadatas=metas, documents=docs)
        self._vector_ids[question_id] = ids

    @staticmethod
    def _embeddings(got: dict) -> list[list[float]]:
        """chromadb 返回 numpy 数组，统一转 Python list（避免 bool/array 歧义）。"""
        embs = got.get("embeddings")
        if embs is None:
            return []
        return [list(e) for e in embs]

    def _dimension_matches(self) -> bool:
        """已存向量维度与当前 Embedding 输出维度是否一致（空集合视为一致）。"""
        if self.collection.count() == 0:
            return True
        got = self.collection.get(limit=1, include=["embeddings"])
        embs = self._embeddings(got)
        return len(embs[0]) == len(_embed("dim-check")) if embs else True

    def _ensure_dimension(self) -> None:
        """replace 重建前置：已存向量维度与当前 Embedding 不一致时删集合换维度。"""
        if self._dimension_matches():
            return
        logger.warning("[VectorSearch] 维度不匹配，删集合重建")
        # 集合维度是其创建配置，删库重建才能换维度
        self._client.delete_collection(COLLECTION)
        self.collection = self._client.get_or_create_collection(
            name=COLLECTION, metadata={"hnsw:space": "cosine"}
        )
        self._content_index.clear()
        self._vector_ids.clear()

    # ---- 两段检索 ----

    def search(
        self,
        content: str,
        knowledge_points: Optional[Iterable[str]] = None,
        exclude_id: int | None = None,
        k: int = 5,
    ) -> list[dict]:
        """关键词 Top-20 初筛 → 候选内向量精筛 Top-K；排除自身，相似度降序。"""
        kps = list(knowledge_points or [])
        candidates = [qid for qid in self._keyword_candidates(content, kps) if qid != exclude_id]
        if not self.available:
            return [
                self._item(self._content_index[qid], None)
                for qid in candidates[:k]
                if qid in self._content_index
            ]
        query_vec = _embed(" ".join(kps) + "：" + content)
        scored: list[tuple[float, int]] = []
        for qid in candidates:
            meta = self._content_index.get(qid)
            vids = self._vector_ids.get(qid)
            if meta is None or not vids:
                continue
            got = self.collection.get(ids=vids, include=["embeddings"])
            sim = max((_cosine(query_vec, emb) for emb in self._embeddings(got)), default=0.0)
            scored.append((sim, qid))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._item(self._content_index[qid], sim) for sim, qid in scored[:k]]

    def _keyword_candidates(self, content: str, kps: list[str]) -> list[int]:
        """关键词初筛：内容/知识点命中任一关键词，上限 20。"""
        terms = _tokenize(content) | set(kps)
        hits: list[int] = []
        for qid, meta in self._content_index.items():
            hay = meta["content"] + " " + " ".join(meta["kps"])
            if any(term and term in hay for term in terms):
                hits.append(qid)
        return hits[:KEYWORD_LIMIT]

    @staticmethod
    def _item(meta: dict, similarity: float | None) -> dict:
        item = {
            "question_id": meta["question_id"],
            "content": meta["content"],
            "answer": meta["answer"],
            "knowledge_points": meta["kps"],
        }
        if similarity is not None:
            item["similarity"] = round(similarity, 4)
        return item


def _default_persist_dir() -> str:
    from app.config import settings

    # Group 7.1 在 config.py 增加 chroma_dir；未配置时回落 data/chroma
    return getattr(settings, "chroma_dir", str(Path(__file__).resolve().parent.parent.parent.parent / "data" / "chroma"))


_global_vector: VectorSearch | None = None


def get_vector() -> VectorSearch:
    """全局向量检索单例：main 启动时初始化，测试可用 reload_vector 注入。"""
    global _global_vector
    if _global_vector is None:
        _global_vector = VectorSearch()
    return _global_vector


def reload_vector(persist_dir: str | None = None) -> VectorSearch:
    """重建全局向量检索（测试注入 / 启动初始化共用）。"""
    global _global_vector
    _global_vector = VectorSearch(persist_dir=persist_dir)
    return _global_vector


def sync_question_to_vector(question) -> None:
    """新题保存入库后增量 append 同步到向量索引（5.4）。"""
    vec = get_vector()
    if not vec.available:
        return
    vec.index_question(
        question.id,
        question.content,
        split_knowledge_points(question.knowledge_points),
        question.answer or "",
    )
