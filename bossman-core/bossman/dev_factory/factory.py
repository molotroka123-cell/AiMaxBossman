"""Stage 10 — DevFactory: петля автономной разработки.

Task → план → изолированная копия → код → тесты в песочнице → состязательное
ревью → правка → доказательства → ПАТЧ → ОЖИДАНИЕ ВЛАДЕЛЬЦА.

Границы, которые код не даёт обойти:
- фабрика НИКОГДА не пушит/мержит: терминал петли — AWAITING_APPROVAL;
- «успех» невозможен без Evidence.proves_success;
- бюджет попыток конечен;
- консеквентные шаги (сборка патча) отмечаются в журнале — перезапуск их не
  повторяет;
- отмена работает из любого нетерминального состояния;
- незнакомый репозиторий недоверен → тесты идут в песочнице с усиленной
  изоляцией, и при её недоступности мы падаем закрыто.
"""
from __future__ import annotations

from pathlib import Path

from .. import errors, events, obs
from . import store
from .evidence import from_test_output, write_evidence
from .models import (
    CONSEQUENTIAL_KINDS,
    DevJob,
    DevStep,
    Evidence,
    JobState,
    Patch,
    RetryBudget,
    StepKind,
    Verdict,
    can_transition,
    new_id,
)
from .planner import FakePlanner, Planner, detect_injection
from .reviewer import AdversarialReviewer, ReviewResult
from .workspace import WorkspaceManager

log = obs.get_logger("bossman.dev_factory")


class DevFactory:
    def __init__(
        self,
        root: str | Path,
        *,
        planner: Planner | None = None,
        reviewer: AdversarialReviewer | None = None,
        executor=None,
        max_attempts: int = 3,
    ) -> None:
        self.root = Path(root)
        self.planner = planner or FakePlanner()
        self.reviewer = reviewer or AdversarialReviewer()
        self.executor = executor          # см. executor.SandboxExecutor
        self.max_attempts = max_attempts
        self.workspaces = WorkspaceManager(self.root / "_workspaces")
        self.jobs: dict[str, DevJob] = {}

    # ---- переходы ----

    def _to(self, job: DevJob, state: JobState, note: str = "") -> None:
        if not can_transition(job.state, state):
            raise errors.InvalidTransition(
                f"illegal job transition {job.state.value} -> {state.value}",
                extra={"job": job.id, "from": job.state.value, "to": state.value})
        job.record(state, note)
        events.emit("dev_factory.job", job_id=job.id, state=state.value, note=note)
        store.save(self.root, job)

    # ---- создание ----

    def create(self, task: str, repo_path: str, *, repo_context: str = "",
               trusted_repo: bool = False) -> DevJob:
        job = DevJob(id=new_id("dj"), task=task, repo_path=str(repo_path),
                     trusted_repo=trusted_repo,
                     budget=RetryBudget(max_attempts=self.max_attempts))
        # Содержимое репозитория — ДАННЫЕ. Инъекции не исполняем, а фиксируем.
        inj = detect_injection(repo_context)
        job.steps = list(self.planner.plan(task, repo_context))
        self.jobs[job.id] = job
        job.record(JobState.PLANNED, f"шагов={len(job.steps)}")
        if inj:
            job.history.append((job.updated_at, JobState.PLANNED.value,
                                f"обнаружены попытки инъекции (не исполнены): {', '.join(inj[:3])}"))
            events.emit("dev_factory.injection_detected", job_id=job.id, markers=list(inj[:5]))
        store.save(self.root, job)
        return job

    # ---- исполнение ----

    async def run(self, job: DevJob) -> JobState:
        """Прогнать петлю до патча или до исчерпания бюджета."""
        if job.state is JobState.PLANNED:
            self._to(job, JobState.PREPARING, "изолированная копия")
            job.workspace = str(self.workspaces.prepare(job.id, job.repo_path))
            self._to(job, JobState.RUNNING, "старт шагов")

        while True:
            if job.state is JobState.CANCELLED:
                return job.state
            ok = await self._run_steps(job)
            if job.state is JobState.CANCELLED:
                return job.state
            if not ok:
                if job.budget.exhausted:
                    job.error = "бюджет попыток исчерпан"
                    self._to(job, JobState.FAILED, job.error)
                    return job.state
                job.budget.spend()
                self._to(job, JobState.NEEDS_FIX, f"попытка {job.budget.used}")
                self._to(job, JobState.RUNNING, "повтор")
                continue

            self._to(job, JobState.REVIEWING, "состязательное ревью")
            patch = self.workspaces.diff(job.id)
            verdict = self._test_verdict(job)
            result: ReviewResult = self.reviewer.review(patch, evidence_verdict=verdict)
            if not result.approved:
                if job.budget.exhausted:
                    job.error = "ревью не пройдено, бюджет исчерпан: " + "; ".join(result.findings)
                    self._to(job, JobState.FAILED, job.error)
                    return job.state
                job.budget.spend()
                self._to(job, JobState.NEEDS_FIX, "; ".join(result.findings)[:200])
                self._to(job, JobState.RUNNING, "повтор после ревью")
                continue

            # Сборка патча — консеквентный шаг: отмечаем, чтобы рестарт не повторил.
            patch.evidence_summary = self._evidence_summary(job)
            job.patch = patch
            for st in job.steps:
                if st.kind in CONSEQUENTIAL_KINDS:
                    job.mark_performed(st.id)
            self._to(job, JobState.AWAITING_APPROVAL,
                     f"патч готов: файлов={len(patch.files)}")
            return job.state

    async def _run_steps(self, job: DevJob) -> bool:
        """Выполнить незавершённые шаги. False — что-то не дало доказательств."""
        for step in job.steps:
            if job.state is JobState.CANCELLED:
                return False
            if step.done:
                continue
            if step.kind in CONSEQUENTIAL_KINDS:
                # Патч собирается в run(), а при рестарте не повторяется.
                if job.already_performed(step.id):
                    step.done = True
                continue
            step.attempts += 1
            if step.kind is StepKind.EDIT:
                # Правку кода делает исполнитель (модель/агент). Без него шаг
                # считается выполненным вхолостую — доказательства даст TEST.
                if self.executor is not None:
                    try:
                        await self.executor.edit(job, step)
                    except errors.BossmanError as exc:
                        # Сбой правки — потраченная попытка (bounded retry),
                        # а не крах всего задания и не тихий «успех».
                        job.history.append((job.updated_at, job.state.value,
                                            f"edit failed: {exc.code.value}"))
                        store.save(self.root, job)
                        return False
                step.done = True
            elif step.kind is StepKind.TEST:
                ev = await self._run_tests(job, step)
                step.evidence = ev
                step.done = ev.proves_success
                store.save(self.root, job)
                if not ev.proves_success:
                    return False
            elif step.kind is StepKind.REVIEW:
                step.done = True
        return True

    async def _run_tests(self, job: DevJob, step: DevStep) -> Evidence:
        """Тесты идут ТОЛЬКО через песочницу: чужой код не исполняется на хосте."""
        if self.executor is None:
            return Evidence(verdict=Verdict.UNKNOWN, summary="исполнитель не задан")
        try:
            output = await self.executor.run_tests(job, step)
        except errors.BossmanError as exc:
            # Песочница недоступна/отказала → это НЕ успех (fail closed).
            return Evidence(verdict=Verdict.FAIL, summary=f"{exc.code.value}: {exc.detail}")
        except Exception as exc:  # noqa: BLE001
            return Evidence(verdict=Verdict.FAIL, summary=f"{type(exc).__name__}: {exc}")
        path = write_evidence(self.root / job.id / "evidence", f"{step.id}.log", output)
        return from_test_output(output, stdout_path=path)

    def _test_verdict(self, job: DevJob) -> Verdict:
        tests = [s for s in job.steps if s.kind is StepKind.TEST]
        if not tests:
            return Verdict.UNKNOWN
        if all(s.evidence.proves_success for s in tests):
            return Verdict.PASS
        if any(s.evidence.verdict is Verdict.FAIL for s in tests):
            return Verdict.FAIL
        return Verdict.UNKNOWN

    def _evidence_summary(self, job: DevJob) -> str:
        return "; ".join(f"{s.kind.value}:{s.evidence.summary}"
                         for s in job.steps if s.evidence.summary)

    # ---- владелец ----

    def approve(self, job: DevJob, *, by: str) -> DevJob:
        """Подтверждение ВЛАДЕЛЬЦА. Публикацию (push/PR) делает он сам —
        фабрика не имеет для этого пути."""
        if not by:
            raise errors.PolicyDenied("approval requires an identity")
        self._to(job, JobState.APPROVED, f"подтвердил: {by}")
        self._to(job, JobState.DONE, "патч передан владельцу")
        return job

    def reject(self, job: DevJob, *, by: str, reason: str = "") -> DevJob:
        self._to(job, JobState.REJECTED, f"{by}: {reason}"[:200])
        return job

    def cancel(self, job: DevJob, *, note: str = "отменено") -> DevJob:
        if job.state in (JobState.APPROVED, JobState.DONE, JobState.REJECTED,
                         JobState.FAILED, JobState.CANCELLED):
            return job
        self._to(job, JobState.CANCELLED, note)
        self.workspaces.cleanup(job.id)
        return job

    # ---- восстановление ----

    def recover(self) -> list[str]:
        """Поднять состояние с диска. Консеквентные шаги НЕ повторяются:
        job.performed переживает рестарт и помечает их выполненными."""
        recovered: list[str] = []
        for jid in store.list_jobs(self.root):
            job = store.load(self.root, jid)
            if job is None:
                continue
            self.jobs[job.id] = job
            recovered.append(job.id)
        return recovered
