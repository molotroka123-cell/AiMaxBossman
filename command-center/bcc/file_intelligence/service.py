"""§5/§21/§22 — управляемый поток File Intelligence.

    USER → preflight области и приватности → сайдкар --review-only →
    план → проверка Bossman → ОДОБРЕНИЕ ВЛАДЕЛЬЦА → свежая проверка источников →
    сайдкар --headless-apply → независимое наблюдение ФС → квитанция

Сайдкар ПРЕДЛАГАЕТ. Bossman РАЗРЕШАЕТ. Из этого следует всё остальное: анализ
не может ничего изменить (он запускается только с `--review-only`), применение
не принимает ни путей, ни операции (оно исполняет утверждённый файл плана), а
успешный выход процесса не является исходом работы, пока файловую систему не
посмотрели своими глазами.

Журнал (§21/§22) — write-ahead: намерение применить записывается ДО запуска
процесса. Иначе падение между «процесс стартовал» и «мы записали, что стартовал»
неотличимо от «мы ничего не запускали», и рестарт спокойно применит план второй
раз. Второй журнал при этом не заводится: состояние работы живёт в одном файле
на работу, и он же — точка восстановления.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import protocol, verify
from .discovery import Discovery, default_config_path, discover, pinned_sha
from .models import (BackendMode, BinaryStatus, Denied, JobState, Operation,
                     PlanEntry, Receipt, Refusal, ReviewEnvelope, TERMINAL_STATES,
                     UpstreamStatus)
from .privacy import assert_processing_permitted, read_backend_mode
from .runtime_lock import RuntimeLock
from .scope import ScopePolicy, canonical

#: Сколько ждать сайдкар. Предел нужен: процесс, который не отвечает, — это
#: исход работы, а не повод ждать вечно.
ANALYZE_TIMEOUT = 30 * 60.0
APPLY_TIMEOUT = 30 * 60.0
_POLL = 0.25


def _digest_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Job:
    """Состояние одной работы. Это и есть журнал: пишется на диск на каждом шаге."""
    job_id: str
    state: str = JobState.QUEUED.value
    operation: str = Operation.CATEGORIZE.value
    targets: list[str] = field(default_factory=list)
    scope_root: str = ""
    backend_mode: str = BackendMode.UNKNOWN.value
    envelope: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    refusal: str | None = None
    detail: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    #: §22 — write-ahead: намерение применить, записанное ДО запуска процесса.
    apply_dispatched_at: float | None = None
    owner_stopped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class FileIntelligenceService:
    """Единственный исполняющий путь. Все задачи §7 приходят сюда.

    §7 требует, чтобы `file.rename_smart`, `downloads.clean`, `photos.organize`
    и остальные вели в ОДИН управляемый маршрут. Поэтому классов задач здесь
    нет — есть операция сайдкара и область действия, а красивые имена живут в
    слое представления и разворачиваются в эти два поля.
    """

    def __init__(self, *, state_dir: str | os.PathLike[str], policy: ScopePolicy,
                 discovery: Discovery | None = None,
                 config_path: Path | None = None,
                 runner: Any | None = None):
        self.state_dir = Path(state_dir)
        self.jobs_dir = self.state_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self.lock = RuntimeLock(self.state_dir)
        self._discovery = discovery
        self._config_path = config_path
        # Подменяемый запускатель процесса — то, на чём стоят контрактные тесты
        # уровня A: детерминированный фейковый сайдкар вместо настоящего.
        self._runner = runner or _subprocess_runner

    # ------------------------------------------------------------------ утилиты

    def discovery(self) -> Discovery:
        if self._discovery is None:
            self._discovery = discover()
        return self._discovery

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _work_dir(self, job_id: str) -> Path:
        path = self.jobs_dir / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(self, job: Job) -> Job:
        """Записать состояние атомарно.

        os.replace, а не открыть-и-писать: журнал, обрезанный падением на
        середине записи, хуже отсутствующего — он выглядит как настоящий.
        """
        job.updated_at = time.time()
        path = self._job_path(job.job_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.as_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return job

    def load(self, job_id: str) -> Job | None:
        try:
            data = json.loads(self._job_path(job_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        known = {f for f in Job.__dataclass_fields__}
        return Job(**{k: v for k, v in data.items() if k in known})

    def list_jobs(self) -> list[Job]:
        jobs = [self.load(p.stem) for p in sorted(self.jobs_dir.glob("*.json"))]
        return [j for j in jobs if j is not None]

    # ------------------------------------------------------------------ preflight

    def preflight(self, targets: Sequence[str], *, operation: Operation,
                  mutating: bool = False,
                  remote_approved: bool = False) -> tuple[Path, list[Path], BackendMode]:
        """Всё, что должно отказать ДО запуска процесса.

        Порядок намеренный: сначала есть ли чем работать, потом можно ли трогать
        эти пути, и только потом — доказана ли локальность. Иначе владелец
        получал бы жалобу на приватность там, где на самом деле путь вне области.
        """
        found = self.discovery()
        if found.status is BinaryStatus.NOT_INSTALLED:
            raise Denied(Refusal.BINARY_NOT_INSTALLED, found.detail)
        if found.status is BinaryStatus.PROTOCOL_FAILED:
            raise Denied(Refusal.PROTOCOL_FAILED, found.detail)

        root, resolved = self.policy.single_parent(targets, mutating=mutating)

        config = self._config_path if self._config_path is not None \
            else default_config_path()
        mode, _raw = read_backend_mode(config)
        assert_processing_permitted(mode, remote_approved=remote_approved)
        return root, resolved, mode

    # ------------------------------------------------------------------ анализ

    async def analyze(self, targets: Sequence[str], *,
                      operation: Operation = Operation.CATEGORIZE,
                      remote_approved: bool = False,
                      timeout: float = ANALYZE_TIMEOUT) -> Job:
        """Шаг 1. Только предложение: сайдкар запускается с `--review-only`."""
        job = Job(job_id=uuid.uuid4().hex[:24], operation=operation.value,
                  targets=[str(t) for t in targets])
        try:
            root, resolved, mode = self.preflight(
                targets, operation=operation, mutating=False,
                remote_approved=remote_approved)
        except Denied as denied:
            job.state, job.refusal, job.detail = (
                JobState.DENIED.value, denied.refusal.value, denied.detail)
            return self.save(job)

        job.scope_root, job.backend_mode = str(root), mode.value
        job.state = JobState.QUEUED.value
        self.save(job)

        work = self._work_dir(job.job_id)
        review_file, status_file = work / "review.json", work / "status.json"
        argv = protocol.analyze_argv(
            self.discovery().resolved_executable, operation=operation,
            path=root if len(resolved) == 1 and resolved[0].is_dir() else root,
            review_file=review_file, status_file=status_file, job_id=job.job_id)

        try:
            async with self.lock.hold(job.job_id, f"analyze {root}"):
                job.state = JobState.ANALYZING.value
                self.save(job)
                status = await self._run(argv, status_file, timeout=timeout,
                                         job=job)
        except Denied as denied:
            job.state = JobState.FAILED.value if denied.refusal is not Refusal.BUSY \
                else JobState.WAITING.value
            job.refusal, job.detail = denied.refusal.value, denied.detail
            return self.save(job)

        upstream = status.get("status_enum", UpstreamStatus.UNKNOWN)
        if upstream is not UpstreamStatus.REVIEW_REQUIRED:
            # `completed` здесь тоже неправильный исход: мы просили ревью.
            job.state = JobState.FAILED.value
            job.refusal = Refusal.PROTOCOL_FAILED.value
            job.detail = (f"review-only analysis ended as {upstream.value!r}; "
                          f"{status.get('error') or status.get('message') or ''}")
            return self.save(job)

        try:
            raw = review_file.read_text(encoding="utf-8")
            document = protocol.parse_review_plan(raw)
        except (OSError, Denied) as exc:
            job.state = JobState.FAILED.value
            job.refusal = (exc.refusal.value if isinstance(exc, Denied)
                           else Refusal.REVIEW_PLAN_MALFORMED.value)
            job.detail = str(exc)
            return self.save(job)

        envelope = self._wrap(job, document, _digest_text(raw), root)
        # §11/§12 — личность снимается ЗДЕСЬ, на ревью. Снять её только перед
        # применением значило бы записать уже подменённое содержимое и затем
        # подтвердить, что оно совпадает с самим собой.
        envelope.entries = verify.capture_identities(envelope.entries)
        job.envelope = envelope.as_dict()
        job.state = JobState.REVIEW_REQUIRED.value
        return self.save(job)

    def _wrap(self, job: Job, document: dict[str, Any], digest: str,
              root: Path) -> ReviewEnvelope:
        """§11 — обернуть план сайдкара метаданными Bossman.

        Дайджест берётся от ТЕКСТА файла, который прочитали. Он привязывает
        будущее решение владельца к конкретному содержимому: подменённый между
        ревью и применением план не пройдёт.
        """
        entries: list[PlanEntry] = []
        for raw in document.get("entries") or []:
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("filePath") or "")
            name = str(raw.get("fileName") or "")
            suggested = str(raw.get("suggestedName") or "")
            rename_only = bool(raw.get("renameOnly"))
            entries.append(PlanEntry(
                file_path=source, file_name=name,
                entry_type=str(raw.get("type") or "file"),
                category=str(raw.get("category") or ""),
                subcategory=str(raw.get("subcategory") or ""),
                suggested_name=suggested, rename_only=rename_only,
                destination=self._planned_destination(source, raw, root),
            ))
        return ReviewEnvelope(
            job_id=job.job_id,
            bossman_run_id=uuid.uuid4().hex[:16],
            upstream_sha=pinned_sha(),
            source_roots=[str(root)],
            review_file_digest=digest,
            created_at=str(document.get("createdAtUtc") or ""),
            owner_scope=[str(r) for r in self.policy.authorized_roots],
            operation=job.operation,
            backend_mode=job.backend_mode,
            entries=entries,
        )

    @staticmethod
    def _planned_destination(source: str, raw: dict[str, Any], root: Path) -> str:
        """Куда запись собирается попасть по плану.

        Считается из полей плана, а не спрашивается у модели. Пусто — значит,
        назначение не выведено, и §13 откажет по INVALID_FILENAME, вместо того
        чтобы «догадаться».
        """
        if not source:
            return ""
        name = str(raw.get("suggestedName") or raw.get("fileName") or "")
        if not name:
            return ""
        if raw.get("renameOnly"):
            return str(Path(source).parent / name)
        parts = [str(raw.get("category") or "").strip()]
        sub = str(raw.get("subcategory") or "").strip()
        if sub:
            parts.append(sub)
        folder = root
        for part in parts:
            if part:
                folder = folder / part
        return str(folder / name)

    # ------------------------------------------------------------------ применение

    async def apply(self, job_id: str, selected: Sequence[str], *,
                    remote_approved: bool = False,
                    timeout: float = APPLY_TIMEOUT) -> Job:
        """Шаг 2. Применить УТВЕРЖДЁННЫЙ план. Не идемпотентно, поэтому с журналом."""
        job = self.load(job_id)
        if job is None:
            raise Denied(Refusal.PATH_DOES_NOT_EXIST, "unknown job", job_id=job_id)
        if job.owner_stopped:
            raise Denied(Refusal.OWNER_STOPPED, "the owner stopped this job")
        if job.state == JobState.VERIFIED.value:
            # §22 — проверенное применение не повторяется.
            raise Denied(Refusal.ALREADY_APPLIED,
                         "this job was already applied and verified")
        if job.state == JobState.AMBIGUOUS.value:
            raise Denied(Refusal.AMBIGUOUS_APPLY_NEEDS_RECONCILIATION,
                         "a previous apply attempt left an ambiguous outcome; it must "
                         "be reconciled before anything else runs")
        if job.state != JobState.REVIEW_REQUIRED.value or not job.envelope:
            raise Denied(Refusal.STALE_REVIEW_PLAN,
                         f"job is in state {job.state!r}, not awaiting approval")

        work = self._work_dir(job_id)
        review_file, status_file = work / "review.json", work / "apply-status.json"

        # §11 — план не доверяется повторно: дайджест перечитывается с диска.
        try:
            raw = review_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise Denied(Refusal.REVIEW_PLAN_MALFORMED,
                         f"review plan is unreadable: {exc.strerror}") from None
        if _digest_text(raw) != job.envelope.get("review_file_digest"):
            raise Denied(Refusal.REVIEW_PLAN_DIGEST_MISMATCH,
                         "the review plan changed after it was presented for approval")

        entries = [PlanEntry(**{k: v for k, v in e.items() if k != "identity"},
                             identity=_identity_from(e.get("identity")))
                   for e in job.envelope.get("entries") or []]
        chosen = {str(s) for s in selected}
        for entry in entries:
            entry.selected = entry.file_path in chosen
        if not any(e.selected for e in entries):
            raise Denied(Refusal.INVALID_FILENAME, "no plan entries were selected")

        # §12 идёт ПЕРВОЙ содержательной проверкой. План, чьи источники уже
        # изменились, не должен доходить ни до разговора о назначениях, ни до
        # жалобы «такого файла нет»: причина отказа обязана называть настоящую
        # проблему — источник разъехался с тем, что утверждал владелец.
        verify.assert_not_stale(entries)

        # Свежая проверка области: полномочия перепроверяются во ВРЕМЯ эффекта,
        # а не только в момент, когда владелец нажал «одобрить».
        for entry in (e for e in entries if e.selected):
            self.policy.check(entry.file_path, mutating=True)

        _root, _resolved, mode = self.preflight(
            [e.file_path for e in entries if e.selected], mutating=True,
            operation=Operation(job.operation), remote_approved=remote_approved)
        job.backend_mode = mode.value

        verify.check_destinations(entries, self.policy)

        job.envelope["entries"] = [e.as_dict() for e in entries]
        job.state = JobState.APPROVED.value
        self.save(job)

        scope_root = Path(job.scope_root or _root)
        before = verify.scan_scope(scope_root)

        # `--headless-apply` применяет ВЕСЬ переданный файл плана. Отдать ему
        # исходный план означало бы применить и то, что владелец не выбирал:
        # одобрение подмножества превратилось бы в одобрение всего. Поэтому
        # применяется ПРОИЗВОДНЫЙ план — тот же документ, из которого убраны
        # невыбранные записи. Дайджест выше по-прежнему привязывает решение
        # владельца к тому, что ему показали.
        approved_file = work / "approved.json"
        document = json.loads(raw)
        keep = {e.file_path for e in entries if e.selected}
        document["entries"] = [e for e in document.get("entries") or []
                               if str(e.get("filePath")) in keep]
        approved_file.write_text(json.dumps(document, ensure_ascii=False),
                                 encoding="utf-8")

        argv = protocol.apply_argv(self.discovery().resolved_executable,
                                   review_file=approved_file, status_file=status_file,
                                   job_id=job_id)
        try:
            async with self.lock.hold(job_id, f"apply {scope_root}"):
                # WRITE-AHEAD: намерение записано ДО запуска. Падение сразу после
                # этой строки оставляет работу в APPLYING — то есть в состоянии
                # «эффект мог произойти», которое рестарт обязан разбирать, а не
                # переигрывать.
                job.state = JobState.APPLYING.value
                job.apply_dispatched_at = time.time()
                self.save(job)
                status = await self._run(argv, status_file, timeout=timeout, job=job)
        except Denied as denied:
            job.refusal, job.detail = denied.refusal.value, denied.detail
            job.state = (JobState.WAITING.value if denied.refusal is Refusal.BUSY
                         else JobState.AMBIGUOUS.value)
            return self.save(job)

        return self._settle(job, status, entries, scope_root, before)

    def _settle(self, job: Job, status: dict[str, Any], entries: list[PlanEntry],
                scope_root: Path, before: dict[str, tuple[int, int]]) -> Job:
        """§14 — исход решает наблюдение, а не код выхода процесса."""
        upstream = status.get("status_enum", UpstreamStatus.UNKNOWN)
        applied = verify.observe_effects(entries)
        after = verify.scan_scope(scope_root)
        expected: list[str] = []
        for entry in entries:
            if entry.selected and entry.identity is not None:
                expected.extend([entry.identity.canonical_source_path,
                                 entry.identity.planned_destination])
        unexpected = verify.unexpected_mutations(before, after, expected)

        every_verified = bool(applied) and all(e.verified for e in applied)
        receipt = Receipt(job_id=job.job_id, state="", applied=applied,
                          unexpected_mutations=unexpected)

        if upstream is not UpstreamStatus.COMPLETED:
            # Процесс не сказал «готово» — исход неоднозначен, потому что часть
            # эффекта могла произойти. Это НЕ "failed": failed означало бы, что
            # мы знаем, что ничего не изменилось.
            job.state = (JobState.AMBIGUOUS.value if any(e.verified for e in applied)
                         else JobState.FAILED.value)
            receipt.refusal = Refusal.POST_STATE_MISMATCH.value
            receipt.detail = (f"sidecar ended as {upstream.value!r}: "
                              f"{status.get('error') or status.get('message') or ''}")
        elif not every_verified or unexpected:
            # Процесс сказал «готово», файловая система не подтвердила.
            job.state = JobState.VERIFICATION_FAILED.value
            receipt.refusal = Refusal.POST_STATE_MISMATCH.value
            receipt.detail = ("the sidecar reported completion but the observed "
                              "filesystem state does not match the approved plan")
        else:
            job.state = JobState.VERIFIED.value

        receipt.state = job.state
        job.receipt = receipt.as_dict()
        job.refusal = receipt.refusal
        job.detail = receipt.detail
        return self.save(job)

    # ------------------------------------------------------------------ §22 рестарт

    def recover(self, job_id: str) -> Job | None:
        """Разобрать работу, застигнутую падением.

        Правила §22, целиком:
          * анализ можно перезапустить — эффекта не было;
          * УТВЕРЖДЁННОЕ применение не переигрывается автоматически;
          * неоднозначный исход требует разбора владельцем;
          * проверенное применение не повторяется;
          * Stop владельца переживает рестарт.
        """
        job = self.load(job_id)
        if job is None:
            return None
        if job.owner_stopped:
            job.state = JobState.CANCELLED.value
            job.refusal = Refusal.OWNER_STOPPED.value
            return self.save(job)
        if job.state in {s.value for s in TERMINAL_STATES}:
            return job                                   # исход уже есть, не трогаем
        if job.state == JobState.ANALYZING.value:
            # Эффекта не было: анализ запускается только с --review-only.
            job.state, job.detail = JobState.QUEUED.value, "analysis restartable"
            return self.save(job)
        if job.state in (JobState.APPLYING.value, JobState.APPROVED.value):
            job.state = JobState.AMBIGUOUS.value
            job.refusal = Refusal.AMBIGUOUS_APPLY_NEEDS_RECONCILIATION.value
            job.detail = ("apply was dispatched and the outcome is unknown; the "
                          "filesystem must be reconciled before this job continues")
            return self.save(job)
        if job.state == JobState.WAITING.value:
            job.state = JobState.QUEUED.value
            return self.save(job)
        return job

    def recover_all(self) -> list[Job]:
        return [j for j in (self.recover(job.job_id) for job in self.list_jobs())
                if j is not None]

    def reconcile(self, job_id: str) -> Job:
        """Разобрать неоднозначный исход НАБЛЮДЕНИЕМ, а не догадкой."""
        job = self.load(job_id)
        if job is None or job.state != JobState.AMBIGUOUS.value:
            raise Denied(Refusal.PATH_DOES_NOT_EXIST,
                         "no ambiguous job with that id", job_id=job_id)
        entries = [PlanEntry(**{k: v for k, v in e.items() if k != "identity"},
                             identity=_identity_from(e.get("identity")))
                   for e in (job.envelope or {}).get("entries") or []]
        applied = verify.observe_effects(entries)
        every = bool(applied) and all(e.verified for e in applied)
        receipt = Receipt(job_id=job_id, state="", applied=applied)
        job.state = JobState.VERIFIED.value if every else \
            JobState.VERIFICATION_FAILED.value
        receipt.state = job.state
        if not every:
            receipt.refusal = Refusal.POST_STATE_MISMATCH.value
            receipt.detail = "reconciliation found the plan only partially applied"
        job.receipt = receipt.as_dict()
        job.refusal, job.detail = receipt.refusal, receipt.detail
        return self.save(job)

    def stop(self, job_id: str) -> Job | None:
        """Stop владельца. Липкий: переживает рестарт (§22)."""
        job = self.load(job_id)
        if job is None:
            return None
        job.owner_stopped = True
        if job.state not in {s.value for s in TERMINAL_STATES}:
            job.state = JobState.CANCELLED.value
            job.refusal = Refusal.OWNER_STOPPED.value
        return self.save(job)

    # ------------------------------------------------------------------ процесс

    async def _run(self, argv: list[str], status_file: Path, *, timeout: float,
                   job: Job) -> dict[str, Any]:
        """Запустить сайдкар и дождаться нерабочего состояния.

        stdout читается как протокол, stderr — как диагностика. Ни то ни другое
        не логируется целиком: имена и содержимое файлов владельца в общий
        журнал не попадают (§16/§23).
        """
        result = await self._runner(argv, timeout=timeout)
        raw = None
        with contextlib.suppress(OSError):
            raw = status_file.read_text(encoding="utf-8")
        if not raw:
            raw = result.get("stdout") or ""
        return protocol.parse_status(raw)


def _identity_from(data: Any):
    if not isinstance(data, dict):
        return None
    from .models import SourceIdentity
    known = {f for f in SourceIdentity.__dataclass_fields__}
    return SourceIdentity(**{k: v for k, v in data.items() if k in known})


async def _subprocess_runner(argv: list[str], *, timeout: float) -> dict[str, Any]:
    """Единственное место, где рождается процесс. Список argv, без shell.

    `shell=False` здесь не настройка, а отсутствие интерпретатора: имя файла
    `; rm -rf ~` остаётся именем файла, потому что его некому разобрать как
    команду.
    """
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
        raise Denied(Refusal.TIMEOUT,
                     "the sidecar did not finish within the allowed time") from None
    return {
        "returncode": process.returncode,
        "stdout": (stdout or b"").decode("utf-8", "replace"),
        "stderr": (stderr or b"").decode("utf-8", "replace"),
    }


def cleanup_job_files(service: FileIntelligenceService, job_id: str) -> None:
    with contextlib.suppress(OSError):
        shutil.rmtree(service.jobs_dir / job_id)
