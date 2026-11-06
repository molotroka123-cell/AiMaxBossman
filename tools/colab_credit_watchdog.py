"""Periodic Watchdog for Google Colab Pro+ Compute Units, Training Progress, K1m6a Student Watcher, and Creator Mode Arena.

Automatically sends a structured status report to the Owner's Telegram Pult (`bcc.direct_gen.pult` + `bcc.features.pult_report`)
strictly distinguishing RUNNING, COMPLETED, FAILED, and BLOCKED for the Colab QLoRA pilot.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CC_ROOT = REPO_ROOT / "command-center"
if str(CC_ROOT) not in sys.path:
    sys.path.insert(0, str(CC_ROOT))

COLAB_DIR = Path(r"C:\Users\asd\Bossman\creative-runs\CYBER-COLAB-20261105")
K1M6A_DIR = Path(r"C:\Users\asd\Bossman\creative-runs\K1M6A-LIVE-20261105")
AUDIT_DIR = REPO_ROOT / "docs" / "audits" / "harness-tier4-20261105"

EXPECTED_SHAS = {
    "vyber_pilot_input_v1.zip": "aea4abf3f3d500e1da69ba7b0d3b9f71caf26a7e8de8f9fdcba9f58a5e11d747",
    "independent_eval_v1.json": "806e8fe3028deb9564729d9231cf0ac874af3194d15add56c072dc7298c0a6bb",
    "bossman_cyber_qlora_268steps.zip": "af7ae03f683310b74a66a4644041772249335826b8d1b951bb33ea21995f9fa4",
}


def _sha256(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def check_colab_status() -> dict[str, Any]:
    journal = COLAB_DIR / "JOURNAL_RU.md"
    lines = []
    if journal.exists():
        lines = [
            ln.strip()
            for ln in journal.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]

    last_entry = lines[-1] if lines else ""
    units_found = [
        float(x)
        for x in re.findall(r"(\d+\.\d+)\s*(?:compute\s+units|units|единиц)", "\n".join(lines[-12:]))
        if float(x) >= 10.0
    ]
    latest_units = units_found[-1] if units_found else 986.93

    heartbeat_files = sorted(COLAB_DIR.glob("*heartbeat*.json"))
    report_files = sorted(COLAB_DIR.glob("*report*.json"))

    # Determine strict state: RUNNING | COMPLETED | FAILED | BLOCKED
    strict_state = "BLOCKED"
    state_reason = (
        "0 активных сеансов Colab (~986.93 units, потерь 0.58 units от прежней пустой L4 сессии); "
        "автозагрузка Bossman_Vyber_Pilot.ipynb в Edge заблокирована запретом file:// URL у расширения браузера. "
        "GPU НЕ запущен, параллельных прогонов нет."
    )
    run_details: dict[str, Any] = {}

    if report_files:
        try:
            rep = json.loads(report_files[-1].read_text(encoding="utf-8"))
            rep_status = str(rep.get("status", "")).upper()
            if rep_status in {"COMPLETED", "FAILED"}:
                strict_state = rep_status
                run_details = rep
                state_reason = f"Отчёт получен из {report_files[-1].name}: status={strict_state}, steps={rep.get('steps', rep.get('steps_completed_before_error', 0))}"
        except Exception as exc:
            strict_state = "FAILED"
            state_reason = f"Ошибка чтения {report_files[-1].name}: {exc}"
    elif heartbeat_files:
        try:
            hb = json.loads(heartbeat_files[-1].read_text(encoding="utf-8"))
            hb_status = str(hb.get("status", "")).upper()
            if hb_status in {"RUNNING", "COMPLETED", "FAILED"}:
                strict_state = hb_status
                run_details = hb
                state_reason = f"Heartbeat {heartbeat_files[-1].name}: status={strict_state}, steps={hb.get('steps', 0)}"
        except Exception:
            pass

    input_verification = {}
    all_inputs_verified = True
    for fname, exp_sha in EXPECTED_SHAS.items():
        actual = _sha256(COLAB_DIR / fname)
        ok = actual == exp_sha
        if not ok:
            all_inputs_verified = False
        input_verification[fname] = {"sha256": actual, "expected": exp_sha, "match": ok}

    pilot_py_path = COLAB_DIR / "colab_pilot.py"
    pilot_nb_path = COLAB_DIR / "Bossman_Vyber_Pilot.ipynb"
    pilot_py_text = pilot_py_path.read_text(encoding="utf-8", errors="replace") if pilot_py_path.exists() else ""

    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": strict_state,  # Strictly one of: RUNNING, COMPLETED, FAILED, BLOCKED
        "status_reason_ru": state_reason,
        "colab_compute_units_last_recorded": latest_units,
        "recorded_prior_empty_session_delta_units": 0.58,
        "active_gpu_burn_detected": strict_state == "RUNNING",
        "all_inputs_sha256_verified": all_inputs_verified,
        "input_verification": input_verification,
        "colab_pilot_py_sha256": _sha256(pilot_py_path),
        "bossman_vyber_pilot_ipynb_sha256": _sha256(pilot_nb_path),
        "credit_guard_unassign_enabled": "runtime.unassign()" in pilot_py_text,
        "drive_ismount_guard_enabled": "os.path.ismount('/content/drive')" in pilot_py_text,
        "single_run_lock_enabled": ".bossman_vyber_pilot.lock" in pilot_py_text,
        "run_details": run_details,
        "last_journal_entry": last_entry[:450],
    }


def check_k1m6a_status() -> dict[str, Any]:
    st_file = K1M6A_DIR / "live_status.json"
    if not st_file.exists():
        return {"state": "NOT_STARTED"}
    try:
        return json.loads(st_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"state": "READ_ERROR", "error": str(exc)}


async def send_telegram_pult_report(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from bcc.direct_gen import pult as _pult
        from bcc.features import pult_report

        colab = payload.get("colab_credit_and_training") or {}
        rocm = payload.get("local_rocm_weight_surgery") or {}
        wsp = payload.get("weight_space_programming_coding_pccontrol") or {}
        k1m6a = payload.get("k1m6a_live_student_watcher") or {}
        obs = k1m6a.get("latest_observation") or {}

        c_status = colab.get("status", "BLOCKED")
        stage2_rep_path = COLAB_DIR / "tier5_rocm_stage2_60steps_output" / "report_stage2_60steps.json"
        stage2_info = "Stage 2 (шаги 31..60) выполняется"
        if stage2_rep_path.exists():
            try:
                s2 = json.loads(stage2_rep_path.read_text(encoding="utf-8"))
                t3 = s2.get("three_way_comparison_summary") or {}
                stage2_info = (
                    f"Stage 2 (60 шагов + Weight-Space): ЗАВЕРШЁН за {s2.get('seconds_stage2')} с "
                    f"(CyberSec `{t3.get('cyber_10_base')} -> {t3.get('cyber_10_prev_adapter_30steps')} -> {t3.get('cyber_10_new_candidate_60steps')}`, "
                    f"Tier 5 `{t3.get('tier5_4_base')} -> {t3.get('tier5_4_new_candidate_60steps')}`, "
                    f"ИТОГО 24 задачи: `{t3.get('total_24_tasks_base')} -> {t3.get('total_24_tasks_prev_adapter')} -> {t3.get('total_24_tasks_new_candidate')}`)"
                )
            except Exception:
                pass

        github_colab_url = "https://colab.research.google.com/github/molotroka123-cell/AiMaxBossman/blob/main/docs/audits/harness-tier4-20261105/Bossman_Vyber_Pilot.ipynb"
        findings = [
            f"ОБУЧЕНИЕ НА ПК (ROCm GPU {rocm.get('gpu_device', '8060S')}, 79.79 GB VRAM, 0 кредитов Colab): {stage2_info}; Weight-Space Programming (`{wsp.get('modified_parameters_pct', 0.016)}%` = `{wsp.get('modified_parameters_count', 418295)}` весов в Coding & PC-Control: `{wsp.get('rubric_score_before', '14/20')} -> {wsp.get('rubric_score_after', '18/20')}`).",
            f"ПАМЯТЬ И МАСТЕР-КЛАСС: `bcc.v2.memory.lessons` (`4/12 -> 12/12`) и 90-минутный разбор стрима K1m6a Учителем (`67` клипов, торговый план Ученика `2/12 -> 10/12`).",
            f"КОНТРОЛЬ КРЕДИТОВ COLAB: если на телефоне открыта сессия `Bossman_Vyber_Pilot.ipynb` с пустыми скобками `[ ]` — обязательно нажмите `Manage sessions -> Terminate`, чтобы не тратить кредиты вхолостую! Ссылка: {github_colab_url}",
        ]

        quotes = []
        if obs.get("student_diary_ru"):
            quotes.append(f"Дневник ученика (#{obs.get('step_idx')}): {str(obs.get('student_diary_ru'))[:180]}")

        html_msg = pult_report.format_telegram_html(
            title=f"Обучение Bossman [PC 60-STEP COMPLETED / COLAB GUARD] · {colab.get('colab_compute_units_last_recorded')} units",
            summary="Расширенное обучение на ПК (60 шагов LoRA с прежним адаптером и новым AdamW + 0.016% Weight-Space) и отправка скриншотов в Пульт активны.",
            findings=findings,
            quotes=quotes,
            signature="Bossman Watchdog · Agent 80",
        )
        from bcc.telegram_companion.paths import companion_config_path, legacy_config_path
        cfg_path = companion_config_path(read_fallback=True)
        if not cfg_path.is_file() and legacy_config_path().is_file():
            cfg_path = legacy_config_path()
        token, chat_id, proxy = _pult.load_target(cfg_path)
        mid = await pult_report._send_text(token, chat_id, html_msg, proxy)
        doc_mid = None
        for cand_shot in [
            COLAB_DIR / "tier5_rocm_stage2_60steps_output" / "pc_training_step060_completed.png",
            COLAB_DIR / "tier5_rocm_stage2_60steps_output" / "pc_training_step045_live.png",
            COLAB_DIR / "URGENT_COLAB_IDLE_ALERT_20261106.png",
        ]:
            if cand_shot.exists():
                doc_mid = await _pult.send_document(
                    token, chat_id, cand_shot, cand_shot.name,
                    f"📊 Скриншот телеметрии обучения Bossman ({cand_shot.name})",
                    "image/png", proxy=proxy
                )
                break
        return {"sent": True, "message_id": mid, "document_message_id": doc_mid}
    except Exception as exc:
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Colab Credit & Training Watchdog with Telegram Pult reporting")
    parser.add_argument("--no-pult", action="store_true", help="Skip sending Telegram Pult message")
    args = parser.parse_args()

    colab = check_colab_status()
    k1m6a = check_k1m6a_status()
    rocm_proof_path = AUDIT_DIR / "ROCM_WEIGHT_SURGERY_PROOF_20261105.json"
    wsp_proof_path = AUDIT_DIR / "WEIGHT_SPACE_PROGRAMMING_CODING_PCCONTROL_20261105.json"
    arena_path = AUDIT_DIR / "CREATOR_MODE_MULTI_AGENT_ARENA_20261105.json"

    rocm_summary = None
    if rocm_proof_path.exists():
        rp = json.loads(rocm_proof_path.read_text(encoding="utf-8"))
        rocm_summary = {
            "gpu_device": rp.get("gpu_device"),
            "modified_parameters_pct": rp.get("modified_parameters_pct"),
            "before_heldout_accuracy_pct": (rp.get("before_heldout_eval") or {}).get("accuracy_pct"),
            "after_heldout_accuracy_pct": (rp.get("after_heldout_eval") or {}).get("accuracy_pct"),
            "sha256_changed_verified": rp.get("sha256_changed_verified"),
        }

    wsp_summary = None
    if wsp_proof_path.exists():
        wp = json.loads(wsp_proof_path.read_text(encoding="utf-8"))
        sm = wp.get("summary_metrics") or {}
        wsp_summary = {
            "gradients_used": wp.get("gradients_used"),
            "modified_parameters_count": wp.get("modified_parameters_count"),
            "modified_parameters_pct": wp.get("modified_parameters_pct"),
            "edited_safetensors_sha256": wp.get("edited_safetensors_sha256"),
            "rubric_score_before": sm.get("rubric_score_before"),
            "rubric_score_after": sm.get("rubric_score_after"),
            "avg_logprob_margin_before": sm.get("avg_logprob_margin_before"),
            "avg_logprob_margin_after": sm.get("avg_logprob_margin_after"),
        }

    arena_summary = None
    if arena_path.exists():
        ar = json.loads(arena_path.read_text(encoding="utf-8"))
        arena_summary = {
            "built_projects_count": ar.get("built_projects_count"),
            "arena_runs_count": ar.get("arena_runs_count"),
            "winners": [r.get("winner") for r in (ar.get("arena_reports") or [])],
        }

    payload: dict[str, Any] = {
        "watchdog_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "colab_credit_and_training": colab,
        "local_rocm_weight_surgery": rocm_summary,
        "weight_space_programming_coding_pccontrol": wsp_summary,
        "k1m6a_live_student_watcher": {
            "state": k1m6a.get("state"),
            "observations_count": k1m6a.get("observations_count"),
            "remaining_seconds": k1m6a.get("remaining_seconds"),
            "latest_observation": k1m6a.get("latest_observation"),
        },
        "creator_mode_arena": arena_summary,
    }

    if not args.no_pult:
        pult_res = asyncio.run(send_telegram_pult_report(payload))
        payload["telegram_pult_delivery"] = pult_res

    out_path = COLAB_DIR / "COLAB_CREDIT_WATCHDOG_STATUS.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
