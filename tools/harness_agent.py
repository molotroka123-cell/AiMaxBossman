"""Bossman Harness from the terminal: prepare local agents, list local models, run a goal with an independent check.

    python tools/harness_agent.py models                 # what Ollama has installed, which agents exist
    python tools/harness_agent.py sync                   # register local models + create local agents / Harness
    python tools/harness_agent.py run "добавь тест на X" --verify "python -m pytest -q tests/test_x.py" \
        --receipt docs/audits/<date>-harness/receipt.json

Talks to the running Command Center of your data root (same discovery and token as `bossman`), through the same
API the pult uses. The driver never approves anything: actions that need approval wait for you in the pult.
Exit codes: 0 PASS, 1 FAIL/TIMEOUT/STOPPED/NOT_RUN, 3 UNVERIFIED (finished, but nothing checked it).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "command-center"))
from bcc.local_operator.harness_run import run_goal, write_receipt  # noqa: E402
from bcc.terminal_cli.api_client import BossmanError, Client, discover  # noqa: E402

EXIT = {"PASS": 0, "UNVERIFIED": 3}


def _client(args) -> Client:
    return Client(discover(args.url, args.data_dir), timeout=60.0)


def cmd_models(args) -> int:
    with _client(args) as c:
        st = c.get("/api/local-operator/status")
    oll = st["ollama"]
    if not oll["ok"]:
        print(f"Ollama не отвечает: {oll['error']}")
        return 1
    print(f"Ollama {oll['version'] or '?'} на {oll['base_url']}: {len(oll['models'])} моделей")
    for m in oll["models"]:
        flags = [f for f, on in (("инструменты", m["tools"]), ("зрение", m["vision"]), ("облако", m["cloud"]),
                                 ("не чат", not m["chat"])) if on]
        reg = f"модель #{m['model_id']}" if m.get("model_id") else "не подключена"
        print(f"  {m['name']:<40} {m['parameter_size'] or '?':>8}  {', '.join(flags) or '-':<28} {reg}")
    print("Агенты:", ", ".join(f"{a['name']} (#{a['id']})" for a in st["agents"]) or "нет")
    return 0


def cmd_sync(args) -> int:
    with _client(args) as c:
        try:
            out = c.post("/api/local-operator/sync", {"harness": not args.no_harness})
        except BossmanError as exc:
            print(f"не выполнено: {exc}")
            return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


def cmd_run(args) -> int:
    with _client(args) as c:
        rec = run_goal(c, args.goal, agent_name=args.agent, agent_id=args.agent_id, verify=args.verify,
                       verify_cwd=args.verify_cwd, timeout_s=args.timeout,
                       backend={"url": c.target.url, "identity": c.target.identity})
    if args.receipt:
        write_receipt(rec, args.receipt)
    print(f"\nВЕРДИКТ: {rec.verdict} — {rec.reason}")
    if rec.answer_excerpt:
        print("Ответ агента (начало):\n" + rec.answer_excerpt[:800])
    if args.receipt:
        print(f"Квитанция: {args.receipt}")
    return EXIT.get(rec.verdict, 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=None)
    ap.add_argument("--data-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models")
    s = sub.add_parser("sync")
    s.add_argument("--no-harness", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("goal")
    r.add_argument("--agent", default="Bossman Harness")
    r.add_argument("--agent-id", type=int, default=None)
    r.add_argument("--verify", default=None, help="независимая проверка (команда, код 0 = PASS)")
    r.add_argument("--verify-cwd", default=None)
    r.add_argument("--timeout", type=float, default=1800.0)
    r.add_argument("--receipt", default=None)
    args = ap.parse_args(argv)
    try:
        return {"models": cmd_models, "sync": cmd_sync, "run": cmd_run}[args.cmd](args)
    except BossmanError as exc:
        print(f"Bossman: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
