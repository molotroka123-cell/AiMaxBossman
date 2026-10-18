"""CLI. Read-only except ``issue-token`` (owner) and ``rollback`` (reversal).

    python -m bcc.self_improve botmap --out DIR --agents a.json --tasks t.json --models m.json [--probes p.json]
    python -m bcc.self_improve botmap --out DIR --live http://127.0.0.1:8801 --token-file PATH   # GET only
    python -m bcc.self_improve state --root DATA/self_improve
    python -m bcc.self_improve issue-token --root R --hypothesis H --draft D        (OWNER ONLY)
    python -m bcc.self_improve rollback --root R --apply-id ID --apply-root DIR
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from .botmap import build_botmap, render_markdown


def _get(base: str, path: str, token: str):
    req = urllib.request.Request(base.rstrip("/") + path, headers={"X-BCC-Token": token}, method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - local, GET only
        return json.loads(r.read().decode("utf-8"))


def _botmap(a) -> int:
    if a.live:
        token = Path(a.token_file).read_text(encoding="utf-8").strip()      # never printed
        agents, tasks, models = (_get(a.live, p, token) for p in ("/api/agents", "/api/tasks?limit=2000", "/api/models"))
        probes = {}
        for key, path in (("autonomy", "/api/autonomy/status"), ("qwen", "/api/qwen-operator/status"),
                          ("pc", "/api/pc-agent/status"), ("evolution", "/api/evolution/status")):
            try:
                probes[key] = _get(a.live, path, token)
            except Exception:  # noqa: BLE001 - a missing probe is N/A, not an error
                probes[key] = None
        health = _get(a.live, "/api/health", token)
        build = str(health.get("build_sha_short", "N/A"))
    else:
        load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
        agents, tasks, models = load(a.agents), load(a.tasks), load(a.models)
        probes, build = (load(a.probes) if a.probes else {}), a.runtime_build
    m = build_botmap(agents, tasks, models, probes=probes, source_sha=a.source_sha, runtime_build=build)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "BOT_MAP.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "BOT_MAP_RU.md").write_text(render_markdown(m), encoding="utf-8")
    print(f"bot map {m['map_version']}: {m['summary']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bcc.self_improve")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("botmap")
    b.add_argument("--out", required=True)
    b.add_argument("--agents"), b.add_argument("--tasks"), b.add_argument("--models"), b.add_argument("--probes")
    b.add_argument("--live"), b.add_argument("--token-file")
    b.add_argument("--source-sha", default="N/A"), b.add_argument("--runtime-build", default="N/A")
    s = sub.add_parser("state")
    s.add_argument("--root", required=True)
    t = sub.add_parser("issue-token")
    t.add_argument("--root", required=True), t.add_argument("--hypothesis", required=True)
    t.add_argument("--draft", required=True), t.add_argument("--ttl", type=int, default=3600)
    r = sub.add_parser("rollback")
    r.add_argument("--root", required=True), r.add_argument("--apply-id", required=True)
    r.add_argument("--apply-root", required=True)
    a = p.parse_args(argv)
    if a.cmd == "botmap":
        return _botmap(a)
    if a.cmd == "state":
        from .store import Store
        print(json.dumps(Store(a.root).state(), indent=2, ensure_ascii=False))
        return 0
    if a.cmd == "issue-token":
        from .owner import issue_token
        print(issue_token(a.root, hypothesis_id=a.hypothesis, draft_id=a.draft, ttl_s=a.ttl))
        return 0
    from .apply import rollback
    print(json.dumps(rollback(a.root, apply_id=a.apply_id, apply_root=a.apply_root, actor="owner-cli"), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
