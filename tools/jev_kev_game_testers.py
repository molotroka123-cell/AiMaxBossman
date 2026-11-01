"""Owner rule 01.11.2026: game manager/tester agents — FIRST the cheap cloud one (Jev, OpenRouter), SECOND the local one (Kev).

Creates/updates the two agents, runs the same game-testing task through the REAL chat path (project sandbox =
the SwapMe Roblox game folder) on each, and writes a receipt. Chat-only agents: no PC access, project folder only.
Usage: python tools/jev_kev_game_testers.py [--project swapme-roblox] [--skip-run]
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("BCC_BASE", "http://127.0.0.1:8801/api")
JEV_MODEL = "z-ai/glm-5.3-flash"          # OpenRouter, cheapest proven-online paid model ($0.15/$0.5 per Mtok)
JEV_FALLBACK = "qwen3-coder:30b"           # local fallback when the cloud is down/over cap
KEV_MODEL = "qwen3-coder:30b"              # local-ollama-11434, free
RULE = ("Правило владельца 01.11.2026: управленцы/тестировщики игр — ПЕРВЫЙ дешёвый облачный (Jev, OpenRouter), "
        "ВТОРОЙ локальный (Kev). Jev падает на локальную модель при недоступности облака; Kev никогда не уходит в облако.")

TASK = """Ты тестировщик Roblox-игры SwapMe Coin Tycoon. Проект — папка этого чата.
1) Прочитай src/server/TradeService.server.luau и src/server/PurchaseLedger.luau (инструмент code.read).
2) Найди реальные риски дюпа/эксплойта и ошибки логики (не стиль). Для каждого — файл:строка, сценарий, как воспроизвести.
3) Дай тест-план из 8 проверок для плейтеста (шаги, ожидаемый результат).
Отвечай по-русски, кратко, списком. Ничего не записывай в файлы."""


def _token() -> str:
    d = Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")
    return (d / "token").read_text(encoding="utf-8").strip()


def api(method: str, path: str, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"X-BCC-Token": _token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} -> {exc.code}: {exc.read()[:400]!r}") from None


def _list(payload, key: str) -> list[dict]:
    if isinstance(payload, dict):
        payload = payload.get(key, payload.get("items", []))
    return payload if isinstance(payload, list) else []


def model_id(models: list[dict], name: str) -> int:
    for m in models:
        if m.get("name") == name:
            return int(m["id"])
    raise SystemExit(f"model not registered: {name}")


def upsert_agent(agents: list[dict], name: str, **fields) -> dict:
    for a in agents:
        if a.get("name") == name:
            return api("PATCH", f"/agents/{a['id']}", fields)
    return api("POST", "/agents", {"name": name, **fields})


def run_turn(project: str, agent: dict, label: str) -> dict:
    thread = api("POST", "/chat/threads", {"title": f"{label} game test", "project": project})
    tid = thread["id"]
    t0 = time.time()
    sent = api("POST", f"/chat/threads/{tid}/send", {"text": TASK, "agent_id": agent["id"]}, timeout=600)
    task_id = sent.get("task_id")
    status, item = "unknown", {}
    for _ in range(240):                                  # up to 20 min
        detail = api("GET", f"/chat/threads/{tid}")
        turns = _list(detail, "turns")
        item = next((t for t in turns if t.get("task_id") == task_id), turns[-1] if turns else {})
        status = str(item.get("status") or status)
        if status in {"done", "completed", "failed", "stopped", "cancelled", "error"}:
            break
        time.sleep(5)
    answer = str(item.get("result") or "")
    model = item.get("model") or {}
    return {"agent": agent["name"], "thread": tid, "task_id": task_id, "status": status,
            "seconds": round(time.time() - t0, 1), "model_alias": model.get("alias"),
            "locality": model.get("locality"), "error": item.get("error"),
            "failure_summary": item.get("failure_summary"), "answer_chars": len(answer), "answer_head": answer[:1500]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="swapme-roblox")
    ap.add_argument("--skip-run", action="store_true")
    args = ap.parse_args()

    models = _list(api("GET", "/models"), "models")
    agents = _list(api("GET", "/agents"), "agents")

    common = dict(role="game manager / tester (SwapMe Roblox)", tools=["code.read"], max_steps=6, max_tokens=4096,
                  permissions={"cloud_pc_access": False}, enabled=True)
    jev = upsert_agent(agents, "Jev (cheap cloud game tester)", model_id=model_id(models, JEV_MODEL),
                       fallback_model_id=model_id(models, JEV_FALLBACK), budget_usd=2.0,
                       system_prompt="Ты Jev — управленец и тестировщик игр. Дешёвая облачная модель, первый эшелон. "
                                     "Работаешь только внутри папки проекта чата, файлы не меняешь, отвечаешь по делу. " + RULE,
                       **common)
    kev = upsert_agent(agents, "Kev (local game tester)", model_id=model_id(models, KEV_MODEL),
                       fallback_model_id=None, budget_usd=0.0,
                       system_prompt="Ты Kev — локальный тестировщик игр, второй эшелон (бесплатно, без облака). "
                                     "Работаешь только внутри папки проекта чата, файлы не меняешь. " + RULE,
                       **common)
    print("agents:", jev["id"], jev["name"], "|", kev["id"], kev["name"])

    lesson = api("POST", "/memory/lessons", {
        "title": "Game testers: first cheap cloud (Jev), second local (Kev)",
        "body": RULE + f" Jev = agent {jev['id']} ({JEV_MODEL}, fallback {JEV_FALLBACK}); Kev = agent {kev['id']} ({KEV_MODEL}). "
                       "Применять для любых задач «проверь/протестируй игру», «менеджер игры». Проверка: tools/jev_kev_game_testers.py.",
        "author": "claude", "source": "file:tools/jev_kev_game_testers.py",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "category": "ops",
        "tags": ["games", "routing", "owner-rule"],
    })
    print("lesson:", lesson.get("status"), (lesson.get("lesson") or {}).get("id"), "deduped" if lesson.get("deduped") else "")

    results = []
    if not args.skip_run:
        for agent, label in ((jev, "Jev"), (kev, "Kev")):
            try:
                r = run_turn(args.project, agent, label)
            except Exception as exc:  # noqa: BLE001 — receipt must record the failure, not hide it
                r = {"agent": agent["name"], "status": "error", "error": str(exc)[:600]}
            print(json.dumps({k: v for k, v in r.items() if k != "answer_head"}, ensure_ascii=False))
            results.append(r)

    out = Path("docs/audits/JEV_KEV_GAME_TESTERS_20261101.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Jev / Kev — управленцы и тестировщики игр (01.11.2026)", "", RULE, "",
             f"- Jev: agent {jev['id']}, model `{JEV_MODEL}` (OpenRouter), fallback `{JEV_FALLBACK}`, budget $2/день",
             f"- Kev: agent {kev['id']}, model `{KEV_MODEL}` (local), без облака",
             f"- Lesson: {(lesson.get('lesson') or {}).get('id')} (recorded, not proven)", "",
             "## Живой прогон (реальный чат, проект = папка игры)", ""]
    for r in results:
        lines.append(f"### {r.get('agent')}")
        lines.append("```json\n" + json.dumps({k: v for k, v in r.items() if k != "answer_head"}, ensure_ascii=False, indent=1) + "\n```")
        if r.get("answer_head"):
            lines.append("Ответ (начало):\n\n" + r["answer_head"] + "\n")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("receipt:", out)


if __name__ == "__main__":
    main()
