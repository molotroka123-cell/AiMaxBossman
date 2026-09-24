"""Compile verified task traces into reusable skill manifests.

Compilation is not promotion. The compiler requires an independently verified
unseen transfer result and then creates an EXPERIMENTAL SkillCandidate for the
existing SkillFactory shadow/production gates.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from .factory import SkillCandidate, SkillFactory, TraceStep


@dataclass(frozen=True)
class CompiledSkill:
    name: str
    task_class: str
    source_sha: str
    verifier_ref: str
    transfer_ref: str
    trigger_terms: tuple[str, ...]
    workflow_fingerprint: str
    candidate: SkillCandidate

    def manifest(self) -> dict:
        return {
            "schema": "bossman.compiled-skill/1",
            "name": self.name,
            "task_class": self.task_class,
            "source_sha": self.source_sha,
            "verifier_ref": self.verifier_ref,
            "transfer_ref": self.transfer_ref,
            "trigger_terms": list(self.trigger_terms),
            "workflow_fingerprint": self.workflow_fingerprint,
            "stage": self.candidate.stage.value,
            "input_schema": dict(self.candidate.input_schema),
            "output_schema": dict(self.candidate.output_schema),
            "actions": [asdict(a) for a in self.candidate.actions],
        }


class SkillCompiler:
    def __init__(self, factory: SkillFactory | None = None):
        self.factory = factory or SkillFactory()

    @staticmethod
    def _fingerprint(trace: Sequence[TraceStep]) -> str:
        rows = []
        for step in trace:
            action = step.action
            rows.append({
                "type": action.action_type,
                "args_keys": sorted(action.args),
                "side_effect": getattr(action.side_effect, "value", str(action.side_effect)),
                "verified": step.verified,
            })
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()

    def compile(self, *, name: str, task_class: str, trace: Sequence[TraceStep],
                input_schema: Mapping[str, str], output_schema: Mapping[str, str],
                source_sha: str, verifier_ref: str, transfer_ref: str,
                unseen_transfer_passed: bool, verifier_independent: bool,
                trigger_terms: Sequence[str] = ()) -> CompiledSkill:
        if len(source_sha) != 40:
            raise ValueError("source_sha")
        if not verifier_ref or not transfer_ref:
            raise ValueError("evidence refs required")
        if not unseen_transfer_passed:
            raise ValueError("unseen transfer did not pass")
        if not verifier_independent:
            raise ValueError("verifier must be independent")
        if not task_class:
            raise ValueError("task_class")
        candidate = self.factory.from_verified_trace(
            name, trace, input_schema=input_schema, output_schema=output_schema
        )
        return CompiledSkill(
            name=name,
            task_class=task_class,
            source_sha=source_sha,
            verifier_ref=verifier_ref,
            transfer_ref=transfer_ref,
            trigger_terms=tuple(dict.fromkeys(str(x) for x in trigger_terms if str(x).strip())),
            workflow_fingerprint=self._fingerprint(trace),
            candidate=candidate,
        )
