"""Durable, model-independent task journal (V3.1).

Почему не `task_runs.checkpoint` из V2: тот чекпоинт хранит `messages` —
транскрипт КОНКРЕТНОЙ модели. Его достаточно, чтобы продолжить тем же
раннером, и недостаточно для двух вещей, ради которых существует V3.1:
смены модели (чужой транскрипт для неё — мусор и лишние токены) и
переполнения контекста (транскрипт растёт, а состояние задачи — нет).

Здесь состояние возобновления — это ПЛАН и ЧЕКИ ИСПОЛНЕНИЯ, а не переписка:
что за шаг, сделан ли он, чем это доказано. Такое состояние одинаково читается
любой моделью и не растёт от многословности.

Инвариант V2 перенесён дословно: шаг считается сделанным только когда есть чек
исполнения И подтверждение. Заявление модели «готово» шагом не закрывает —
поэтому после рестарта незакрытый шаг будет доделан, а не пропущен.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

PENDING, DONE, FAILED, STARTED = "PENDING", "DONE", "FAILED", "STARTED"


class JournalIntegrityError(ValueError):
    """Existing execution state must be reconciled, never silently replayed."""


def journal_path(root: str | Path, task_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,219}", task_id):
        raise JournalIntegrityError("invalid journal identifier")
    stem = task_id.split(".")[0].upper()
    if task_id.endswith(".") or stem in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1,10)],
                                        *[f"LPT{i}" for i in range(1,10)]}:
        raise JournalIntegrityError("reserved journal identifier")
    safe_task_id(task_id)
    base = Path(root).resolve()
    path = base / f"{task_id}.json"
    if path.is_symlink() or path.resolve().parent != base:
        raise JournalIntegrityError("journal path escapes storage root")
    return path


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class JournalConflict(RuntimeError):
    """Попытка переписать уже закрытый (подписанный) шаг журнала."""


_TASK_ID_BAD = ("/", "\\", "\x00")


def safe_task_id(task_id: str) -> str:
    """Идентификатор журнала — имя файла внутри root. Разделители путей, `..`, пустая
    строка и слишком длинные значения отвергаются (Astra-аудит: journal path traversal)."""
    tid = str(task_id or "")
    if not tid or tid in (".", "..") or any(ch in tid for ch in _TASK_ID_BAD) or ".." in tid or len(tid) > 200:
        raise JournalIntegrityError(f"unsafe journal task_id {task_id!r}: must be a plain name without path separators or '..'")
    return tid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JournalStep:
    step_id: str
    intent: str
    status: str = PENDING
    receipt: Mapping[str, Any] | None = None
    verified: bool = False
    by: str = ""
    note: str = ""
    updated_at: str = ""
    # EH-01: подпись закрытого шага (receipt ∧ verified) ключом процесса.
    sig: str = ""
    signer: str = ""
    nonce: str = ""
    issued_at: str = ""
    task_binding: str = ""
    action_digest: str = ""
    attempt_id: str = ""
    effect_key: str = ""
    in_flight: bool = False
    execution_binding: dict[str, Any] = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        """Чек исполнения И подтверждение. Ни одного из двух по отдельности
        не хватает — это тот же контракт, что V2 защищает в gate_completion."""
        return (self.status == DONE and self.receipt is not None and self.verified
                and bool(self.task_binding) and self.signature_valid(self.task_binding))

    def signed_record(self, task_id: str) -> dict[str, Any]:
        return {"task_id": task_id, "step_id": self.step_id, "receipt": dict(self.receipt or {}),
                "verified": self.verified, "by": self.by, "updated_at": self.updated_at,
                "status": self.status, "intent": self.intent, "task_binding": self.task_binding,
                "action_digest": self.action_digest, "attempt_id": self.attempt_id,
                "effect_key": self.effect_key, "in_flight": self.in_flight,
                "execution_binding": dict(self.execution_binding),
                "sig": self.sig, "signer": self.signer, "nonce": self.nonce, "issued_at": self.issued_at}

    def signature_valid(self, task_id: str) -> bool:
        from bossman_v3 import evidence as _signing
        return (task_id == self.task_binding and bool(self.sig)
                and _signing.verify_signed(self.signed_record(task_id)))


@dataclass
class TaskJournal:
    task_id: str
    steps: list[JournalStep]
    notes: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    root: Path | None = None
    plan_digest: str = ""
    execution_binding: dict[str, Any] = field(default_factory=dict)

    _disk_digest: str | None = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------ lifecycle

    @classmethod
    def start(cls, *, task_id: str, plan: Sequence[tuple[str, str]], root: str | Path,
              plan_digest: str = "") -> "TaskJournal":
        task_id = safe_task_id(task_id)
        j = cls(task_id=task_id,
                steps=[JournalStep(step_id=sid, intent=intent, task_binding=task_id) for sid, intent in plan],
                root=Path(root))
        j._save()
        return j

    @classmethod
    def load(cls, *, task_id: str, root: str | Path) -> "TaskJournal":
        path = journal_path(root, task_id)
        data = path.read_bytes()
        raw = json.loads(data)
        if raw["task_id"] != task_id:
            raise JournalIntegrityError("journal task identity mismatch")
        j = cls(task_id=raw["task_id"],
                   steps=[JournalStep(**s) for s in raw["steps"]],
                   notes=list(raw.get("notes") or []),
                   created_at=raw.get("created_at", _now()),
                   root=Path(root), plan_digest=raw.get("plan_digest", ""),
                   execution_binding=dict(raw.get("execution_binding") or {}))
        j._disk_digest = hashlib.sha256(data).hexdigest()
        j.validate()
        return j

    def validate(self) -> None:
        if len({s.step_id for s in self.steps}) != len(self.steps):
            raise JournalIntegrityError("duplicate step identifiers")
        for s in self.steps:
            if (s.verified or s.status == DONE) and not s.signature_valid(self.task_id):
                raise JournalIntegrityError(f"invalid or legacy unsigned completion: {s.step_id}")

    def bind_plan(self, manifest: list[dict[str, Any]]) -> None:
        self.validate()
        new_digest = digest(manifest)
        if [s.step_id for s in self.steps] != [s["step_id"] for s in manifest]:
            raise JournalIntegrityError("plan step identity changed")
        if self.plan_digest and self.plan_digest != new_digest:
            raise JournalIntegrityError("action, expectation or policy changed; fresh plan required")
        if not self.plan_digest:
            if any(s.receipt is not None or s.status != PENDING for s in self.steps):
                raise JournalIntegrityError("legacy unbound execution requires reconciliation")
            self.plan_digest = new_digest
            self.steps = [replace(s, action_digest=digest(m), task_binding=self.task_id)
                          for s, m in zip(self.steps, manifest)]
            self._save()
        elif any(s.action_digest != digest(m) for s, m in zip(self.steps, manifest)):
            raise JournalIntegrityError("journal action binding mismatch")

    def begin(self, step_id: str, *, by: str = "") -> None:
        for i, s in enumerate(self.steps):
            if s.step_id == step_id:
                self.steps[i] = replace(s, status=STARTED, in_flight=True, by=by,
                                        attempt_id=uuid.uuid4().hex,
                                        effect_key=s.effect_key or uuid.uuid4().hex,
                                        updated_at=_now())
                self._save()  # durable intent BEFORE dispatch
                return
        raise KeyError(step_id)

    def _save(self) -> None:
        """Compare-and-swap under an exclusive, cross-process writer lock.

        A crash leaves the lock in place: explicit reconciliation is safer than
        replaying an irreversible effect from a stale journal.
        """
        if self.root is None:
            return
        path = journal_path(self.root, self.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = path.with_suffix(".lock")
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise JournalIntegrityError("journal writer active or interrupted; reconcile before retry") from exc
        try:
            os.write(fd, str(os.getpid()).encode())
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if current != self._disk_digest:
                raise JournalIntegrityError("journal changed or already exists; reload before writing")
            self._save_locked()
            self._disk_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        finally:
            os.close(fd)
            lock.unlink()

    def _save_locked(self) -> None:
        if self.root is None:
            return
        path = journal_path(self.root, self.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"task_id": self.task_id, "created_at": self.created_at,
                   "steps": [asdict(s) for s in self.steps], "notes": self.notes,
                   "plan_digest": self.plan_digest, "schema_version": 2,
                   "execution_binding": dict(self.execution_binding)}
        fd, temp = tempfile.mkstemp(prefix=".journal-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
            if os.name == "posix":
                dfd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    # -------------------------------------------------------------- writing

    def record(self, step_id: str, *, receipt: Mapping[str, Any] | None = None,
               verified: bool = False, by: str = "", note: str = "") -> JournalStep:
        for i, s in enumerate(self.steps):
            if s.step_id != step_id:
                continue
            if s.finished and s.signature_valid(self.task_id):
                # TRUTH-003 §12: ПОДПИСАННЫЙ закрытый шаг не переписывается — ни зомби-воркером,
                # ни повтором. Закрытый без валидной подписи (подделка/битый файл) истиной не
                # является и заменяется честной подписанной записью (ASTRA-002).
                raise JournalConflict(f"step {step_id!r} of {self.task_id!r} is already finished; "
                                      f"refusing to overwrite a signed receipt")
            done = receipt is not None and verified
            new = replace(s, receipt=dict(receipt) if receipt else None,
                          verified=bool(done), by=by, note=note,
                          execution_binding=dict(self.execution_binding),
                          status=DONE if done else s.status, updated_at=_now(),
                          task_binding=self.task_id, in_flight=False if done else s.in_flight,
                          sig="", signer="", nonce="", issued_at="")
            if done:
                # EH-01: закрытый шаг подписывается здесь и только здесь.
                from bossman_v3 import evidence as _signing
                f = _signing.sign_fields(new.signed_record(self.task_id), signer=_signing.JOURNAL_SIGNER)
                new = replace(new, **f)
            self.steps[i] = new
            self._save()
            return self.steps[i]
        raise KeyError(f"шага {step_id!r} нет в плане задачи {self.task_id!r}")

    def fail(self, step_id: str, *, error: str, by: str = "") -> JournalStep:
        for i, s in enumerate(self.steps):
            if s.step_id == step_id:
                self.steps[i] = replace(s, status=FAILED, note=error, by=by,
                                        verified=False, updated_at=_now())
                self._save()
                return self.steps[i]
        raise KeyError(step_id)

    def note(self, text: str, *, source: str = "run") -> None:
        self.notes.append({"text": text, "source": source, "at": _now()})
        self._save()

    # -------------------------------------------------------------- reading

    def next_step(self) -> JournalStep | None:
        """Первый НЕзакрытый шаг. Именно он — ответ на вопрос «с чего
        продолжить после рестарта»; сделанное не переигрывается."""
        return next((s for s in self.steps if not s.finished), None)

    def finished(self) -> list[JournalStep]:
        return [s for s in self.steps if s.finished]

    def finished_signed(self) -> list[JournalStep]:
        """ASTRA-002: закрытым для возобновления считается только шаг с валидной подписью —
        флаги `receipt/verified` в файле без подписи не пропускают работу."""
        return [s for s in self.steps if s.finished and s.signature_valid(self.task_id)]

    def remaining(self) -> list[JournalStep]:
        return [s for s in self.steps if not s.finished]
