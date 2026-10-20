"""Branch 8 (Memory): a lesson that recurs every cycle must not grow without bound.

Behavioural: real LessonBook on tmp storage. Retrieval context only — saving a
lesson is never model training.
"""
from __future__ import annotations

from pathlib import Path

from learning import lessons
from learning.lessons import CoachingEpisode, LessonBook, Provenance


def _ep(i: int) -> CoachingEpisode:
    return CoachingEpisode(
        attempt_id=f"att-{i}", task_id=f"task-{i}", project_id="proj-b08",
        failure_observation="test_inclusive_end failed: expected 15, got 10",
        correction="When the task says the end is inclusive, iterate range(a, b + 1); range() excludes the end.",
        source="student", task_class="off-by-one",
        provenance=Provenance(who="student:qwen-local", what="self-correction", model="qwen-local"),
        model="qwen-local")


def test_recurring_lesson_keeps_true_count_but_bounded_id_lists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(lessons, "MAX_TRACKED_IDS", 3, raising=False)
    book = LessonBook(tmp_path / "lessons")
    for i in range(7):
        book.save(_ep(i))
    lesson = book.get(_ep(0).lesson_id)
    assert lesson["occurrences"] == 7
    rec = book._current_by_task_id(_ep(0).lesson_id)["lesson"]
    assert rec["attempt_ids"] == ["att-4", "att-5", "att-6"]
    assert rec["source_task_ids"] == ["task-4", "task-5", "task-6"]
    # re-seeing the newest attempt is still a dedup, not a new occurrence
    book.save(_ep(6))
    assert book.get(_ep(0).lesson_id)["occurrences"] == 7
