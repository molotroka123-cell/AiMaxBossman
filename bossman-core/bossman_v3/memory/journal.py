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
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

PENDING, DONE, FAILED = "PENDING", "DONE", "FAILED"


class JournalConflict(RuntimeError):
    """Попытка переписать уже закрытый (подписанный) шаг журнала."""


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

    @property
    def finished(self) -> bool:
        """Чек исполнения И подтверждение. Ни одного из двух по отдельности
        не хватает — это тот же контракт, что V2 защищает в gate_completion."""
        return self.receipt is not None and self.verified

    def signed_record(self, task_id: str) -> dict[str, Any]:
        return {"task_id": task_id, "step_id": self.step_id, "receipt": dict(self.receipt or {}),
                "verified": self.verified, "by": self.by, "updated_at": self.updated_at,
                "sig": self.sig, "signer": self.signer, "nonce": self.nonce, "issued_at": self.issued_at}

    def signature_valid(self, task_id: str) -> bool:
        from bossman_v3 import evidence as _signing
        return bool(self.sig) and _signing.verify_signed(self.signed_record(task_id))


@dataclass
class TaskJournal:
    task_id: str
    steps: list[JournalStep]
    notes: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    root: Path | None = None

    # ------------------------------------------------------------ lifecycle

    @classmethod
    def start(cls, *, task_id: str, plan: Sequence[tuple[str, str]], root: str | Path) -> "TaskJournal":
        j = cls(task_id=task_id,
                steps=[JournalStep(step_id=sid, intent=intent) for sid, intent in plan],
                root=Path(root))
        j._save()
        return j

    @classmethod
    def load(cls, *, task_id: str, root: str | Path) -> "TaskJournal":
        path = Path(root) / f"{task_id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(task_id=raw["task_id"],
                   steps=[JournalStep(**s) for s in raw["steps"]],
                   notes=list(raw.get("notes") or []),
                   created_at=raw.get("created_at", _now()),
                   root=Path(root))

    def _save(self) -> None:
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"task_id": self.task_id, "created_at": self.created_at,
                   "steps": [asdict(s) for s in self.steps], "notes": self.notes}
        (self.root / f"{self.task_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    # -------------------------------------------------------------- writing

    def record(self, step_id: str, *, receipt: Mapping[str, Any] | None = None,
               verified: bool = False, by: str = "", note: str = "") -> JournalStep:
        for i, s in enumerate(self.steps):
            if s.step_id != step_id:
                continue
            if s.finished:
                # TRUTH-003 §12: закрытый шаг не переписывается — ни зомби-воркером,
                # ни повтором. Существующая подписанная запись остаётся истиной.
                raise JournalConflict(f"step {step_id!r} of {self.task_id!r} is already finished; "
                                      f"refusing to overwrite a signed receipt")
            done = receipt is not None and verified
            new = replace(s, receipt=dict(receipt) if receipt else None,
                          verified=bool(verified), by=by, note=note,
                          status=DONE if done else s.status, updated_at=_now(),
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

    def remaining(self) -> list[JournalStep]:
        return [s for s in self.steps if not s.finished]
