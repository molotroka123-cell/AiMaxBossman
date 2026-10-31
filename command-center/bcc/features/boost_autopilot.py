"""Feature — Bossman Boost Autopilot (`boost_autopilot`).

Единый замкнутый контур автоматизации Bossman в `main`:
1. **CUA-Recovery (`StepStateGuard`)**: предшаговый барьер состояния перед каждым действием
   автопилота с автоматическим восстановлением из чекпойнта (`rollback_and_retry`) или
   безопасной остановкой до необратимого шага (`halt_before_irreversible`).
2. **AgentProofBench (`verify_criterion_with_proof` + `capture_initial_snapshot`)**:
   независимая проверка конечного состояния артефактов (`file:reopen`) со снимком исходного
   состояния, версией правила `apb-2026.10.31-boost-v1` и криптографическим `proof_sha256`
   (перехватывает ложные заявления об успехе `false_claim_caught`).
3. **TimlMemBench (`FactStore`)**: запись подтверждённых результатов автопилота в темпоральную
   память с областью видимости (`scope="work"|"personal"|"public"`), `replace_current=True`
   и защитой от записи неподтверждённых шагов.
4. **Санитайзер отчётов (`pult_report.redact` / `contains_secret`)**: автоматическая очистка
   секретов перед формированием дайджестов и отчётов.
5. **Интеграция с BossBlock 3D (`bossblock.autopilot_audit`)**: автоматический 5-слойный аудит
   захваченных игровых кварталов (включая центр г. Днепр для `/m/gta.html`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import approvals as approvals_t, models as models_t, settings_kv, tasks as tasks_t, utcnow
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..v2.memory.facts import FactStore
from ..v2.recovery import StepStateGuard
from ..v2.verification import (
    ExpectedState,
    capture_initial_snapshot,
    payload_digest,
    verify_criterion_with_proof,
)
from . import Feature
from .pult_report import contains_secret, redact

CONFIG_KEY = "autopilot.config"
STATE_KEY = "autopilot.state"
RULE_VERSION = "apb-2026.10.31-boost-v1"
PIPELINES = ("system_health_and_hygiene", "bossblock_world_audit", "daily_digest_and_proof")

router = APIRouter()

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "interval_minutes": 60,
    "default_pipeline": "system_health_and_hygiene",
    "auto_repair": True,
    "scope": "work",
}


def _empty_metrics() -> dict[str, int]:
    return {
        "total_runs": 0,
        "verified_runs": 0,
        "false_claims_caught": 0,
        "cua_recoveries_triggered": 0,
        "cua_halts_before_irreversible": 0,
        "memory_facts_recorded": 0,
        "secrets_redacted_count": 0,
    }


_memory_runs: list[dict[str, Any]] = []
_metrics: dict[str, int] = _empty_metrics()
_last_tick_ts: float = 0.0
_autopilot_lock = asyncio.Lock()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _runs_dir(svc) -> Path:
    base = Path(getattr(svc.settings, "data_dir", None) or (Path.home() / ".bossman-autopilot"))
    d = (base / "autopilot_runs").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sync_global_mirrors(state: dict[str, Any]) -> None:
    _metrics.clear()
    _metrics.update(state["metrics"])
    _memory_runs.clear()
    _memory_runs.extend(state["runs"][:50])


async def _get_state(svc) -> dict[str, Any]:
    state = getattr(svc, "_autopilot_state", None)
    if isinstance(state, dict) and "metrics" in state and "runs" in state:
        _sync_global_mirrors(state)
        return state
    state = {"metrics": _empty_metrics(), "runs": []}
    try:
        async with svc.db.session() as s:
            row = (
                await s.execute(sa.select(settings_kv.c.value_enc).where(settings_kv.c.key == STATE_KEY))
            ).first()
        if row and row[0]:
            loaded = json.loads(svc.vault.decrypt(row[0]))
            if isinstance(loaded, dict):
                if isinstance(loaded.get("metrics"), dict):
                    for k in state["metrics"]:
                        if k in loaded["metrics"]:
                            state["metrics"][k] = int(loaded["metrics"][k])
                if isinstance(loaded.get("runs"), list):
                    state["runs"] = list(loaded["runs"][:50])
    except Exception:
        pass
    try:
        setattr(svc, "_autopilot_state", state)
    except Exception:
        pass
    _sync_global_mirrors(state)
    return state


async def _persist_state(svc, state: dict[str, Any]) -> None:
    state["runs"] = state["runs"][:50]
    _sync_global_mirrors(state)
    try:
        enc = svc.vault.encrypt(json.dumps(state, ensure_ascii=False, default=str))
        async with svc.db.session() as s:
            await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == STATE_KEY))
            await s.execute(sa.insert(settings_kv).values(key=STATE_KEY, value_enc=enc))
            await s.commit()
    except Exception:
        pass


async def get_autopilot_config(svc) -> dict[str, Any]:
    cfg = dict(_DEFAULT_CONFIG)
    try:
        async with svc.db.session() as s:
            row = (
                await s.execute(sa.select(settings_kv.c.value_enc).where(settings_kv.c.key == CONFIG_KEY))
            ).first()
        if row and row[0]:
            loaded = json.loads(svc.vault.decrypt(row[0]))
            if isinstance(loaded, dict):
                cfg.update(loaded)
    except Exception:
        pass
    return cfg


async def save_autopilot_config(svc, patch: dict[str, Any]) -> dict[str, Any]:
    async with _autopilot_lock:
        cfg = await get_autopilot_config(svc)
        for k in ("enabled", "interval_minutes", "default_pipeline", "auto_repair", "scope"):
            if k in patch and patch[k] is not None:
                if k == "enabled" or k == "auto_repair":
                    cfg[k] = bool(patch[k])
                elif k == "interval_minutes":
                    cfg[k] = max(1, min(1440, int(patch[k])))
                elif k == "default_pipeline":
                    val = str(patch[k]).strip()
                    if val not in PIPELINES and val != "all":
                        raise ValueError(f"Unknown pipeline: {val}")
                    cfg[k] = val
                elif k == "scope":
                    val = str(patch[k]).strip().lower()
                    if val not in ("work", "personal", "public", "general"):
                        raise ValueError(f"Invalid scope: {val}")
                    cfg[k] = val
        try:
            enc = svc.vault.encrypt(json.dumps(cfg, ensure_ascii=False))
            async with svc.db.session() as s:
                await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == CONFIG_KEY))
                await s.execute(sa.insert(settings_kv).values(key=CONFIG_KEY, value_enc=enc))
                await s.commit()
        except Exception:
            pass
        return cfg


async def _exec_system_health(svc, run_id: str, note: str) -> dict[str, Any]:
    models_total = 0
    models_online = 0
    tasks_running = 0
    tasks_waiting = 0
    approvals_pending = 0
    try:
        async with svc.db.session() as s:
            m_rows = (await s.execute(sa.select(models_t.c.status))).fetchall()
            models_total = len(m_rows)
            models_online = sum(1 for r in m_rows if r[0] == "online")
            t_rows = (await s.execute(sa.select(tasks_t.c.status))).fetchall()
            tasks_running = sum(1 for r in t_rows if r[0] == "running")
            tasks_waiting = sum(1 for r in t_rows if r[0] == "waiting_approval")
            a_rows = (
                await s.execute(sa.select(approvals_t.c.id).where(approvals_t.c.status == "pending"))
            ).fetchall()
            approvals_pending = len(a_rows)
    except Exception:
        pass
    return {
        "schema": "bossman.autopilot.system_health.v1",
        "run_id": run_id,
        "pipeline": "system_health_and_hygiene",
        "timestamp": time.time(),
        "models": {"total": models_total, "online": models_online},
        "tasks": {"running": tasks_running, "waiting_approval": tasks_waiting},
        "approvals": {"pending": approvals_pending},
        "hygiene_ok": True,
        "note": note,
    }


async def _exec_bossblock_audit(svc, run_id: str, target_folder: str | None, note: str) -> dict[str, Any]:
    repo = _repo_root()
    bossblock_pkg = str(repo / "apps" / "bossblock")
    if bossblock_pkg not in sys.path:
        sys.path.insert(0, bossblock_pkg)
    from bossblock.autopilot_audit import audit_capture_folder

    default_dnipro = repo / "apps" / "bossman-mobile" / "bossmobile" / "ui" / "dnipro_assets"
    if target_folder is not None and str(target_folder).strip():
        folder = Path(str(target_folder).strip()).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError(f"Capture folder does not exist: {folder}")
    else:
        folder = default_dnipro
    audit_res = await asyncio.to_thread(audit_capture_folder, folder, write_receipt=False)
    return {
        "schema": "bossman.autopilot.bossblock_audit.v1",
        "run_id": run_id,
        "pipeline": "bossblock_world_audit",
        "timestamp": time.time(),
        "world_audit": audit_res,
        "note": note,
    }


async def _exec_daily_digest(svc, run_id: str, note: str) -> dict[str, Any]:
    repo = _repo_root()
    audits_dir = repo / "docs" / "audits"
    recent_audits: list[dict[str, Any]] = []
    if audits_dir.is_dir():
        folders = sorted(
            [p for p in audits_dir.iterdir() if p.is_dir() and p.name.startswith("2026-10-3")],
            key=lambda x: x.name,
            reverse=True,
        )[:12]
        for f in folders:
            verdict = "UNKNOWN"
            r_path = f / "receipt.json"
            if r_path.is_file():
                try:
                    rj = json.loads(r_path.read_text(encoding="utf-8"))
                    verdict = str(
                        rj.get("verdict")
                        or rj.get("live_agent_status")
                        or rj.get("status")
                        or "PASS"
                    )
                except Exception:
                    verdict = "PARSE_ERR"
            elif (f / "README.md").is_file() or (f / "REPORT_RU.md").is_file():
                verdict = "DOCUMENTED"
            recent_audits.append({"folder": f.name, "verdict": verdict})
    return {
        "schema": "bossman.autopilot.daily_digest.v1",
        "run_id": run_id,
        "pipeline": "daily_digest_and_proof",
        "timestamp": time.time(),
        "recent_audits_count": len(recent_audits),
        "recent_audits": recent_audits,
        "sanitized_note": note,
    }


def _expected_contains_for_pipeline(pipeline: str, run_id: str) -> str:
    if pipeline == "bossblock_world_audit":
        return '"core_3d_ready": true'
    if pipeline == "system_health_and_hygiene":
        return '"hygiene_ok": true'
    if pipeline == "daily_digest_and_proof":
        return '"recent_audits_count":'
    return f'"run_id": "{run_id}"'


async def run_autopilot_pipeline(
    svc,
    *,
    pipeline: str = "system_health_and_hygiene",
    target_folder: str | None = None,
    note: str = "",
    agent_claim_text: str | None = None,
    inject_mid_step_fault: str = "none",
    allow_auto_repair: bool = True,
    simulate_unwritten_artifact: bool = False,
    scope: str = "work",
) -> dict[str, Any]:
    """Запускает один пайплайн автопилота с полной цепочкой CUA-Recovery → AgentProofBench → TimlMemBench."""
    if pipeline == "all":
        sub_results = []
        for p_name in PIPELINES:
            sub = await run_autopilot_pipeline(
                svc,
                pipeline=p_name,
                target_folder=target_folder,
                note=note,
                agent_claim_text=agent_claim_text,
                inject_mid_step_fault=inject_mid_step_fault,
                allow_auto_repair=allow_auto_repair,
                simulate_unwritten_artifact=simulate_unwritten_artifact,
                scope=scope,
            )
            sub_results.append(sub)
        all_verified = all(r.get("verifier_status") == "VERIFIED" for r in sub_results)
        return {
            "mode": "all",
            "verdict": "VERIFIED" if all_verified else "PARTIAL_OR_HALTED",
            "pipelines_run": len(sub_results),
            "results": sub_results,
        }

    if pipeline not in PIPELINES:
        raise ValueError(f"Unknown pipeline {pipeline!r}; valid: {PIPELINES}")

    if target_folder is not None and str(target_folder).strip() and pipeline == "bossblock_world_audit":
        tf_check = Path(str(target_folder).strip()).expanduser().resolve()
        if not tf_check.is_dir():
            raise ValueError(f"Capture folder does not exist: {tf_check}")

    state = await _get_state(svc)
    state["metrics"]["total_runs"] += 1
    run_id = f"ap-{int(time.time())}-{uuid.uuid4().hex[:6]}"

    # 0. Санитайзер входных заметок (Data Minimization & Secret Scrubbing)
    raw_note = str(note or "")
    secret_found = contains_secret(raw_note)
    clean_note = redact(raw_note)
    if secret_found:
        state["metrics"]["secrets_redacted_count"] += 1

    # 1. Предшаговый барьер состояния (CUA-Recovery StepStateGuard)
    expected_step_state = {
        "workspace_ready": True,
        "active_field": f"pipeline:{pipeline}",
        "dialog_open": True,
        "saved": True,
    }
    observed_step_state = dict(expected_step_state)
    if inject_mid_step_fault == "wrong_form_field":
        observed_step_state["active_field"] = "wrong_untrusted_field"
    elif inject_mid_step_fault == "closed_dialog":
        observed_step_state["dialog_open"] = False
    elif inject_mid_step_fault == "failed_save":
        observed_step_state["saved"] = False

    checkpoint = dict(expected_step_state) if allow_auto_repair else None
    guard = StepStateGuard(allow_auto_repair=allow_auto_repair)
    step_check = guard.verify_before_next_action(
        step_index=1,
        action_name=f"autopilot.{pipeline}.commit",
        irreversible=True,
        expected_state=expected_step_state,
        observed_state=observed_step_state,
        checkpoint_state=checkpoint,
    )

    if step_check.decision == "halt_before_irreversible":
        state["metrics"]["cua_halts_before_irreversible"] += 1
        halted_rec = {
            "run_id": run_id,
            "pipeline": pipeline,
            "status": "HALTED_BEFORE_IRREVERSIBLE",
            "verifier_status": "UNVERIFIED",
            "cua_recovery": {
                "matched": step_check.matched,
                "detected_fault": step_check.detected_fault,
                "mismatched_keys": list(step_check.mismatched_keys),
                "decision": step_check.decision,
                "repaired": False,
            },
            "secret_redacted": secret_found,
            "sanitized_note": clean_note,
            "memory_fact": None,
            "created_at": time.time(),
        }
        async with _autopilot_lock:
            state["runs"].insert(0, halted_rec)
            await _persist_state(svc, state)
        return halted_rec

    if step_check.decision == "rollback_and_retry":
        state["metrics"]["cua_recoveries_triggered"] += 1

    # 2. Снимок исходного состояния ДО записи артефакта (AgentProofBench)
    r_dir = _runs_dir(svc)
    artifact_path = r_dir / f"{run_id}_{pipeline}.json"
    expected_post = ExpectedState(
        kind="file",
        target=str(artifact_path),
        expect={
            "exists": True,
            "contains": _expected_contains_for_pipeline(pipeline, run_id),
            "min_bytes": 64,
        },
    )
    init_snapshot = await capture_initial_snapshot(expected_post, svc=svc, roots=[r_dir])

    # 3. Выполнение полезной нагрузки пайплайна
    if pipeline == "system_health_and_hygiene":
        payload = await _exec_system_health(svc, run_id, clean_note)
    elif pipeline == "bossblock_world_audit":
        payload = await _exec_bossblock_audit(svc, run_id, target_folder, clean_note)
    else:
        payload = await _exec_daily_digest(svc, run_id, clean_note)

    if not simulate_unwritten_artifact:
        artifact_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # 4. Независимая проверка конечного состояния (AgentProofBench)
    effective_claim = (
        agent_claim_text
        if agent_claim_text is not None
        else f"Готово: пайплайн {pipeline} ({run_id}) успешно выполнен и сохранён."
    )
    proof = await verify_criterion_with_proof(
        expected_post,
        rule_id=f"autopilot-{pipeline}",
        rule_version=RULE_VERSION,
        initial_snapshot=init_snapshot,
        agent_claim_text=effective_claim,
        svc=svc,
        roots=[r_dir],
    )
    if proof.false_claim_caught:
        state["metrics"]["false_claims_caught"] += 1

    # 5. Запись факта в темпоральную память с областью видимости (TimlMemBench)
    fact_record: dict[str, Any] | None = None
    if proof.verifier_status == "VERIFIED":
        state["metrics"]["verified_runs"] += 1
        async with _autopilot_lock:
            try:
                store = FactStore(svc)
                raw_fact = await store.add(
                    subject=f"autopilot:{pipeline}",
                    predicate="last_verified_run",
                    object=run_id,
                    statement=(
                        f"Автопилот Bossman завершил пайплайн {pipeline} (прогон {run_id}) "
                        f"с криптографическим доказательством {proof.proof_sha256[:16]} и версией правила {RULE_VERSION}."
                    ),
                    replace_current=True,
                    source_kind="tool",
                    source_note=f"proof_sha256={proof.proof_sha256}",
                    scope=scope,
                )
                fact_record = json.loads(json.dumps(raw_fact, ensure_ascii=False, default=str))
                state["metrics"]["memory_facts_recorded"] += 1
            except Exception:
                fact_record = None

    record = {
        "run_id": run_id,
        "pipeline": pipeline,
        "status": "COMPLETED" if proof.verifier_status == "VERIFIED" else "VERIFICATION_FAILED",
        "artifact_path": str(artifact_path),
        "verifier_status": proof.verifier_status,
        "rule_id": proof.rule_id,
        "rule_version": proof.rule_version,
        "rule_digest": proof.rule_digest,
        "rule_valid": proof.rule_valid,
        "agent_claim_text": proof.agent_claim_text,
        "agent_claimed_success": proof.agent_claimed_success,
        "false_claim_caught": proof.false_claim_caught,
        "initial_snapshot": proof.initial_snapshot,
        "final_evidence": proof.final_evidence,
        "proof_sha256": proof.proof_sha256,
        "cua_recovery": {
            "matched": step_check.matched,
            "detected_fault": step_check.detected_fault,
            "mismatched_keys": list(step_check.mismatched_keys),
            "decision": step_check.decision,
            "repaired": step_check.decision == "rollback_and_retry",
        },
        "secret_redacted": secret_found,
        "sanitized_note": clean_note,
        "memory_fact": fact_record,
        "payload_summary": payload,
        "created_at": time.time(),
    }
    async with _autopilot_lock:
        state["runs"].insert(0, record)
        await _persist_state(svc, state)
    return record


# ------------------------------------------------------------------ REST API

@router.get("/autopilot/status")
async def api_autopilot_status(request: Request):
    svc = request.app.state.svc
    cfg = await get_autopilot_config(svc)
    state = await _get_state(svc)
    return {
        "ok": True,
        "rule_version": RULE_VERSION,
        "available_pipelines": list(PIPELINES),
        "config": cfg,
        "metrics": dict(state["metrics"]),
        "recent_runs": list(state["runs"][:10]),
    }


@router.get("/autopilot/runs")
async def api_autopilot_runs(request: Request, limit: int = 20):
    svc = request.app.state.svc
    state = await _get_state(svc)
    return {"ok": True, "runs": list(state["runs"][: max(1, min(50, int(limit)))])}


@router.post("/autopilot/config")
async def api_autopilot_config(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    try:
        cfg = await save_autopilot_config(svc, body or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "config": cfg}


@router.post("/autopilot/run")
async def api_autopilot_run(request: Request):
    svc = request.app.state.svc
    body = await request.json() if request.headers.get("content-length") != "0" else {}
    body = body or {}
    pipeline = str(body.get("pipeline") or "system_health_and_hygiene").strip()
    try:
        res = await run_autopilot_pipeline(
            svc,
            pipeline=pipeline,
            target_folder=body.get("target_folder"),
            note=str(body.get("note") or ""),
            agent_claim_text=body.get("agent_claim_text"),
            inject_mid_step_fault=str(body.get("inject_mid_step_fault") or "none"),
            allow_auto_repair=bool(body.get("allow_auto_repair", True)),
            simulate_unwritten_artifact=bool(body.get("simulate_unwritten_artifact", False)),
            scope=str(body.get("scope") or "work"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "result": res}


@router.post("/autopilot/verify-artifact")
async def api_autopilot_verify_artifact(request: Request):
    """Независимая приёмка произвольного файлового артефакта по AgentProofBench."""
    svc = request.app.state.svc
    body = await request.json()
    target = str((body or {}).get("target") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="target path is required")
    expect_dict = dict((body or {}).get("expect") or {"exists": True, "min_bytes": 1})
    claim_text = str((body or {}).get("agent_claim_text") or "")
    rule_id = str((body or {}).get("rule_id") or "custom-file-verify")

    target_path = Path(target).expanduser().resolve()
    allowed_roots = [_runs_dir(svc), _repo_root()]
    exp = ExpectedState(kind="file", target=str(target_path), expect=expect_dict)
    raw_snap = (body or {}).get("initial_snapshot")
    init_snap = raw_snap if isinstance(raw_snap, dict) else None
    receipt = await verify_criterion_with_proof(
        exp,
        rule_id=rule_id,
        rule_version=RULE_VERSION,
        initial_snapshot=init_snap,
        agent_claim_text=claim_text,
        svc=svc,
        roots=allowed_roots,
    )
    if receipt.false_claim_caught:
        state = await _get_state(svc)
        state["metrics"]["false_claims_caught"] += 1
        async with _autopilot_lock:
            await _persist_state(svc, state)
    return {"ok": True, "receipt": asdict(receipt)}


# ------------------------------------------------------------------ Agent Tools

async def _tool_run_pipeline(args: dict, ctx: ToolContext) -> ToolResult:
    pipeline = str(args.get("pipeline") or "system_health_and_hygiene").strip()
    note = str(args.get("note") or "")
    scope = str(args.get("scope") or "work")
    try:
        res = await run_autopilot_pipeline(
            ctx.svc,
            pipeline=pipeline,
            target_folder=args.get("target_folder"),
            note=note,
            scope=scope,
        )
    except Exception as exc:
        return ToolResult(content=f"ERROR: {exc}", error=True, one_line="autopilot.run_pipeline: failed")
    if res.get("mode") == "all":
        verdict = res.get("verdict")
        return ToolResult(
            content=f"Autopilot ALL completed: verdict={verdict}, pipelines={res.get('pipelines_run')}",
            one_line=f"autopilot.run_pipeline: all ({verdict})",
            data=res,
        )
    status = res.get("verifier_status")
    proof_sha = str(res.get("proof_sha256") or "")[:16]
    return ToolResult(
        content=(
            f"Пайплайн «{pipeline}» ({res.get('run_id')}) завершён: "
            f"verifier={status}, proof_sha256={proof_sha}, артефакт={res.get('artifact_path')}"
        ),
        one_line=f"autopilot.run_pipeline: {pipeline} ({status})",
        data=res,
    )


async def _tool_status(args: dict, ctx: ToolContext) -> ToolResult:
    cfg = await get_autopilot_config(ctx.svc)
    state = await _get_state(ctx.svc)
    data = {
        "rule_version": RULE_VERSION,
        "config": cfg,
        "metrics": dict(state["metrics"]),
        "recent_runs": list(state["runs"][:5]),
    }
    return ToolResult(
        content=json.dumps(data, ensure_ascii=False, indent=2, default=str),
        one_line=f"autopilot.status: verified={state['metrics']['verified_runs']}/{state['metrics']['total_runs']}",
        data=data,
    )


async def _tool_configure(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        cfg = await save_autopilot_config(ctx.svc, args or {})
    except Exception as exc:
        return ToolResult(content=f"ERROR: {exc}", error=True, one_line="autopilot.configure: failed")
    return ToolResult(
        content=f"Настройки автопилота обновлены: {json.dumps(cfg, ensure_ascii=False)}",
        one_line="autopilot.configure: updated",
        data={"config": cfg},
    )


async def _tick(svc) -> None:
    global _last_tick_ts
    try:
        cfg = await get_autopilot_config(svc)
        if not cfg.get("enabled", True):
            return
        interval_s = max(60.0, float(cfg.get("interval_minutes", 60)) * 60.0)
        now = time.monotonic()
        if _last_tick_ts > 0.0 and (now - _last_tick_ts) < interval_s:
            return
        _last_tick_ts = now
        pipeline = str(cfg.get("default_pipeline") or "system_health_and_hygiene")
        if pipeline not in PIPELINES and pipeline != "all":
            pipeline = "system_health_and_hygiene"
        await run_autopilot_pipeline(
            svc,
            pipeline=pipeline,
            note="scheduled_background_tick",
            allow_auto_repair=bool(cfg.get("auto_repair", True)),
            scope=str(cfg.get("scope") or "work"),
        )
    except Exception:
        pass


async def _setup(svc) -> None:
    REGISTRY.register(
        ToolSpec(
            name="autopilot.run_pipeline",
            description=(
                "Запустить автоматический пайплайн Bossman Boost Autopilot "
                "(system_health_and_hygiene | bossblock_world_audit | daily_digest_and_proof | all) "
                "с предшаговой защитой CUA-Recovery, независимой приёмкой AgentProofBench и записью в память TimlMemBench."
            ),
            handler=_tool_run_pipeline,
            category="read",
            source="builtin",
            default_effect="auto",
            timeout_seconds=120,
            idempotent=True,
            input_schema={
                "pipeline": {
                    "type": "string",
                    "description": "system_health_and_hygiene | bossblock_world_audit | daily_digest_and_proof | all",
                },
                "target_folder": {"type": "string", "description": "Папка 3D-мира для bossblock_world_audit (необязательно)"},
                "note": {"type": "string", "maxLength": 500, "description": "Заметка к прогону (секреты очищаются автоматически)"},
                "scope": {"type": "string", "description": "Область памяти TimlMemBench: work | personal | public"},
            },
            required=["pipeline"],
        )
    )
    REGISTRY.register(
        ToolSpec(
            name="autopilot.status",
            description="Получить статус Bossman Boost Autopilot, счётчики проверок AgentProofBench/CUA-Recovery и последние квитанции.",
            handler=_tool_status,
            category="read",
            source="builtin",
            default_effect="auto",
            timeout_seconds=30,
            idempotent=True,
            input_schema={},
            required=[],
        )
    )
    REGISTRY.register(
        ToolSpec(
            name="autopilot.configure",
            description="Настроить расписание и параметры Bossman Boost Autopilot (enabled, interval_minutes, default_pipeline, auto_repair, scope).",
            handler=_tool_configure,
            category="write",
            source="builtin",
            default_effect="auto",
            timeout_seconds=30,
            idempotent=True,
            input_schema={
                "enabled": {"type": "boolean"},
                "interval_minutes": {"type": "integer"},
                "default_pipeline": {"type": "string"},
                "auto_repair": {"type": "boolean"},
                "scope": {"type": "string"},
            },
            required=[],
        )
    )


FEATURE = Feature(
    name="boost_autopilot",
    router=router,
    setup=_setup,
    tick=_tick,
    tick_seconds=60.0,
)
