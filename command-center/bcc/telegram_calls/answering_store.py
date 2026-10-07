"""Answering machine: the call LOG (one report per incoming call) and its delivery to the owner.

* A report is a small JSON file ``<calls home>/answering/reports/ar-<12 hex>.json`` (0600, atomic, secrets scrubbed, bounded) -
  the existing call store of the module, not a second database. The history entry of the call (``history.jsonl``) stays
  text-free as before; only this log carries the words, because the owner asked for a transcript of what a caller said.
* Delivery to the owner's Telegram console is a POLLED outbox, like the browser login receipts: the Telegram companion asks the
  Command Center for the reports that have ``notify`` set and no ``<id>.delivered`` marker, sends one notice to the owner
  (the existing owner-console checks apply there), and then marks the report delivered. Nothing here talks to Telegram, no
  token is read, and a lost notice is simply sent again (delivery is at-least-once; the marker is written after the send).
* A report whose outcome the owner never needs to hear about (the owner answered himself, a denied caller, STOP) is stored with
  ``notify = False`` so it is in the log but never in the outbox.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .answering_policy import detect_callback, mechanical_wants
from .hardening import redact
from .types import IncomingCall

REPORT_VERSION = 1
REPORT_ID = re.compile(r"^ar-[0-9a-f]{12}$")
KEEP_REPORTS = 200
MAX_UTTERANCE = 400
MAX_TRANSCRIPT_CHARS = 6000
MAX_NOTICE_CHARS = 3500

#: outcome -> (owner-facing Russian label, does the owner want a Telegram notice)
OUTCOMES: dict[str, tuple[str, bool]] = {
    "message_taken": ("принято сообщение", True),
    "no_message": ("ответил, но звонивший ничего не сообщил", True),
    "ended_early": ("разговор оборвался до конца", True),
    "missed": ("пропущенный звонок (Джефф не отвечал)", True),
    "busy": ("пропущенный: Джефф уже занят другим звонком", True),
    "not_ready": ("пропущенный: модели ещё не загружены", True),
    "owner_answered": ("владелец ответил сам", False),
    "denied": ("звонящий не из разрешённых — не отвечал", False),
    "stopped": ("остановлено владельцем (STOP)", False),
    "disabled": ("автоответчик выключен", False),
    "failed": ("не удалось ответить", True),
}

ANSWERED_OUTCOMES = ("message_taken", "no_message", "ended_early")
PRIVATE_FLAGS = frozenset({"credentials", "personal_data", "injection", "settings", "impersonation"})
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f  ]")


def new_report_id() -> str:
    return "ar-" + uuid.uuid4().hex[:12]


def scrub(value: Any, secrets: list[str], limit: int) -> str:
    """One bounded string with no control characters and no secret-shaped content (known secrets first, then patterns)."""
    if not isinstance(value, str):
        return ""
    text = _CTRL.sub(" ", value)
    text = " ".join(text.split())
    return redact(text, secrets)[:limit].rstrip()


def _lines(text: str, secrets: list[str], limit_each: int, max_lines: int) -> list[str]:
    parts = [scrub(p, secrets, limit_each) for p in re.split(r"[\n\r]+", text or "")]
    return [p for p in parts if p][:max_lines]


def build_report(*, call: IncomingCall, outcome: str, reason: str = "", record: dict | None = None,
                 transcript: list[dict] | None = None, summary_text: str = "", flags: set[str] | list[str] | None = None,
                 secrets: list[str] | None = None, test: bool = False, ring_delay_s: float | None = None,
                 answered: bool = False, received_at: float | None = None, now: float | None = None) -> dict:
    """The report of one incoming call. ``record`` is the finished ``CallRecord.as_dict()`` when the call was answered."""
    secrets = [s for s in (secrets or []) if isinstance(s, str)]
    label, notify = OUTCOMES.get(outcome, (outcome, True))
    rec = record or {}
    started, ended = rec.get("started_at"), rec.get("ended_at")
    duration = int(ended - started) if isinstance(started, (int, float)) and isinstance(ended, (int, float)) and ended >= started else 0
    clean_transcript: list[dict] = []
    budget = MAX_TRANSCRIPT_CHARS
    for turn in transcript or []:
        who = "caller" if turn.get("role") == "user" else "assistant"
        text = scrub(turn.get("text"), secrets, MAX_UTTERANCE)
        if not text or budget <= 0:
            continue
        budget -= len(text)
        clean_transcript.append({"role": who, "text": text})
    caller_words = [t["text"] for t in clean_transcript if t["role"] == "caller"]
    cb_requested, cb_note = detect_callback(caller_words)
    if outcome in ANSWERED_OUTCOMES:
        wants_lines = _lines(summary_text, secrets, 240, 2) or [scrub(mechanical_wants(caller_words), secrets, 240)]
        summary = [f"Суть: {wants_lines[0]}"] + [f"  {extra}" for extra in wants_lines[1:2]]
        summary.append("Просьба перезвонить: " + (f"да — «{scrub(cb_note, secrets, 160)}»" if cb_requested else "нет"))
        if len(summary) < 4:
            summary.append(f"Разговор: {duration} с, реплик звонящего: {len(caller_words)}")
    else:
        summary = [f"Итог: {label}"] + ([f"Причина: {scrub(reason, secrets, 120)}"] if reason else [])
    flag_list = sorted({scrub(f, secrets, 40) for f in (flags or []) if f})
    return {
        "version": REPORT_VERSION, "id": new_report_id(), "kind": "incoming_call",
        "call_id": scrub(rec.get("call_id") or "", secrets, 40), "transport": scrub(call.transport or rec.get("transport") or "", secrets, 20),
        "test": bool(test or (call.transport or rec.get("transport")) == "loopback"),
        "caller": {"id": int(call.caller_id) if call.known else None, "label": scrub(call.caller_label, secrets, 120), "known": call.known},
        "received_at": float(received_at if received_at is not None else call.received_at), "ended_at": ended if isinstance(ended, (int, float)) else None,
        "created_at": float(now if now is not None else time.time()),
        "duration_s": duration, "ring_delay_s": ring_delay_s, "answered": bool(answered),
        "outcome": outcome, "outcome_label": label, "reason": scrub(reason, secrets, 120),
        "call_outcome": scrub(rec.get("outcome") or "", secrets, 24), "error_code": scrub(rec.get("error_code") or "", secrets, 40),
        "summary": summary[:4], "callback": {"requested": cb_requested, "note": scrub(cb_note, secrets, 160)},
        "flags": flag_list, "transcript": clean_transcript, "turns": len(caller_words),
        "notify": bool(notify),
    }


def render_notice(report: dict) -> str:
    """The text of the owner's Telegram notice (plain text, bounded). Everything in it is data from the report."""
    caller = report.get("caller") or {}
    who = (caller.get("label") or "").strip()
    if caller.get("known"):
        who = f"{who} (id {caller.get('id')})" if who else f"id {caller.get('id')}"
    elif not who:
        who = "неизвестный звонящий"
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(report.get("received_at") or time.time()))
    lines = ["Автоответчик Джефф: входящий звонок"]
    if report.get("test"):
        lines.append("ТЕСТ БЕЗ TELEGRAM: настоящий звонок не совершался")
    lines += [f"Кто: {who}", f"Когда: {when}" + (f", разговор {report.get('duration_s')} с" if report.get("answered") else ""),
              f"Итог: {report.get('outcome_label')}"]
    lines += list(report.get("summary") or [])
    if PRIVATE_FLAGS & set(report.get("flags") or []):
        lines.append("Внимание: звонивший просил личные данные или настройки владельца — отказано.")
    transcript = report.get("transcript") or []
    if transcript:
        lines.append("")
        lines.append("Расшифровка:")
        for turn in transcript:
            lines.append(("Звонящий: " if turn.get("role") == "caller" else "Джефф: ") + str(turn.get("text")))
    lines.append("")
    lines.append(f"Полный журнал: bossman call answer reports {report.get('id')}")
    text = "\n".join(lines)
    if len(text) > MAX_NOTICE_CHARS:
        text = text[:MAX_NOTICE_CHARS - 40].rstrip() + "\n… (обрезано; полный текст в журнале)"
    return text


class AnsweringStore:
    """The report log + outbox of one calls home. Safe to use from the worker (writes) and the Command Center (reads, acks)."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.dir = self.home / "answering"
        self.reports = self.dir / "reports"

    # ------------------------------------------------------------ write
    def save(self, report: dict) -> Path:
        rid = str(report.get("id") or "")
        if not REPORT_ID.match(rid):
            raise ValueError("report id invalid")
        self.reports.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._restrict(self.dir)
        path = self.reports / f"{rid}.json"
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
            out.flush()
            os.fsync(out.fileno())
        self._restrict(tmp)
        os.replace(tmp, path)
        self.prune()
        return path

    def ack(self, report_id: str) -> bool:
        """Mark a report delivered to the owner. False when there is no such report (never creates a marker for an unknown id)."""
        if not REPORT_ID.match(str(report_id or "")) or not (self.reports / f"{report_id}.json").is_file():
            return False
        marker = self.reports / f"{report_id}.delivered"
        marker.write_text(str(int(time.time())), encoding="utf-8")
        self._restrict(marker)
        return True

    def prune(self, keep: int = KEEP_REPORTS) -> None:
        files = sorted(self.reports.glob("ar-*.json"), key=lambda p: p.stat().st_mtime)
        for old in files[:-keep] if len(files) > keep else []:
            old.unlink(missing_ok=True)
            (self.reports / (old.stem + ".delivered")).unlink(missing_ok=True)

    # ------------------------------------------------------------ read
    def get(self, report_id: str) -> dict | None:
        if not REPORT_ID.match(str(report_id or "")):
            return None
        path = self.reports / f"{report_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return self._with_state(data) if isinstance(data, dict) else None

    def list(self, limit: int = 50) -> list[dict]:
        if not self.reports.is_dir():
            return []
        files = sorted(self.reports.glob("ar-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, min(limit, KEEP_REPORTS))]
        out = []
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                out.append(self._with_state(data))
        return out

    def pending(self, limit: int = 20) -> list[dict]:
        """Reports the owner has to be told about, oldest first (so the notices arrive in the order the calls came)."""
        rows = [r for r in self.list(KEEP_REPORTS) if r.get("notify") and not r.get("delivered")]
        rows.sort(key=lambda r: r.get("created_at") or 0)
        return rows[:limit]

    def _with_state(self, data: dict) -> dict:
        rid = str(data.get("id") or "")
        return {**data, "delivered": (self.reports / f"{rid}.delivered").is_file()}

    @staticmethod
    def _restrict(path: Path) -> None:
        try:
            from ..auth import _restrict_to_owner
            _restrict_to_owner(path)
        except Exception:  # noqa: BLE001 - best effort; the doctor re-checks the calls home
            pass
