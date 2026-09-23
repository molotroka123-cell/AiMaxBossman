"""_cdp_diagnostics читает улики и никогда не бросает: мёртвый порт, отсутствующий
лог и завершившийся процесс дают именованные строки, а живой HTTP — его тело."""
import http.server
import subprocess
import sys
import threading

from .test_ux2_desktop import _cdp_diagnostics


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b'{"Browser":"Chrome/999"}' if self.path == "/json/version" else b"ok"
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # тишина
        pass


def test_diagnostics_name_every_source_without_raising(tmp_path):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"]); proc.wait()
    (tmp_path / "chrome_debug.log").write_text("x" * 5000 + "LAST LINE", encoding="utf-8")
    try:
        text = _cdp_diagnostics(tmp_path, srv.server_port, proc, f"http://127.0.0.1:{srv.server_port}")
    finally:
        srv.shutdown()
    assert "window rc=7" in text and "Chrome/999" in text and "server GET /: 'ok'" in text
    assert text.endswith("LAST LINE") and "x" * 5000 not in text          # хвост, не весь файл
    # мёртвый порт и отсутствующий лог — именованные улики, не исключения
    dead = _cdp_diagnostics(tmp_path / "nope", 9, proc, "http://127.0.0.1:9")
    assert "<нет файла>" in dead and "Error" in dead
