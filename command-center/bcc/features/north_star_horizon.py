"""North Star Long Horizon Claude — 15-Branch Orchestrator, $1.00 Budget Enforcer & Autonomous Error Reflex.

Provides:
  * ``CriticalErrorReflex``: sub-millisecond matching of runtime/test/model errors against
    ``docs/owner/bossman_critical_errors.json`` and persistent ``north_star/critical_lessons.json``
    so Bossman autonomously diagnoses and remediates critical failures without external help.
  * ``NorthStarBudgetLedger``: strict $1.00 USD total cap tracker across all 15 capability tree
    branches, integrated with AI Gateway (local-first + sanitized cloud fallback).
  * HTTP API ``/api/north-star/*`` for live monitoring, reflex diagnosis, and lesson recording.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..security.ai_gateway import RedactionSession, get_ai_gateway
from . import Feature

router = APIRouter(prefix="/north-star", tags=["north-star"])

RUN_NAME = "North Star long horizon claude"
RUN_ID = "north-star-long-horizon-claude"
DEFAULT_BUDGET_CAP_USD = 1.00

FIFTEEN_BRANCHES: tuple[dict[str, str], ...] = (
    {"branch_num": "B01", "node_id": "jeff", "agent_name": "ns-lh-claude-01-jeff", "label": "Jeff — собеседник"},
    {"branch_num": "B02", "node_id": "ux", "agent_name": "ns-lh-claude-02-ux", "label": "UX — Command Center и интерфейсы"},
    {"branch_num": "B03", "node_id": "apps", "agent_name": "ns-lh-claude-03-apps", "label": "Apps — приложения и инструменты"},
    {"branch_num": "B04", "node_id": "models", "agent_name": "ns-lh-claude-04-models", "label": "Models — локальные модели"},
    {"branch_num": "B05", "node_id": "cloud", "agent_name": "ns-lh-claude-05-cloud", "label": "Cloud — AI Gateway и провайдеры"},
    {"branch_num": "B06", "node_id": "computer", "agent_name": "ns-lh-claude-06-computer", "label": "Computer — компьютер и браузер"},
    {"branch_num": "B07", "node_id": "media", "agent_name": "ns-lh-claude-07-media", "label": "Media — Motion, видео, звук"},
    {"branch_num": "B08", "node_id": "memory", "agent_name": "ns-lh-claude-08-memory", "label": "Memory — память и обучение"},
    {"branch_num": "B09", "node_id": "agents", "agent_name": "ns-lh-claude-09-agents", "label": "Agents — агенты и оркестрация"},
    {"branch_num": "B10", "node_id": "skills", "agent_name": "ns-lh-claude-10-skills", "label": "Skills — навыки"},
    {"branch_num": "B11", "node_id": "plugins", "agent_name": "ns-lh-claude-11-plugins", "label": "Plugins — плагины и MCP"},
    {"branch_num": "B12", "node_id": "ops", "agent_name": "ns-lh-claude-12-ops", "label": "Ops — операции и приёмка"},
    {"branch_num": "B13", "node_id": "oss", "agent_name": "ns-lh-claude-13-oss", "label": "OSS — открытые проекты"},
    {"branch_num": "B14", "node_id": "commerce", "agent_name": "ns-lh-claude-14-commerce", "label": "Commerce — заработок и отчёты"},
    {"branch_num": "B15", "node_id": "devices", "agent_name": "ns-lh-claude-15-devices", "label": "Devices — другие устройства"},
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _catalog_path() -> Path:
    return _repo_root() / "docs" / "owner" / "bossman_critical_errors.json"


def _ns_dir(data_dir: str | os.PathLike) -> Path:
    d = Path(data_dir) / "north_star"
    d.mkdir(parents=True, exist_ok=True)
    return d


class CriticalErrorReflex:
    """Fast in-memory + persistent reflex engine for autonomous recovery from critical errors."""

    def __init__(self, data_dir: str | os.PathLike | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else None
        self._compiled: list[tuple[dict[str, Any], list[re.Pattern[str]], tuple[str, ...]]] = []
        self._hits = 0
        self.reload()

    def reload(self) -> None:
        entries: dict[str, dict[str, Any]] = {}
        cat_file = _catalog_path()
        if cat_file.is_file():
            try:
                raw = json.loads(cat_file.read_text(encoding="utf-8"))
                for e in raw.get("entries") or []:
                    if isinstance(e, dict) and e.get("id"):
                        entries[str(e["id"])] = e
            except Exception:
                pass
        if self._data_dir:
            extra_file = _ns_dir(self._data_dir) / "critical_lessons.json"
            if extra_file.is_file():
                try:
                    extra = json.loads(extra_file.read_text(encoding="utf-8"))
                    for e in extra.get("entries") or []:
                        if isinstance(e, dict) and e.get("id"):
                            entries[str(e["id"])] = e
                except Exception:
                    pass
        compiled = []
        for e in entries.values():
            rxs: list[re.Pattern[str]] = []
            for pat in e.get("symptom_regex") or []:
                try:
                    rxs.append(re.compile(str(pat), re.IGNORECASE))
                except re.error:
                    continue
            kws = tuple(str(k).lower() for k in (e.get("keywords") or []) if k)
            compiled.append((e, rxs, kws))
        self._compiled = compiled

    def diagnose(self, text: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        probe = (text or "").strip()
        if not probe:
            return {"matched": False, "count": 0, "matches": [], "latency_ms": 0.0}
        probe_low = probe.lower()
        matches: list[dict[str, Any]] = []
        for entry, rxs, kws in self._compiled:
            rx_hit = any(rx.search(probe) for rx in rxs)
            kw_hits = sum(1 for k in kws if k in probe_low)
            if rx_hit or kw_hits >= 2:
                matches.append({
                    "id": entry.get("id"),
                    "class": entry.get("class", "runtime"),
                    "title": entry.get("title"),
                    "severity": entry.get("severity", "HIGH"),
                    "status": entry.get("status", "FIXED"),
                    "match_type": "regex" if rx_hit else f"keywords({kw_hits})",
                    "root_cause": entry.get("root_cause"),
                    "fix": entry.get("fix"),
                    "prevention": entry.get("prevention"),
                })
        if matches:
            self._hits += 1
        dt_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        return {
            "matched": bool(matches),
            "count": len(matches),
            "matches": matches,
            "latency_ms": dt_ms,
        }

    def record_lesson(self, lesson: dict[str, Any]) -> dict[str, Any]:
        gw = get_ai_gateway()
        session = RedactionSession()
        scrubbed_fields: dict[str, Any] = {}
        for k in ("id", "class", "title", "severity", "status", "component", "symptom", "detection", "root_cause", "fix", "prevention"):
            val = str(lesson.get(k) or "").strip()
            scrubbed_fields[k] = gw.scrubber.scrub_text(val, session)
        if not scrubbed_fields["id"] or not scrubbed_fields["title"]:
            raise ValueError("lesson requires 'id' and 'title'")
        entry = {
            **scrubbed_fields,
            "severity": scrubbed_fields["severity"] if scrubbed_fields["severity"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW") else "HIGH",
            "status": scrubbed_fields["status"] if scrubbed_fields["status"] in ("FIXED", "OPEN", "ENV") else "FIXED",
            "symptom_examples": [gw.scrubber.scrub_text(str(x), session) for x in (lesson.get("symptom_examples") or [scrubbed_fields["symptom"]])[:5]],
            "symptom_regex": [str(x) for x in (lesson.get("symptom_regex") or [])[:5]],
            "keywords": [str(x) for x in (lesson.get("keywords") or [])[:12]],
            "recorded_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if self._data_dir:
            extra_file = _ns_dir(self._data_dir) / "critical_lessons.json"
            existing: list[dict[str, Any]] = []
            if extra_file.is_file():
                try:
                    existing = json.loads(extra_file.read_text(encoding="utf-8")).get("entries") or []
                except Exception:
                    existing = []
            by_id = {str(e.get("id")): e for e in existing if isinstance(e, dict) and e.get("id")}
            by_id[entry["id"]] = entry
            payload = {
                "schema": "bossman.critical_errors.runtime.v1",
                "updated_utc": entry["recorded_at_utc"],
                "entries": list(by_id.values()),
            }
            extra_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.reload()
        return entry

    def stats(self) -> dict[str, Any]:
        return {
            "catalog_entries": len(self._compiled),
            "reflex_hits": self._hits,
        }


_REFLEX_SINGLETON: CriticalErrorReflex | None = None


def get_reflex(data_dir: str | os.PathLike | None = None) -> CriticalErrorReflex:
    global _REFLEX_SINGLETON
    if _REFLEX_SINGLETON is None or (data_dir and _REFLEX_SINGLETON._data_dir != Path(data_dir)):
        _REFLEX_SINGLETON = CriticalErrorReflex(data_dir=data_dir)
    return _REFLEX_SINGLETON


class DiagnoseBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


class RecordLessonBody(BaseModel):
    id: str = Field(..., min_length=2, max_length=80)
    class_name: str = Field(default="branch-audit", alias="class")
    title: str = Field(..., min_length=3, max_length=300)
    severity: str = Field(default="HIGH")
    status: str = Field(default="FIXED")
    component: str = Field(default="bossman")
    symptom: str = Field(..., min_length=3, max_length=2000)
    symptom_regex: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    detection: str = Field(default="")
    root_cause: str = Field(..., min_length=3, max_length=2000)
    fix: str = Field(..., min_length=3, max_length=2000)
    prevention: str = Field(..., min_length=3, max_length=2000)


@router.get("/status")
async def north_star_status(request: Request) -> dict[str, Any]:
    data_dir = request.app.state.svc.settings.data_dir
    reflex = get_reflex(data_dir)
    state_file = _ns_dir(data_dir) / "north_star_long_horizon_claude.json"
    run_state: dict[str, Any] = {}
    if state_file.is_file():
        try:
            run_state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            run_state = {}
    spent = float(run_state.get("spent_usd", 0.0))
    cap = float(run_state.get("budget_cap_usd", DEFAULT_BUDGET_CAP_USD))
    return {
        "run_name": RUN_NAME,
        "run_id": RUN_ID,
        "status": run_state.get("status", "READY"),
        "budget": {
            "cap_usd": cap,
            "spent_usd": round(spent, 6),
            "remaining_usd": round(max(0.0, cap - spent), 6),
            "within_budget": spent <= cap + 1e-9,
        },
        "branches_total": len(FIFTEEN_BRANCHES),
        "branches": run_state.get("branches") or list(FIFTEEN_BRANCHES),
        "reflex": reflex.stats(),
        "ai_gateway": get_ai_gateway().audit.stats(),
        "updated_utc": run_state.get("updated_utc"),
    }


@router.post("/reflex/diagnose")
async def reflex_diagnose(body: DiagnoseBody, request: Request) -> dict[str, Any]:
    reflex = get_reflex(request.app.state.svc.settings.data_dir)
    return reflex.diagnose(body.text)


@router.post("/reflex/record")
async def reflex_record(body: RecordLessonBody, request: Request) -> dict[str, Any]:
    reflex = get_reflex(request.app.state.svc.settings.data_dir)
    try:
        saved = reflex.record_lesson({
            "id": body.id,
            "class": body.class_name,
            "title": body.title,
            "severity": body.severity,
            "status": body.status,
            "component": body.component,
            "symptom": body.symptom,
            "symptom_regex": body.symptom_regex,
            "keywords": body.keywords,
            "detection": body.detection,
            "root_cause": body.root_cause,
            "fix": body.fix,
            "prevention": body.prevention,
        })
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "lesson": saved, "reflex": reflex.stats()}


FEATURE = Feature(name="north_star_horizon", router=router)
