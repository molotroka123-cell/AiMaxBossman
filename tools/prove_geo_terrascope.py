"""Live proof on the owner's PC: Terrascope-derived geo.* tools with real data, directly and through the REAL chat path.

    python tools/prove_geo_terrascope.py [--agent 41] [--out docs/audits/2026-10-25-terrascope] [--skip-chat]

(a) direct calls (bcc.geo.GeoService, same code the tools run): aircraft over Czechia (count + 3 callsigns),
    weather in Prague, earthquakes M>=4.5 last 24 h (count + strongest), day/night in Prague now, braille map of Europe.
(b) chat path (backend :8801, owner token read from the data dir, never printed): three narrow Russian questions,
    sequential, each in its own synthetic thread; checks that the agent CALLED the geo tool and that every number /
    callsign / magnitude in the answer is present in that tool's own result (no invented values). Threads are purged
    afterwards. Exit 0 only when every check passes. Writes live-<UTC>.json into --out.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bcc.geo.service import GeoInputError, GeoUnavailable, service_for  # noqa: E402

from prove_chat_attachments import api  # noqa: E402

QUESTIONS = [
    ("aircraft", "geo.aircraft", "Сколько самолётов сейчас над Чехией? Назови три рейса (позывные)."),
    ("weather", "geo.weather", "Какая погода в Праге сейчас?"),
    ("earthquakes", "geo.earthquakes", "Были ли за последние сутки землетрясения сильнее 5 баллов (магнитуда) и где?"),
]
# Callsign / registration token: 4-8 upper-case letters+digits with at least one of each (BAW695, 9AJIP).
CALLSIGN = re.compile(r"\b(?=[0-9A-Z]*[A-Z])(?=[0-9A-Z]*[0-9])[0-9A-Z]{4,8}\b")
NOW = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())  # noqa: E731


def _data_dir() -> Path:
    return Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")


def direct() -> dict:
    svc = service_for(_data_dir())
    out: dict = {}

    def run(key, fn):
        t0 = time.time()
        try:
            res = fn()
            out[key] = {"ok": True, "at": NOW(), "seconds": round(time.time() - t0, 2), "result": res}
        except (GeoUnavailable, GeoInputError) as exc:
            out[key] = {"ok": False, "at": NOW(), "seconds": round(time.time() - t0, 2), "error": str(exc)}

    run("aircraft_czechia", lambda: svc.aircraft(region="Czechia", limit=10))
    run("weather_prague", lambda: svc.weather(city="Prague"))
    run("earthquakes_m45_24h", lambda: svc.earthquakes(min_magnitude=4.5, hours=24, limit=10))
    run("daynight_prague", lambda: svc.daynight(city="Prague"))
    run("map_europe", lambda: svc.map_render(region="europe", width=80, height=24))
    m = out["map_europe"]
    if m["ok"]:
        text = m["result"].pop("text")
        m["result"]["first_lines"] = text.split("\n")[:6]
        m["result"]["line_count"] = len(text.split("\n"))
        m["result"]["max_line_len"] = max(len(x) for x in text.split("\n"))
        m["result"]["non_blank_cells"] = sum(ch not in ("⠀", " ", "\n") for ch in text)
    a = out["aircraft_czechia"]
    checks = {
        "aircraft_live": a["ok"] and a["result"]["count"] > 0 and sum(1 for i in a["result"]["items"]
                                                                      if i["callsign"]) >= 3,
        "weather_live": out["weather_prague"]["ok"] and out["weather_prague"]["result"]["temperature_c"] is not None,
        "earthquakes_live": out["earthquakes_m45_24h"]["ok"],
        "daynight_ok": out["daynight_prague"]["ok"],
        "map_ok": m["ok"] and m["result"]["line_count"] == 24 and m["result"]["max_line_len"] <= 80
        and m["result"]["non_blank_cells"] > 100,
    }
    return {"results": out, "checks": checks}


def _events(task_id: int) -> list[dict]:
    ev = api("GET", f"/api/tasks/{task_id}/events?limit=1000")
    return ev.get("events") or ev.get("items") or (ev if isinstance(ev, list) else [])


def _ev_payload(e: dict) -> dict:
    """bus.task_history rows are flat: {**data, kind, seq, ts}."""
    return e if isinstance(e, dict) else {}


def _agent(agent_id: int) -> dict:
    a = api("GET", "/api/agents")
    items = a if isinstance(a, list) else a.get("items") or a.get("agents") or []
    return next((x for x in items if int(x.get("id") or 0) == agent_id), {})


def _nums(text: str) -> list[str]:
    return re.findall(r"-?\d+(?:[.,]\d+)?", text or "")


def verify(kind: str, answer: str, previews: list[str], used: list[dict] | None = None) -> dict:
    tool = "\n".join(previews)
    a = answer or ""
    v: dict = {}
    if kind == "aircraft":
        m = re.search(r"\): (\d+) \(в bbox (\d+)\)", tool)
        count, bbox = (m.group(1), m.group(2)) if m else (None, None)
        listed = set(re.findall(r"^- (\S+) icao24=", tool, re.M))
        said = set(CALLSIGN.findall(a))
        v.update(tool_count=count, tool_bbox_count=bbox, tool_callsigns=sorted(listed),
                 answer_callsigns=sorted(said))
        v["count_matches"] = bool(count) and (re.search(rf"\b{count}\b", a) is not None
                                              or re.search(rf"\b{bbox}\b", a) is not None)
        v["three_callsigns_from_tool"] = len(said & listed) >= 3
        icao = set(re.findall(r"icao24=(\S+)", tool))
        v["no_invented_callsigns"] = said <= (listed | icao)
    elif kind == "weather":
        m = re.search(r": (-?\d+(?:\.\d+)?) °C", tool)
        t = m.group(1) if m else None
        v["tool_temperature"] = t
        forms = set()
        if t is not None:
            forms = {t, t.replace(".", ","), str(round(float(t)))}
        v["temperature_matches"] = bool(forms) and any(re.search(rf"(?<![\d.,]){re.escape(f)}(?![\d])", a)
                                                       for f in forms)
        tool_nums = set(n.replace(",", ".") for n in _nums(tool))
        deg = re.findall(r"(-?\d+(?:[.,]\d+)?)\s*°", a)
        v["answer_degree_values"] = deg
        v["no_invented_degrees"] = all(d.replace(",", ".") in tool_nums or
                                       any(abs(float(d.replace(",", ".")) - float(x)) < 0.51 for x in tool_nums)
                                       for d in deg)
    else:
        m = re.search(r"за [\d.]+ ч \([^)]*\): (\d+)", tool)
        count = m.group(1) if m else None
        mags = set(re.findall(r"^- M(\d+\.\d)", tool, re.M))
        said = set(re.findall(r"(?<![\d.])(\d\.\d)(?![\d])", a.replace(",", ".")))
        said = {x for x in said if 4.0 <= float(x) <= 9.9}
        v.update(tool_count=count, tool_magnitudes=sorted(mags), answer_magnitudes=sorted(said))
        if count == "0":
            v["count_or_none_matches"] = bool(re.search(r"\bне был|\bнет\b|не зафиксир|0", a, re.I))
        else:
            strongest = max(mags, key=float) if mags else None
            v["strongest"] = strongest
            v["count_or_none_matches"] = bool(count) and (re.search(rf"\b{count}\b", a) is not None
                                                         or (strongest is not None and strongest in said))
        # the threshold the agent itself passed (e.g. «более 5.0») is an echo of the question, not a data value
        thresholds = {f"{float(u['args'].get('min_magnitude')):.1f}" for u in (used or [])
                      if u.get("tool") == "geo.earthquakes" and isinstance(u.get("args"), dict)
                      and u["args"].get("min_magnitude") is not None}
        v["thresholds_echoed"] = sorted(said & thresholds - mags)
        v["no_invented_magnitudes"] = said <= (mags | thresholds)
    return v


def chat(agent_id: int, wait: int, only: list[str] | None = None) -> dict:
    agent = _agent(agent_id)
    tools_before = list(agent.get("tools") or [])
    patched = False
    if not any(str(t) in ("geo.*",) or str(t).startswith("geo.") for t in tools_before):
        api("PATCH", f"/api/agents/{agent_id}", {"tools": tools_before + ["geo.*"]})
        patched = True
    tools_after = list(_agent(agent_id).get("tools") or [])
    out = {"agent_id": agent_id, "agent_name": agent.get("name"), "agent_tools_before": len(tools_before),
           "agent_tools_after": len(tools_after), "geo_added_to_agent": patched, "questions": []}
    threads = []
    try:
        for kind, tool, text in [q for q in QUESTIONS if not only or q[0] in only]:
            tid = api("POST", "/api/chat/threads", {"title": f"PROOF geo {kind}"})["id"]
            threads.append(tid)
            t0 = time.time()
            sent = api("POST", f"/api/chat/threads/{tid}/send", {"text": text, "agent_id": agent_id}, timeout=900)
            task_id = sent.get("task_id") or (sent.get("task") or {}).get("id") or (sent.get("turn") or {}).get(
                "task_id")
            answer, status = "", "?"
            while time.time() - t0 < wait:
                turn = next((x for x in api("GET", f"/api/chat/threads/{tid}").get("turns") or []
                             if int(x.get("task_id") or 0) == int(task_id or 0)), {})
                status = turn.get("status")
                if status in ("completed", "failed", "stopped", "cancelled", "blocked"):
                    answer = str(turn.get("result") or turn.get("error") or "")
                    break
                time.sleep(4)
            used, previews, errors = [], [], []
            for e in _events(int(task_id)):
                p = _ev_payload(e)
                et = p.get("kind")
                if et == "run.tool_use":
                    used.append({"tool": p.get("tool"), "args": p.get("args")})
                if et == "run.tool_result" and str(p.get("tool", "")).startswith("geo."):
                    (previews if p.get("ok") else errors).append(p.get("preview") or "")
            q = {"kind": kind, "question": text, "thread_id": tid, "task_id": task_id, "status": status,
                 "seconds": round(time.time() - t0, 1), "at": NOW(), "tools_used": used,
                 "tool_result_previews": previews, "tool_errors": errors, "answer": answer[:2000]}
            q["called_expected_tool"] = any(u["tool"] == tool for u in used)
            q["verify"] = verify(kind, answer, previews, used)
            q["pass"] = (status == "completed" and q["called_expected_tool"] and bool(previews)
                         and all(v for k, v in q["verify"].items() if isinstance(v, bool)))
            out["questions"].append(q)
            print(kind, status, "tool:", q["called_expected_tool"], "PASS" if q["pass"] else "FAIL",
                  {k: v for k, v in q["verify"].items() if isinstance(v, bool)})
    finally:
        deleted = []
        for tid in threads:
            try:
                api("DELETE", f"/api/chat/threads/{tid}?purge=1")
                deleted.append(tid)
            except Exception as exc:  # noqa: BLE001 - report, do not hide
                print("thread purge failed", tid, type(exc).__name__, getattr(exc, "code", ""))
        out["threads_purged"] = deleted
    out["pass"] = bool(out["questions"]) and all(q["pass"] for q in out["questions"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agent", type=int, default=41)
    ap.add_argument("--wait", type=int, default=600)
    ap.add_argument("--skip-chat", action="store_true")
    ap.add_argument("--only", default="", help="comma list of question kinds (aircraft,weather,earthquakes)")
    ap.add_argument("--skip-direct", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "audits" / "2026-10-25-terrascope"))
    a = ap.parse_args(argv)
    started = NOW()
    d = {"results": {}, "checks": {}, "skipped": True} if a.skip_direct else direct()
    for k, v in d["checks"].items():
        print("PASS" if v else "FAIL", "direct", k)
    c = None if a.skip_chat else chat(a.agent, a.wait, [x for x in a.only.split(",") if x] or None)
    ok = all(d["checks"].values()) and (c is None or c["pass"])
    rec = {"verdict": "PASS" if ok else "FAIL", "started": started, "finished": NOW(), "direct": d, "chat": c}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"live-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("VERDICT", rec["verdict"], "->", path)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
