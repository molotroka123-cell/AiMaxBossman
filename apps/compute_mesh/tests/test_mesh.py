import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from apps.compute_mesh import coordinator, make_drive, worker

TOKEN = "t" * 40


@pytest.fixture
def mesh_url():
    mesh = coordinator.Mesh(TOKEN)
    srv = coordinator.serve(mesh, "127.0.0.1", 0)
    yield mesh, "http://127.0.0.1:" + str(srv.server_address[1])
    srv.shutdown()


@pytest.fixture
def fake_ollama():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = json.dumps({"response": "echo:" + body["prompt"], "eval_count": 3}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:" + str(srv.server_address[1])
    srv.shutdown()


def test_short_token_refused():
    with pytest.raises(ValueError):
        coordinator.Mesh("short")


def test_wrong_token_is_401(mesh_url):
    _, url = mesh_url
    with pytest.raises(urllib.error.HTTPError) as e:
        worker.http(url + "/register", {"hostname": "x"}, "wrong-token")
    assert e.value.code == 401


def test_unknown_job_type_refused_on_both_sides(mesh_url):
    mesh, url = mesh_url
    with pytest.raises(urllib.error.HTTPError) as e:
        worker.http(url + "/submit", {"type": "shell", "payload": {"cmd": "whoami"}}, TOKEN)
    assert e.value.code == 400
    ok, result = worker.run_job({"type": "shell", "payload": {"cmd": "whoami"}})
    assert ok is False and "not allowed" in result


def test_full_round_trip_through_a_worker(mesh_url, fake_ollama, monkeypatch):
    mesh, url = mesh_url
    monkeypatch.setattr(worker, "OLLAMA", fake_ollama)
    jid = worker.http(url + "/submit", {"type": "ollama_generate", "payload": {"model": "m", "prompt": "hello"}}, TOKEN)["job_id"]
    ping = worker.http(url + "/submit", {"type": "ping", "payload": {}}, TOKEN)["job_id"]
    worker.loop({"coordinator": url, "token": TOKEN}, rounds=3, pause=0)
    assert mesh.jobs[jid]["state"] == "done" and mesh.jobs[jid]["result"]["response"] == "echo:hello"
    assert mesh.jobs[ping]["state"] == "done"
    st = worker.http(url + "/status", None, TOKEN)
    assert len(st["workers"]) == 1 and all(j["state"] == "done" for j in st["jobs"].values())


def test_another_worker_cannot_finish_a_job_it_did_not_take(mesh_url):
    mesh, url = mesh_url
    a, b = mesh.register({"hostname": "a"}), mesh.register({"hostname": "b"})
    jid = mesh.submit("ping", {})
    assert mesh.take(a)["id"] == jid
    assert mesh.finish(b, jid, True, "stolen") is False and mesh.jobs[jid]["state"] == "running"


def test_make_drive_writes_worker_config_and_start_script(tmp_path):
    out = make_drive.prepare(str(tmp_path / "drive"), "http://192.168.1.10:8850", str(tmp_path / "mesh.token"))
    assert out["files"] == ["START_WORKER.cmd", "mesh.json", "worker.py"]
    cfg = json.loads((tmp_path / "drive" / "bossman-worker" / "mesh.json").read_text(encoding="utf-8"))
    assert cfg["coordinator"] == "http://192.168.1.10:8850" and len(cfg["token"]) >= 32
    with pytest.raises(ValueError):
        make_drive.prepare(str(tmp_path / "d2"), "192.168.1.10:8850", str(tmp_path / "mesh.token"))


def test_dashboard_needs_the_token_and_escapes_names(mesh_url):
    import urllib.request
    mesh, url = mesh_url
    mesh.register({"hostname": "<b>pc</b>", "cpu_count": 8, "models": ["m1", "m2"]})
    mesh.submit("ping", {})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url + "/dashboard?token=wrong", timeout=10)
    assert e.value.code == 401
    page = urllib.request.urlopen(url + "/dashboard?token=" + TOKEN, timeout=10).read().decode("utf-8")
    assert "PCs online: 1 of 1" in page and "queued 1" in page
    assert "<b>pc</b>" not in page and "&lt;b&gt;pc&lt;/b&gt;" in page
