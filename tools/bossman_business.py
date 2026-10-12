"""Reports / Operations MANUAL import tools, one entry point (Reports-Import.cmd in the Windows bundle).

  exchange     exchange_volume.cli      run --export result.json|export.html --period business --out DIR
  report       exchange_volume.report   --result DIR/volume_result.json --deals DIR/deals.csv --out DIR
  ops          ops_intake.cli           requests -> deduplicated table
  ops-report   ops_intake.report        table -> daily report

These are MANUAL imports: a file you hand over is read once and the results are written to the folder you name
(report.md, disputed.csv, deals.csv, volume_result.json). Nothing is imported from Telegram automatically, nothing
is sent anywhere, no network call is made, and a business-month figure is never called profit.

Works from a checkout (packages beside this file) and from the installed bundle (packages in ``business_tools/``).
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = {"exchange": "exchange_volume.cli", "report": "exchange_volume.report",
         "ops": "ops_intake.cli", "ops-report": "ops_intake.report"}


def utf8_console() -> None:
    """The launcher runs with ``-I`` (which implies ``-E``): PYTHONUTF8 / PYTHONIOENCODING are ignored and a Windows
    console gets the locale code page, so the first Cyrillic report line would raise UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in TOOLS:
        print(__doc__)
        return 2
    for folder in (HERE / "business_tools", HERE):          # installed bundle first, then a checkout's tools/
        if (folder / "exchange_volume").is_dir():
            sys.path.insert(0, str(folder))
            break
    module = TOOLS[argv[0]]
    sys.argv = [module, *argv[1:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
