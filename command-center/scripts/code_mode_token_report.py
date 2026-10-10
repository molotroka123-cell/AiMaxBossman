"""Measure what code mode saves on the REAL tool registry of this checkout.

    python command-center/scripts/code_mode_token_report.py [--json]

Boots the application on a throw-away data dir (no network, no workers), reads every
registered tool schema and prints baseline vs facade numbers for: all tools, a typical
working agent, and a synthetic registry grown to 500 / 2500 tools (copies of the real
schemas under new names - shows that the facade cost stays flat while the baseline is linear).
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bcc.api import create_app                      # noqa: E402
from bcc.code_mode.measure import report            # noqa: E402
from bcc.config import Settings                     # noqa: E402
from bcc.tools import REGISTRY                      # noqa: E402

TYPICAL_PREFIXES = ("terminal.", "memory.", "browser.", "computer.", "apps.", "code.", "web.", "bossman.")


def grow(specs: list, target: int) -> list:
    out = list(specs)
    i = 0
    while len(out) < target:
        base = specs[i % len(specs)]
        clone = copy.copy(base)
        clone.name = f"{base.name}.v{i // len(specs) + 2}"
        out.append(clone)
        i += 1
    return out


async def main(as_json: bool) -> None:
    tmp = Path(tempfile.mkdtemp())
    data = tmp / "data"
    settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}", ui_dir=tmp / "no-ui")
    app = create_app(settings, announce_token=False)
    svc = app.state.svc
    await svc.start()
    try:
        everything = REGISTRY.all()
        typical = [s for s in everything if s.name.startswith(TYPICAL_PREFIXES)]
        result = {
            "all_registered_tools": report(everything),
            "typical_agent": report(typical),
            "synthetic_500": report(grow(everything, 500)),
            "synthetic_2500": report(grow(everything, 2500)),
        }
    finally:
        await svc.stop()
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"{'set':<22}{'tools':>6}{'base/call':>11}{'facade/call':>12}{'base turn':>11}{'facade turn':>12}{'ratio':>8}")
    for name, r in result.items():
        pc, tt = r["per_call"], r["typical_turn_mean"]
        print(f"{name:<22}{r['tools']:>6}{pc['baseline']['tokens_chars4']:>11}{pc['facade']['tokens_chars4']:>12}"
              f"{tt['baseline_tokens_chars4']:>11}{tt['facade_tokens_chars4']:>12}{tt['ratio']:>8.3f}")
    first = result["all_registered_tools"]["per_call"]
    print("cl100k per call (all tools): baseline", first["baseline"]["tokens_cl100k"],
          "facade", first["facade"]["tokens_cl100k"], "(None = tiktoken/vocabulary unavailable offline)")
    print("units: tokens = chars/4 proxy; turn = 4 model calls + 1 search listing + 1 snippet")


if __name__ == "__main__":
    asyncio.run(main("--json" in sys.argv))
