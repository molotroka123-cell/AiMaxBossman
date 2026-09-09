"""Trusted packaged UI for the existing File Commander operations."""
from pathlib import Path
import hashlib
import base64
import re
from fastapi.responses import HTMLResponse


def app_shell() -> HTMLResponse:
    html = Path(__file__).with_name("ui.html").read_text(encoding="utf-8")
    script = re.search(r"<script>([\s\S]+?)</script>", html).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return HTMLResponse(html, headers={
        "Content-Security-Policy": "default-src 'none'; script-src 'sha256-" + digest + "'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'self'",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
