"""Owner-authorized loopback integration probe; never prints authentication material."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import httpx
import bossman_os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--model", action="store_true")
    args = parser.parse_args()
    token = (args.data_dir / "token").read_text(encoding="utf-8").strip()
    with httpx.Client(base_url="http://127.0.0.1:8812", timeout=90, trust_env=False) as client:
        assert client.get("/executive-os").status_code == 200
        assert client.get("/api/executive-os/status").status_code in (401, 403)
        client.headers["X-BCC-Token"] = token

        def request(method, path, payload=None):
            response = client.request(method, "/api/executive-os" + path, json=payload)
            assert response.is_success, (path, response.status_code)
            return response.json()

        status = request("GET", "/status")
        assert status["runtime"]["cloud_enabled"] is False
        content = "Executive OS: independently verified UTF-8 result. Проверено."
        mid = "installed-live-proof"
        if not args.resume:
            contract = {"id": mid, "project": "live-proof", "steps": [
                {"id": "write", "depends_on": [], "action": "artifact.write", "path": "report.txt", "content": content},
                {"id": "verify", "depends_on": ["write"], "action": "artifact.verify", "path": "report.txt", "content": content}]}
            request("POST", "/missions", contract)
            baseline = request("POST", "/evaluate", {"suite_id": "live-suite", "phase": "baseline", "cases": {"artifact": mid}})
            assert baseline["passed"] == 0
            result = request("POST", f"/missions/{mid}/run")
            assert result["done"] and len(result["verified_now"]) == 2
            candidate = request("POST", "/evaluate", {"suite_id": "live-suite", "phase": "candidate", "cases": {"artifact": mid}})
            assert candidate["release_gate"]["eligible"] is True and candidate["promoted"] is False
        target = args.data_dir / "artifacts" / mid / "report.txt"
        before = target.stat().st_mtime_ns
        snap = request("GET", f"/missions/{mid}")
        assert snap["done"] and len(snap["verified_now"]) == 2
        assert request("POST", f"/missions/{mid}/run")["done"]
        assert request("POST", "/recover", {"id": mid})["done"]
        assert target.stat().st_mtime_ns == before
        assert target.read_bytes() == content.encode("utf-8")
        report = {"recorded_at_unix": time.time(), "installed_package": str(Path(bossman_os.__file__).resolve()),
                  "http_unauthorized_denied": True, "console_http": 200, "mission": mid,
                  "done": True, "verified_steps": snap["verified_now"],
                  "artifact_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                  "rerun_and_recover_without_write": True, "after_process_restart": args.resume,
                  "cloud_enabled": False, "scope": "local sidecar, real HTTP and independent filesystem read"}
        if args.model:
            start = time.perf_counter()
            proposal = request("POST", "/propose", {"objective": "Предложи три коротких шага проверки отчёта: задача, ограничения, независимая проверка. Только план, ничего не исполняй.", "project": "live-proof"})
            assert proposal["text"] and proposal["executed"] is False
            delivered = request("GET", "/missions/" + proposal["mission_id"])
            assert delivered["done"] is True
            report["local_model"] = {"model": proposal["model"], "text_chars": len(proposal["text"]),
                                     "tokens_in": proposal["tokens_in"], "tokens_out": proposal["tokens_out"],
                                     "seconds": round(time.perf_counter() - start, 3),
                                     "delivery_verified": True, "reasoning_quality_verified": False,
                                     "mission_id": proposal["mission_id"]}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
