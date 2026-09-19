"""Real HTTP owner flow; also runs against a clean installed wheel tree.

Set BCC_ACCEPTANCE_PYTHON and BCC_ACCEPTANCE_SOURCE_SHA for installed mode.
EditorServer checks every runtime import and the packaged UI against that SHA.
No fake app, ASGI transport, model stub or API response is used in this test.
"""
import hashlib
import json

import httpx
import pytest

from .test_editors_user_acceptance import EditorServer

pytestmark = pytest.mark.timeout(120)


def test_actual_apps_files_http_install_contract(tmp_path, monkeypatch):
    workspace = tmp_path / "owner-files"
    workspace.mkdir()
    source = workspace / "owner report.pdf"
    content = b"actual file; preview, move, restart and restore"
    source.write_bytes(content)
    monkeypatch.setenv("FILE_COMMANDER_ROOTS", str(workspace))
    monkeypatch.delenv("BOSSMAN_APPS_CONTROL_ENABLED", raising=False)
    monkeypatch.delenv("BOSSMAN_APPS_CONTROL_LOCK", raising=False)
    server = EditorServer(tmp_path / "bcc")
    evidence = {"flow": "actual HTTP and real files; browser is a separate gate"}
    client = httpx.Client(trust_env=False, timeout=35)
    try:
        server.start()
        # Exercise the session+CSRF path, not just legacy token authentication.
        login = client.post(server.url + "/api/login", json={
            "token": (server.data / "token").read_text().strip(), "label": "file-owner-acceptance"})
        assert login.status_code == 200, login.text
        client.headers["X-BCC-CSRF"] = login.json()["csrf"]
        def call(method, path, data=None, expected=200):
            result = client.request(method, server.url + path, json=data)
            assert result.status_code == expected, result.text
            return result.json()
        apps = call("GET", "/api/apps")
        assert any(app["id"] == "file-commander-mini" for app in apps["apps"])
        call("POST", "/api/apps/file-commander-mini/start", expected=403)
        call("PUT", "/api/apps/control/policy", {"enabled": True})
        started = call("POST", "/api/apps/file-commander-mini/start")
        assert started["ready"] and started["started"] and started["ok"]
        again = call("POST", "/api/apps/file-commander-mini/start")
        assert again["pid"] == started["pid"] and again["already_running"] and not again["started"]
        prefix = "/api/apps/file-commander-mini/view/"
        html = client.get(server.url + prefix)
        assert html.status_code == 200 and 'id="apply"' in html.text and 'id="organize"' in html.text
        with httpx.Client(trust_env=False, timeout=5) as unauthenticated:
            assert unauthenticated.get(server.url + prefix + "api/roots").status_code == 401
            assert unauthenticated.post(f"http://127.0.0.1:{started['port']}/api/files/scan", json={"root": str(workspace)}).status_code == 401
        assert call("GET", prefix + "api/roots")["roots"] == [str(workspace)]
        scanned = call("POST", prefix + "api/files/scan", {"root": str(workspace)})
        assert scanned["count"] == 1
        plan = call("POST", prefix + "api/files/organize-plan", {"root": str(workspace)})
        assert source.read_bytes() == content and plan["count"] == 1
        approved = {"operations": plan["operations"], "approve": True}
        applied = call("POST", prefix + "api/files/apply", approved)
        assert applied["status"] == "APPLIED"
        target = workspace / "Documents/PDF/owner report.pdf"
        assert not source.exists() and target.read_bytes() == content
        repeat = call("POST", prefix + "api/files/apply", approved)
        assert repeat["already_applied"] and repeat["batch_id"] == applied["batch_id"]
        denied = call("POST", prefix + "api/files/scan", {"root": str(workspace.parent)}, expected=403)
        assert denied["detail"] and target.read_bytes() == content
        # Direct child handle stop must work on every supported environment.
        assert call("POST", "/api/apps/file-commander-mini/stop")["stopped"] is True
        assert call("POST", "/api/apps/file-commander-mini/stop")["stopped"] is False
        server.restart()
        assert call("GET", "/api/apps/control/policy")["enabled"] is True
        restarted = call("POST", "/api/apps/file-commander-mini/start")
        assert restarted["ready"] is True
        history = call("GET", prefix + "api/files/batches")["batches"]
        assert any(batch["batch_id"] == applied["batch_id"] and batch["status"] == "APPLIED" for batch in history)
        restored = call("POST", prefix + "api/files/undo/" + applied["batch_id"], {"approve": True})
        assert restored["status"] == "ROLLED_BACK"
        assert source.read_bytes() == content and not target.exists()
        call("POST", "/api/apps/file-commander-mini/stop")
        call("PUT", "/api/apps/control/policy", {"enabled": False})
        server.restart()
        assert call("GET", "/api/apps/control/policy")["enabled"] is False
        call("POST", "/api/apps/file-commander-mini/start", expected=403)
        evidence.update({"status": "PASS", "apps": apps["total"], "file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                         "approval_dedup": True, "policy_persisted": True, "file_history_persisted": True})
        proof = server.data / "acceptance-runtime.json"
        if proof.exists(): evidence["runtime"] = json.loads(proof.read_text())
        (tmp_path / "apps-files-http-evidence.json").write_text(json.dumps(evidence, indent=2))
    finally:
        if server.process is not None and server.process.poll() is None:
            client.put(server.url + "/api/apps/control/policy", json={"enabled": True})
            client.post(server.url + "/api/apps/file-commander-mini/stop")
        client.close()
        server.stop()
