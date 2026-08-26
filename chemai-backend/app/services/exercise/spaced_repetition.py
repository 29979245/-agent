"""间隔复习引擎（doc 29，design.md D4/D5）。

艾宾浩斯 6 级：level1..level6 → 1/3/7/14/30 天/不再安排。首级「当天」用
next_review_at=创建时刻表达（创建即到期）。升降级先判正误再判级：
- 答对：连续答对 +1；达 2 次升级（level<6），level6 置 done（终态）
- 答错：回落豁免（上次连续答对≥1 → 不降）→ 保底（level1 → 不降）→ 否则降 1 级
- 每次提交只写 ReviewHistory 与 ReviewTask，不写 StudentAnswer（D5）
"""
from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Question, ReviewHistory, ReviewTask
from app.db.models.enums import ReviewLevel, ReviewTaskStatus

# 等级 → 间隔天数；level6（不再安排）不再排期
REVIEW_INTERVAL_DAYS: dict[ReviewLevel, Optional[int]] = {
    ReviewLevel.level1: 1,
    ReviewLevel.level2: 3,
    ReviewLevel.level3: 7,
    ReviewLevel.level4: 14,
    ReviewLevel.level5: 30,
    ReviewLevel.level6: None,
}

_LEVEL_ORDER = [ReviewLevel.level1, ReviewLevel.level2, ReviewLevel.level3,
                ReviewLevel.level4, ReviewLevel.level5, ReviewLevel.level6]


def interval_days(level: ReviewLevel) -> Optional[int]:
    return REVIEW_INTERVAL_DAYS.get(level)


def _next_level(level: ReviewLevel) -> ReviewLevel:
    idx = _LEVEL_ORDER.index(level)
    return _LEVEL_ORDER[min(idx + 1, len(_LEVEL_ORDER) - 1)]


def _prev_level(level: ReviewLevel) -> ReviewLevel:
    idx = _LEVEL_ORDER.index(level)
    return _LEVEL_ORDER[max(idx - 1, 0)]


def compute_next_review_at(level: ReviewLevel, now: datetime.datetime) -> datetime.datetime | None:
    days = interval_days(level)
    if days is None:
        return None
    return now + datetime.timedelta(days=days)


def sync_review_tasks(db: Session, student_id: int, question_ids, now: Optional[datetime.datetime] = None) -> int:
    """为错误作答创建/复用 ReviewTask：同一学生同一题去重，已存在不重复创建。"""
    now = now or datetime.datetime.utcnow()
    created = 0
    for qid in question_ids:
        existing = (
            db.query(ReviewTask)
            .filter(ReviewTask.student_id == student_id, ReviewTask.question_id == qid)
            .first()
        )
        if existing is not None:
            continue
        db.add(ReviewTask(
            student_id=student_id,
            question_id=qid,
            review_level=ReviewLevel.level1,
            status=ReviewTaskStatus.pending,
            next_review_at=now,   # 首级当天：创建即到期
            first_studied_at=now,
        ))
        created += 1
    db.flush()
    return created


class SpacedRepetitionEngine:
    """艾宾浩斯升降级状态机（doc 29 §4）。"""

    def __init__(self, db: Session):
        self.db = db

    def apply_review(self, task: ReviewTask, passed: bool, now: Optional[datetime.datetime] = None) -> ReviewTask:
        """提交一次复习结果：判正误 → 升降级 → 重算下次时间 → 写 ReviewHistory。"""
        now = now or datetime.datetime.utcnow()
        if task.status == ReviewTaskStatus.done:
            raise ValueError("已掌握任务不可再复习")
        task.first_studied_at = task.first_studied_at or now
        was_consecutive_correct = task.consecutive_correct

        if passed:
            task.consecutive_correct += 1
            task.consecutive_error = 0
            if task.consecutive_correct >= 2:
                task.review_level = _next_level(task.review_level)
                task.consecutive_correct = 0
        else:
            task.consecutive_error += 1
            task.consecutive_correct = 0
            if was_consecutive_correct >= 1:
                pass  # 回落豁免：上次连续答对，本次首次答错不降级
            elif task.review_level == ReviewLevel.level1:
                pass  # 保底：首级不降
            else:
                task.review_level = _prev_level(task.review_level)

        if task.review_level == ReviewLevel.level6:
            task.status = ReviewTaskStatus.done
            task.completed_at = now
            task.next_review_at = None
        else:
            task.status = ReviewTaskStatus.pending
            task.next_review_at = compute_next_review_at(task.review_level, now)

        self.db.add(ReviewHistory(
            review_task_id=task.id,
            level=_LEVEL_ORDER.index(task.review_level) + 1,
            review_date=now.date(),
            passed=passed,
        ))
        self.db.flush()
        return task

    def list_due_tasks(self, student_id: int) -> list[ReviewTask]:
        """到期查询：pending/overdue，不含 done，按到期时间升序。"""
        return (
            self.db.query(ReviewTask)
            .filter(
                ReviewTask.student_id == student_id,
                ReviewTask.status.in_([ReviewTaskStatus.pending, ReviewTaskStatus.overdue]),
            )
            .order_by(ReviewTask.next_review_at.asc().nullsfirst(), ReviewTask.id.asc())
            .all()
        )

    def mark_overdue(self, student_id: int, now: Optional[datetime.datetime] = None) -> int:
        """超期标记：pending 且 next_review_at 已过当前时刻 → overdue。"""
        now = now or datetime.datetime.utcnow()
        rows = (
            self.db.query(ReviewTask)
            .filter(
                ReviewTask.student_id == student_id,
                ReviewTask.status == ReviewTaskStatus.pending,
                ReviewTask.next_review_at.isnot(None),
                ReviewTask.next_review_at < now,
            )
            .all()
        )
        for task in rows:
            task.status = ReviewTaskStatus.overdue
        if rows:
            self.db.flush()
        return len(rows)
