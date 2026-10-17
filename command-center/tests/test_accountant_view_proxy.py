"""Bossman Accountant behind the BCC session (phone panel): UI from the package, JSON API through an allowlist."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from .test_apps_control import apps_root, make_app  # noqa: F401


async def test_accountant_view_serves_package_ui_and_allowlisted_api(env, apps_root):  # noqa: F811
    seen = []

    class Rogue(BaseHTTPRequestHandler):
        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, self.rfile.read(n) if n else b""))
            body = b'{"transactions":[]}' if self.path == "/api/transactions" else (
                b'<script>window.parent.document.body.textContent="owned"</script>')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_GET = do_POST = _do

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Rogue)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app_dir, _ = make_app(apps_root, "bossman-accountant", port=server.server_port)
    canonical = Path(__file__).resolve().parents[2] / "apps/bossman-accountant/src/bossman_accountant/ui.html"
    (app_dir / "src" / "bossman_accountant").mkdir(parents=True, exist_ok=True)
    (app_dir / "src" / "bossman_accountant" / "ui.html").write_bytes(canonical.read_bytes())
    try:
        card = next(a for a in (await env.client.get("/api/apps?refresh=true")).json()["apps"] if a["id"] == "bossman-accountant")
        assert card["view_url"] == "/api/apps/bossman-accountant/view/"
        seen.clear()  # the card probe asks /health; only requests after this point matter
        ui = await env.client.get("/api/apps/bossman-accountant/view/")
        assert ui.status_code == 200 and "window.parent.document" not in ui.text
        assert "Обзор" in ui.text and "script-src 'sha256-" in ui.headers["Content-Security-Policy"]
        assert "frame-ancestors 'self'" in ui.headers["Content-Security-Policy"]
        assert seen == [], "the UI must never be fetched from the port"
        ok = await env.client.get("/api/apps/bossman-accountant/view/api/transactions")
        assert ok.status_code == 200 and ok.json() == {"transactions": []}
        for bad in ("api/../etc", "docs", "openapi.json", "api/unknown", "api/jobs/../x/cancel"):
            assert (await env.client.get("/api/apps/bossman-accountant/view/" + bad)).status_code == 404
        assert (await env.client.post("/api/apps/bossman-accountant/view/docs", json={})).status_code in (403, 404)
        assert [s[1] for s in seen] == ["/api/transactions"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
