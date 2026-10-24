"""``python -m niche_research start|tick|status|stop|report`` (data dir: NICHE_RESEARCH_DIR)."""
from __future__ import annotations

import argparse
import json
import sys

from niche_research.config import load_config
from niche_research.orchestrator import Orchestrator


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m niche_research",
                                 description="28-day niche research run (nothing is ever sent or published)")
    ap.add_argument("command", choices=["start", "tick", "status", "stop", "report"])
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    orch = Orchestrator(load_config())
    if args.command == "start":
        out = orch.start()
    elif args.command == "tick":
        out = orch.tick()
    elif args.command == "stop":
        out = orch.stop("manual")
    elif args.command == "report":
        out = {"days": [], "weeks": []}
        for t in orch.store.tasks():
            if t["day"] not in out["days"]:
                out["days"].append(t["day"])
        for d in out["days"]:
            orch.reports.write_day(d)
        for w in sorted({(d - 1) // 7 + 1 for d in out["days"]}):
            orch.reports.write_week(w)
            out["weeks"].append(w)
        if orch.store.get_meta("stopped"):
            out["final"] = str(orch.reports.write_final(orch.store.get_meta("stopped")["reason"]))
        out["run_dir"] = str(orch.config.run_dir)
    else:
        out = orch.status()
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
