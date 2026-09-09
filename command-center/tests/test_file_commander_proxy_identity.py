"""A real rogue localhost listener may not become BCC-origin code or evidence."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading

from bcc.features import apps_control as ctl
from .test_apps_control import apps_root, make_app  # noqa: F401


async def test_rogue_service_neither_receives_bearer_nor_supplies_ui_or_success(env, apps_root):  # noqa: F811
    seen = []
    class Rogue(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({"headers": dict(self.headers), "path": self.path})
            body = b'<script>window.parent.document.body.textContent="owned"</script>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Rogue)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app_dir, _ = make_app(apps_root, "file-commander-mini", port=server.server_port)
    canonical = Path(__file__).resolve().parents[2] / "apps/file-commander-mini/src/file_commander_mini/ui.html"
    (app_dir / "ui.html").write_bytes(canonical.read_bytes())
    try:
        response = await env.client.get("/api/apps/file-commander-mini/view/")
        assert response.status_code == 200
        assert 'id="organize"' in response.text and "window.parent.document" not in response.text
        assert "script-src 'sha256-" in response.headers["Content-Security-Policy"]
        assert seen == [], "trusted UI must not ask an arbitrary port for HTML"
        result = await env.client.get("/api/apps/file-commander-mini/view/api/roots")
        assert result.status_code == 502
        assert result.json()["error"]["code"] == "FILE_COMMANDER_IDENTITY_UNVERIFIED"
        token = ctl._app_token(env.settings.data_dir, "file-commander-mini")
        assert token not in json.dumps(seen)
        assert all("X-Bossman-App-Token" not in item["headers"] for item in seen)
        assert len(seen) == 1, "unverifiable result must never cause automatic retry"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
