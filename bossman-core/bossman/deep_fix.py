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
_WIN_DRIVE = __import__("re").compile(r"^[A-Za-z]:")

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


# Классы независимости верификатора относительно кодера (P0-02). Строковое
# сравнение alias'ов («verifier:qwen-14b» ≠ «qwen-14b») независимостью НЕ является.
INDEPENDENCE_CLASSES = ("same_run", "same_model", "cross_model", "external_tool", "human")
INDEPENDENT_CLASSES = frozenset({"cross_model", "external_tool", "human"})
EVIDENCE_TTL_S = 6 * 3600     # свежесть наблюдения по умолчанию


@dataclass(frozen=True, slots=True)
class Principal:
    """Типизированная идентичность участника: кто именно наблюдал/правил."""
    principal_id: str
    model_id: str = ""
    role: str = ""               # coder | verifier | human | tool
    run_id: str = ""
    independence_class: str = "same_run"

    def __post_init__(self) -> None:
        if not self.principal_id:
            raise ValueError("principal_id required")
        if self.independence_class not in INDEPENDENCE_CLASSES:
            raise ValueError(f"unknown independence_class {self.independence_class!r}")

    def independent_of(self, other: "Principal") -> tuple[bool, str]:
        """Независим ⇔ другой principal, другой run, другая модель/инструмент/человек."""
        if self.principal_id == other.principal_id:
            return False, "same principal"
        if self.run_id and other.run_id and self.run_id == other.run_id:
            return False, "same run"
        if self.independence_class not in INDEPENDENT_CLASSES:
            return False, f"independence_class {self.independence_class} is not independent"
        if self.independence_class == "cross_model" and self.model_id and self.model_id == other.model_id:
            return False, "same model execution claimed as cross_model"
        return True, "independent"


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: str            # repro | test | variant | regression | observation
    detail: str
    passed: bool
    source: str = ""     # кто/что наблюдало (pytest, verifier:<name>, human)
    at: float = field(default_factory=time.time)      # observed_at
    collected_at: float = 0.0                         # когда попало в ledger (>= at)
    task_id: str = ""
    run_id: str = ""
    principal_id: str = ""
    environment: str = ""                             # env/session fingerprint
    head_sha: str = ""                                # HEAD/patch/plan binding
    ttl_s: float = EVIDENCE_TTL_S
    expected: str = ""
    actual: str = ""

    def freshness_error(self, *, run: "DeepFixRun", now: float | None = None) -> str:
        """Пустая строка = свежее и привязанное; иначе причина отказа."""
        now = time.time() if now is None else now
        if not self.source.strip():
            return "evidence without source"
        if not self.at or self.at <= 0:
            return "evidence without observed_at"
        if self.collected_at and self.collected_at < self.at:
            return "collected_at before observed_at"
        if self.task_id and self.task_id != run.task_id:
            return f"evidence for another task {self.task_id!r}"
        if not self.task_id:
            return "evidence not bound to task_id"
        if run.run_id and self.run_id != run.run_id:
            return f"evidence for another run {self.run_id!r}"
        if run.head_sha and self.head_sha != run.head_sha:
            return f"evidence bound to another head {self.head_sha[:12]!r}"
        if run.environment and self.environment != run.environment:
            return "evidence from another environment/session"
        if run.plan_bound_at and self.at < run.plan_bound_at:
            return "evidence observed before the plan was bound"
        if run.patched_at and self.at < run.patched_at:
            return "evidence observed before the patch was applied"
        if now - self.at > self.ttl_s:
            return "evidence older than its TTL"
        if not self.expected or not self.actual:
            return "evidence without expected/actual"
        return ""


@dataclass(slots=True)
class DeepFixRun:
    task_id: str
    coder: str                              # агент/модель, который правит код (display)
    allowed_paths: tuple[str, ...] = ()     # область патча (repo-relative префиксы/глобы)
    repo_root: str = ""                     # если задан — canonical containment через realpath
    run_id: str = ""
    head_sha: str = ""
    environment: str = ""
    coder_principal: Principal | None = None
    plan_bound_at: float = 0.0
    patched_at: float = 0.0
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
    verifier_principal: Principal | None = None
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
        self.plan_bound_at = time.time()
        self._to("FIX_PLANNED")

    def patched(self, files_changed: list[str]) -> None:
        self._require("FIX_PLANNED")
        if not files_changed:
            raise DeepFixGateError("no files changed")
        outside = [f for f in files_changed if not self._allowed(f)]
        if outside:
            raise DeepFixGateError(f"patch touches files outside the declared scope: {outside}")
        self.files_changed = list(files_changed)
        self.patched_at = time.time()
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

    def verified(self, *, verifier: Principal, evidence: Evidence, now: float | None = None) -> None:
        """Только независимый верификатор (typed Principal, не строка) со СВЕЖИМ
        наблюдением, привязанным к task/run/head/env и собранным ПОСЛЕ патча и
        привязки плана. Строковый alias, at=0, пустой source, чужой run или старый
        HEAD → отказ (никогда не VERIFIED)."""
        self._require("REGRESSION_TESTED")
        if not isinstance(verifier, Principal):
            raise DeepFixGateError("verifier must be a typed Principal, not a display string")
        coder = self.coder_principal or Principal(principal_id=self.coder, role="coder",
                                                  run_id=self.run_id)
        ok, why = verifier.independent_of(coder)
        if not ok:
            raise DeepFixGateError(f"verifier is not independent of the coder: {why}")
        if evidence.kind != "observation":
            raise DeepFixGateError("verification needs a fresh observation, not a claim")
        if evidence.principal_id and evidence.principal_id != verifier.principal_id:
            raise DeepFixGateError("evidence was observed by a different principal than the verifier")
        stale = evidence.freshness_error(run=self, now=now)
        if stale:
            raise DeepFixGateError(f"evidence not fresh/bound: {stale}")
        if not evidence.passed:
            self.fail("VERIFICATION_FAILED", evidence.detail)
            return
        self.verifier, self.verification = verifier.principal_id, evidence
        self.verifier_principal = verifier
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
            "verifiers": ([{"principal_id": self.verifier_principal.principal_id,
                            "model_id": self.verifier_principal.model_id,
                            "role": self.verifier_principal.role,
                            "run_id": self.verifier_principal.run_id,
                            "independence_class": self.verifier_principal.independence_class}]
                          if self.verifier_principal else []),
            "evidence_records": ([{"observed_at": self.verification.at,
                                   "collected_at": self.verification.collected_at or self.verification.at,
                                   "task_id": self.verification.task_id, "run_id": self.verification.run_id,
                                   "source": self.verification.source,
                                   "principal_id": self.verification.principal_id,
                                   "environment": self.verification.environment,
                                   "head_sha": self.verification.head_sha,
                                   "expected": self.verification.expected,
                                   "actual": self.verification.actual}]
                                 if self.verification else []),
            "run_id": self.run_id, "principal_id": (self.coder_principal.principal_id
                                                    if self.coder_principal else self.coder),
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
        """Canonical repo-relative containment (P0-01).

        Отказ: абсолютные пути (POSIX/Windows drive/UNC), любой `..` после
        нормализации разделителей, пустые/точечные пути. Сравнение — по
        нормализованным компонентам (не по лексическому relative_to с `..`).
        При заданном repo_root дополнительно: realpath файла (и его родителя,
        если файла ещё нет) обязан лежать внутри realpath(root)/allowed — symlink
        наружу отвергается."""
        raw = str(path or "").replace("\\", "/")
        if not raw.strip() or "\x00" in raw:
            return False
        if raw.startswith("/") or raw.startswith("//") or _WIN_DRIVE.match(raw):
            return False
        parts = [c for c in raw.split("/") if c not in ("", ".")]
        if not parts or any(c == ".." for c in parts):
            return False
        rel = PurePosixPath(*parts)
        if self.allowed_paths:
            allowed = False
            for pat in self.allowed_paths:
                pp = PurePosixPath(str(pat).replace("\\", "/").rstrip("/"))
                if not str(pp) or any(c == ".." for c in pp.parts):
                    continue
                if rel == pp or pp in rel.parents or rel.match(str(pp)):
                    allowed = True
                    break
            if not allowed:
                return False
        if self.repo_root:
            root = Path(self.repo_root).resolve()
            target = (root / rel)
            probe = target if target.exists() or target.is_symlink() else target.parent
            try:
                real = probe.resolve(strict=False)
            except OSError:
                return False
            if real != root and root not in real.parents:
                return False
            if target.is_symlink():          # сам файл — symlink наружу
                try:
                    tr = target.resolve(strict=True)
                except OSError:
                    return False
                if tr != root and root not in tr.parents:
                    return False
        return True

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
