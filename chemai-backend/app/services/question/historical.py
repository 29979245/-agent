"""真题库内存加载器（设计 §7/§13，doc 47）。

真题库按 地区/年份/试卷 三层目录存放 JSON，启动时加载到内存
（ExamPaper / HistoricalQuestion），不落 Question 表。文件损坏或
结构缺失时跳过并日志告警，不阻断加载。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.services.question.serializers import PAGE_SIZE, paginate_items

logger = logging.getLogger(__name__)


@dataclass
class HistoricalQuestion:
    """真题库单题（内存对象，无 DB id）。"""

    id: str
    content: str
    answer: str
    analysis: str = ""
    options: list = field(default_factory=list)
    knowledge_points: list[str] = field(default_factory=list)
    difficulty: str = "medium"

    def to_dict(self, region: str, year: int, paper: str) -> dict:
        return {
            "ref_id": f"{region}/{year}/{paper}#{self.id}",
            "content": self.content,
            "options": self.options,
            "answer": self.answer,
            "analysis": self.analysis,
            "knowledge_points": self.knowledge_points,
            "difficulty": self.difficulty,
            "region": region,
            "year": year,
            "paper": paper,
        }


@dataclass
class ExamPaper:
    """一份真题试卷。"""

    region: str
    year: int
    name: str
    questions: list[HistoricalQuestion] = field(default_factory=list)

    @property
    def paper_id(self) -> str:
        return f"{self.region}/{self.year}/{self.name}"


class HistoricalBank:
    """真题库内存索引：加载、试卷树、分页查询、ref_id 解析。"""

    def __init__(self) -> None:
        self.papers: list[ExamPaper] = []
        self.loaded_files = 0
        self.loaded_questions = 0
        self.loaded_skipped = 0

    def load_from(self, base_dir: str | Path) -> "HistoricalBank":
        """扫描 {base_dir}/{region}/{year}/*.json 加载真题。"""
        self.papers = []
        self.loaded_files = 0
        self.loaded_questions = 0
        self.loaded_skipped = 0
        root = Path(base_dir)
        if not root.is_dir():
            logger.warning("[ExamBank] 真题库目录不存在，跳过加载：%s", root)
            return self
        for region_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            region = region_dir.name
            for year_dir in sorted(p for p in region_dir.iterdir() if p.is_dir()):
                try:
                    year = int(year_dir.name)
                except ValueError:
                    logger.warning("[ExamBank] 非年份目录，跳过：%s", year_dir)
                    continue
                for paper_file in sorted(year_dir.glob("*.json")):
                    paper = self._load_paper_file(region, year, paper_file)
                    if paper is not None:
                        self.papers.append(paper)
                        self.loaded_files += 1
                        self.loaded_questions += len(paper.questions)
        return self

    def _load_paper_file(self, region: str, year: int, path: Path) -> Optional[ExamPaper]:
        """解析单个 JSON 文件为 ExamPaper；损坏/缺结构返回 None。"""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            questions_raw = data.get("questions")
            if not isinstance(questions_raw, list):
                raise ValueError("缺少 questions 数组")
            questions = [
                HistoricalQuestion(
                    id=str(q.get("id", f"q{i + 1}")),
                    content=str(q.get("content", "")),
                    answer=str(q.get("answer", "")),
                    analysis=str(q.get("analysis", "")),
                    options=list(q.get("options", [])),
                    knowledge_points=list(q.get("knowledge_points", [])),
                    difficulty=str(q.get("difficulty", "medium")),
                )
                for i, q in enumerate(questions_raw)
                if isinstance(q, dict) and q.get("content") and q.get("answer")
            ]
            return ExamPaper(region=region, year=year, name=path.stem, questions=questions)
        except Exception as exc:  # noqa: BLE001 —— 坏文件跳过，不阻断加载
            self.loaded_skipped += 1
            logger.warning("[ExamBank] 跳过损坏真题文件 %s：%s", path, exc)
            return None

    def paper_tree(self) -> list[dict]:
        """地区 → 年份 → 试卷 层级结构。"""
        tree: dict[str, dict] = {}
        for paper in self.papers:
            region = tree.setdefault(paper.region, {"region": paper.region, "years": {}})
            year = region["years"].setdefault(paper.year, {"year": paper.year, "papers": []})
            year["papers"].append(
                {"paper_id": paper.paper_id, "name": paper.name, "question_count": len(paper.questions)}
            )
        return [
            {"region": region["region"], "years": list(region["years"].values())}
            for region in tree.values()
        ]

    def search(
        self,
        keyword: str | None = None,
        region: str | None = None,
        year: int | None = None,
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> dict:
        """按关键词/地区/年份过滤后分页返回，每项含 ref_id。"""
        items: list[dict] = []
        for paper in self.papers:
            if region and paper.region != region:
                continue
            if year and paper.year != year:
                continue
            for q in paper.questions:
                if keyword:
                    text = f"{q.content} {' '.join(q.knowledge_points)}"
                    if keyword not in text:
                        continue
                items.append(q.to_dict(paper.region, paper.year, paper.name))
        page_items, total, page = paginate_items(items, page, page_size)
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": page_items,
        }

    def get_question(self, ref_id: str) -> Optional[HistoricalQuestion]:
        """按 `地区/年份/试卷#题号` 解析单题（渠道二复制前置）。"""
        try:
            ref, _, qid = ref_id.rpartition("#")
            region, year_s, paper_name = ref.split("/", 2)
            year = int(year_s)
        except (ValueError, AttributeError):
            return None
        for paper in self.papers:
            if paper.region == region and paper.year == year and paper.name == paper_name:
                for q in paper.questions:
                    if q.id == qid:
                        return q
        return None


_global_bank: HistoricalBank | None = None


def get_bank() -> HistoricalBank:
    """全局真题库单例：main 启动时加载，测试可用 reload_bank 注入。"""
    global _global_bank
    if _global_bank is None:
        _global_bank = HistoricalBank()
    return _global_bank


def reload_bank(base_dir: str | Path) -> HistoricalBank:
    """重建全局真题库（启动加载 / 测试注入共用）。"""
    global _global_bank
    _global_bank = HistoricalBank().load_from(base_dir)
    return _global_bank
