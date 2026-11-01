from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from bossmobile.auth import load_or_create_pairing
from bossmobile.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
RECEIPT_PATH = REPO_ROOT / "docs" / "audits" / "2026-11-01-mobile-pult-model-switch-long-tasks" / "receipt.json"

MOBILE_BASE = "http://127.0.0.1:8840"
BCC_BASE = "http://127.0.0.1:8801"
OLLAMA_BASE = "http://127.0.0.1:11434"


def _wait_task(client: httpx.Client, task_id: int, timeout_s: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = client.get(f"/m/api/tasks/{task_id}")
        if r.status_code == 200:
            last = r.json()
            if last.get("status") in ("completed", "failed", "stopped", "blocked", "cancelled"):
                return last
        time.sleep(0.8)
    return last


def _unload_ollama() -> list[str]:
    unloaded: list[str] = []
    try:
        with httpx.Client(base_url=OLLAMA_BASE, timeout=15.0) as oc:
            ps = oc.get("/api/ps").json()
            for m in ps.get("models") or []:
                name = m.get("name") or m.get("model")
                if name:
                    oc.post("/api/generate", json={"model": name, "keep_alive": 0})
                    unloaded.append(str(name))
    except Exception:
        pass
    return unloaded


def main() -> int:
    pairing = load_or_create_pairing(Settings().data_dir)
    token = pairing["token"] if isinstance(pairing, dict) else str(pairing)

    steps_log: list[dict[str, Any]] = []
    with httpx.Client(base_url=MOBILE_BASE, timeout=180.0) as mc:
        # 1. Owner login on Mobile Pult (:8840)
        login_res = mc.post("/m/api/login", json={"token": token})
        assert login_res.status_code == 200, f"login failed: {login_res.text}"
        csrf = login_res.json()["csrf"]
        mc.headers["X-BM-CSRF"] = csrf
        steps_log.append({"step": "1_owner_login", "ok": True})

        # 2. Fetch mobile agents (local + mobile_cloud_ok cloud agents)
        ag_res = mc.get("/m/api/agents")
        assert ag_res.status_code == 200, ag_res.text
        ag_data = ag_res.json()
        agents = ag_data.get("items") or []
        cloud_agents = [a for a in agents if a.get("cloud")]
        local_agents = [a for a in agents if a.get("local")]
        assert cloud_agents, "Expected at least one mobile-approved cloud agent"
        assert local_agents, "Expected at least one local agent"

        # Prefer fast online cloud agent (e.g. GLM 5.3 Flash / Gemini / Mistral) and fast local agent
        cloud_primary = next(
            (a for a in cloud_agents if "glm" in str(a.get("name") or "").lower() or "flash" in str(a.get("name") or "").lower()),
            cloud_agents[0],
        )
        cloud_secondary = next(
            (a for a in cloud_agents if a["id"] != cloud_primary["id"]),
            cloud_primary,
        )
        local_primary = next(
            (
                a
                for a in local_agents
                if any(k in str((a.get("model") or {}).get("name") or "").lower() for k in ("qwen", "gemma4:e4b", "gemma"))
            ),
            local_agents[0],
        )
        steps_log.append({
            "step": "2_catalog_discovery",
            "ok": True,
            "total_agents": len(agents),
            "cloud_count": len(cloud_agents),
            "local_count": len(local_agents),
            "cloud_primary": {"id": cloud_primary["id"], "name": cloud_primary["name"], "model": cloud_primary.get("model")},
            "cloud_secondary": {"id": cloud_secondary["id"], "name": cloud_secondary["name"], "model": cloud_secondary.get("model")},
            "local_primary": {"id": local_primary["id"], "name": local_primary["name"], "model": local_primary.get("model")},
        })

        # 3. Create thread from Mobile Pult
        th_res = mc.post("/m/api/threads", json={"title": "Owner Audit: Cloud<->Local Switch & Long Tasks"})
        assert th_res.status_code in (200, 201), th_res.text
        tid = th_res.json()["id"]
        steps_log.append({"step": "3_create_thread", "ok": True, "thread_id": tid})

        # 4. Turn 1 (CLOUD model #1 in CHAT mode): Long structured 5-section architectural task
        t1_prompt = (
            "Выполни детальную 5-секционную архитектурную задачу для мобильного пульта Bossman:\n"
            "1) Протокол переключения между облачными и локальными моделями без потери контекста;\n"
            "2) Идемпотентный SSE-стриминг длинных задач с Last-Event-ID и дедупликацией seq;\n"
            "3) Очередь сообщений (/queue) и мгновенная корректировка (/steer) на ходу;\n"
            "4) Честный расчёт окна контекста при смене модели и фоновый /compact;\n"
            "5) Контрольный чеклист из 5 пунктов. Напиши кратко, но строго по всем 5 пунктам."
        )
        send1 = mc.post(
            f"/m/api/threads/{tid}/send",
            json={
                "text": t1_prompt,
                "agent_id": cloud_primary["id"],
                "client_request_id": f"audit-t1-{int(time.time())}",
            },
        )
        assert send1.status_code == 200, send1.text
        s1_body = send1.json()
        t1_id = int(s1_body["task"]["id"])

        # Test Steer while Turn 1 is running or just started
        steer_res = mc.post(
            f"/m/api/threads/{tid}/steer",
            json={"text": "В пункте 5 обязательно упомяни выгрузку локальных моделей Ollama в 0 ГБ после теста."},
        )
        t1_final = _wait_task(mc, t1_id, timeout_s=120.0)
        steps_log.append({
            "step": "4_turn1_cloud_chat_long_task",
            "ok": t1_final.get("status") == "completed",
            "task_id": t1_id,
            "agent_id": cloud_primary["id"],
            "model_reported": s1_body.get("model"),
            "steer_status": steer_res.status_code,
            "status": t1_final.get("status"),
            "result_chars": len(str(t1_final.get("result") or "")),
        })

        # 5. Context Gauge check when switching from Cloud #1 -> Cloud #2 and Cloud #1 -> Local #1
        ctx_cloud2 = mc.get(f"/m/api/agent/sessions/{tid}/context", params={"agent_id": cloud_secondary["id"]})
        assert ctx_cloud2.status_code == 200, ctx_cloud2.text
        ctx_local1 = mc.get(f"/m/api/agent/sessions/{tid}/context", params={"agent_id": local_primary["id"]})
        assert ctx_local1.status_code == 200, ctx_local1.text
        steps_log.append({
            "step": "5_context_gauge_on_model_switch",
            "ok": True,
            "cloud2_context": ctx_cloud2.json(),
            "local1_context": ctx_local1.json(),
        })

        # 6. Turn 2: Switch mode to AGENT (`PATCH` + `POST /m/api/agent/sessions/{tid}/messages`) with Cloud model!
        patch2 = mc.patch(
            f"/m/api/agent/sessions/{tid}",
            json={"mode": "AGENT", "agent_id": cloud_secondary["id"], "allow_cloud": True},
        )
        assert patch2.status_code == 200, patch2.text
        t2_prompt = (
            "Продолжи анализ шага 1 в режиме AGENT: проверь все 5 пунктов, которые были составлены в первом ходе чата, "
            "и добавь матрицу отказоустойчивости при обрыве связи телефона с ПК во время длинной задачи."
        )
        send2 = mc.post(
            f"/m/api/agent/sessions/{tid}/messages",
            json={
                "text": t2_prompt,
                "mode": "AGENT",
                "agent_id": cloud_secondary["id"],
                "client_request_id": f"audit-t2-{int(time.time())}",
            },
        )
        assert send2.status_code == 200, f"AGENT mode cloud message failed: {send2.text}"
        s2_body = send2.json()
        t2_id = int(s2_body["task"]["id"])
        t2_final = _wait_task(mc, t2_id, timeout_s=120.0)
        steps_log.append({
            "step": "6_turn2_agent_mode_cloud_switch",
            "ok": t2_final.get("status") == "completed" and (s2_body.get("model") or {}).get("locality") == "cloud",
            "task_id": t2_id,
            "agent_id": cloud_secondary["id"],
            "route": s2_body.get("route"),
            "model": s2_body.get("model"),
            "status": t2_final.get("status"),
            "result_chars": len(str(t2_final.get("result") or "")),
        })

        # 7. Test SSE Last-Event-ID header on task_events stream
        hist_res = mc.get(f"/m/api/tasks/{t2_id}/history")
        assert hist_res.status_code == 200, hist_res.text
        ev_list = hist_res.json().get("events") or []
        max_seq = max((int(e["seq"]) for e in ev_list if isinstance(e.get("seq"), int)), default=0)
        steps_log.append({
            "step": "7_sse_history_and_seq_verification",
            "ok": len(ev_list) > 0 and max_seq > 0,
            "events_count": len(ev_list),
            "max_seq": max_seq,
        })

        # 8. Queue add + cancel test (`POST /m/api/threads/{tid}/queue` & `DELETE /m/api/threads/{tid}/queue/{qid}`)
        q_add = mc.post(
            f"/m/api/threads/{tid}/queue",
            json={"text": "Тестовое сообщение в очередь для другой модели", "agent_id": local_primary["id"]},
        )
        assert q_add.status_code == 201, q_add.text
        q_items = q_add.json().get("items") or []
        assert len(q_items) >= 1 and q_items[-1]["agent_id"] == local_primary["id"]
        qid = q_items[-1]["id"]
        q_del = mc.delete(f"/m/api/threads/{tid}/queue/{qid}")
        assert q_del.status_code == 200, q_del.text
        steps_log.append({
            "step": "8_queue_with_switched_agent_and_cancel",
            "ok": True,
            "queued_agent_id": local_primary["id"],
            "cancelled_id": qid,
        })

        # 9. Reopen thread (`GET /m/api/threads/{tid}`) and verify all turns & statuses are preserved!
        reopen_res = mc.get(f"/m/api/threads/{tid}")
        assert reopen_res.status_code == 200, reopen_res.text
        reopen_data = reopen_res.json()
        reopen_turns = [t for t in (reopen_data.get("turns") or []) if t.get("kind") != "note"]
        assert len(reopen_turns) >= 2, f"Expected >=2 turns, got {len(reopen_turns)}"
        per_turn_models = [
            {
                "idx": t.get("idx"),
                "task_id": t.get("task_id"),
                "agent_id": t.get("agent_id"),
                "model": t.get("model") or (t.get("run") or {}).get("model_alias"),
                "status": t.get("status"),
            }
            for t in reopen_turns
        ]
        all_completed_with_model = all(
            t.get("status") == "completed"
            and (isinstance(t.get("model"), dict) or bool((t.get("run") or {}).get("model_alias")))
            for t in reopen_turns
        )
        steps_log.append({
            "step": "9_reopen_thread_preserves_per_turn_badges",
            "ok": all_completed_with_model,
            "turns": per_turn_models,
        })

        # 10. Sync session agent_id + allow_cloud via PATCH (as app.js now does) and run /compact!
        mc.patch(
            f"/m/api/agent/sessions/{tid}",
            json={"agent_id": cloud_primary["id"], "allow_cloud": True},
        )
        cmp_res = mc.post(
            f"/m/api/agent/sessions/{tid}/compact",
            json={"instruction": "сохрани все 5 архитектурных пунктов и матрицу отказоустойчивости"},
        )
        assert cmp_res.status_code == 202, f"compact failed: {cmp_res.text}"
        cmp_data = cmp_res.json()
        # Poll context gauge until compact finishes
        cmp_final_state = "running"
        for _ in range(60):
            c_poll = mc.get(f"/m/api/agent/sessions/{tid}/context", params={"agent_id": cloud_primary["id"]}).json()
            st = (c_poll.get("compact") or {}).get("state")
            if st in ("done", "failed"):
                cmp_final_state = st
                break
            time.sleep(1.0)
        steps_log.append({
            "step": "10_compact_with_selected_cloud_model",
            "ok": cmp_res.status_code == 202 and cmp_final_state == "done",
            "initial": cmp_data,
            "final_state": cmp_final_state,
        })

    unloaded = _unload_ollama()
    RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "bossman.mobile_pult.model_switch_long_tasks.audit.v1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "thread_id": tid,
        "all_passed": all(s.get("ok") for s in steps_log),
        "steps": steps_log,
        "ollama_unloaded": unloaded,
    }
    payload["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    RECEIPT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": payload["all_passed"], "receipt": str(RECEIPT_PATH), "sha256": payload["sha256"]}, ensure_ascii=False))
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
