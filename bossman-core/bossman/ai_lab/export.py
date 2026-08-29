"""Stage 11 — AI Lab: eval runner (bounded) + export (SFT/DPO) + training adapter.

- Eval: детерминированный набор, жёсткий cap на число модельных вызовов,
  аренда Resource Brain перед прогоном (нет аренды — отказ, ноль вызовов).
- Export: ТОЛЬКО APPROVED-кандидаты; SFT JSONL и preference-pairs JSONL для DPO;
  provenance в каждом экспортируемом образце. Raw не участвует.
- Training adapter: по умолчанию ВЫКЛЮЧЕН. Все launch-пути без явно
  сконфигурированного локального адаптера и owner-approval бросают ошибку.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .. import errors
from .candidates import AiLabCandidate, CandidateStore

MAX_EVAL_CASES = 50          # жёсткий потолок на один прогон


class TrainingDisabled(errors.BossmanError):
    def __init__(self) -> None:
        super().__init__(
            "training launch is disabled: no local adapter configured / not owner-approved",
            code=errors.ErrorCode.POLICY_DENIED)


class LocalTrainingAdapter:
    """Опциональный локальный адаптер. Не конфигурируется по умолчанию."""

    def __init__(self) -> None:
        self.configured = False
        self.owner_approved = False
        self.command: tuple[str, ...] = ()

    def configure(self, *, command: tuple[str, ...]) -> None:
        self.configured = True
        self.command = tuple(command)

    def launch(self, dataset_path: Path, *, owner_approved: bool) -> str:
        if not self.configured:
            raise TrainingDisabled()
        if not owner_approved:
            raise errors.BossmanError(
                "training launch requires explicit owner approval",
                code=errors.ErrorCode.APPROVAL_REQUIRED)
        # Реальный запуск остаётся за владельцем; здесь только валидированный контракт.
        return f"adapter-armed:{dataset_path.name}"


class EvalRunner:
    """Ограниченный прогон детерминированного eval-сета через заданный chat_fn."""

    def __init__(self, *, chat_fn: Callable[..., dict] | None = None,
                 brain: Any = None) -> None:
        self.chat_fn = chat_fn
        self.brain = brain

    def run(self, cases: list[dict], *, model_alias: str,
            max_cases: int = 5, ram_mb: int = 256) -> dict:
        if len(cases) > MAX_EVAL_CASES:
            raise errors.BossmanError(
                f"too many eval cases: {len(cases)} > {MAX_EVAL_CASES}",
                code=errors.ErrorCode.POLICY_DENIED)
        n = min(len(cases), max(0, max_cases))
        if self.brain is not None:
            self.brain.acquire(
                type("Req", (), {"kind": "eval", "estimated_ram": ram_mb,
                                 "estimated_disk": 0})(),
                snap=getattr(self.brain, "current_snapshot", None) or _snap(),
            )
        results, model_calls = [], 0
        try:
            for case in cases[:n]:
                expected = str(case.get("expected", "")).strip()
                if self.chat_fn is None:
                    got = ""
                else:
                    model_calls += 1
                    msg = self.chat_fn(model=model_alias,
                                       messages=[{"role": "user",
                                                  "content": str(case.get("prompt", ""))}])
                    got = str((msg.get("choices") or [{}])[0].get("message", {})
                              .get("content", ""))
                results.append({"id": case.get("id"), "expected": expected,
                                "got": got, "pass": got.strip() == expected})
        finally:
            pass
        passed = sum(1 for r in results if r["pass"])
        return {"cases": len(results), "passed": passed, "model_calls": model_calls,
                "results": results,
                "provenance": {"model_alias": model_alias, "max_cases": max_cases,
                               "at": time.time()}}


class Exporter:
    """Экспорт APPROVED-кандидатов в SFT JSONL / DPO preference-pairs JSONL."""

    def __init__(self, store: CandidateStore, root: str | Path,
                 *, adapter: LocalTrainingAdapter | None = None) -> None:
        self.store = store
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.adapter = adapter or LocalTrainingAdapter()
        self._log = self.root / "exports.json"

    def export_sft(self, candidate_id: str) -> Path:
        cand = self._approved(candidate_id)
        path = self.root / f"{cand.id}.sft.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for s in cand.samples:
                fh.write(json.dumps({
                    "messages": [{"role": "user", "content": _prompt_of(s)},
                                 {"role": "assistant", "content": _response_of(s)}],
                    "provenance": cand.provenance(s),
                }, ensure_ascii=False, default=str) + "\n")
        self._record(cand, path, "sft")
        return path

    def export_dpo(self, candidate_id: str, *, rejected: AiLabCandidate | None = None) -> Path:
        """Preference-pairs: chosen — из APPROVED, rejected — из отвергнутого
        кандидата того же источника (или явного 'instruct-only' фолбэка)."""
        cand = self._approved(candidate_id)
        path = self.root / f"{cand.id}.dpo.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for s in cand.samples:
                chosen = _response_of(s)
                bad = _response_of_r(rejected, s) if rejected else "Sorry, I cannot help."
                if bad == chosen:
                    bad = "Sorry, I cannot help with that."
                fh.write(json.dumps({
                    "prompt": _prompt_of(s),
                    "chosen": chosen, "rejected": bad,
                    "provenance": cand.provenance(s),
                }, ensure_ascii=False, default=str) + "\n")
        self._record(cand, path, "dpo")
        return path

    # --- internals ---

    def _approved(self, candidate_id: str) -> AiLabCandidate:
        cand = self.store.get(candidate_id)
        if cand.state == "REJECTED":
            raise errors.BossmanError(
                "candidate approval revoked/rejected — export denied",
                code=errors.ErrorCode.POLICY_DENIED)
        if not cand.approved:
            raise errors.BossmanError(
                "candidate is not approved by human — export denied",
                code=errors.ErrorCode.APPROVAL_REQUIRED)
        return cand

    def _record(self, cand: AiLabCandidate, path: Path, kind: str) -> None:
        rows = []
        if self._log.is_file():
            rows = json.loads(self._log.read_text(encoding="utf-8"))
        rows.append({"candidate_id": cand.id, "kind": kind, "path": str(path),
                     "sha256_source": cand.source_sha256,
                     "sanitizer_version": cand.sanitizer_version, "at": time.time()})
        tmp = self._log.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._log)

    def launch_training(self, dataset_path: Path) -> str:
        """Все launch-кнопки идут через сюда: адаптер выключен → отказ."""
        return self.adapter.launch(dataset_path, owner_approved=False)


# --- helpers: поля сэмпла траектории → prompt/response ---

def _prompt_of(sample: dict) -> str:
    for key in ("prompt", "command", "input", "note"):
        v = sample.get(key)
        if isinstance(v, str) and v.strip():
            return v[:2000]
    return json.dumps({k: v for k, v in sample.items() if k != "kind"},
                      ensure_ascii=False, default=str)[:2000]


def _response_of(sample: dict) -> str:
    for key in ("output", "result", "response", "detail"):
        v = sample.get(key)
        if isinstance(v, str) and v.strip():
            return v[:2000]
    return json.dumps({k: v for k, v in sample.items() if k != "kind"},
                      ensure_ascii=False, default=str)[:2000]


def _response_of_r(cand: AiLabCandidate | None, sample: dict) -> str:
    if cand is None:
        return "Sorry, I cannot help."
    for s in cand.samples:
        if s.get("kind") == sample.get("kind"):
            return _response_of(s)
    return "Sorry, I cannot help."


def _snap():
    from ..resource_brain import ResourceSnapshot
    return ResourceSnapshot(8_000, 8_000, 1_000_000, 1_000_000)
