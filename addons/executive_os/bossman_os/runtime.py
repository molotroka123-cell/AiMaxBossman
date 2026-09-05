"""Host-owned local mission runtime over existing Bossman execution/memory ports.

An authenticated owner submits bounded artifact contracts. Models can propose
text but cannot grant capabilities, change limits, sign proofs or promote code.
Existing Core/BCC/Fleet tasks remain owned by their existing authorities.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

from bossman_shared import evidence
from bossman_shared.reasoning_protocol import reasoning_protocol_prompt

from .planning import (checkpoint_interval, choose_route, effective_capabilities,
                       evaluate_release, ready_nodes, select_context, wilson)
from .store import Store


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise ValueError("invalid identifier")
    return value


class Runtime:
    """Only explicitly submitted local missions are managed by this add-on."""

    ACTIONS = frozenset({"artifact.write", "artifact.verify"})
    LIMITS = {"slots": 2, "ram_mb": 8192, "gpu_mb": 8192}

    def __init__(self, state_root, artifact_root):
        self.state_root = Path(state_root).resolve()
        self.artifact_root = Path(artifact_root).resolve()
        if (self.state_root == self.artifact_root or self.state_root.is_relative_to(self.artifact_root)
                or self.artifact_root.is_relative_to(self.state_root)):
            raise ValueError("state and artifacts require separate sibling roots")
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.key = evidence.load_or_create_key(self.state_root / "keys" / "proof.key")
        self.store = Store(self.state_root / "missions.sqlite", self.LIMITS)
        self.owner_capabilities = self.ACTIONS
        self.project_capabilities = {}
        self.role_capabilities = {"local-artifact-worker": self.ACTIONS}
        self._local_url = "http://127.0.0.1:11435/v1"
        # Host-approved installed local model IDs, not request/model-authored URLs.
        self._models = ("qwen2.5:7b", "llama3.2:latest")

    @contextmanager
    def memory(self):
        from bossman.context_engine import ContextEngine
        engine = ContextEngine(self.state_root / "context.sqlite")
        try:
            yield engine
        finally:
            engine.close()

    def _target(self, mission_id, relative):
        identifier(mission_id)
        if (not isinstance(relative, str) or not relative or len(relative) > 240
                or "\\" in relative or ":" in relative
                or any(p in ("", ".", "..") for p in relative.split("/"))):
            raise ValueError("artifact path must be a confined relative path")
        for part in relative.split("/"):
            stem = part.split(".")[0].upper()
            if (part.endswith((".", " ")) or any(ord(c) < 32 or c in '<>"|?*' for c in part)
                    or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                    or re.fullmatch(r"(?:COM|LPT)[0-9¹²³]", stem)):
                raise ValueError("nonportable Windows artifact path")
        base = self.artifact_root / mission_id
        target = base / relative
        if target.is_symlink() or not target.resolve().is_relative_to(base.resolve()):
            raise PermissionError("artifact path escaped mission")
        if not base.resolve().is_relative_to(self.artifact_root):
            raise PermissionError("mission root escaped artifact mount")
        return target

    def submit(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"id", "project", "steps", "context_roots"}:
            raise ValueError("unknown mission fields")
        mid = identifier(payload.get("id"))
        project = identifier(payload.get("project", "default"))
        raw = payload.get("steps")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 64:
            raise ValueError("mission needs 1..64 steps")
        steps = []
        for item in raw:
            if not isinstance(item, dict) or set(item) - {"id", "depends_on", "action", "path", "content"}:
                raise ValueError("unknown step fields")
            sid = identifier(item.get("id"))
            action = item.get("action")
            if action not in self.ACTIONS:
                raise PermissionError("action is not admitted by host")
            self._target(mid, item.get("path"))
            content = item.get("content")
            if not isinstance(content, str) or len(content.encode("utf-8")) > 65536:
                raise ValueError("expected UTF-8 artifact content exceeds contract limit")
            step = {"id": sid, "depends_on": item.get("depends_on", []),
                    "action": action, "path": item["path"], "content": content}
            step["effect_digest"] = digest(step)
            steps.append(step)
        ready_nodes(self._dag(steps), [])
        write_paths = [s["path"].casefold() for s in steps if s["action"] == "artifact.write"]
        if len(write_paths) != len(set(write_paths)):
            raise ValueError("each artifact path has one immutable writer")
        roots = payload.get("context_roots", [])
        if not isinstance(roots, list) or any(not isinstance(x, str) for x in roots):
            raise ValueError("context roots must be memory IDs")
        contract = {"project": project, "steps": steps, "context_roots": roots,
                    "local_only": True, "cost_microusd": 0, "kind": "artifact-mission"}
        self._context(project, roots)  # required dependency/expiry failure before admission
        return self.store.create(mid, contract)

    @staticmethod
    def _dag(steps):
        return [{"id": s["id"], "depends_on": s["depends_on"]} for s in steps]

    def _context(self, project, roots):
        from bossman.context_engine import MemoryStatus
        with self.memory() as engine:
            rows = engine.store.memories(project, (MemoryStatus.ACTIVE, MemoryStatus.DISPUTED))
            facts = [{"id": row.memory_id, "text": row.text,
                      "source": "|".join(row.source_refs),
                      "expires_at": row.metadata.get("expires_at", 2**53-1),
                      "privacy": "LOCAL", "depends_on": row.metadata.get("depends_on", [])}
                     for row in rows if row.project == project and row.status == MemoryStatus.ACTIVE]
        return select_context(facts, roots, 1200, time.time(), cloud=False)

    def _authorize(self, action, mission_id):
        project = self.store.snapshot(mission_id)["contract"]["project"]
        allowed = effective_capabilities(self.owner_capabilities,
                                         self.project_capabilities.get(project, self.ACTIONS),
                                         self.role_capabilities["local-artifact-worker"],
                                         self.ACTIONS, {action.action_type})
        if action.action_type not in allowed:
            raise PermissionError("owner/project/role/tool intersection denied action")
        self._target(mission_id, action.args["path"])

    def _execute(self, mid, step, actor, fence):
        from bossman_v3.computer_agent.agent import UniversalComputerAgent
        from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation,
                                         PolicyDecision, SideEffectClass, TypedAction, VerificationResult)
        runtime = self
        target = self._target(mid, step["path"])
        expected = step["content"].encode("utf-8")
        class Policy:
            def authorize(self, action, context):
                runtime._authorize(action, mid)
                return PolicyDecision(True)
        class Approval:
            def request(self, *args):
                return ApprovalDecision(False, reason="no implicit approval escalation")
        class Executor:
            def supports(self, action_type):
                return action_type in runtime.ACTIONS
            def execute(self, action):
                runtime._authorize(action, mid)
                now = datetime.now(timezone.utc)
                if action.action_type == "artifact.write":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    runtime._target(mid, step["path"])
                    # Unique mission workspace and O_EXCL: never overwrite an
                    # existing artifact or blindly repeat an uncertain write.
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(expected)
                        stream.flush()
                        os.fsync(stream.fileno())
                return ExecutionReceipt(action.action_type, now, datetime.now(timezone.utc),
                                        f"{mid}/{step['id']}/{fence}", (str(target),))
        class Observer:
            def observe_fresh(self, action, receipt):
                runtime._target(mid, step["path"])
                actual = target.read_bytes()
                return Observation(datetime.now(timezone.utc), "host-file-reader",
                                   {"sha256": hashlib.sha256(actual).hexdigest()}, (str(target),))
        class Verifier:
            def verify(self, action, receipt, observation):
                return VerificationResult(observation.state["sha256"] == hashlib.sha256(expected).hexdigest(),
                                          evidence_refs=(str(target),))
        action = TypedAction(step["action"], {"path": step["path"]}, scopes=(mid,),
                             side_effect=SideEffectClass.IDEMPOTENT_WRITE if step["action"] == "artifact.write"
                             else SideEffectClass.READ_ONLY,
                             idempotency_key=f"{mid}:{step['id']}", source="executive-os-owner-contract")
        result = UniversalComputerAgent(Policy(), Approval(), Executor(), Observer(), Verifier()).run(action)
        if not result.verification.passed:
            raise RuntimeError("independent post-state verification rejected outcome")
        return self._receipt(mid, step, actor, fence)

    def _receipt(self, mid, step, actor, fence):
        snap = self.store.snapshot(mid)
        target = self._target(mid, step["path"])
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        expected = hashlib.sha256(step["content"].encode("utf-8")).hexdigest()
        if actual != expected:
            raise RuntimeError("post-state does not match owner contract")
        body = {"mission_id": mid, "contract_digest": snap["contract_digest"],
                "step_id": step["id"], "effect_digest": step["effect_digest"],
                "actor": actor, "fence": fence,
                "dispatch_binding": digest([snap["contract_digest"], step["id"], fence]),
                "observed_sha256": actual, "expected_sha256": expected,
                "verifier_principal": "host-file-reader", "observed_at": time.time()}
        return body | evidence.sign_fields(body, signer="bossman_v3.verifier", key=self.key)

    def _check_receipt(self, mid, step, receipt, *, observe=True):
        snap = self.store.snapshot(mid)
        stored = next(s for s in snap["steps"] if s["id"] == step["id"])
        if (not isinstance(receipt, dict) or not evidence.verify_signed(receipt, key=self.key)
                or receipt.get("mission_id") != mid or receipt.get("step_id") != step["id"]
                or receipt.get("contract_digest") != snap["contract_digest"]
                or receipt.get("effect_digest") != step["effect_digest"]
                or receipt.get("actor") != stored["actor"] or receipt.get("fence") != stored["fence"]
                or receipt.get("dispatch_binding") != digest([snap["contract_digest"], step["id"], stored["fence"]])
                or receipt.get("expected_sha256") != hashlib.sha256(step["content"].encode()).hexdigest()
                or receipt.get("observed_sha256") != receipt.get("expected_sha256")
                or receipt.get("verifier_principal") != "host-file-reader"
                or receipt.get("verifier_principal") == receipt.get("actor")):
            raise RuntimeError("invalid bound host receipt")
        if observe and hashlib.sha256(self._target(mid, step["path"]).read_bytes()).hexdigest() != receipt["observed_sha256"]:
            raise RuntimeError("artifact diverged after confirmation")

    def _remember(self, mid, step, project, receipt):
        # Only fixed labels and hashes; model/clinical artifact contents are not
        # promoted into durable instructions or public evidence.
        with self.memory() as engine:
            row = engine.memory.fact(f"Confirmed artifact digest {receipt['observed_sha256']}",
                                     project=project, source_refs=[f"os-receipt:{mid}/{step['id']}"],
                                     memory_id=f"os-{digest([mid, step['id']])}")
            engine.memory.promote(row.memory_id, verified=True)

    def run(self, mission_id):
        mid = identifier(mission_id)
        while True:
            snap = self.store.snapshot(mid)
            contract = snap["contract"]
            if contract.get("kind") != "artifact-mission":
                raise ValueError("only artifact missions can execute typed actions")
            self._context(contract["project"], contract["context_roots"])
            completed = {s["id"] for s in snap["steps"] if s["state"] == "verified"}
            for stored in snap["steps"]:
                if stored["id"] in completed:
                    step = next(s for s in contract["steps"] if s["id"] == stored["id"])
                    self._check_receipt(mid, step, stored["receipt"])
            candidates = ready_nodes(self._dag(contract["steps"]), completed)
            pending = {s["id"] for s in snap["steps"] if s["state"] == "ready"}
            chosen = next((sid for sid in candidates if sid in pending), None)
            if chosen is None:
                return self.snapshot(mid)
            step = next(s for s in contract["steps"] if s["id"] == chosen)
            actor = "local-artifact-worker"
            claimed = self.store.claim(mid, chosen, actor, snap["version"],
                                       {"slots": 1, "ram_mb": 16, "gpu_mb": 0})
            claimed_step = next(s for s in claimed["steps"] if s["id"] == chosen)
            fence = claimed_step["fence"]
            try:
                receipt = self._execute(mid, step, actor, fence)
                self._check_receipt(mid, step, receipt)
                self.store.confirm(mid, chosen, actor, fence, receipt)
                self._remember(mid, step, contract["project"], receipt)
            except Exception:
                # A failed post-confirm audit is not permission to repeat IO.
                current = self.store.snapshot(mid)
                state = next(s["state"] for s in current["steps"] if s["id"] == chosen)
                if state == "running":
                    self.store.uncertain(mid, chosen, actor, fence)
                raise

    def snapshot(self, mission_id):
        snap = self.store.snapshot(identifier(mission_id))
        proofs = []
        proof_errors = []
        for stored in snap["steps"]:
            if stored["state"] == "verified":
                step = next(s for s in snap["contract"]["steps"] if s["id"] == stored["id"])
                try:
                    if snap["contract"].get("kind") == "local-proposal":
                        self._check_proposal(snap, stored)
                    else:
                        self._check_receipt(mission_id, step, stored["receipt"])
                    proofs.append(stored["id"])
                except (ValueError, RuntimeError, OSError):
                    proof_errors.append(stored["id"])
        snap["verified_now"] = proofs
        snap["proof_errors"] = proof_errors
        snap["done"] = len(proofs) == len(snap["steps"])
        snap["checkpoint_interval_seconds"] = checkpoint_interval(0.05, 1/3600)
        return snap

    def recover(self, mission_id):
        snap = self.store.snapshot(identifier(mission_id))
        for stored in snap["steps"]:
            if stored["state"] not in ("running", "unknown"):
                continue
            step = next(s for s in snap["contract"]["steps"] if s["id"] == stored["id"])
            if step["action"] not in self.ACTIONS:
                continue
            try:
                from bossman_v3.contracts import TypedAction
                self._authorize(TypedAction(step["action"], {"path": step["path"]}), mission_id)
                receipt = self._receipt(mission_id, step, stored["actor"], stored["fence"])
                self._check_receipt(mission_id, step, receipt)
            except (RuntimeError, OSError):
                continue  # absence/mismatch is NOT proof that replay is safe
            self.store.confirm(mission_id, stored["id"], stored["actor"], stored["fence"], receipt)
            self._remember(mission_id, step, snap["contract"]["project"], receipt)
        return self.snapshot(mission_id)

    def status(self):
        return {"enabled": True, "version": "0.1.0", "mode": "managed-local-missions",
                "existing_tasks_intercepted": False, "cloud_enabled": False,
                "automatic_skill_promotion": False, "physical_resource_enforcement": False,
                "limits": self.LIMITS, "actions": sorted(self.ACTIONS),
                "unresolved": self.store.recover_read(),
                "memory": "existing Core ContextEngine", "execution": "existing V3 UniversalComputerAgent",
                "auth": "existing BCC owner token/session"}

    def _check_proposal(self, snap, stored):
        receipt = stored["receipt"]
        if (not isinstance(receipt, dict) or not evidence.verify_signed(receipt, key=self.key)
                or receipt.get("mission_id") != snap["id"]
                or receipt.get("contract_digest") != snap["contract_digest"]
                or receipt.get("effect_digest") != stored["effect_digest"]
                or receipt.get("step_id") != stored["id"]
                or receipt.get("actor") != stored["actor"] or receipt.get("fence") != stored["fence"]
                or receipt.get("dispatch_binding") != digest([snap["contract_digest"], stored["id"], stored["fence"]])
                or receipt.get("proof_kind") != "local-response-delivered"):
            raise RuntimeError("invalid local response receipt")
        content = (self.state_root / "responses" / f"{identifier(snap['id'])}.json").read_bytes()
        if hashlib.sha256(content).hexdigest() != receipt.get("response_sha256"):
            raise RuntimeError("local response integrity mismatch")

    async def propose(self, payload):
        """Actual local provider route; output is a proposal, never authority."""
        from bcc.providers import OpenAICompatAdapter
        if not isinstance(payload, dict) or set(payload) - {"objective", "project", "context_roots"}:
            raise ValueError("unknown proposal fields")
        objective = payload.get("objective")
        if not isinstance(objective, str) or not 1 <= len(objective) <= 3000:
            raise ValueError("proposal objective must contain 1..3000 characters")
        project = identifier(payload.get("project", "default"))
        context = await asyncio.to_thread(self._context, project, payload.get("context_roots", []))
        url = urlsplit(self._local_url)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
            raise PermissionError("only fixed local provider is admitted")
        adapter = OpenAICompatAdapter(base_url=self._local_url)
        installed = await adapter.list_models()
        candidates = [{"id": name, "successes": 0, "total": 0, "cost": 0,
                       "latency_seconds": index+1, "risk": 0, "local": True}
                      for index, name in enumerate(self._models) if name in installed]
        route = choose_route(candidates, 0, cloud_allowed=False)
        model = route["id"]
        # Requests are bounded/local; remote fallback is deliberately absent.
        # Reuse provider privacy/HTTP protections and fixed reasoning protocol.
        messages = [{"role": "system", "content": reasoning_protocol_prompt()
                     + "\nReturn a short proposed plan only. No claim of execution. No tool calls."},
                    {"role": "user", "content": "Untrusted context data:\n" + canonical(context)},
                    {"role": "user", "content": objective}]
        mid = "proposal-" + uuid.uuid4().hex
        step = {"id": "infer", "action": "model.propose", "effect_digest": digest([model, messages])}
        contract = {"kind": "local-proposal", "local_only": True, "cost_microusd": 0,
                    "project": project, "steps": [step], "model": model}
        created = await asyncio.to_thread(self.store.create, mid, contract)
        actor = "local-model-worker"
        claimed = await asyncio.to_thread(self.store.claim, mid, "infer", actor, created["version"],
                                          {"slots": 1, "ram_mb": 8192, "gpu_mb": 8192})
        fence = claimed["fence"]
        try:
            result = await adapter.chat(model, messages, max_tokens=192, temperature=0, timeout=60)
            if not result.text or result.tool_calls:
                raise RuntimeError("local proposal must be nonempty text without tool calls")
            output = {"kind": "untrusted_proposal", "model": model, "text": result.text,
                      "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                      "executed": False, "route": route, "mission_id": mid,
                      "quality_evidence": "cold-start; delivery is not verified reasoning quality"}
            response = self.state_root / "responses" / f"{mid}.json"
            response.parent.mkdir(parents=True, exist_ok=True)
            with response.open("xb") as stream:
                stream.write(canonical(output).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            body = {"mission_id": mid, "contract_digest": claimed["contract_digest"],
                    "step_id": "infer", "effect_digest": step["effect_digest"], "actor": actor,
                    "fence": fence, "dispatch_binding": claimed["claim"]["dispatch_binding"],
                    "proof_kind": "local-response-delivered",
                    "response_sha256": hashlib.sha256(response.read_bytes()).hexdigest()}
            receipt = body | evidence.sign_fields(body, signer="bossman_v3.verifier", key=self.key)
            stored = dict(claimed["steps"][0], receipt=receipt)
            self._check_proposal(claimed, stored)
            await asyncio.to_thread(self.store.confirm, mid, "infer", actor, fence, receipt)
            return output
        except BaseException:
            current = self.store.snapshot(mid)
            if current["steps"][0]["state"] == "running":
                self.store.uncertain(mid, "infer", actor, fence)
            raise

    def evaluate(self, payload):
        """Freeze suite contracts and compare verified outcomes; never edits code."""
        if not isinstance(payload, dict) or set(payload) != {"suite_id", "phase", "cases"}:
            raise ValueError("evaluation requires suite_id, phase, cases")
        suite = identifier(payload["suite_id"])
        phase = payload["phase"]
        cases = payload["cases"]
        if phase not in ("baseline", "candidate") or not isinstance(cases, dict) or not cases:
            raise ValueError("invalid suite phase/cases")
        results, definitions, hard_failures = {}, {}, 0
        for case, mid in cases.items():
            identifier(case)
            snap = self.snapshot(mid)
            if snap["contract"].get("kind") != "artifact-mission":
                raise ValueError("model text delivery cannot count as verified task quality")
            definitions[case] = snap["contract_digest"]
            results[case] = snap["done"]
            hard_failures += len(snap["proof_errors"])
        suite_digest = digest(definitions)
        run = {"suite_digest": suite_digest, "cases": results, "hard_failures": hard_failures}
        path = self.state_root / "evaluations" / f"{suite}.{phase}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"run": run, "definitions": definitions, "missions": cases}
        gate = None
        if phase == "candidate":
            base = json.loads((path.parent / f"{suite}.baseline.json").read_text(encoding="utf-8"))["run"]
            if base["suite_digest"] != suite_digest:
                raise ValueError("candidate must use the unchanged baseline suite")
            gate = evaluate_release(base, run, suite_digest)
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != body:
                raise ValueError("evaluation phase is immutable")
        else:
            with path.open("x", encoding="utf-8") as f:
                f.write(canonical(body))
                f.flush()
                os.fsync(f.fileno())
        passed = sum(results.values())
        report = {"suite_digest": suite_digest, "passed": passed, "total": len(results),
                  "score": 10000*passed/len(results), "wilson95": wilson(passed, len(results)),
                  "scope": "these fixed local owner missions only", "promoted": False}
        if gate is not None:
            report["release_gate"] = gate
        return report
