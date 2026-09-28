#!/usr/bin/env python3
"""Honest self-improvement for the 24/7 learning loop: candidate -> A/B -> OWNER approval -> promote.

1. A failed cycle leaves a QUARANTINED lesson candidate (``lesson_candidates.jsonl``,
   written by the supervisor). Triage failures become a candidate lesson = the
   message with its ground-truth labels (fake, privacy-safe data only). Other kinds
   have no offline A/B harness yet and stay quarantined (``NO_AB_HARNESS``).
2. Offline A/B on held-out tasks: baseline = the current prompt (policy + already
   promoted lessons), candidate = baseline + this lesson; evaluated on the held-out
   triage set MINUS the lesson's own message and minus promoted lessons, repeated
   (default 2x) on the LOCAL model only ($0).
3. Only a gain in EVERY repeat of at least ``min_gain_items`` AND zero new safety
   violations makes it ``GAIN_PROVEN``. Then an approval request is created in
   Bossman's own approval queue (the one the пульт shows under /approvals and the
   app shows under «Подтверждения»). Nothing is promoted by this code on its own.
4. Promotion happens only after the approval row says ``approved`` with a
   ``decided_by`` and the same lesson digest. Rejected/revoked/expired -> not promoted.
5. Rollback is one command and needs no approval (it only removes a lesson)::

       python tools/owner_journeys/lesson_pipeline.py rollback <lesson_id> --state-dir <state>

A lesson is only an example line in the triage prompt: it never grants a tool,
a permission or Computer Use, and nothing is sent or published.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.owner_journeys import triage_dataset as ds  # noqa: E402

LESSON_KIND = "learning_lesson_promotion"
TERMINAL = {"NOT_PROVEN", "REJECTED_BY_OWNER", "ROLLED_BACK", "PROMOTED", "NO_AB_HARNESS"}
DEFAULT_AB = {"repeats": 2, "min_gain_items": 2, "every_cycles": 20, "max_lessons_per_day": 6}


def _norm(text: str) -> str:
    import re
    return " ".join(re.findall(r"\w+", str(text).lower()))


def lesson_id(text: str, labels: dict[str, str]) -> str:
    raw = json.dumps({"t": _norm(text), **{k: labels.get(k) for k in ds.FIELDS}}, sort_keys=True)
    return "L" + hashlib.sha256(raw.encode()).hexdigest()[:10]


def lesson_digest(lesson: dict[str, Any]) -> str:
    body = {"text": lesson["text"], **{k: lesson["labels"][k] for k in ds.FIELDS}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ registry (durable)

class Registry:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "lessons"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "registry.json"
        self.history = self.dir / "history.jsonl"
        self.candidates = Path(state_dir) / "lesson_candidates.jsonl"

    def read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"lessons": {}, "candidates_offset": 0, "no_harness": 0, "last_ab_cycle": 0, "ab_per_day": {}}

    def write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=1))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def log(self, lid: str, status: str, **data: Any) -> None:
        with self.history.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "lesson": lid, "status": status, **data},
                                ensure_ascii=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def set_status(self, data: dict[str, Any], lid: str, status: str, **extra: Any) -> None:
        les = data["lessons"][lid]
        les["status"] = status
        les["updated"] = time.time()
        les.update(extra)
        self.log(lid, status, **{k: v for k, v in extra.items() if k != "ab"})

    # -- ingest quarantined candidates (append-only file, read from a byte offset)
    def ingest(self) -> int:
        data = self.read()
        if not self.candidates.is_file():
            return 0
        added = 0
        with self.candidates.open("rb") as fh:
            fh.seek(int(data.get("candidates_offset", 0)))
            chunk = fh.read()
        complete = chunk[: chunk.rfind(b"\n") + 1] if b"\n" in chunk else b""
        for line in complete.decode("utf-8", errors="replace").splitlines():
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            exp = c.get("expected") or {}
            text = (c.get("task") or {}).get("text")
            if c.get("kind") != "triage" or not text or not all(exp.get(k) for k in ds.FIELDS):
                data["no_harness"] = int(data.get("no_harness", 0)) + 1
                continue
            labels = {k: exp[k] for k in ds.FIELDS}
            lid = lesson_id(text, labels)
            if lid in data["lessons"]:
                data["lessons"][lid]["seen_failures"] = data["lessons"][lid].get("seen_failures", 1) + 1
                continue
            data["lessons"][lid] = {"id": lid, "text": text, "labels": labels, "source_cycle": c.get("cycle_id"),
                                    "got": c.get("got"), "status": "QUARANTINED", "created": time.time(),
                                    "seen_failures": 1, "privacy": "FAKE_NO_PII"}
            self.log(lid, "QUARANTINED", source_cycle=c.get("cycle_id"))
            added += 1
        data["candidates_offset"] = int(data.get("candidates_offset", 0)) + len(complete)
        self.write(data)
        return added

    def active(self) -> list[dict[str, Any]]:
        """Promoted lessons as prompt examples (what the triage cycles use)."""
        rows = [x for x in self.read()["lessons"].values() if x["status"] == "PROMOTED"]
        return [{"text": x["text"], **x["labels"]} for x in sorted(rows, key=lambda r: r.get("promoted_at", 0))]

    def next_for_ab(self) -> Optional[dict[str, Any]]:
        rows = [x for x in self.read()["lessons"].values() if x["status"] == "QUARANTINED"]
        return min(rows, key=lambda r: r["created"]) if rows else None

    def counts(self) -> dict[str, int]:
        data = self.read()
        out: dict[str, int] = {"no_ab_harness": int(data.get("no_harness", 0))}
        for x in data["lessons"].values():
            out[x["status"]] = out.get(x["status"], 0) + 1
        return out


# ------------------------------------------------------------------ offline A/B

def eval_items(lesson: dict[str, Any], active: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Held-out tasks for this A/B: never the lesson's own message nor any promoted lesson."""
    banned = {_norm(lesson["text"])} | {_norm(a["text"]) for a in active}
    return [h for h in ds.HELD_OUT if _norm(h["text"]) not in banned]


def system_for(examples: list[dict[str, Any]]) -> str:
    from tools.owner_journeys import learning_lab as lab
    return lab.system_prompt("candidate", examples) if examples else lab.system_prompt("baseline", [])


Ask = Callable[[str, str, str], Awaitable[tuple[str, float]]]


async def run_ab(lesson: dict[str, Any], active: list[dict[str, Any]], ask: Ask, *, repeats: int = 2,
                 min_gain_items: int = 2) -> dict[str, Any]:
    """Baseline (active lessons) vs candidate (active + lesson) on held-out items, ``repeats`` times."""
    from tools.owner_journeys import learning_lab as lab

    items = eval_items(lesson, active)
    new = {"text": lesson["text"], **lesson["labels"]}
    base_sys, cand_sys = system_for(active), system_for(active + [new])
    runs = []
    for rep in range(max(1, repeats)):
        base = await lab.evaluate(_Runner(ask), "baseline", base_sys, items)
        cand = await lab.evaluate(_Runner(ask), "candidate", cand_sys, items)
        cmp_ = lab.compare(base, cand)
        new_unsafe = sorted(set(cand["safety_violations"]) - set(base["safety_violations"]))
        runs.append({"repeat": rep + 1, "baseline_exact": base["exact"], "candidate_exact": cand["exact"],
                     "n": len(items), "delta": cmp_["delta_exact"], "fixed": len(cmp_["fixed"]),
                     "broken": len(cmp_["broken"]), "new_safety_violations": new_unsafe,
                     "errors": base["errors"] + cand["errors"],
                     "parse_failures": [base["parse_failures"], cand["parse_failures"]]})
    return {"lesson": lesson["id"], "n": len(items), "repeats": runs, **decide(runs, min_gain_items)}


def decide(runs: list[dict[str, Any]], min_gain_items: int) -> dict[str, Any]:
    reasons = []
    if len(runs) < 2:
        reasons.append("fewer than 2 repeats")
    if any(r["errors"] for r in runs):
        reasons.append("model errors during the A/B")
    if any(r["delta"] < min_gain_items for r in runs):
        reasons.append(f"gain below {min_gain_items} items in at least one repeat "
                       f"({[r['delta'] for r in runs]})")
    if any(r["new_safety_violations"] for r in runs):
        reasons.append("new safety violations")
    return {"verdict": "NOT_PROVEN" if reasons else "GAIN_PROVEN", "reasons": reasons,
            "min_gain_items": min_gain_items}


class _Runner:
    def __init__(self, ask: Ask):
        self.ask = ask


def ollama_ask(model: str) -> Ask:
    from tools.owner_journeys.learning_lab import OllamaRunner
    runner = OllamaRunner(model)
    return runner.ask


# ------------------------------------------------------------------ Bossman approval queue

class ApprovalBackend:
    """Bossman's own approvals API (the queue the пульт and the app show). Local core only."""

    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        host = urllib.parse.urlparse(base_url).hostname
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("approval backend must be the local Bossman core")
        self.base, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    @classmethod
    def from_companion(cls, override_url: str = "") -> Optional["ApprovalBackend"]:
        from tools.owner_journeys.owner_notify import COMPANION_DIR, _env_value
        token = _env_value(COMPANION_DIR / "companion.env", "TG_COMPANION_CORE_TOKEN")
        url = override_url or os.environ.get("TG_COMPANION_CORE_URL", "")
        if not url and (COMPANION_DIR / "config.json").is_file():
            url = json.loads((COMPANION_DIR / "config.json").read_text(encoding="utf-8")).get("core_url", "")
        if not token or not url:
            return None
        return cls(url, token)

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        req = urllib.request.Request(self.base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-BCC-Token": self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - loopback core
            return json.loads(resp.read().decode())

    def create(self, kind: str, preview: str) -> dict[str, Any]:
        row = self._call("POST", "/api/approvals", {"kind": kind, "preview": preview})
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise ValueError("APPROVAL_CREATE_UNCONFIRMED")
        return row

    def get(self, approval_id: int) -> Optional[dict[str, Any]]:
        rows = self._call("GET", "/api/approvals?status=all")
        return next((r for r in rows if isinstance(r, dict) and r.get("id") == approval_id), None)


def approval_preview(lesson: dict[str, Any], ab: dict[str, Any], state_dir: Path) -> str:
    runs = ", ".join(f"{r['baseline_exact']}→{r['candidate_exact']} из {r['n']}" for r in ab["repeats"])
    return (f"Урок для разбора входящих (обучение 24/7), id {lesson['id']}.\n"
            f"Пример: «{lesson['text'][:200]}» → бизнес {lesson['labels']['business']}, "
            f"намерение {lesson['labels']['intent']}, действие {lesson['labels']['action']}.\n"
            f"A/B на отложенных задачах (без этого примера): {runs}; новых нарушений безопасности нет.\n"
            "Одобрение только добавит этот пример в подсказку разбора. Прав, инструментов и "
            "Computer Use урок не даёт. Откат одной командой:\n"
            f"python tools/owner_journeys/lesson_pipeline.py rollback {lesson['id']} --state-dir \"{state_dir}\"\n"
            f"digest {lesson_digest(lesson)}")


def request_approval(reg: Registry, lid: str, backend: Optional[ApprovalBackend]) -> str:
    data = reg.read()
    les = data["lessons"][lid]
    if les["status"] not in ("GAIN_PROVEN", "APPROVAL_BACKEND_UNAVAILABLE"):
        return les["status"]
    if backend is None:
        reg.set_status(data, lid, "APPROVAL_BACKEND_UNAVAILABLE", why="no local Bossman core configured",
                       retry_at=time.time() + 600)
        reg.write(data)
        return "APPROVAL_BACKEND_UNAVAILABLE"
    try:
        row = backend.create(LESSON_KIND, approval_preview(les, les["ab"], reg.dir.parent))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        reg.set_status(data, lid, "APPROVAL_BACKEND_UNAVAILABLE", why=type(exc).__name__,
                       retry_at=time.time() + 600)
        reg.write(data)
        return "APPROVAL_BACKEND_UNAVAILABLE"
    reg.set_status(data, lid, "APPROVAL_REQUESTED", approval_id=row["id"], approval_digest=lesson_digest(les),
                   requested_at=time.time())
    reg.write(data)
    return "APPROVAL_REQUESTED"


def poll_approvals(reg: Registry, backend: Optional[ApprovalBackend]) -> list[dict[str, Any]]:
    """Promote ONLY on an owner-approved row bound to the same lesson digest."""
    changes: list[dict[str, Any]] = []
    data = reg.read()
    for lid, les in data["lessons"].items():
        if les["status"] == "APPROVAL_BACKEND_UNAVAILABLE" and backend is not None:
            if time.time() < float(les.get("retry_at", 0)):
                continue
            reg.set_status(data, lid, "GAIN_PROVEN")
            reg.write(data)
            st = request_approval(reg, lid, backend)
            data = reg.read()
            changes.append({"lesson": lid, "status": st})
            continue
        if les["status"] != "APPROVAL_REQUESTED" or backend is None:
            continue
        try:
            row = backend.get(int(les["approval_id"]))
        except (OSError, ValueError, urllib.error.URLError):
            continue
        if row is None:
            continue
        status = row.get("status")
        bound = (row.get("kind") == LESSON_KIND and f"digest {les['approval_digest']}" in str(row.get("preview"))
                 and les["approval_digest"] == lesson_digest(les))
        if status == "approved" and row.get("decided_by") and bound:
            reg.set_status(data, lid, "PROMOTED", promoted_at=time.time(), approved_by=row.get("decided_by"),
                           approval_status=status)
            changes.append({"lesson": lid, "status": "PROMOTED", "by": row.get("decided_by")})
        elif status == "approved" and not bound:
            reg.set_status(data, lid, "REJECTED_BY_OWNER", why="approval row does not match this lesson")
            changes.append({"lesson": lid, "status": "REJECTED_BY_OWNER"})
        elif status in ("rejected", "revoked", "expired"):
            reg.set_status(data, lid, "REJECTED_BY_OWNER", approval_status=status, decided_by=row.get("decided_by"))
            changes.append({"lesson": lid, "status": "REJECTED_BY_OWNER", "approval_status": status})
    reg.write(data)
    return changes


def record_ab(reg: Registry, lid: str, ab: dict[str, Any]) -> str:
    data = reg.read()
    reg.set_status(data, lid, ab["verdict"], ab=ab, ab_at=time.time())
    reg.write(data)
    return ab["verdict"]


def rollback(state_dir: Path, lid: str, by: str = "owner-cli") -> dict[str, Any]:
    reg = Registry(state_dir)
    data = reg.read()
    les = data["lessons"].get(lid)
    if les is None:
        raise KeyError(f"no lesson {lid}")
    if les["status"] not in ("PROMOTED", "APPROVAL_REQUESTED", "GAIN_PROVEN", "APPROVAL_BACKEND_UNAVAILABLE"):
        return {"lesson": lid, "status": les["status"], "changed": False}
    reg.set_status(data, lid, "ROLLED_BACK", rolled_back_by=by, rolled_back_at=time.time())
    reg.write(data)
    return {"lesson": lid, "status": "ROLLED_BACK", "changed": True}


# ------------------------------------------------------------------ CLI

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("list", "rollback", "active"))
    ap.add_argument("lesson", nargs="?", default="")
    ap.add_argument("--state-dir", default=os.path.join(os.path.expanduser("~"), "Bossman", "learning247"))
    args = ap.parse_args(argv)
    state = Path(args.state_dir)
    if args.cmd == "rollback":
        if not args.lesson:
            ap.error("rollback needs a lesson id")
        print(json.dumps(rollback(state, args.lesson), ensure_ascii=False))
        return 0
    reg = Registry(state)
    if args.cmd == "active":
        print(json.dumps(reg.active(), ensure_ascii=False, indent=1))
        return 0
    for les in reg.read()["lessons"].values():
        ab = les.get("ab") or {}
        print(f"{les['id']}  {les['status']:<30} {les['labels']}  "
              f"ab={[(r['baseline_exact'], r['candidate_exact']) for r in ab.get('repeats', [])]}  "
              f"«{les['text'][:60]}»")
    print(json.dumps(reg.counts()))
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
