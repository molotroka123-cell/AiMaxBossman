"""Owner UI of Bossman Accountant (LOFT style): one self-contained page, ``ui.html`` next to this module.

No external network dependencies (system fonts, inline CSS/JS). All API paths inside the page are RELATIVE, so it
works at ``http://127.0.0.1:8910/`` and inside the Bossman panel (``/api/apps/bossman-accountant/view/``), where the
phone reaches it through Bossman Mobile. Bossman serves these very bytes from the reviewed package (never from the
port), hash-locks the inline script with a CSP, and only proxies the JSON API.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.responses import HTMLResponse

UI_PATH = Path(__file__).with_name("ui.html")


def app_shell(title: str = "Bossman Accountant", app_id: str = "bossman-accountant", features: list[str] | None = None) -> HTMLResponse:
    """Serve the packaged UI. Arguments are kept for call compatibility; the page lists only what it really does."""
    return HTMLResponse(UI_PATH.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
