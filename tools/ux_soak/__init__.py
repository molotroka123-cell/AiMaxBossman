"""Long-session UX soak for Bossman Command Center (RC 1.9, workstream F).

Drives the real UI (Playwright Chromium, throwaway profile), the Control API and the
terminal CLI against ONE backend started from this checkout on an isolated data dir,
restarts that backend repeatedly and checks that history, open context, task states and
settings survive. Never touches the owner data root.

    python -m tools.ux_soak.soak --port 8870 --data-dir <fresh dir> --out <evidence dir>

See ``soak.py --help``. Pure helpers live in ``stats.py`` (unit-tested).
"""
