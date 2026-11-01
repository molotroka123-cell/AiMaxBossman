"""Readiness of a RUNNING Bossman for a Codex teaching session — read-only probes, PASS/FAIL per capability.

    python tools/codex_learning_readiness.py http://127.0.0.1:8899 --data-dir <BCC_DATA_DIR of that backend>
    python tools/codex_learning_readiness.py http://127.0.0.1:8899 --token-file <path> --out receipt.json

Every request is a GET (enforced by `ReadOnlyClient`: any other method raises before anything is sent).
Nothing is clicked, written, stopped or approved. The script never prints the token or provider URLs.

Capabilities probed:
  health                 /api/health answers
  memory_lessons         /api/memory/lessons/policy: sources+dates required, status computed, recorded != proven
  memory_lessons_list    /api/memory/lessons answers and says proven_learning=false
  freshness_check        /api/memory/lessons/stale answers (stale-lesson flagging is wired)
  facts_store            /api/memory/facts answers (the ONE existing bi-temporal store)
  business_exercises     catalog has >= 3 synthetic exercises and a task carries no ground truth
  browser_gates          browser.submit/login/download are not AUTO; no send/exec browser tool is AUTO
  pc_control_gates       computer.act / computer.observe are not AUTO
  stop_switch            /api/computer/status and /api/autonomy/status expose the STOP state
  approvals              /api/approvals?status=pending answers
  cloud_chat_only        no agent on a cloud model has PC access without the owner's explicit grant (grant = WARN)
  web_deny_policy        /api/web-access/status reports a non-empty deny list
Exit code 0 only when nothing FAILs (WARN does not fail).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

HEADER = "X-BCC-Token"
GATED_BROWSER = ("browser.submit", "browser.login", "browser.download")
GATED_COMPUTER = ("computer.act", "computer.observe")


class ReadOnlyViolation(RuntimeError):
    pass


class ReadOnlyClient:
    """Wraps an httpx.AsyncClient and refuses every method except GET."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def get(self, path: str, **kw) -> httpx.Response:
        return await self._client.get(path, **kw)

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        if method.upper() != "GET":
            raise ReadOnlyViolation(f"readiness probe refuses {method} {path}: read-only")
        return await self._client.request(method, path, **kw)

    def __getattr__(self, name: str):
        if name in {"post", "put", "patch", "delete", "send", "stream"}:
            raise ReadOnlyViolation(f"readiness probe refuses .{name}(): read-only")
        raise AttributeError(name)


def _result(name: str, status: str, detail: str, **extra) -> dict:
    return {"capability": name, "status": status, "detail": detail, **extra}


async def _json(c: ReadOnlyClient, path: str) -> tuple[int, Any]:
    r = await c.get(path)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


async def probe_health(c):
    code, body = await _json(c, "/api/health")
    if code == 200:
        return _result("health", "PASS", "/api/health -> 200 (ready)")
    comps = (body or {}).get("components") if isinstance(body, dict) else None
    if code == 503 and isinstance(body, dict) and body.get("alive") is True:
        weak = sorted(k for k, v in (comps or {}).items()
                      if isinstance(v, dict) and v.get("status") not in {"HEALTHY", "ALIVE"})
        return _result("health", "WARN", f"alive but not ready: {weak}")
    return _result("health", "FAIL", f"/api/health -> {code}")


async def probe_memory_lessons(c):
    code, body = await _json(c, "/api/memory/lessons/policy")
    if code != 200 or not isinstance(body, dict):
        return _result("memory_lessons", "FAIL", f"policy -> {code}")
    need = {"title", "body", "author", "source", "observed_at"}
    ok = (body.get("recorded_is_not_proven_learning") is True
          and need <= set(body.get("required_fields") or [])
          and {"lesson_recorded", "verified_by_test", "refuted"} <= set(body.get("statuses") or [])
          and bool(body.get("owner_private_categories")) and body.get("secrets_redacted") is True)
    return _result("memory_lessons", "PASS" if ok else "FAIL",
                   "source+dates required, status computed by server, secrets redacted" if ok
                   else "policy is missing a required rule", store=body.get("store"))


async def probe_lessons_list(c):
    code, body = await _json(c, "/api/memory/lessons?limit=1")
    ok = code == 200 and isinstance(body, dict) and body.get("proven_learning") is False
    return _result("memory_lessons_list", "PASS" if ok else "FAIL", f"list -> {code}")


async def probe_freshness(c):
    code, body = await _json(c, "/api/memory/lessons/stale?max_age_days=30&limit=1")
    ok = code == 200 and isinstance(body, dict) and "items" in body
    return _result("freshness_check", "PASS" if ok else "FAIL", f"stale -> {code}")


async def probe_facts(c):
    code, _ = await _json(c, "/api/memory/facts?limit=1")
    return _result("facts_store", "PASS" if code == 200 else "FAIL", f"facts -> {code}")


async def probe_exercises(c):
    code, body = await _json(c, "/api/learning/business-exercises")
    items = (body or {}).get("items") if isinstance(body, dict) else None
    if code != 200 or not items or len(items) < 3:
        return _result("business_exercises", "FAIL", f"catalog -> {code}, items={len(items or [])}")
    tcode, task = await _json(c, f"/api/learning/business-exercises/{items[0]['exercise_id']}")
    leaked = isinstance(task, dict) and any(k in task for k in ("truth", "answer", "expected", "key"))
    ok = tcode == 200 and not leaked and task.get("simulated") is True
    return _result("business_exercises", "PASS" if ok else "FAIL",
                   f"{len(items)} synthetic exercises; task carries no ground truth" if ok
                   else f"task -> {tcode}, leaked={leaked}", count=len(items))


async def _tools(c) -> dict[str, dict]:
    code, body = await _json(c, "/api/agent/tools?compact=false")
    if code != 200 or not isinstance(body, dict):
        return {}
    return {str(t.get("name")): t for t in body.get("tools") or [] if isinstance(t, dict)}


async def probe_browser(c, tools):
    if not tools:
        return _result("browser_gates", "FAIL", "/api/agent/tools unavailable")
    bad = [n for n in GATED_BROWSER if n in tools and tools[n].get("default_effect") == "auto"]
    missing = [n for n in GATED_BROWSER if n not in tools]
    loose = [n for n, t in tools.items() if n.startswith("browser.")
             and t.get("category") in {"send", "exec", "admin"} and t.get("default_effect") == "auto"]
    ok = not bad and not missing and not loose
    return _result("browser_gates", "PASS" if ok else "FAIL",
                   "submit/login/download need approval" if ok
                   else f"auto={bad + loose}, missing={missing}")


async def probe_computer(c, tools):
    if not tools:
        return _result("pc_control_gates", "FAIL", "/api/agent/tools unavailable")
    present = [n for n in GATED_COMPUTER if n in tools]
    bad = [n for n in present if tools[n].get("default_effect") == "auto"]
    if not present:
        return _result("pc_control_gates", "WARN", "computer.* tools not registered on this backend")
    return _result("pc_control_gates", "PASS" if not bad else "FAIL",
                   "computer.act/observe need approval" if not bad else f"auto={bad}")


async def probe_stop(c):
    c1, comp = await _json(c, "/api/computer/status")
    c2, auto = await _json(c, "/api/autonomy/status")
    ok = c1 == 200 and isinstance(comp, dict) and "stopped" in comp and c2 == 200
    stopped = bool(comp.get("stopped")) if isinstance(comp, dict) else None
    return _result("stop_switch", "PASS" if ok else "FAIL",
                   f"STOP readable (computer.stopped={stopped})" if ok else f"computer={c1}, autonomy={c2}")


async def probe_approvals(c):
    code, _ = await _json(c, "/api/approvals?status=pending")
    return _result("approvals", "PASS" if code == 200 else "FAIL", f"approvals -> {code}")


def _is_local(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host in {"localhost", "::1"} or host.startswith("127.")


async def probe_cloud_chat_only(c):
    ca, agents = await _json(c, "/api/agents")
    cm, models = await _json(c, "/api/models")
    cp, providers = await _json(c, "/api/providers")
    if ca != 200 or cm != 200 or cp != 200:
        return _result("cloud_chat_only", "FAIL", f"agents={ca} models={cm} providers={cp}")
    def rows(x):
        return x if isinstance(x, list) else (x or {}).get("items") or []
    prov = {p.get("id"): p for p in rows(providers) if isinstance(p, dict)}
    cloud_models = set()
    for m in rows(models):
        if not isinstance(m, dict):
            continue
        p = prov.get(m.get("provider_id")) or {}
        if not _is_local(str(p.get("base_url") or "")) or ":cloud" in str(m.get("name") or ""):
            cloud_models.add(m.get("id"))
    open_pc, granted = [], []
    for a in rows(agents):
        if not isinstance(a, dict) or a.get("model_id") not in cloud_models:
            continue
        perms = a.get("permissions") if isinstance(a.get("permissions"), dict) else {}
        if perms.get("cloud_pc_access") is True:
            granted.append(a.get("id"))
        elif "cloud_pc_access" not in perms:
            open_pc.append(a.get("id"))
    if open_pc:
        return _result("cloud_chat_only", "FAIL", f"cloud agents without chat-only flag: {open_pc}")
    if granted:
        return _result("cloud_chat_only", "WARN", f"owner granted PC access to cloud agents {granted}")
    return _result("cloud_chat_only", "PASS", "every cloud-model agent is chat-only")


async def probe_web_deny(c):
    code, body = await _json(c, "/api/web-access/status")
    count = (body or {}).get("deny_domains") if isinstance(body, dict) else None
    ok = code == 200 and isinstance(count, int) and count > 0
    return _result("web_deny_policy", "PASS" if ok else "FAIL", f"deny_domains={count} (-> {code})")


async def run_probes(client: httpx.AsyncClient) -> dict:
    c = ReadOnlyClient(client)
    out: list[dict] = []
    for probe in (probe_health, probe_memory_lessons, probe_lessons_list, probe_freshness,
                  probe_facts, probe_exercises):
        try:
            out.append(await probe(c))
        except ReadOnlyViolation:
            raise
        except Exception as exc:  # noqa: BLE001 — a probe failure is a FAIL, not a crash
            out.append(_result(probe.__name__.replace("probe_", ""), "FAIL", type(exc).__name__))
    tools = await _tools(c)
    for probe in (probe_browser, probe_computer):
        out.append(await probe(c, tools))
    for probe in (probe_stop, probe_approvals, probe_cloud_chat_only, probe_web_deny):
        try:
            out.append(await probe(c))
        except ReadOnlyViolation:
            raise
        except Exception as exc:  # noqa: BLE001
            out.append(_result(probe.__name__.replace("probe_", ""), "FAIL", type(exc).__name__))
    counts = {s: sum(1 for r in out if r["status"] == s) for s in ("PASS", "WARN", "FAIL")}
    return {"verdict": "PASS" if counts["FAIL"] == 0 else "FAIL", "counts": counts, "results": out,
            "read_only": True, "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "note": "Readiness of the plumbing. It does not prove that Codex taught Bossman anything."}


def _token(args) -> str:
    if args.token_file:
        return Path(args.token_file).read_text(encoding="utf-8").strip()
    if os.environ.get("BCC_TOKEN"):
        return os.environ["BCC_TOKEN"].strip()
    data = args.data_dir or os.environ.get("BCC_DATA_DIR")
    if not data:
        raise SystemExit("need --data-dir, --token-file or BCC_TOKEN")
    return (Path(data) / "token").read_text(encoding="utf-8").strip()


async def _main(args) -> int:
    async with httpx.AsyncClient(base_url=args.url.rstrip("/"), timeout=20.0,
                                 headers={HEADER: _token(args)}) as client:
        report = await run_probes(client)
    report["backend"] = args.url
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["verdict"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url", help="backend base URL, e.g. http://127.0.0.1:8899")
    ap.add_argument("--data-dir", help="BCC_DATA_DIR of that backend (reads <dir>/token)")
    ap.add_argument("--token-file")
    ap.add_argument("--out", help="write the JSON receipt here")
    return asyncio.run(_main(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
