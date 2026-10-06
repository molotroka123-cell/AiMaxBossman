"""Poker-LoRA boundaries: no hidden info, no test leakage, loopback-only model server, review queue never becomes labels, no OS input."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from pokerlora import dataset, mistakes, sft
from pokerlora.openai_policy import NotLoopback, OpenAICompatPolicy
from pokerlora.evaluate import example_metrics

ROOT = Path(__file__).resolve().parents[1]


def test_no_os_input_money_or_external_platform_code():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "pokerlora").rglob("*.py")).lower()
    for banned in ("pyautogui", "pynput", "sendinput", "mouse_event", "playwright", "mss", "ggpoker", "pokerstars", "clubgg"):
        assert banned not in code, banned


def test_review_queue_is_not_a_label_source(small_data, tmp_path):
    d, _ = small_data
    inp = dataset.load(d, "train")[0]["input"]
    mistakes.record(tmp_path, inp, {"action": "teleport"}, ["illegal"], "bossman-live")
    assert mistakes.read(tmp_path)[0]["state"] == "needs_review"
    sft.write(d, tmp_path / "sft")
    assert "teleport" not in (tmp_path / "sft" / "sft_train.jsonl").read_text(encoding="utf-8")


def test_model_server_must_be_loopback():
    with pytest.raises(NotLoopback):
        OpenAICompatPolicy("https://api.example.com", "m")
    with pytest.raises(NotLoopback):
        OpenAICompatPolicy("http://user@127.0.0.1:8080", "m")


def _server(reply):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers["Content-Length"]); body = json.loads(self.rfile.read(n))
            assert body["messages"][0]["role"] == "system" and body["temperature"] == 0
            payload = json.loads(body["messages"][1]["content"])            # the model receives exactly the validated input
            assert "villain_hole" not in payload
            out = json.dumps({"choices": [{"message": {"content": reply(payload)}}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_openai_compat_policy_roundtrip_and_validation(small_data):
    d, _ = small_data
    te = dataset.load(d, "test")[:30]
    def good(p):
        a = p["legal"][0]
        return json.dumps({"action": a["action"], "size": a["amount"], "probs": {a["action"]: 1.0}, "explanation": "x"})
    srv = _server(good)
    try:
        pol = OpenAICompatPolicy(f"http://127.0.0.1:{srv.server_address[1]}", "fake")
        assert example_metrics(pol, te)["valid"]["mean"] == 1.0
    finally:
        srv.shutdown()
    srv = _server(lambda p: "Sure! I would go all in {oops")
    try:
        pol = OpenAICompatPolicy(f"http://127.0.0.1:{srv.server_address[1]}", "fake")
        assert example_metrics(pol, te)["valid"]["mean"] == 0.0
    finally:
        srv.shutdown()
    srv = _server(lambda p: json.dumps({"action": p["legal"][0]["action"], "size": p["legal"][0]["amount"] + 100}))     # the model tries to change the amount
    try:
        pol = OpenAICompatPolicy(f"http://127.0.0.1:{srv.server_address[1]}", "fake")
        m = example_metrics(pol, te)
        assert m["valid"]["mean"] < 1.0 and any("size" in k for k in m["invalid_reasons"])
    finally:
        srv.shutdown()


def test_training_scripts_have_a_dry_run_and_an_honest_environment_check(small_data, tmp_path, capsys):
    import importlib.util
    d, _ = small_data
    sft.write(d, tmp_path)
    spec = importlib.util.spec_from_file_location("lora_train", ROOT / "train" / "lora_train.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.main(["--base", "none", "--data", str(tmp_path), "--out", str(tmp_path / "o"), "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] and out["examples"] > 0 and len(out["data_sha256"]) == 64
    spec = importlib.util.spec_from_file_location("env_check", ROOT / "train" / "env_check.py"); e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
    r = e.probe()
    assert r["verdict"] in ("NOT_READY", "CAN_TRY_SMOKE_TRAIN") and "modules" in r
