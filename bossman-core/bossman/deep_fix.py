"""Deep Fix Mode — дисциплина глубокого исправления для локальных coding-агентов.

Флаг: BOSSMAN_DEEP_FIX_ENABLED (по умолчанию OFF). При OFF модуль инертен —
ни один продакшн-путь его не вызывает; `enabled()` возвращает False.

Конвейер (DEEP_FIX_MODE_SPEC):
  RECEIVED → CONTEXT_READY → REPRODUCED → ROOT_CAUSE_PROPOSED → FIX_PLANNED →
  PATCHED → FOCUSED_TESTED → ADVERSARIAL_TESTED → REGRESSION_TESTED →
  VERIFIED → LEARNING_RECORDED → DONE
Состояния отказа: BLOCKED_ENV, NOT_REPRODUCIBLE, REGRESSION, VERIFICATION_FAILED,
OWNER_DECISION_REQUIRED.

Инварианты (гейты — код, не промпт):
  * REPRODUCED требует зафиксированный результат воспроизведения ДО патча
    (или явное `not_reproducible_reason` → NOT_REPRODUCIBLE, не «пропустить»);
  * PATCHED требует, чтобы изменённые файлы лежали в объявленной области
    (никакого «широкого диффа»: allowed_paths);
  * ADVERSARIAL_TESTED требует ≥1 вариант ПОСЛЕ патча;
  * VERIFIED ставит только независимый верификатор (verifier != coder) с
    результатом свежего наблюдения; кодер не может сам себя сертифицировать;
  * LEARNING_RECORDED порождает запись learning trace автоматически, статус
    записи = VERIFIED только если состояние VERIFIED.
Модуль не исполняет команды и не вызывает модели — это состояние и гейты,
которыми оркестратор (bcc engine / core runner) ведёт агента. Ничего из
существующих движков (Verification, Flight Recorder, Approval) не дублируется:
свежие наблюдения приходят как `Evidence` извне.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

FLAG = "BOSSMAN_DEEP_FIX_ENABLED"

STATES = ("RECEIVED", "CONTEXT_READY", "REPRODUCED", "ROOT_CAUSE_PROPOSED", "FIX_PLANNED",
          "PATCHED", "FOCUSED_TESTED", "ADVERSARIAL_TESTED", "REGRESSION_TESTED", "VERIFIED",
          "LEARNING_RECORDED", "DONE")
FAILURE_STATES = ("BLOCKED_ENV", "NOT_REPRODUCIBLE", "REGRESSION", "VERIFICATION_FAILED",
                  "OWNER_DECISION_REQUIRED")
_ORDER = {s: i for i, s in enumerate(STATES)}


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


class DeepFixGateError(RuntimeError):
    """Переход запрещён гейтом — с причиной."""


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: str            # repro | test | variant | regression | observation
    detail: str
    passed: bool
    source: str = ""     # кто/что наблюдало (pytest, verifier:<name>, human)
    at: float = field(default_factory=time.time)


@dataclass(slots=True)
class DeepFixRun:
    task_id: str
    coder: str                              # агент/модель, который правит код
    allowed_paths: tuple[str, ...] = ()     # область патча (posix-глобы/префиксы)
    state: str = "RECEIVED"
    history: list[tuple[str, str, float]] = field(default_factory=list)   # (from, to, ts)
    context: dict[str, Any] = field(default_factory=dict)
    repro_before: Evidence | None = None
    not_reproducible_reason: str = ""
    hypotheses: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    root_cause: str = ""
    plan: str = ""
    files_changed: list[str] = field(default_factory=list)
    focused: list[Evidence] = field(default_factory=list)
    variants: list[Evidence] = field(default_factory=list)
    regression: Evidence | None = None
    verification: Evidence | None = None
    verifier: str = ""
    failure_reason: str = ""

    # ------------------------------------------------------------ helpers
    def _to(self, new: str) -> None:
        self.history.append((self.state, new, time.time()))
        self.state = new

    def _require(self, state: str) -> None:
        if self.state != state:
            raise DeepFixGateError(f"expected state {state}, got {self.state}")

    def fail(self, state: str, reason: str) -> None:
        if state not in FAILURE_STATES:
            raise DeepFixGateError(f"unknown failure state {state}")
        self.failure_reason = reason
        self._to(state)

    # ------------------------------------------------------------ pipeline
    def context_ready(self, *, repo_map: list[str], targeted: list[str]) -> None:
        self._require("RECEIVED")
        if not targeted:
            raise DeepFixGateError("targeted context is empty — no files selected")
        self.context = {"repo_map": list(repo_map), "targeted": list(targeted)}
        self._to("CONTEXT_READY")

    def reproduced(self, evidence: Evidence | None, *, not_reproducible_reason: str = "") -> None:
        """Воспроизведение ДО патча. evidence.passed=True означает «баг воспроизведён».
        Нет воспроизведения → только явный NOT_REPRODUCIBLE с причиной."""
        self._require("CONTEXT_READY")
        if evidence is None or not evidence.passed:
            if not not_reproducible_reason:
                raise DeepFixGateError("reproduction missing: provide evidence or a documented "
                                       "not_reproducible_reason")
            self.not_reproducible_reason = not_reproducible_reason
            self.fail("NOT_REPRODUCIBLE", not_reproducible_reason)
            return
        if evidence.kind != "repro":
            raise DeepFixGateError("reproduction evidence must have kind='repro'")
        self.repro_before = evidence
        self._to("REPRODUCED")

    def root_cause_proposed(self, hypotheses: list[str], rejected: list[str], root_cause: str) -> None:
        self._require("REPRODUCED")
        if len(hypotheses) < 1 or not root_cause:
            raise DeepFixGateError("need at least one hypothesis and a root cause")
        if root_cause not in hypotheses:
            raise DeepFixGateError("root cause must be one of the considered hypotheses")
        self.hypotheses, self.rejected, self.root_cause = list(hypotheses), list(rejected), root_cause
        self._to("ROOT_CAUSE_PROPOSED")

    def fix_planned(self, plan: str) -> None:
        self._require("ROOT_CAUSE_PROPOSED")
        if not plan.strip():
            raise DeepFixGateError("empty plan")
        self.plan = plan
        self._to("FIX_PLANNED")

    def patched(self, files_changed: list[str]) -> None:
        self._require("FIX_PLANNED")
        if not files_changed:
            raise DeepFixGateError("no files changed")
        outside = [f for f in files_changed if not self._allowed(f)]
        if outside:
            raise DeepFixGateError(f"patch touches files outside the declared scope: {outside}")
        self.files_changed = list(files_changed)
        self._to("PATCHED")

    def focused_tested(self, evidence: list[Evidence]) -> None:
        self._require("PATCHED")
        tests = [e for e in evidence if e.kind == "test"]
        if not tests:
            raise DeepFixGateError("focused tests missing")
        if not all(e.passed for e in tests):
            self.fail("REGRESSION", "focused tests failing after patch")
            return
        self.focused = tests
        self._to("FOCUSED_TESTED")

    def adversarial_tested(self, repro_after: Evidence, variants: list[Evidence]) -> None:
        """Оригинальное воспроизведение ПОСЛЕ патча должно быть passed=False
        (баг больше не воспроизводится) и ≥1 вариант с тем же результатом."""
        self._require("FOCUSED_TESTED")
        if repro_after.kind != "repro":
            raise DeepFixGateError("repro_after must have kind='repro'")
        if repro_after.passed:
            self.fail("VERIFICATION_FAILED", "original reproduction still succeeds after patch")
            return
        vs = [v for v in variants if v.kind == "variant"]
        if not vs:
            raise DeepFixGateError("at least one adversarial variant required after patch")
        if any(v.passed for v in vs):
            self.fail("VERIFICATION_FAILED", "an adversarial variant still reproduces the bug")
            return
        self.variants = [repro_after] + vs
        self._to("ADVERSARIAL_TESTED")

    def regression_tested(self, evidence: Evidence) -> None:
        self._require("ADVERSARIAL_TESTED")
        if evidence.kind != "regression":
            raise DeepFixGateError("regression evidence required")
        if not evidence.passed:
            self.fail("REGRESSION", evidence.detail)
            return
        self.regression = evidence
        self._to("REGRESSION_TESTED")

    def verified(self, *, verifier: str, evidence: Evidence) -> None:
        """Только независимый верификатор (не coder) со свежим наблюдением."""
        self._require("REGRESSION_TESTED")
        if not verifier or verifier.strip().lower() == self.coder.strip().lower():
            raise DeepFixGateError("verifier must be independent of the coder (no self-certification)")
        if evidence.kind != "observation":
            raise DeepFixGateError("verification needs a fresh observation, not a claim")
        if not evidence.passed:
            self.fail("VERIFICATION_FAILED", evidence.detail)
            return
        self.verifier, self.verification = verifier, evidence
        self._to("VERIFIED")

    def learning_record(self, *, model: str, start_sha: str, end_sha: str,
                        task: str, symptom: str, **extra: Any) -> dict:
        """Запись learning trace (dict по schemas/learning_fix_case.schema.json).
        Статус VERIFIED только из состояния VERIFIED; иначе PARTIAL/FAILED_EXPERIMENT."""
        if self.state == "VERIFIED":
            status = "VERIFIED"
        elif self.state in ("REGRESSION", "VERIFICATION_FAILED"):
            status = "FAILED_EXPERIMENT"
        else:
            status = "PARTIAL"
        rec = {
            "task_id": self.task_id, "model": model, "agent": self.coder,
            "start_sha": start_sha, "end_sha": end_sha, "task": task, "symptom": symptom,
            "reproduction": [self.repro_before.detail] if self.repro_before else [self.not_reproducible_reason],
            "evidence": [e.detail for e in ([self.repro_before] if self.repro_before else [])
                         + self.focused + self.variants
                         + ([self.regression] if self.regression else [])
                         + ([self.verification] if self.verification else [])],
            "root_cause_hypotheses": list(self.hypotheses), "rejected_hypotheses": list(self.rejected),
            "root_cause": self.root_cause, "relevant_code_paths": list(self.context.get("targeted", [])),
            "fix_strategy": self.plan, "alternatives_considered": list(extra.pop("alternatives_considered", [])),
            "why_this_fix": str(extra.pop("why_this_fix", self.plan)),
            "files_changed": list(self.files_changed),
            "tests_added": [e.detail for e in self.focused],
            "original_repro_result": (self.variants[0].detail if self.variants else "n/a"),
            "adversarial_variants": [v.detail for v in self.variants[1:]],
            "regression_result": self.regression.detail if self.regression else "n/a",
            "external_verification": self.verification.detail if self.verification else "",
            "failure_recovery_lessons": [self.failure_reason] if self.failure_reason else [],
            "generalizable_lessons": list(extra.pop("generalizable_lessons", [])),
            "teach_local_model": list(extra.pop("teach_local_model", [])),
            "confidence": float(extra.pop("confidence", 0.7 if status == "VERIFIED" else 0.3)),
            "limitations": list(extra.pop("limitations", [])),
            "verified_by": [self.verifier] if self.verifier else [],
            "learning_status": status,
            "outcome": "FIXED" if status == "VERIFIED" else ("PARTIAL" if status == "PARTIAL" else "REJECTED"),
        }
        rec.update({k: v for k, v in extra.items() if k in ("tags", "finding_ids")})
        if self.state == "VERIFIED":
            self._to("LEARNING_RECORDED")
        return rec

    def done(self) -> None:
        self._require("LEARNING_RECORDED")
        self._to("DONE")

    # ------------------------------------------------------------ scope
    def _allowed(self, path: str) -> bool:
        if not self.allowed_paths:
            return True
        p = PurePosixPath(Path(path).as_posix())
        for pat in self.allowed_paths:
            pp = PurePosixPath(pat)
            if p == pp or pat.endswith("/") and str(p).startswith(pat) or p.match(pat):
                return True
            try:
                p.relative_to(pp)
                return True
            except ValueError:
                continue
        return False

    def summary(self) -> dict:
        return {"task_id": self.task_id, "state": self.state, "coder": self.coder,
                "verifier": self.verifier, "failure_reason": self.failure_reason,
                "transitions": len(self.history)}


def store_learning_record(record: dict) -> dict | None:
    """Сохранить запись через корневой пакет `learning` (repo root). Возвращает
    сохранённую запись или None, если пакет недоступен (core установлен отдельно)."""
    root = Path(__file__).resolve().parents[2]
    import sys
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from learning import LearningStore  # noqa: WPS433
    except Exception:  # noqa: BLE001
        return None
    return LearningStore().add(record, write_markdown=False)
