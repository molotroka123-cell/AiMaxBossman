"""V3.1 Memory & Context Kernel — killer case, written as the specification.

The one case that matters (owner's words): a long task is given in the evening;
context overflows several times, the model is switched, the process is
restarted — and in the morning Bossman remembers what it did, does not repeat
finished work, and shows a verified result.

Everything here is derived from that single sentence. No feature is tested
because it appears on a list; each assertion is a thing that breaks the case if
it is false:

  * resume picks the first UNFINISHED step, not step one (no repeated work);
  * a step counts as finished only with a receipt AND verification — the V2
    invariant carried into V3 (a model saying "done" is not done);
  * the journal survives process death: nothing needed to resume lives only in
    RAM or in one model's transcript;
  * a DIFFERENT model resumes without the previous model's messages, because
    the resume state is model-independent by construction;
  * approaches that already failed come back with the context, so the new model
    does not retry them;
  * the assembled context is bounded and carries provenance for every fact;
  * secrets never enter the assembled context.
"""
from __future__ import annotations

import pytest

from bossman_v3.memory import ContextAssembler, FailureMemory, TaskJournal
from bossman_v3.memory.journal import JournalStep

PLAN = [
    ("s1", "открыть проект"),
    ("s2", "исправить баг"),
    ("s3", "запустить тесты"),
    ("s4", "закоммитить и запушить"),
]


def _journal(tmp_path) -> TaskJournal:
    return TaskJournal.start(task_id="evening-task", plan=PLAN, root=tmp_path)


# ------------------------------------------------------------------ resume

def test_fresh_journal_starts_at_the_first_step(tmp_path):
    assert _journal(tmp_path).next_step().step_id == "s1"


def test_resume_after_process_death_skips_finished_work(tmp_path):
    j = _journal(tmp_path)
    j.record("s1", receipt={"effect_id": "proj-opened"}, verified=True)
    j.record("s2", receipt={"effect_id": "patch-abc123"}, verified=True)

    del j                                    # процесс умер: в памяти не осталось ничего
    revived = TaskJournal.load(task_id="evening-task", root=tmp_path)

    assert revived.next_step().step_id == "s3"
    assert [s.step_id for s in revived.finished()] == ["s1", "s2"]


def test_a_step_without_verification_is_not_finished(tmp_path):
    """Инвариант V2, перенесённый в V3: чек исполнения без подтверждения —
    ещё не сделанный шаг, и после рестарта его надо доделать, а не пропустить."""
    j = _journal(tmp_path)
    j.record("s1", receipt={"effect_id": "proj-opened"}, verified=True)
    j.record("s2", receipt={"effect_id": "patch-abc123"}, verified=False)

    revived = TaskJournal.load(task_id="evening-task", root=tmp_path)
    assert revived.next_step().step_id == "s2"


def test_a_model_claim_without_a_receipt_is_not_finished(tmp_path):
    j = _journal(tmp_path)
    j.record("s1", receipt=None, verified=True, note="модель написала «готово»")
    assert TaskJournal.load(task_id="evening-task", root=tmp_path).next_step().step_id == "s1"


def test_all_steps_finished_means_no_next_step(tmp_path):
    j = _journal(tmp_path)
    for sid, _ in PLAN:
        j.record(sid, receipt={"effect_id": sid}, verified=True)
    assert TaskJournal.load(task_id="evening-task", root=tmp_path).next_step() is None


# ------------------------------------------------------- model independence

def test_resume_state_carries_no_model_transcript(tmp_path):
    """Состояние возобновления не должно быть привязано к переписке одной
    модели — иначе смена модели теряет его. Проверяется по существу: в
    сериализованном журнале нет ни ролей чата, ни сообщений."""
    j = _journal(tmp_path)
    j.record("s1", receipt={"effect_id": "proj-opened"}, verified=True)

    raw = (tmp_path / "evening-task.json").read_text(encoding="utf-8")
    for chat_shape in ('"role"', '"messages"', '"assistant"', '"system"'):
        assert chat_shape not in raw, f"журнал завязан на транскрипт модели: {chat_shape}"


def test_a_different_model_resumes_from_the_same_journal(tmp_path):
    j = _journal(tmp_path)
    j.record("s1", receipt={"effect_id": "proj-opened"}, verified=True, by="glm-local")
    j.record("s2", receipt={"effect_id": "patch-abc"}, verified=True, by="glm-local")

    revived = TaskJournal.load(task_id="evening-task", root=tmp_path)
    pack = ContextAssembler().assemble(revived, budget_tokens=4000, model="claude")

    assert revived.next_step().step_id == "s3"
    assert "s1" in pack.text and "s2" in pack.text      # что уже сделано — видно
    assert "s3" in pack.text                             # и что делать дальше


# ------------------------------------------------------ failed approaches

def test_failed_approaches_come_back_so_they_are_not_retried(tmp_path):
    fm = FailureMemory(root=tmp_path)
    fm.record({"signature": "s3", "approach": "pytest -x без установки зависимостей",
               "error": "ModuleNotFoundError: fastapi"})

    revived = TaskJournal.load(task_id="evening-task", root=tmp_path) if False else _journal(tmp_path)
    revived.record("s1", receipt={"effect_id": "x"}, verified=True)
    revived.record("s2", receipt={"effect_id": "y"}, verified=True)

    pack = ContextAssembler(failure_memory=fm).assemble(revived, budget_tokens=4000)
    assert "ModuleNotFoundError" in pack.text
    assert "pytest -x без установки зависимостей" in pack.text


def test_failure_memory_survives_restart(tmp_path):
    FailureMemory(root=tmp_path).record({"signature": "s3", "approach": "a", "error": "e"})
    assert FailureMemory(root=tmp_path).query("s3")


# ------------------------------------------------------------- context pack

def test_context_pack_is_bounded_and_reports_what_it_dropped(tmp_path):
    j = _journal(tmp_path)
    for i in range(60):
        j.note(f"наблюдение {i}: " + "детали " * 60)
    pack = ContextAssembler().assemble(j, budget_tokens=500)

    assert pack.tokens <= 500
    assert pack.dropped > 0, "ничего не выброшено — бюджет не соблюдался"


def test_every_fact_in_the_pack_has_provenance(tmp_path):
    j = _journal(tmp_path)
    j.record("s1", receipt={"effect_id": "proj-opened"}, verified=True, by="glm-local")
    pack = ContextAssembler().assemble(j, budget_tokens=4000)

    assert pack.provenance, "происхождение фактов не заполнено"
    for entry in pack.provenance.values():
        assert entry.get("source")


@pytest.mark.parametrize("secret", [
    "sk-test-abcdefghijklmnopqrstuvwxyz0123",        # ci-secret-scan: allow — канарейка, доказывает редакцию
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",       # ci-secret-scan: allow — канарейка, доказывает редакцию
    "Authorization: Bearer abcdef.token.value",
])
def test_secrets_never_reach_the_assembled_context(tmp_path, secret):
    j = _journal(tmp_path)
    j.note(f"конфиг сервиса: {secret}")
    j.record("s1", receipt={"effect_id": "x", "detail": secret}, verified=True)

    pack = ContextAssembler().assemble(j, budget_tokens=4000)
    assert secret not in pack.text
    assert secret not in str(pack.provenance)


# --------------------------------------------------------------- the case

def test_the_killer_case_end_to_end(tmp_path):
    """Вечерняя задача → часть сделана → процесс убит → другая модель утром
    продолжает ровно с незавершённого шага, не повторяя сделанное и не
    наступая на уже провалившийся подход."""
    evening = _journal(tmp_path)
    evening.record("s1", receipt={"effect_id": "proj-opened"}, verified=True, by="glm-local")
    evening.record("s2", receipt={"effect_id": "patch-abc123"}, verified=True, by="glm-local")

    fm = FailureMemory(root=tmp_path)
    fm.record({"signature": "s3", "approach": "запуск тестов без venv",
               "error": "ModuleNotFoundError"})
    del evening                                            # ночь: процесс убит

    morning = TaskJournal.load(task_id="evening-task", root=tmp_path)
    pack = ContextAssembler(failure_memory=fm).assemble(
        morning, budget_tokens=8000, model="claude")

    assert morning.next_step().step_id == "s3"             # не повторяем s1/s2
    assert isinstance(morning.next_step(), JournalStep)
    assert "patch-abc123" in pack.text                     # помним результат s2
    assert "ModuleNotFoundError" in pack.text              # помним, что провалилось
    assert pack.tokens <= 8000
