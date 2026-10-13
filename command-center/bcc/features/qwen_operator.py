"""Stage 2.2 - the local uncensored Qwen as an L1-Lab OPERATOR of Bossman (owner 2026-10-13).

The owner writes a task in the pult (``/work <text>``); the operator plans, uses free cloud models where
useful, rewrites Bossman source INSIDE an isolated lab worktree, runs the tests, commits on a ``lab/*`` branch
and reports receipts. No second core: it composes what already exists -

* the registry (models, providers, governed free-only adapters, agents table, task engine),
* the coding-task sidecar (``features.coding_tasks``: disposable clone, tool loop, Bossman's own test verdict),
* the autonomy policy (``bcc.autonomy.policy``: path/argv scope, protected globs, always-the-user list),
* the approvals queue (one owner decision per parked step).

Authority model (constitution v1 stays in force; nothing here edits, pins or bypasses it):

* inside the lab the operator may read, write, run tests and commit - broad on purpose;
* an owner-decision item (money, publication/messages, keys, deleting data, disabling safety, constitution /
  approval-gate files, force-push, deployment, push/merge) does NOT hard-fail and is NOT silently skipped: the
  step is parked, one approvals row carries the exact action + why + recommendation + concerns, the other steps
  continue, and the owner answers Approve/Deny (pult buttons). Approval is the owner's decision; what the
  operator can still not do is recorded in the decision outcome (``APPROVED_BLOCKED_BY_RULE_4`` ...);
* nothing is pushed, merged or applied to the canonical project: ``promote`` is refused and says what the
  owner does (constitution v1 rule 4: Claude + Codex approve the same SHA, the owner presses Release);
* the owner's STOP files stop the operator between steps - the owner's kill-switch, not a limit on the model;
* self-correction is bounded: at most ``MAX_ATTEMPTS`` per error, mandatory verification, rollback when worse.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import Feature

router = APIRouter()

OPERATOR_ROLE = "operator:l1-lab"
WORKER_ROLE = "operator:worker"
OPERATOR_NAME = "Qwen-Operator L1 (uncensored)"
LAB_BRANCH_PREFIX = "lab/"
MAX_ATTEMPTS = 3
OLLAMA = "http://127.0.0.1:11434"
CTX_256K = 262144
DEFAULT_DECISION_WAIT_S = None      # no deadline: wait for the owner until STOP / cancel (0 = do not wait)
DECISION_KIND = "operator_decision"
TERMINAL = ("completed", "completed_with_pending_decisions", "failed", "stopped")

LOCAL_UNCENSORED = ("bossman-community-qwen-uncensored:latest", "bossman-uncensored-chat:latest")

#: Defaults only; the owner reconfigures through POST /api/qwen-operator/config.
DEFAULT_ROLES: dict[str, dict[str, Any]] = {
    # owner 2026-10-13: ALL operator roles default to the uncensored Qwen (the first of these Ollama has)
    "planner": {"model": LOCAL_UNCENSORED[0], "num_ctx": CTX_256K},
    "coder": {"model": LOCAL_UNCENSORED[0], "num_ctx": CTX_256K},
    "diagnostician": {"model": LOCAL_UNCENSORED[0], "num_ctx": CTX_256K},
    # cloud, free, a different family than the planner/coder (qwen): picked from the registry
    "verifier": {"prefer": ["nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia/nemotron-3-ultra-550b-a55b"],
                 "avoid_family": "qwen"},
}

#: Written by the operator only in the lab, and even there never these: the constitution, the approval gate,
#: budgets / STOP / vault isolation and the operator's own scope guard. Extra to autonomy PROTECTED_GLOBS.
OPERATOR_EXTRA_PROTECTED = (
    "command-center/bcc/approvals.py", "command-center/bcc/approval_scope.py", "command-center/bcc/permissions.py",
    "command-center/bcc/secrets.py", "command-center/bcc/tools.py", "command-center/bcc/features/tools_terminal.py",
    "command-center/bcc/mission_budget.py", "command-center/bcc/fable_cap.py", "command-center/bcc/long_run.py",
    "command-center/bcc/provider_governance.py", "command-center/bcc/features/qwen_operator.py",
    "command-center/bcc/telegram_companion/operator_bridge.py",
    "command-center/bcc/telegram_companion/agent_bridge.py", "command-center/bcc/telegram_companion/secret_intake.py",
)
SECRET_READ_NAMES = ("provider-keys.env", ".env", "constitution.sha256", "bcc.db", "vault.key", "secrets.json")
PROMOTION_GIT = frozenset({"push", "merge", "pull", "rebase", "cherry-pick", "remote", "tag", "am", "fetch"})

RELEASE_STEPS_RU = (
    "Продвижение кандидата в основной проект делает только владелец (конституция v1, правило 4): "
    "1) посмотреть diff коммита на ветке lab/*; 2) Claude и Codex одобряют ОДИН и тот же SHA; "
    "3) владелец нажимает Release (например POST /api/coding-tasks/{id}/apply с одобрением или ручной merge). "
    "Оператор не может ни push, ни merge, ни apply.")


# ---------------------------------------------------------------- scope guard
@dataclass(frozen=True)
class Verdict:
    kind: str                  # allow | ask | refuse
    category: str = ""         # what kind of owner decision / refusal
    reason: str = ""
    executable_after_approval: bool = False


def _policy(worktree: Path, extra_protected: tuple[Path, ...] = ()):
    from ..autonomy.policy import PROTECTED_GLOBS, Policy, Scope
    scope = Scope(worktree=Path(worktree), protected_globs=PROTECTED_GLOBS + OPERATOR_EXTRA_PROTECTED,
                  extra_protected=extra_protected)
    return Policy(scope, constitution_status=lambda: None, level="L1")


class OperatorScope:
    """The only place that decides what the operator may touch. Pure and offline: tests use it with fakes.

    The autonomy ``Policy.check`` is NOT used as a whole: its first rule is the constitution pin, which
    blocks the autonomous LOOP. The operator is owner-directed (one owner command = one task, no goal queue),
    so only the policy's scope pieces are reused (path resolution, protected globs, argv allowlist)."""

    def __init__(self, worktree: Path, *, data_dir: Path | None = None):
        self.worktree = Path(worktree).resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir else None
        # writes are confined to the worktree (which lives under the data dir), so the data dir itself is not
        # listed as protected here; secrets are guarded by check_read and the child env has none
        self.policy = _policy(self.worktree)

    # ---- files
    def check_read(self, raw: str) -> Verdict:
        p = Path(raw)
        if p.name.lower() in SECRET_READ_NAMES or any(part.lower() in ("vault", "secrets", "keys") for part in p.parts):
            return Verdict("ask", "credentials", "path holds keys/vault/pin material", False)
        return Verdict("allow", reason="read-only access is allowed by the existing policy")

    def check_write(self, raw: str) -> Verdict:
        p = self.policy.resolve(raw)
        if p is None:
            return Verdict("ask", "outside_lab", f"{raw}: outside the lab worktree", True)
        if self.policy._protected(p):
            return Verdict("ask", "protected_file",
                           f"{self.policy.rel(p)} is a constitution / approval-gate / safety file", False)
        return Verdict("allow", reason=f"{self.policy.rel(p)} inside the lab")

    # ---- commands
    def check_command(self, argv: Any) -> Verdict:
        from ..autonomy.policy import ALWAYS_USER  # noqa: F401  (documented source of the categories)
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            return Verdict("refuse", "malformed", "argv must be a non-empty list of strings (no shell strings)")
        base = Path(argv[0].replace("\\", "/")).name.lower().removesuffix(".exe")
        if base == "git" and len(argv) > 1 and argv[1] in PROMOTION_GIT:
            force = any(a in ("--force", "-f", "--force-with-lease") or a.startswith("+") for a in argv[2:])
            return Verdict("ask", "force_push" if force and argv[1] == "push" else "promotion",
                           f"git {argv[1]} leaves the lab: promotion is the owner's decision (rule 4)", False)
        low = " ".join(argv).lower()
        for needle, cat in (("deploy", "deployment"), ("publish", "publication"), ("sendmessage", "message"),
                            ("--no-verify", "disable_safety"), ("pay", "money")):
            if base not in ("python", "python3", "py", "pytest") and needle in low.split():
                return Verdict("ask", cat, f"command looks like {cat}", False)
        # a "python -m pytest" with a selftest-bypass flag is an owner decision, not a lab action
        if any(a in ("--no-verify", "-p", "no:cacheprovider") and a == "--no-verify" for a in argv):
            return Verdict("ask", "disable_safety", "bypasses a safety check", False)
        decision = self.policy.check_argv(argv, goal=None)
        if decision.allowed:
            return Verdict("allow", reason=decision.reason)
        cat = ("destructive" if "destructive" in decision.reason else "network" if "network" in decision.reason
               else "protected_file" if "protected" in decision.reason else "not_in_allowlist")
        return Verdict("ask", cat, decision.reason, executable_after_approval=cat != "protected_file")

    def check_commit_branch(self, branch: str) -> Verdict:
        if branch.startswith(LAB_BRANCH_PREFIX):
            return Verdict("allow", reason=f"branch {branch}")
        return Verdict("refuse", "branch", f"commits only on {LAB_BRANCH_PREFIX}* (current: {branch or '?'})")

    def check_promotion(self, action: str) -> Verdict:
        return Verdict("refuse", "promotion", f"{action}: {RELEASE_STEPS_RU}")


# ---------------------------------------------------------------- helpers
def _now() -> float:
    return time.time()


def _store(svc) -> Path:
    root = Path(svc.settings.data_dir) / "qwen-operator"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _rec_path(svc, rid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", rid or ""):
        raise HTTPException(404, {"message": "задача оператора не найдена"})
    return _store(svc) / f"{rid}.json"


def _save(svc, rec: dict) -> None:
    path = _rec_path(svc, rec["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _load(svc, rid: str) -> dict:
    path = _rec_path(svc, rid)
    if not path.exists():
        raise HTTPException(404, {"message": "задача оператора не найдена"})
    return json.loads(path.read_text(encoding="utf-8"))


def stop_reason(svc) -> str:
    """The owner's kill-switch: global STOP (<data>/computer/STOP) or the autonomy STOP."""
    from ..autonomy import stop as stop_mod
    return stop_mod.stop_reason(Path(svc.settings.data_dir) / "autonomy", svc.settings.data_dir)


def constitution_state() -> dict:
    try:
        from ..autonomy import constitution
        st = constitution.verify()
        return {"autonomy_loop": "OK" if st.ok else "BLOCKED", "reason": str(getattr(st, "reason", ""))}
    except Exception as exc:  # noqa: BLE001
        return {"autonomy_loop": "UNKNOWN", "reason": f"{type(exc).__name__}"}


def load_config(svc) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_ROLES))
    try:
        user = json.loads((_store(svc) / "config.json").read_text(encoding="utf-8"))
        for role, values in (user.get("roles") or {}).items():
            if role in cfg and isinstance(values, dict):
                cfg[role].update(values)
    except (OSError, ValueError):
        pass
    return cfg


def agent_values(model_id: int, *, workspace: str = "") -> dict:
    """Exact ``agents`` values of the operator (create it through POST /api/agents).

    Scoped rights live under ``operator_l1`` and the bare dangerous names are deliberately ABSENT: the generic
    engine tool loop then treats terminal.run / filesystem.write / git.write as not granted (approval), and the
    operator's own pipeline enforces the lab scope with ``OperatorScope``."""
    return {
        "name": OPERATOR_NAME, "role": OPERATOR_ROLE, "model_id": model_id,
        "system_prompt": ("Ты — оператор Bossman уровня L1 Lab. Работаешь только в изолированной лаборатории "
                          "(worktree + ветка lab/*): читаешь, правишь код, запускаешь тесты, коммитишь. Никогда не "
                          "делаешь push/merge/deploy. Если нужно действие из списка «решение владельца» "
                          "(деньги, публикации и сообщения, ключи, удаление данных, отключение защиты, "
                          "конституция/гейт одобрений) — не обходи и не пропускай: опиши действие, зачем оно, "
                          "свою рекомендацию и опасения. Перед завершением обязательно запусти тесты. "
                          "Итог — честный отчёт: что сделано, что проверено, что нет."),
        "tools": ["read_file", "search", "list_dir", "edit_file", "write_file", "run_tests"],
        "max_steps": 40, "max_tokens": 4096, "budget_usd": 0.0, "enabled": True,
        "permissions": {
            "filesystem.read": True, "require_tests_before_finish": True, "use_memory": False,
            "operator_l1": {"level": "L1", "workspace": workspace or "lab worktree (per task)",
                            "filesystem.write": "lab_worktree_only", "terminal.run": "lab_worktree_only",
                            "git.write": "lab_branch_commit_only", "git.push": False, "git.merge": False,
                            "owner_decisions": "park_and_ask", "max_attempts": MAX_ATTEMPTS},
        },
    }


# ---------------------------------------------------------------- local models (native Ollama API)
async def ollama_ps(client: httpx.AsyncClient | None = None) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as c:
            r = await (client or c).get(OLLAMA + "/api/ps")
            return r.json().get("models", [])
    except Exception:  # noqa: BLE001
        return []


async def ollama_chat(model: str, messages: list[dict], *, num_ctx: int = CTX_256K, json_mode: bool = False,
                      num_predict: int = 2048, timeout: float = 1800.0, transport: Any = None) -> dict:
    """One request to the local Ollama (native API: ``num_ctx`` is only settable here). Returns text + measured
    receipts; never raises for a model failure - the caller records ``error``."""
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False, "think": False,
                               "options": {"num_ctx": num_ctx, "temperature": 0.2, "num_predict": num_predict}}
    if json_mode:
        payload["format"] = "json"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, transport=transport) as c:
            r = await c.post(OLLAMA + "/api/chat", json=payload)
            data = r.json()
            if r.status_code >= 400:
                raise RuntimeError(str(data.get("error") or r.status_code)[:300])
            ps = await ollama_ps(c) if transport is None else []
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "model": model, "error": f"{type(exc).__name__}: {exc}"[:300], "text": "",
                "seconds": round(time.monotonic() - started, 1), "num_ctx_requested": num_ctx}
    text = str((data.get("message") or {}).get("content") or "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    loaded = next((m for m in ps if m.get("name") == model or m.get("model") == model), {})
    return {"ok": True, "model": model, "text": text, "tokens_in": data.get("prompt_eval_count", 0),
            "tokens_out": data.get("eval_count", 0), "cost_usd": 0.0,
            "seconds": round(time.monotonic() - started, 1),
            "load_seconds": round((data.get("load_duration") or 0) / 1e9, 1),
            "num_ctx_requested": num_ctx, "context_length_loaded": loaded.get("context_length"),
            "size_bytes": loaded.get("size"), "size_vram_bytes": loaded.get("size_vram")}


async def ensure_ctx_variant(base: str, num_ctx: int, *, transport: Any = None) -> dict:
    """A derived Ollama tag with ``num_ctx`` baked in, so the OpenAI-compatible sidecar (which cannot pass
    ``num_ctx``) also runs at that context. Metadata only: no weights are copied."""
    tag = f"{base.split(':')[0].replace('/', '-')}-ctx{num_ctx // 1024}k:latest"
    try:
        async with httpx.AsyncClient(timeout=120, trust_env=False, transport=transport) as c:
            tags = (await c.get(OLLAMA + "/api/tags")).json().get("models", [])
            if any(m.get("name") == tag for m in tags):
                return {"ok": True, "tag": tag, "created": False}
            r = await c.post(OLLAMA + "/api/create", json={"model": tag, "from": base, "stream": False,
                                                           "parameters": {"num_ctx": num_ctx}})
            if r.status_code >= 400:
                return {"ok": False, "tag": base, "error": r.text[:200]}
            return {"ok": True, "tag": tag, "created": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "tag": base, "error": f"{type(exc).__name__}: {exc}"[:200]}


async def ollama_has(model: str, transport: Any = None) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, transport=transport) as c:
            return any(m.get("name") == model for m in (await c.get(OLLAMA + "/api/tags")).json().get("models", []))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- git / command execution in the lab
def _git(repo: Path, *args: str, data: bytes | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), "--literal-pathspecs", *args], input=data, capture_output=True,
                          timeout=timeout, check=False)


def _env_factory() -> dict[str, str]:
    from ..rave.connectors import child_env
    env = child_env()
    env.update({"GIT_AUTHOR_NAME": "Qwen-Operator L1", "GIT_AUTHOR_EMAIL": "qwen-operator@bossman.local",
                "GIT_COMMITTER_NAME": "Qwen-Operator L1", "GIT_COMMITTER_EMAIL": "qwen-operator@bossman.local"})
    return env


def run_in_lab(scope: OperatorScope, argv: list[str], *, cwd: str = ".", timeout_s: int = 600) -> dict:
    """argv-only, no shell, env without secrets, cwd confined to the lab. Caller has already passed the guard."""
    from ..autonomy.hands import SubprocessExecutor
    from ..autonomy.policy import Scope
    from ..autonomy.types import HandRequest
    req = HandRequest(goal_id="operator", requested_by="operator", action="run_command", target=cwd,
                      arguments={"argv": argv, "cwd": cwd}, expected_evidence=(), risk_class="low",
                      timeout_s=timeout_s, rollback="git restore from the lab branch")
    out = SubprocessExecutor(env_factory=_env_factory)(req, Scope(worktree=scope.worktree))
    return {"argv": argv, "cwd": cwd, "exit_code": out.exit_code, "timed_out": out.timed_out, "error": out.error,
            "stdout": out.stdout.decode("utf-8", "replace")[-6000:],
            "stderr": out.stderr.decode("utf-8", "replace")[-3000:]}


# ---------------------------------------------------------------- pipeline
@dataclass
class Run:
    svc: Any
    rec: dict
    cfg: dict
    scope: OperatorScope
    free_cloud: Callable[..., Any] | None = None      # injected in tests
    local_chat: Callable[..., Any] = ollama_chat
    coder: Callable[..., Any] | None = None           # injected in tests (replaces the sidecar)
    decision_wait_s: float | None = DEFAULT_DECISION_WAIT_S
    steps: list = field(default_factory=list)

    # ---- bookkeeping
    def step(self, kind: str, status: str, **fields: Any) -> dict:
        row = {"n": len(self.rec["steps"]) + 1, "kind": kind, "status": status, "at": _now(), **fields}
        self.rec["steps"].append(row)
        _save(self.svc, self.rec)
        return row

    def stopped(self) -> str:
        reason = stop_reason(self.svc)
        if reason or self.rec.get("cancel_requested"):
            return reason or "cancelled by the owner"
        return ""

    def usage(self, res: dict, role: str) -> dict:
        row = {"role": role, "model": res.get("model"), "tokens_in": res.get("tokens_in", 0),
               "tokens_out": res.get("tokens_out", 0), "cost_usd": res.get("cost_usd", 0.0),
               "seconds": res.get("seconds"), "ok": res.get("ok", False)}
        for k in ("num_ctx_requested", "context_length_loaded", "size_bytes", "size_vram_bytes", "load_seconds",
                  "error", "provider", "task_id", "run_id"):
            if res.get(k) is not None:
                row[k] = res[k]
        self.rec["model_calls"].append(row)
        return row

    # ---- owner decisions (park + continue)
    async def park(self, *, action: str, category: str, why: str, recommendation: str, concerns: str,
                   argv: list[str] | None = None, path: str | None = None, executable: bool = False) -> dict:
        preview = (f"Решение владельца · {category}\nДействие: {action}\nЗачем: {why}\n"
                   f"Рекомендация оператора: {recommendation}\nОпасения: {concerns}")
        appr = await self.svc.approvals.create(DECISION_KIND, preview)
        decision = {"approval_id": appr.get("id"), "category": category, "action": action, "why": why,
                    "recommendation": recommendation, "concerns": concerns, "argv": argv, "path": path,
                    "executable": executable, "status": "PENDING", "preview": preview, "created_at": _now()}
        self.rec["decisions"].append(decision)
        self.step("owner_decision", "parked", approval_id=decision["approval_id"], category=category, action=action)
        return decision

    async def gate_command(self, argv: list[str], *, why: str, recommendation: str = "", concerns: str = "",
                           cwd: str = ".") -> dict:
        verdict = self.scope.check_command(argv)
        if verdict.kind == "allow":
            res = await asyncio.to_thread(run_in_lab, self.scope, argv, cwd=cwd)
            self.rec["commands"].append({**res, "verdict": "allow", "why": why})
            return {"ran": True, **res}
        if verdict.kind == "refuse":
            self.rec["commands"].append({"argv": argv, "verdict": "refuse", "reason": verdict.reason})
            return {"ran": False, "refused": verdict.reason}
        d = await self.park(action=" ".join(argv), category=verdict.category, why=why or verdict.reason,
                            recommendation=recommendation or "выполнить только если вы сами этого хотите",
                            concerns=concerns or verdict.reason, argv=argv,
                            executable=verdict.executable_after_approval)
        d["cwd"] = cwd
        self.rec["commands"].append({"argv": argv, "verdict": "ask", "category": verdict.category,
                                     "approval_id": d["approval_id"]})
        return {"ran": False, "parked": d["approval_id"]}

    async def settle_decisions(self) -> None:
        """Wait (bounded) for the owner's answers, then execute what is executable. Never loops forever."""
        deadline = None if self.decision_wait_s is None else time.monotonic() + self.decision_wait_s
        while True:
            pending = [d for d in self.rec["decisions"] if d["status"] == "PENDING"]
            if not pending:
                return
            rows = {r["id"]: r for r in await self.svc.approvals.list("all", 200)}
            for d in pending:
                row = rows.get(d["approval_id"]) or {}
                st = row.get("status")
                if st in ("approved", "rejected", "denied", "expired", "revoked", "cancelled"):
                    await self.apply_decision(d, st)
            if not [d for d in self.rec["decisions"] if d["status"] == "PENDING"]:
                return
            if (deadline is not None and time.monotonic() >= deadline) or self.stopped():
                return
            await asyncio.sleep(5.0 if deadline is None else min(5.0, max(0.05, self.decision_wait_s / 20)))

    async def apply_decision(self, d: dict, status: str) -> None:
        if status != "approved":
            d["status"] = "DENIED_BY_OWNER" if status in ("rejected", "denied") else status.upper()
            self.step("owner_decision", d["status"], approval_id=d["approval_id"], action=d["action"])
            return
        cat = d["category"]
        if cat in ("promotion", "force_push") or (d.get("argv") and d["argv"][:2] and d["argv"][0] == "git"
                                                   and d["argv"][1] in PROMOTION_GIT):
            d["status"] = "APPROVED_BLOCKED_BY_RULE_4"
            d["note"] = ("владелец одобрил, но конституция v1 (правило 4) требует одобрения Claude и Codex для "
                         "того же SHA; оператор push/merge не выполняет. " + RELEASE_STEPS_RU)
        elif d.get("executable") and d.get("argv"):
            res = await asyncio.to_thread(run_in_lab, self.scope, d["argv"], cwd=d.get("cwd", "."))
            self.rec["commands"].append({**res, "verdict": "executed_after_owner_approval",
                                         "approval_id": d["approval_id"]})
            d["status"] = "APPROVED_EXECUTED" if res["exit_code"] == 0 else "APPROVED_EXECUTED_FAILED"
            d["exit_code"] = res["exit_code"]
        else:
            d["status"] = "APPROVED_NOT_EXECUTED"
            d["note"] = ("одобрено, но оператор такие изменения не выполняет (конституция / гейт одобрений / "
                         "деньги / публикации / ключи правит и делает сам владелец)")
        self.step("owner_decision", d["status"], approval_id=d["approval_id"], action=d["action"])

    # ---- planning
    async def plan(self, task: str) -> dict | None:
        files = _git(self.scope.worktree, "ls-files").stdout.decode("utf-8", "replace").splitlines()
        words = {w.lower() for w in re.findall(r"[\w./\\-]{4,}", task)}
        hits = [f for f in files if any(w.strip("./\\") and w.strip("./\\") in f.lower() for w in words)][:60]
        context = "\n".join(hits) or "(нет совпадений по именам файлов; опирайся на текст задачи)"
        system = ("Ты — оператор Bossman (L1 Lab). Составь план в формате СТРОГО JSON без пояснений: "
                  '{"summary": str, "files": [repo-relative paths to change], "edit_instruction": str, '
                  '"verify_tests": [repo-relative test files or node ids], "worker_prompt": str (опционально: '
                  'подзадача для независимой бесплатной облачной модели, например черновик формулировки; пусто '
                  'если не нужна), "extra_commands": [{"argv": [..], "why": str, "recommendation": str, '
                  '"concerns": str}] (только если действительно нужны команды вне правки кода)}. '
                  "Правь только то, что просит задача. Пути бери из списка файлов репозитория.")
        user = f"ЗАДАЧА ВЛАДЕЛЬЦА:\n{task}\n\nФАЙЛЫ РЕПОЗИТОРИЯ (отобраны по задаче):\n{context}"
        cfg = self.cfg["planner"]
        last_problem = ""
        for attempt in range(1, 3):
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": user +
                    (f"\n\nПРОШЛЫЙ ПЛАН ОТКЛОНЁН: {last_problem}. Исправь." if last_problem else "")}]
            res = await self.local_chat(cfg["model"], msgs, num_ctx=int(cfg.get("num_ctx") or CTX_256K),
                                        json_mode=True)
            row = self.usage(res, "planner")
            self.step("plan", "ok" if res.get("ok") else "failed", attempt=attempt, model=cfg["model"],
                      tokens_in=row["tokens_in"], tokens_out=row["tokens_out"], error=res.get("error"))
            if not res.get("ok"):
                last_problem = res.get("error", "model error")
                continue
            try:
                plan = json.loads(res["text"])
                if not isinstance(plan, dict):
                    raise ValueError("plan is not an object")
            except ValueError as exc:
                last_problem = f"not valid JSON ({exc})"
                continue
            problems = []
            tracked = set(files)
            for f in plan.get("files") or []:
                if not isinstance(f, str) or (f not in tracked and not (self.scope.worktree / f).exists()
                                              and not f.startswith(("docs/", "command-center/tests/", "tests/"))):
                    problems.append(f"unknown path {f!r}")
            if not plan.get("files") and not plan.get("extra_commands"):
                problems.append("plan has no files and no commands")
            if problems:
                last_problem = "; ".join(problems)[:300]
                continue
            self.rec["plan"] = plan
            return plan
        self.step("plan", "failed", reason=last_problem)
        return None

    # ---- cloud worker / verifier
    async def cloud(self, role: str, system: str, prompt: str) -> dict:
        """One call on a FREE cloud model of another family through the governed registry adapter, as an
        engine task of a worker agent when the engine is available. NOT_RUN with the reason when none works."""
        if self.free_cloud is not None:
            return await self.free_cloud(role, system, prompt)
        return await run_cloud_worker(self.svc, role, system, prompt, self.cfg["verifier"])

    async def finish(self, status: str, error: str = "") -> dict:
        self.rec.update(status=status, finished_at=_now(), error=error)
        _save(self.svc, self.rec)
        return self.rec


async def _free_candidates(svc, cfg: dict) -> list[dict]:
    from ..pit.model_policy import MIN_MAIN_PARAMS_B, is_banned_model, resolve_params
    from ..provider_governance import free_only_refusal
    models = await svc.registry.list_models()
    providers = {p["id"]: p for p in await svc.registry.list_providers()}
    avoid = str(cfg.get("avoid_family") or "").lower()
    prefer = list(cfg.get("prefer") or [])
    out = []
    for m in models:
        prov = providers.get(m.get("provider_id")) or {}
        name = str(m.get("name") or "")
        if m.get("kind") != "cloud" or is_banned_model(name) or (avoid and avoid in name.lower()):
            continue
        if m.get("price_in") not in (0, 0.0) or m.get("price_out") not in (0, 0.0):
            continue                                   # unknown price (None) is NOT proof of free
        if free_only_refusal(prov, m):
            continue
        params = resolve_params(name, m)
        if not params.known or params.total_b < MIN_MAIN_PARAMS_B:
            continue
        out.append({**m, "_provider": prov.get("name"), "_rank": prefer.index(name) if name in prefer else 99})
    out.sort(key=lambda m: (m["_rank"], 0 if m.get("status") == "online" else 1, m["id"]))
    return out


async def run_cloud_worker(svc, role: str, system: str, prompt: str, cfg: dict) -> dict:
    cands = await _free_candidates(svc, cfg)
    attempts = []
    for m in cands[:4]:
        started = time.monotonic()
        try:
            adapter, _row = await svc.registry.adapter_for(m["id"])
            res = await asyncio.wait_for(adapter.chat(m["name"], [{"role": "system", "content": system},
                                                                  {"role": "user", "content": prompt}],
                                                      max_tokens=1200, temperature=0.2), timeout=180)
            return {"ok": True, "role": role, "model": m["name"], "provider": m.get("_provider"),
                    "text": res.text, "tokens_in": res.tokens_in, "tokens_out": res.tokens_out, "cost_usd": 0.0,
                    "seconds": round(time.monotonic() - started, 1), "attempts": attempts}
        except Exception as exc:  # noqa: BLE001 - next free candidate, honestly recorded
            attempts.append({"model": m["name"], "provider": m.get("_provider"),
                             "error": f"{type(exc).__name__}: {exc}"[:200]})
    return {"ok": False, "role": role, "model": None, "text": "", "attempts": attempts, "cost_usd": 0.0,
            "error": "NOT_RUN: no free cloud model answered" if cands else "NOT_RUN: no free cloud model in the registry"}


async def unload_model(model: str) -> None:
    """Free the RAM of a loaded Ollama runner (keep_alive=0) so two tags of one model are never both resident."""
    with contextlib.suppress(Exception):
        async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
            await c.post(OLLAMA + "/api/generate", json={"model": model, "keep_alive": 0})


async def resolve_local_roles(run: Run) -> None:
    """Use the uncensored Qwen that Ollama actually has (default tag, else bossman-uncensored-chat), pin it to
    ONE derived tag with the requested context baked in (so planner, diagnostician and the OpenAI-compatible
    sidecar share one resident runner), and record what was chosen. A role the owner set explicitly is kept."""
    have = {m: await ollama_has(m) for m in LOCAL_UNCENSORED}
    chosen = next((m for m in LOCAL_UNCENSORED if have[m]), None)
    variants = {}
    for role in ("planner", "coder", "diagnostician"):
        cfg = run.cfg[role]
        if cfg.get("model") in LOCAL_UNCENSORED and chosen:
            cfg["base_model"] = chosen
            cfg["model"] = chosen
        base = cfg.get("base_model") or cfg.get("model")
        n = int(cfg.get("num_ctx") or CTX_256K)
        if (base, n) not in variants:
            variants[(base, n)] = await ensure_ctx_variant(base, n)
            if variants[(base, n)].get("ok") and variants[(base, n)]["tag"] != base:
                await unload_model(base)
        v = variants[(base, n)]
        cfg["model"] = v["tag"]
        cfg["ctx_variant"] = v
    run.rec["roles"] = run.cfg
    run.step("resolve_models", "ok" if chosen else "failed", uncensored_available=have, chosen=chosen,
             variants={f"{b}@{n}": v for (b, n), v in variants.items()})


async def local_verifier(run: Run, task: str, diff: str) -> dict:
    """Fallback when no free cloud verifier answered: a SECOND local Qwen request with a fresh context (no
    history of the change). Not an independent family - the receipts say so."""
    cfg = run.cfg["planner"]
    res = await run.local_chat(cfg["model"], [
        {"role": "system", "content": "Ты — независимый рецензент изменений. Ответь: VERDICT: APPROVE или "
                                      "VERDICT: REJECT, затем 2-4 предложения. Проверяй только присланный diff."},
        {"role": "user", "content": "ЗАДАЧА:\n" + task[:1500] + "\n\nDIFF:\n" + diff[:12000]}],
        num_ctx=int(cfg.get("num_ctx") or CTX_256K))
    res["role"] = "verifier"
    return res


# ---------------------------------------------------------------- the task
async def execute(run: Run) -> dict:
    rec, scope, svc = run.rec, run.scope, run.svc
    task = rec["task"]
    rec["status"] = "running"
    _save(svc, rec)
    if (why := run.stopped()):
        return await run.finish("stopped", why)
    await resolve_local_roles(run)
    plan = await run.plan(task)
    if plan is None:
        return await run.finish("failed", "планировщик не дал валидный план")

    # parked owner decisions come first so the owner sees them while the work goes on
    paths_ok: list[str] = []
    for f in plan.get("files") or []:
        v = scope.check_write(f)
        if v.kind == "allow":
            paths_ok.append(f)
        else:
            await run.park(action=f"изменить файл {f}", category=v.category, why=str(plan.get("summary") or task)[:300],
                           recommendation=("правку этого файла лучше сделать вам самому или через поправку "
                                           "конституции v2" if v.category == "protected_file"
                                           else "разрешить только если файл действительно нужно трогать"),
                           concerns=v.reason, path=f, executable=False)
    for c in plan.get("extra_commands") or []:
        if isinstance(c, dict):
            await run.gate_command([str(a) for a in c.get("argv") or []], why=str(c.get("why") or ""),
                                   recommendation=str(c.get("recommendation") or ""),
                                   concerns=str(c.get("concerns") or ""))

    draft = ""
    if str(plan.get("worker_prompt") or "").strip() and not run.stopped():
        res = await run.cloud("worker", "Ты — независимый помощник. Отвечай коротко и по делу.",
                              str(plan["worker_prompt"])[:4000])
        run.usage(res, "worker")
        run.step("worker_draft", "ok" if res.get("ok") else "NOT_RUN", model=res.get("model"),
                 error=res.get("error"), attempts=res.get("attempts"))
        draft = res.get("text", "") if res.get("ok") else ""

    commit_sha, final_diff = "", ""
    if paths_ok:
        commit_sha, final_diff = await edit_loop(run, plan, paths_ok, draft)
    elif not rec["decisions"]:
        return await run.finish("failed", "в плане нет файлов, которые оператор может менять")
    rec["commit_sha"], rec["diff"] = commit_sha, final_diff

    if final_diff and not run.stopped():
        review = await run.cloud(
            "verifier", "Ты — независимый рецензент изменений. Ответь: VERDICT: APPROVE или REJECT, затем 2-4 "
            "предложения с причинами. Проверяй только присланный diff и результат тестов.",
            f"ЗАДАЧА:\n{task[:1500]}\n\nDIFF:\n{final_diff[:12000]}\n\nТЕСТЫ:\n{rec.get('test_summary', '')[:1500]}")
        independence = "different_family_free_cloud"
        if not review.get("ok"):
            cloud_error = review.get("error")
            review = await local_verifier(run, task, final_diff)
            review["cloud_error"] = cloud_error
            independence = "NOT_INDEPENDENT_same_model_fresh_context (no free cloud verifier answered)"
        run.usage(review, "verifier")
        verdict = ("APPROVE" if re.search(r"VERDICT:\s*APPROVE", review.get("text", ""), re.I) else
                   "REJECT" if re.search(r"VERDICT:\s*REJECT", review.get("text", ""), re.I) else "NO_VERDICT")
        rec["verifier"] = {"ran": review.get("ok", False), "model": review.get("model"), "verdict": verdict,
                           "text": review.get("text", "")[:1500], "error": review.get("error"),
                           "independence": independence, "cloud_error": review.get("cloud_error")}
        run.step("verify_cloud", verdict if review.get("ok") else "NOT_RUN", model=review.get("model"),
                 error=review.get("error"))

    await run.settle_decisions()
    rec["promotion"] = {"status": "BLOCKED", "reason": "constitution v1 rule 4 + L1 Lab never merges",
                        "what_the_owner_does": RELEASE_STEPS_RU,
                        "lab_branch": rec.get("lab", {}).get("branch"), "commit": commit_sha or None}
    pending = [d for d in rec["decisions"] if d["status"] == "PENDING"]
    if rec.get("edit_failed"):
        return await run.finish("failed", rec["edit_failed"])
    return await run.finish("completed_with_pending_decisions" if pending else "completed")


async def edit_loop(run: Run, plan: dict, paths: list[str], draft: str) -> tuple[str, str]:
    """Up to MAX_ATTEMPTS: coder (sidecar in a disposable clone) -> Bossman's own test verdict -> apply in the
    lab -> tests in the lab -> commit; worse/failed -> rollback and the diagnostician explains the failure."""
    rec, scope, svc = run.rec, run.scope, run.svc
    tests = [t for t in (plan.get("verify_tests") or []) if isinstance(t, str)]
    feedback = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if (why := run.stopped()):
            rec["edit_failed"] = f"stopped: {why}"
            return "", ""
        instruction = str(plan.get("edit_instruction") or rec["task"])
        if draft:
            instruction += f"\n\nЧерновик от независимой облачной модели (используй по усмотрению):\n{draft[:3000]}"
        if feedback:
            instruction += f"\n\nПРОШЛАЯ ПОПЫТКА НЕ ПРОШЛА. Диагностика:\n{feedback[:3000]}"
        coded = await (run.coder or run_sidecar)(run, instruction, paths, tests, attempt)
        run.step("edit", "ok" if coded.get("ok") else "failed", attempt=attempt, model=coded.get("model"),
                 changed_files=coded.get("changed_files"), error=coded.get("error"),
                 verification=coded.get("verification"))
        run.rec["model_calls"].append({"role": "coder", "model": coded.get("model"), "ok": coded.get("ok", False),
                                       "tokens_in": coded.get("tokens_in"), "tokens_out": coded.get("tokens_out"),
                                       "cost_usd": 0.0, "seconds": coded.get("seconds"),
                                       "num_ctx_requested": coded.get("num_ctx"), "ctx_variant": coded.get("ctx_variant"),
                                       "coding_task_id": coded.get("coding_task_id")})
        problem = ""
        diff = coded.get("diff") or ""
        if not coded.get("ok") or not diff:
            problem = coded.get("error") or "пустой diff"
        else:
            applied = await asyncio.to_thread(apply_in_lab, scope, diff, paths)
            if applied.get("error"):
                problem = applied["error"]
            else:
                ran = await asyncio.to_thread(run_tests_in_lab, scope, tests or [])
                rec["test_summary"] = (ran.get("stdout", "") + ran.get("stderr", ""))[-2500:]
                rec["commands"].append({**ran, "verdict": "allow", "why": "mandatory verification"})
                if ran.get("exit_code") != 0 or not tests:
                    await asyncio.to_thread(rollback_in_lab, scope, diff)
                    problem = ("нет verify_tests: завершать без проверки нельзя" if not tests
                               else f"тесты в лаборатории упали (exit {ran.get('exit_code')}): "
                                    + rec["test_summary"][-800:])
                    run.step("rollback", "done", attempt=attempt, reason=problem[:200])
                else:
                    sha = await asyncio.to_thread(commit_in_lab, run, applied["paths"], rec["task"])
                    if sha.get("error"):
                        await asyncio.to_thread(rollback_in_lab, scope, diff)
                        problem = sha["error"]
                    else:
                        run.step("commit", "ok", sha=sha["sha"], branch=rec["lab"]["branch"], files=applied["paths"])
                        return sha["sha"], diff
        # self-correction: the diagnostician explains, the next attempt gets it (bounded)
        run.rec["attempts"].append({"attempt": attempt, "problem": problem[:800]})
        if attempt == MAX_ATTEMPTS:
            break
        cfg = run.cfg["diagnostician"]
        res = await run.local_chat(cfg["model"], [
            {"role": "system", "content": "Ты — диагност. Объясни коротко причину сбоя и что именно исправить."},
            {"role": "user", "content": f"Инструкция:\n{instruction[:2000]}\n\nСбой:\n{problem[:2500]}"}],
            num_ctx=int(cfg.get("num_ctx") or CTX_256K))
        run.usage(res, "diagnostician")
        run.step("diagnose", "ok" if res.get("ok") else "failed", attempt=attempt, model=cfg["model"])
        feedback = res.get("text", "") if res.get("ok") else problem
    rec["edit_failed"] = f"правка не прошла за {MAX_ATTEMPTS} попытки; изменений в лаборатории нет"
    return "", ""


def apply_in_lab(scope: OperatorScope, diff: str, allowed: list[str]) -> dict:
    from . import coding_tasks as ct
    patch = diff.encode("utf-8")
    try:
        touched = ct._touched(scope.worktree, patch)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"patch unreadable: {exc}"[:300]}
    for p in touched:
        v = scope.check_write(p)
        if v.kind != "allow":
            return {"error": f"patch touches {p}: {v.reason}"}
    chk = _git(scope.worktree, "apply", "--check", "--whitespace=nowarn", "-", data=patch)
    if chk.returncode:
        return {"error": "git apply --check: " + chk.stderr.decode("utf-8", "replace")[:300]}
    done = _git(scope.worktree, "apply", "--whitespace=nowarn", "-", data=patch)
    if done.returncode:
        return {"error": "git apply: " + done.stderr.decode("utf-8", "replace")[:300]}
    return {"paths": touched}


def rollback_in_lab(scope: OperatorScope, diff: str) -> bool:
    return _git(scope.worktree, "apply", "-R", "--whitespace=nowarn", "-", data=diff.encode("utf-8")).returncode == 0


def run_tests_in_lab(scope: OperatorScope, tests: list[str]) -> dict:
    if not tests:
        return {"exit_code": None, "stdout": "", "stderr": "no tests named", "argv": []}
    argv = [sys.executable, "-m", "pytest", *tests, "-q", "-x", "-p", "no:cacheprovider"]
    v = scope.check_command(argv)
    if v.kind != "allow":
        return {"exit_code": None, "stdout": "", "stderr": f"guard: {v.reason}", "argv": argv}
    return run_in_lab(scope, argv, cwd=".", timeout_s=900)


def commit_in_lab(run: Run, paths: list[str], task: str) -> dict:
    scope = run.scope
    branch = _git(scope.worktree, "rev-parse", "--abbrev-ref", "HEAD").stdout.decode().strip()
    v = scope.check_commit_branch(branch)
    if v.kind != "allow":
        return {"error": v.reason}
    msg = (f"operator: {task.strip().splitlines()[0][:70]}\n\nTask: {run.rec['id']}\n"
           f"Operator: {OPERATOR_NAME}\nPlanner: {run.cfg['planner']['model']}\nLevel: L1 Lab (never merged)")
    for argv in (["git", "add", "--", *paths], ["git", "commit", "-m", msg, "--", *paths]):
        verdict = scope.check_command(argv)
        if verdict.kind != "allow":
            return {"error": f"guard refused {' '.join(argv[:2])}: {verdict.reason}"}
        res = run_in_lab(scope, argv)
        run.rec["commands"].append({**{k: res[k] for k in ("argv", "exit_code", "stdout", "stderr")},
                                    "verdict": "allow", "why": "lab commit"})
        if res["exit_code"] != 0:
            return {"error": f"{argv[1]} failed: {res['stderr'][-300:]}"}
    sha = _git(scope.worktree, "rev-parse", "HEAD").stdout.decode().strip()
    return {"sha": sha}


async def run_sidecar(run: Run, instruction: str, paths: list[str], tests: list[str], attempt: int) -> dict:
    """The coder: the existing coding-task sidecar (disposable clone, tool loop, Bossman's own test verdict)."""
    from . import coding_tasks as ct
    rec, svc = run.rec, run.svc
    cfg = run.cfg["coder"]
    num_ctx = int(cfg.get("num_ctx") or CTX_256K)
    variant = cfg.get("ctx_variant") or await ensure_ctx_variant(cfg["model"], num_ctx)
    model = cfg["model"] if cfg.get("ctx_variant") else variant["tag"]
    oc, _wt, reason = ct._runtime()
    if oc is None:
        return {"ok": False, "model": model, "error": reason}
    agent_id = rec.get("operator_agent_id")
    context: dict[str, Any] = {}
    if agent_id:
        profile = await ct._agent_profile(svc, int(agent_id))
        if profile:
            context["profile"] = {k: v for k, v in profile.items() if k != "use_memory" and v is not None}
    argv = [sys.executable, "-I", "-m", "bossman.apprentice.local_sidecar", "--endpoint",
            OLLAMA + "/v1", "--model", model]
    body = ct.TaskIn(instruction=instruction, source_repo=str(run.scope.worktree), allowed_paths=list(paths),
                     protected_paths=[], model=model, timeout_seconds=7200, agent_id=agent_id,
                     verify_tests=list(tests), use_memory=False)
    record = {"id": secrets.token_hex(6), "status": "running", "instruction": instruction, "boot_id": ct.BOOT_ID,
              "agent_id": agent_id, "project_id": None, "memory": {}, "skills": {}, "verify_tests": list(tests),
              "source_repo": str(run.scope.worktree), "allowed_paths": list(paths), "protected_paths": [],
              "model": model, "worker": None, "created_at": _now(), "finished_at": None, "changed_files": [],
              "diff": "", "error": "", "evidence": None, "sandbox_cleanup": None, "operator_task": rec["id"],
              "authority": {"push": False, "merge": False, "deploy": False}}
    final = await asyncio.to_thread(ct._execute, record, run.scope.worktree, body, context,
                                    ct._verify_python_env(), argv)
    ct._write(svc, final)
    side = final.get("sidecar") or {}
    ok = final.get("status") == "completed" and bool(final.get("diff"))
    return {"ok": ok, "model": model, "diff": final.get("diff", ""), "changed_files": final.get("changed_files"),
            "error": final.get("error") or ("" if ok else "sidecar: " + str(side.get("summary") or final.get("status"))),
            "verification": final.get("verification"), "coding_task_id": final.get("id"),
            "seconds": final.get("duration_seconds"), "num_ctx": num_ctx, "ctx_variant": variant,
            "tokens_in": side.get("tokens_in"), "tokens_out": side.get("tokens_out")}


def create_lab(svc, source_repo: Path, task_id: str, base_ref: str = "HEAD") -> dict:
    lab = _store(svc) / f"lab-{task_id}"
    branch = f"{LAB_BRANCH_PREFIX}op-{task_id}"
    out = _git(source_repo, "worktree", "add", "-b", branch, str(lab), base_ref)
    if out.returncode:
        raise RuntimeError("git worktree add: " + out.stderr.decode("utf-8", "replace")[:300])
    base = _git(lab, "rev-parse", "HEAD").stdout.decode().strip()
    return {"path": str(lab), "branch": branch, "base_commit": base, "source_repo": str(source_repo)}


_LOCK = asyncio.Lock()
_JOBS: dict[str, asyncio.Task] = {}


async def start_task(svc, task: str, *, source_repo: str | None = None, base_ref: str = "HEAD",
                     decision_wait_s: float | None = None, **inject: Any) -> dict:
    reason = stop_reason(svc)
    if reason:
        raise HTTPException(423, {"code": "OWNER_STOP", "message": f"владелец остановил систему: {reason}"})
    async with svc.db.session() as s:
        from ..db import agents as agents_t
        row = (await s.execute(sa.select(agents_t).where(agents_t.c.role == OPERATOR_ROLE)
                               .order_by(agents_t.c.id))).mappings().first()
    if row is None or not row["enabled"]:
        raise HTTPException(409, {"code": "OPERATOR_NOT_CONFIGURED",
                                  "message": "агент «Qwen-Operator L1» не создан или выключен (POST /api/agents)"})
    if _LOCK.locked():
        raise HTTPException(409, {"code": "OPERATOR_BUSY", "message": "оператор уже выполняет задачу"})
    repo = Path(source_repo or Path(__file__).resolve().parents[3]).resolve()
    if not (repo / ".git").exists():
        raise HTTPException(409, {"code": "NO_GIT_REPO", "message": f"{repo} не git-репозиторий"})
    rid = secrets.token_hex(6)
    try:
        lab = await asyncio.to_thread(create_lab, svc, repo, rid, base_ref)
    except RuntimeError as exc:
        raise HTTPException(409, {"code": "LAB_CREATE_FAILED", "message": str(exc)})
    rec = {"id": rid, "task": task, "status": "queued", "created_at": _now(), "finished_at": None,
           "operator_agent_id": int(row["id"]), "lab": lab, "steps": [], "model_calls": [], "commands": [],
           "decisions": [], "attempts": [], "constitution": constitution_state(),
           "authority": {"push": False, "merge": False, "deploy": False, "level": "L1 Lab"},
           "roles": load_config(svc), "cost_usd": 0.0}
    _save(svc, rec)
    run = Run(svc, rec, rec["roles"], OperatorScope(Path(lab["path"]), data_dir=Path(svc.settings.data_dir)),
              decision_wait_s=DEFAULT_DECISION_WAIT_S if decision_wait_s is None else decision_wait_s, **inject)

    async def go():
        async with _LOCK:
            try:
                await execute(run)
            except asyncio.CancelledError:
                await run.finish("stopped", "cancelled")
                raise
            except Exception as exc:  # noqa: BLE001
                await run.finish("failed", f"{type(exc).__name__}: {exc}"[:500])

    _JOBS[rid] = asyncio.create_task(go())
    return rec


def public(rec: dict) -> dict:
    out = dict(rec)
    out["diff_bytes"] = len(rec.get("diff") or "")
    out["cost_usd"] = round(sum(float(c.get("cost_usd") or 0) for c in rec.get("model_calls", [])), 6)
    out["pending_decisions"] = [d for d in rec.get("decisions", []) if d.get("status") == "PENDING"]
    return out


# ---------------------------------------------------------------- API
class TaskIn(BaseModel):
    task: str = Field(min_length=3, max_length=4000)
    source_repo: str | None = Field(default=None, max_length=1000)
    base_ref: str = Field(default="HEAD", max_length=200)
    decision_wait_s: float | None = Field(default=None, ge=0, le=7200)


class CommandIn(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=64)
    why: str = Field(default="", max_length=500)
    recommendation: str = Field(default="", max_length=500)
    concerns: str = Field(default="", max_length=500)


class ConfigIn(BaseModel):
    roles: dict[str, dict] = Field(default_factory=dict)


@router.get("/qwen-operator/status")
async def get_status(request: Request):
    svc = request.app.state.svc
    return {"operator_role": OPERATOR_ROLE, "level": "L1 Lab", "roles": load_config(svc),
            "stop": stop_reason(svc) or None, "constitution": constitution_state(),
            "authority": {"push": False, "merge": False, "deploy": False},
            "busy": _LOCK.locked(), "max_attempts": MAX_ATTEMPTS}


@router.get("/qwen-operator/config")
async def get_config(request: Request):
    return {"roles": load_config(request.app.state.svc)}


@router.post("/qwen-operator/config")
async def set_config(body: ConfigIn, request: Request):
    svc = request.app.state.svc
    bad = [r for r in body.roles if r not in DEFAULT_ROLES]
    if bad:
        raise HTTPException(422, {"message": f"неизвестные роли: {bad}", "roles": sorted(DEFAULT_ROLES)})
    (_store(svc) / "config.json").write_text(json.dumps({"roles": body.roles}, ensure_ascii=False), encoding="utf-8")
    return {"roles": load_config(svc)}


@router.post("/qwen-operator/tasks")
async def create_task(body: TaskIn, request: Request):
    rec = await start_task(request.app.state.svc, body.task, source_repo=body.source_repo,
                           base_ref=body.base_ref, decision_wait_s=body.decision_wait_s)
    return public(rec)


@router.get("/qwen-operator/tasks")
async def list_tasks(request: Request):
    svc = request.app.state.svc
    items = []
    for p in _store(svc).glob("*.json"):
        if re.fullmatch(r"[a-f0-9]{12}\.json", p.name):
            try:
                items.append(public(json.loads(p.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
    items.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return {"items": [{k: i.get(k) for k in ("id", "task", "status", "created_at", "commit_sha", "cost_usd",
                                             "pending_decisions")} for i in items]}


@router.get("/qwen-operator/tasks/{rid}")
async def get_task(rid: str, request: Request):
    return public(_load(request.app.state.svc, rid))


@router.post("/qwen-operator/tasks/{rid}/cancel")
async def cancel_task(rid: str, request: Request):
    svc = request.app.state.svc
    rec = _load(svc, rid)
    if rec.get("status") in TERMINAL:
        return {"id": rid, "status": rec["status"], "cancelled": False}
    rec["cancel_requested"] = True
    _save(svc, rec)
    job = _JOBS.get(rid)
    if job is not None and not job.done():
        job.cancel()
    return {"id": rid, "cancelled": True}


@router.post("/qwen-operator/tasks/{rid}/promote")
async def promote(rid: str, request: Request):
    """Always refused: the operator never pushes, merges or applies. Says what the owner does."""
    svc = request.app.state.svc
    rec = _load(svc, rid)
    scope = OperatorScope(Path(rec["lab"]["path"]), data_dir=Path(svc.settings.data_dir))
    v = scope.check_promotion("promote/push/merge")
    rec.setdefault("refused_attempts", []).append({"at": _now(), "action": "promote", "reason": v.category})
    _save(svc, rec)
    raise HTTPException(403, {"code": "PROMOTION_REQUIRES_USER", "message": v.reason, "lab_branch": rec["lab"]["branch"],
                              "commit": rec.get("commit_sha")})


@router.post("/qwen-operator/tasks/{rid}/command")
async def command(rid: str, body: CommandIn, request: Request):
    """One command through the same guard the pipeline uses: allowed -> runs in the lab; owner-decision ->
    parked with an approvals row (answer it in the pult); refused -> says why."""
    svc = request.app.state.svc
    rec = _load(svc, rid)
    scope = OperatorScope(Path(rec["lab"]["path"]), data_dir=Path(svc.settings.data_dir))
    run = Run(svc, rec, rec.get("roles") or load_config(svc), scope, decision_wait_s=0)
    out = await run.gate_command(body.argv, why=body.why, recommendation=body.recommendation,
                                 concerns=body.concerns)
    _save(svc, rec)
    return {**out, "verdict": scope.check_command(body.argv).kind}


FEATURE = Feature(name="qwen_operator", router=router)

__all__ = ["OPERATOR_ROLE", "OPERATOR_NAME", "OperatorScope", "Verdict", "agent_values", "start_task",
           "DEFAULT_ROLES", "MAX_ATTEMPTS", "RELEASE_STEPS_RU", "FEATURE"]
