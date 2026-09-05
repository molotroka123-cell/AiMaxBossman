"""ORG-02 (TZ-04 §2.2): планировщик шагов для контракта без `steps`.

Организация не выдумывает действий за инструмент: план строится ТОЛЬКО из
детерминированного лексикона односложных целей и только из способностей,
которые реально зарегистрированы (`supports(action_type)`). Всё остальное —
`None` → рантайм ставит BLOCKED/no_executable_steps (это не провал исполнителя).
Модельный планировщик (TZ-03 §2.5) подключается тем же портом позже.
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Callable, Protocol

from bossman_v3.contracts import SideEffectClass, TypedAction
from bossman_v3.execution import PlanStep

from .bridges import step_to_dict
from .contracts import DelegationContract

NO_EXECUTABLE_STEPS = "no_executable_steps"


class PlannerPort(Protocol):
    def plan(self, contract: DelegationContract) -> list[dict[str, Any]] | None: ...


_URL = re.compile(r"(https?://\S+)", re.I)
_OPEN = re.compile(r"^\s*(открой|открыть|open|перейди|перейти|go to)\b", re.I)
_CREATE_FILE = re.compile(r"^\s*(создай|создать|create|запиши|записать|write)\s+(файл|file)\s+(\S+)(?:\s+(?:с текстом|с содержимым|with|containing)\s+(.+))?\s*$",
                          re.I | re.S)
_RUN = re.compile(r"^\s*(выполни|выполнить|запусти|запустить|run|execute)\s+(команду|command)?\s*(.+)$", re.I | re.S)


class DeterministicPlanner:
    """Лексикон → шаги. `supports` — реестр способностей исполнителя (bcc REGISTRY
    или тестовый набор); шаг с незарегистрированным action_type не выдаётся."""

    def __init__(self, supports: Callable[[str], bool], *, workspace: str = "") -> None:
        self.supports = supports
        self.workspace = workspace

    def plan(self, contract: DelegationContract) -> list[dict[str, Any]] | None:
        goal = contract.goal.strip()
        steps: list[PlanStep] = []
        m = _CREATE_FILE.match(goal)
        if m:
            path, text = m.group(3).strip("«»\"' "), (m.group(4) or "").strip().strip("«»\"'")
            steps = self._create_file(contract, path, text)
        elif _OPEN.match(goal) and _URL.search(goal):
            url = _URL.search(goal).group(1).rstrip(".,;")
            steps = self._open(contract, url)
        else:
            m = _RUN.match(goal)
            if m:
                steps = self._run(contract, m.group(3).strip().strip("`«»\"'"))
        allowed = {contract.required_capability} | set(contract.metadata.get("allowed_actions") or ())
        valid = [s for s in steps if self.supports(s.action.action_type) and s.action.action_type in allowed]
        if not valid or len(valid) != len(steps):
            return None
        return [step_to_dict(s) for s in valid]

    # ---------------------------------------------------------------- rules

    def _expect_for(self, contract: DelegationContract, kind: str) -> dict[str, Any] | None:
        for req in contract.evidence_required:
            if req.kind == kind:
                return {"kind": req.kind, "target": req.target, "expect": dict(req.expect or {"exists": True})}
        return None

    def _create_file(self, c: DelegationContract, path: str, text: str) -> list[PlanStep]:
        expect = self._expect_for(c, "file") or {"kind": "file", "target": path, "expect": {"exists": True}}
        cmd = f"python -c \"import pathlib; pathlib.Path({path!r}).write_text({text!r}, encoding='utf-8')\""
        args: dict[str, Any] = {"command": cmd, "expect": expect}
        if self.workspace:
            args["cwd"] = self.workspace
        return [PlanStep("s1", f"создать файл {path}", TypedAction("terminal.run", args,
                         side_effect=SideEffectClass.IDEMPOTENT_WRITE))]

    def _open(self, c: DelegationContract, url: str) -> list[PlanStep]:
        expect = self._expect_for(c, "browser") or {"kind": "browser", "target": url, "expect": {"url_contains": url}}
        return [PlanStep("s1", f"открыть {url}", TypedAction("browser.open", {"url": url, "expect": expect},
                         side_effect=SideEffectClass.READ_ONLY))]

    def _run(self, c: DelegationContract, command: str) -> list[PlanStep]:
        try:
            shlex.split(command)
        except ValueError:
            return []
        expect = self._expect_for(c, "file")
        args: dict[str, Any] = {"command": command}
        if expect:
            args["expect"] = expect
        if self.workspace:
            args["cwd"] = self.workspace
        effect = SideEffectClass.IDEMPOTENT_WRITE if expect or c.side_effect else SideEffectClass.READ_ONLY
        return [PlanStep("s1", f"выполнить {command[:60]}", TypedAction("terminal.run", args, side_effect=effect))]
