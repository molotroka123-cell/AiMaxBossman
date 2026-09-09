"""§10/§22 — блокировка рантайма, падения и рестарт.

Разбираемый здесь вопрос — не «переживёт ли система падение», а «что она
считает известным после него». Применение файлов не идемпотентно: повторить его
«на всякий случай» значит сделать второй эффект там, где владелец разрешал один.
Поэтому неоднозначный исход остаётся НЕОДНОЗНАЧНЫМ до разбора, а не
доигрывается до удобного.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bcc.file_intelligence.models import Denied, JobState, Refusal
from bcc.file_intelligence.runtime_lock import RuntimeLock
from bcc.file_intelligence.scope import ScopePolicy
from bcc.file_intelligence.service import FileIntelligenceService

from . import fileintel_fake as fake


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "Downloads"
    files = fake.make_corpus(root, 2)
    state = tmp_path / "state"
    policy = ScopePolicy(authorized_roots=[root], protected_paths=[state],
                         repository_root=tmp_path / "repo")
    plan = fake.review_plan(
        [fake.plan_entry(str(f), category="Documents") for f in files],
        paths=[str(root)])
    sidecar = fake.FakeSidecar(plan=plan, effect=fake.move_effect)
    service = FileIntelligenceService(
        state_dir=state, policy=policy,
        discovery=fake.fake_discovery(fake.make_executable(tmp_path)),
        config_path=fake.local_config(tmp_path), runner=sidecar)
    return type("WS", (), {"root": root, "files": files, "service": service,
                           "sidecar": sidecar, "policy": policy, "state": state,
                           "tmp": tmp_path})


def _fresh_service(ws):
    """Новый сервис на том же состоянии — это и есть рестарт процесса."""
    return FileIntelligenceService(
        state_dir=ws.state, policy=ws.policy,
        discovery=fake.fake_discovery(fake.make_executable(ws.tmp)),
        config_path=fake.local_config(ws.tmp), runner=ws.sidecar)


# ------------------------------------------------------------------ §10 замок

def test_one_runtime_owner_at_a_time(tmp_path):
    lock = RuntimeLock(tmp_path)
    assert lock.try_acquire("first") is True
    assert lock.try_acquire("second") is False, "второй хозяин у одного рантайма"
    lock.release("first")
    assert lock.try_acquire("second") is True


def test_release_only_touches_your_own_lock(tmp_path):
    """Иначе завершающаяся работа снесла бы замок, уже взятый следующей."""
    lock = RuntimeLock(tmp_path)
    lock.try_acquire("mine")
    lock.release("not-mine")
    assert lock.read().job_id == "mine"


async def test_a_concurrent_job_waits_rather_than_racing(ws):
    """Две работы на одной папке не порождают двух процессов сортировщика."""
    held = RuntimeLock(ws.state)
    held.try_acquire("someone-else", "analyze")
    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.WAITING.value
    assert job.refusal == Refusal.BUSY.value
    assert ws.sidecar.calls == [], "второй процесс не должен был стартовать"


def test_a_stale_lock_from_a_dead_process_is_recoverable(tmp_path):
    """Восстановление по правилам upstream: замок с ЭТОГО хоста, чей процесс мёртв.

    Смотрим на живость процесса, а не на возраст: медленный анализ большой папки
    законно держит рантайм долго, и «старше N минут — снимаем» отобрал бы его у
    работающего.
    """
    import socket
    lock = RuntimeLock(tmp_path)
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(json.dumps({
        "host": socket.gethostname(), "pid": 2 ** 22, "job_id": "ghost",
        "started_at": 0.0, "description": "crashed"}), encoding="utf-8")
    assert lock.stale() is True
    assert lock.try_acquire("new-owner") is True


def test_a_live_lock_is_never_stolen(tmp_path):
    """Слепое снятие замка — способ получить два процесса на одном каталоге."""
    import socket
    lock = RuntimeLock(tmp_path)
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(json.dumps({
        "host": socket.gethostname(), "pid": os.getpid(), "job_id": "alive",
        "started_at": 0.0, "description": "running"}), encoding="utf-8")
    assert lock.stale() is False
    assert lock.try_acquire("thief") is False


def test_a_lock_from_another_host_is_left_alone(tmp_path):
    """Судить о живости чужого процесса отсюда нечем, поэтому и не судим."""
    lock = RuntimeLock(tmp_path)
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(json.dumps({
        "host": "some-other-machine", "pid": 1, "job_id": "remote",
        "started_at": 0.0, "description": ""}), encoding="utf-8")
    assert lock.stale() is False
    assert lock.try_acquire("local") is False


async def test_the_lock_is_released_after_a_job_finishes(ws):
    await ws.service.analyze([str(ws.root)])
    assert ws.service.lock.read() is None


async def test_the_lock_is_released_even_when_the_sidecar_fails(ws):
    ws.sidecar.raw_status = "not json at all"
    await ws.service.analyze([str(ws.root)])
    assert ws.service.lock.read() is None, "замок пережил неудачу и заблокировал всё"


# ------------------------------------------------------------------ §22 падения

async def test_crash_before_spawn_leaves_no_effect_and_restarts_cleanly(ws):
    """1. Падение до запуска сайдкара."""
    ws.sidecar.raise_on_run = RuntimeError("crash before spawn")
    with pytest.raises(RuntimeError):
        await ws.service.analyze([str(ws.root)])
    assert all(f.exists() for f in ws.files)
    restarted = _fresh_service(ws)
    for job in restarted.recover_all():
        assert job.state != JobState.VERIFIED.value


async def test_crash_during_analysis_is_restartable(ws):
    """2. Анализ можно перезапустить: эффекта не было — он `--review-only`."""
    job = await ws.service.analyze([str(ws.root)])
    job.state = JobState.ANALYZING.value
    ws.service.save(job)

    recovered = _fresh_service(ws).recover(job.job_id)
    assert recovered.state == JobState.QUEUED.value
    assert all(f.exists() for f in ws.files)


async def test_crash_after_the_review_plan_was_written_keeps_the_plan(ws):
    """3. План пережил падение и не превратился в разрешение."""
    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.REVIEW_REQUIRED.value

    restarted = _fresh_service(ws)
    recovered = restarted.recover(job.job_id)
    assert recovered.state == JobState.REVIEW_REQUIRED.value
    assert len(recovered.envelope["entries"]) == 2
    assert all(f.exists() for f in ws.files), "план сам себя не применил"


async def test_crash_after_approval_before_apply_is_not_replayed(ws):
    """4. Утверждённое применение не переигрывается автоматически.

    Согласие владельца — на ОДИН эффект. Рестарт, доигрывающий его сам,
    превращает одно разрешение в неограниченное их число.
    """
    job = await ws.service.analyze([str(ws.root)])
    job.state = JobState.APPROVED.value
    ws.service.save(job)

    recovered = _fresh_service(ws).recover(job.job_id)
    assert recovered.state == JobState.AMBIGUOUS.value
    assert recovered.refusal == Refusal.AMBIGUOUS_APPLY_NEEDS_RECONCILIATION.value
    assert all(f.exists() for f in ws.files)


async def test_crash_after_apply_started_is_ambiguous_not_failed(ws):
    """5. Процесс стартовал — исход НЕИЗВЕСТЕН.

    "failed" означало бы, что мы знаем: ничего не изменилось. Мы этого не знаем,
    и разница здесь — это разница между «повторить безопасно» и «повторить
    значит сделать второй раз».
    """
    job = await ws.service.analyze([str(ws.root)])
    job.state = JobState.APPLYING.value
    job.apply_dispatched_at = 1.0
    ws.service.save(job)

    recovered = _fresh_service(ws).recover(job.job_id)
    assert recovered.state == JobState.AMBIGUOUS.value
    with pytest.raises(Denied) as denied:
        await _fresh_service(ws).apply(job.job_id, [str(ws.files[0])])
    assert denied.value.refusal is Refusal.AMBIGUOUS_APPLY_NEEDS_RECONCILIATION


async def test_crash_after_effect_before_receipt_is_reconciled_by_observation(ws):
    """6. Эффект произошёл, квитанции нет. Разбирается НАБЛЮДЕНИЕМ.

    Это тот самый случай, ради которого журнал пишется до запуска: файлы уже
    переехали, а система об этом не знает. Правильный ответ — посмотреть на
    диск, а не запустить применение ещё раз.
    """
    job = await ws.service.analyze([str(ws.root)])
    entries = job.envelope["entries"]
    for entry in entries:
        entry["selected"] = True
        entry["destination"] = str(ws.root / "Documents" / Path(entry["file_path"]).name)
    job.envelope["entries"] = entries
    job.state = JobState.APPLYING.value
    ws.service.save(job)

    # эффект случился, Bossman об этом не записал
    fake.move_effect({"--review-file": str(
        ws.service.jobs_dir / job.job_id / "review.json")})
    assert not ws.files[0].exists()

    restarted = _fresh_service(ws)
    assert restarted.recover(job.job_id).state == JobState.AMBIGUOUS.value
    settled = restarted.reconcile(job.job_id)
    assert settled.state == JobState.VERIFIED.value, settled.detail
    assert settled.receipt["effect_count"] == 2


async def test_a_verified_apply_is_never_repeated(ws):
    """7. Проверенное применение не повторяется — второй эффект не покупается
    повторным нажатием."""
    job = await ws.service.analyze([str(ws.root)])
    applied = await ws.service.apply(job.job_id, [str(f) for f in ws.files])
    assert applied.state == JobState.VERIFIED.value, applied.detail

    with pytest.raises(Denied) as denied:
        await _fresh_service(ws).apply(job.job_id, [str(f) for f in ws.files])
    assert denied.value.refusal is Refusal.ALREADY_APPLIED

    restarted = _fresh_service(ws)
    assert restarted.recover(job.job_id).state == JobState.VERIFIED.value
    assert restarted.load(job.job_id).receipt["effect_count"] == 2


async def test_owner_stop_survives_restart(ws):
    """Stop владельца липкий. Рестарт — не способ его отменить."""
    job = await ws.service.analyze([str(ws.root)])
    ws.service.stop(job.job_id)

    restarted = _fresh_service(ws)
    recovered = restarted.recover(job.job_id)
    assert recovered.state == JobState.CANCELLED.value
    assert recovered.owner_stopped is True
    with pytest.raises(Denied) as denied:
        await restarted.apply(job.job_id, [str(ws.files[0])])
    assert denied.value.refusal is Refusal.OWNER_STOPPED
    assert all(f.exists() for f in ws.files)


async def test_owner_stop_cancels_a_waiting_job(ws):
    """§10 — Stop отменяет работу, стоящую в очереди за рантаймом."""
    RuntimeLock(ws.state).try_acquire("other", "analyze")
    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.WAITING.value
    stopped = ws.service.stop(job.job_id)
    assert stopped.state == JobState.CANCELLED.value


async def test_the_journal_is_written_before_the_process_is_dispatched(ws):
    """Write-ahead, проверенный наблюдением, а не чтением кода.

    В момент, когда сайдкар только позвали, состояние на диске уже обязано
    говорить APPLYING. Иначе падение между «процесс стартовал» и «мы записали,
    что стартовал» неотличимо от «мы ничего не запускали».
    """
    ws2 = ws
    seen: list[str] = []
    original = ws2.sidecar.__call__

    async def watching(argv, *, timeout):
        job_id = argv[argv.index("--job-id") + 1]
        on_disk = ws2.service.load(job_id)
        seen.append(on_disk.state if on_disk else "MISSING")
        return await original(argv, timeout=timeout)

    job = await ws2.service.analyze([str(ws2.root)])
    ws2.sidecar.__call__ = watching
    ws2.service._runner = watching
    await ws2.service.apply(job.job_id, [str(f) for f in ws2.files])
    assert seen == [JobState.APPLYING.value], seen


async def test_a_terminal_job_is_not_re_opened_by_recovery(ws):
    job = await ws.service.analyze([str(ws.root)])
    ws.service.stop(job.job_id)
    restarted = _fresh_service(ws)
    first = restarted.recover(job.job_id)
    second = restarted.recover(job.job_id)
    assert first.state == second.state == JobState.CANCELLED.value


async def test_reconciliation_reports_a_partial_apply_honestly(ws):
    """Половина применённого — не успех и не провал, а именно половина."""
    job = await ws.service.analyze([str(ws.root)])
    entries = job.envelope["entries"]
    for entry in entries:
        entry["selected"] = True
        entry["destination"] = str(ws.root / "Documents" / Path(entry["file_path"]).name)
    job.envelope["entries"] = entries
    job.state = JobState.APPLYING.value
    ws.service.save(job)

    # переехал ровно один файл из двух
    destination = ws.root / "Documents"
    destination.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.move(str(ws.files[0]), str(destination / ws.files[0].name))

    restarted = _fresh_service(ws)
    assert restarted.recover(job.job_id).state == JobState.AMBIGUOUS.value
    settled = restarted.reconcile(job.job_id)
    assert settled.state == JobState.VERIFICATION_FAILED.value
    assert settled.receipt["effect_count"] == 1
    assert settled.refusal == Refusal.POST_STATE_MISMATCH.value
