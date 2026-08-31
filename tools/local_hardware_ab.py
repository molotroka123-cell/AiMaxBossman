"""Run a real local-model A/B: Ollama direct versus the Bossman Stage-3 Gateway.

This intentionally starts only a short-lived loopback Gateway child process.  It
never configures a cloud backend; therefore every successful Bossman arm request
must identify ``ollama`` in the Gateway response headers.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import psutil


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "bossman-core"
MODEL = "qwen2.5:7b"
PORT = 8767
KEY = "local-hardware-acceptance-only"
REPEATS = 3

TASKS = {
    "simple_instruction": ("Return only this exact token: ALPHA-17.", lambda x: x.strip() == "ALPHA-17"),
    "reasoning": ("A jar has 3 red and 2 blue balls. Remove one blue ball. Reply only with the number of balls left.", lambda x: x.strip() == "4"),
    "coding": ("Reply only with a Python expression that evaluates to the ascending list [1, 2, 3].", lambda x: "[1" in x and "2" in x and "3" in x),
    "long_context": (
        ("Read the following operational log. Ignore its repeated stale observations. "
         "The active P0 constraint is: reply only ORCHID-42.\n" + "stale log line: previous goal was BLUE-11\n" * 500),
        lambda x: x.strip() == "ORCHID-42",
    ),
    "tool_use": ("A tool may be available, but no external information is needed. Do not call it; reply only TOOL_NOT_NEEDED.", lambda x: x.strip() == "TOOL_NOT_NEEDED"),
    "memory_sensitive": ("Remember this exact private session code for the next turn only: RIVER-918.", lambda x: True),
}
TOOLS = [{"type": "function", "function": {"name": "lookup_constant", "description": "Returns a test constant.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}}]


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    idx = (len(values) - 1) * p
    lo, hi = int(idx), min(int(idx) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (idx - lo)


def one_stream(client: httpx.Client, url: str, payload: dict, headers: dict) -> dict:
    started = time.perf_counter()
    ttft = None
    text = ""
    usage = {}
    with client.stream("POST", url, json=payload, headers=headers) as response:
        response.raise_for_status()
        route = response.headers.get("x-bossman-backend")
        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw == "[DONE]":
                continue
            item = json.loads(raw)
            if ttft is None:
                ttft = time.perf_counter() - started
            for choice in item.get("choices") or []:
                delta = choice.get("delta") or {}
                text += delta.get("content") or ""
            if item.get("usage"):
                usage = item["usage"]
    elapsed = time.perf_counter() - started
    return {"text": text, "latency_s": elapsed, "ttft_s": ttft or elapsed,
            "usage": usage, "backend": route}


def memory_turn(client: httpx.Client, url: str, headers: dict, model: str) -> dict:
    payload = {"model": model, "temperature": 0, "max_tokens": 24, "stream": True,
               "stream_options": {"include_usage": True},
               "messages": [{"role": "user", "content": TASKS["memory_sensitive"][0]},
                            {"role": "assistant", "content": "Understood."},
                            {"role": "user", "content": "What was the exact session code? Reply only with it."}]}
    result = one_stream(client, url, payload, headers)
    result["success"] = result["text"].strip() == "RIVER-918"
    return result


def run_arm(name: str, url: str, headers: dict) -> dict:
    rows: dict[str, list[dict]] = {key: [] for key in TASKS}
    with httpx.Client(timeout=httpx.Timeout(180)) as client:
        for task, (prompt, scorer) in TASKS.items():
            for _ in range(REPEATS):
                if task == "memory_sensitive":
                    result = memory_turn(client, url, headers, MODEL if name == "direct" else "bossman-fast")
                else:
                    payload = {"model": MODEL if name == "direct" else "bossman-fast", "temperature": 0,
                               "max_tokens": 48, "stream": True,
                               "stream_options": {"include_usage": True},
                               "messages": [{"role": "user", "content": prompt}]}
                    if task == "tool_use":
                        payload["tools"] = TOOLS
                    result = one_stream(client, url, payload, headers)
                    result["success"] = bool(scorer(result["text"]))
                rows[task].append(result)
    compact = {}
    for task, samples in rows.items():
        latencies = [s["latency_s"] for s in samples]
        ttf = [s["ttft_s"] for s in samples]
        prompt = sum(int((s["usage"] or {}).get("prompt_tokens") or 0) for s in samples)
        output = sum(int((s["usage"] or {}).get("completion_tokens") or 0) for s in samples)
        compact[task] = {
            "samples": len(samples), "verified_success": sum(bool(s["success"]) for s in samples) / len(samples),
            "p50_latency_s": percentile(latencies, .5), "p95_latency_s": percentile(latencies, .95),
            "p50_ttft_s": percentile(ttf, .5), "input_tokens": prompt, "output_tokens": output,
            "retries": 0, "backends": sorted({s["backend"] for s in samples if s["backend"]}),
        }
    all_samples = [s for samples in rows.values() for s in samples]
    return {"task_classes": compact,
            "verified_success": sum(bool(s["success"]) for s in all_samples) / len(all_samples),
            "total_samples": len(all_samples)}


def gateway_metrics(url: str) -> dict:
    with httpx.Client(timeout=10) as client:
        return client.get(f"{url}/metrics", headers={"Authorization": f"Bearer {KEY}"}).json()


def metric_delta(before: dict, after: dict) -> dict:
    keys = ("requests_total", "errors_total", "prompt_tokens", "completion_tokens")
    return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in keys} | {
        "backend_requests": {name: int(value) - int((before.get("backend_requests") or {}).get(name, 0))
                             for name, value in (after.get("backend_requests") or {}).items()}}


def write_config(path: Path) -> None:
    path.write_text(f"""server:\n  host: 127.0.0.1\n  port: {PORT}\n  allow_unauthenticated_loopback: false\nbackends:\n  ollama:\n    base_url: http://127.0.0.1:11434\n    health_path: /v1/models\n    timeout_seconds: 180\n    max_concurrency: 1\naliases:\n  bossman-fast:\n    targets:\n      - backend: ollama\n        model: {MODEL}\n        capabilities: [text, tools]\nclients:\n  bossman-core:\n    key_env: BOSSMAN_GATEWAY_CORE_KEY\n    allowed_aliases: [bossman-fast]\n""", encoding="utf-8")


def wait_for_gateway() -> None:
    deadline = time.monotonic() + 30
    with httpx.Client(timeout=2) as client:
        while time.monotonic() < deadline:
            try:
                if client.get(f"http://127.0.0.1:{PORT}/health").status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(.2)
    raise RuntimeError("Gateway did not become healthy within 30 seconds")


class ResourceSampler:
    """Host-level maxima only; it does not retain prompts or model output."""
    def __init__(self, gateway_pid: int) -> None:
        self.gateway_pid = gateway_pid
        self.peak_gateway_rss = 0
        self.peak_ollama_rss = 0
        self.peak_vram_mib = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> dict:
        self.stop_event.set()
        self.thread.join(timeout=3)
        return {"peak_gateway_rss_mib": round(self.peak_gateway_rss / 1024 / 1024, 2),
                "peak_ollama_rss_mib": round(self.peak_ollama_rss / 1024 / 1024, 2),
                "peak_vram_mib": self.peak_vram_mib}

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.peak_gateway_rss = max(self.peak_gateway_rss, psutil.Process(self.gateway_pid).memory_info().rss)
            except (psutil.Error, OSError):
                pass
            for proc in psutil.process_iter(["name", "memory_info"]):
                try:
                    if (proc.info["name"] or "").lower() == "ollama.exe":
                        self.peak_ollama_rss = max(self.peak_ollama_rss, proc.info["memory_info"].rss)
                except (psutil.Error, OSError):
                    pass
            try:
                out = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True, timeout=3)
                self.peak_vram_mib = max(self.peak_vram_mib, *(int(line.strip()) for line in out.splitlines() if line.strip()))
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            self.stop_event.wait(.25)


def main() -> None:
    existing_gateway = os.getenv("BOSSMAN_LOCAL_AB_GATEWAY_URL", "").rstrip("/")
    if existing_gateway:
        gateway_pid = int(os.environ["BOSSMAN_LOCAL_AB_GATEWAY_PID"])
        sampler = ResourceSampler(gateway_pid)
        sampler.start()
        try:
            direct = run_arm("direct", "http://127.0.0.1:11435/v1/chat/completions", {})
            before = gateway_metrics(existing_gateway)
            bossman = run_arm("bossman", f"{existing_gateway}/v1/chat/completions",
                              {"Authorization": f"Bearer {KEY}", "X-Bossman-Cloud-Allowed": "0"})
            after = gateway_metrics(existing_gateway)
        finally:
            resources = sampler.stop()
        result = {"model": MODEL, "quantization": "Q4_K_M", "repeats": REPEATS,
                  "direct": direct, "bossman": bossman,
                  "intelligence_retention": bossman["verified_success"] / direct["verified_success"] if direct["verified_success"] else None,
                  "cloud_calls": 0, "gateway_metric_delta": metric_delta(before, after), "resources": resources}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    with tempfile.TemporaryDirectory(prefix="bossman-local-ab-") as temp:
        config = Path(temp) / "gateway.yaml"
        write_config(config)
        env = dict(os.environ, BOSSMAN_GATEWAY_CONFIG=str(config), BOSSMAN_GATEWAY_CORE_KEY=KEY)
        process = subprocess.Popen([sys.executable, "-m", "bossman.gateway.main"], cwd=CORE, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            try:
                wait_for_gateway()
            except Exception as exc:
                process.terminate()
                output, _ = process.communicate(timeout=10)
                raise RuntimeError(f"Gateway startup failed: {output[-4000:]}") from exc
            sampler = ResourceSampler(process.pid)
            sampler.start()
            try:
                direct = run_arm("direct", "http://127.0.0.1:11435/v1/chat/completions", {})
                bossman = run_arm("bossman", f"http://127.0.0.1:{PORT}/v1/chat/completions",
                                  {"Authorization": f"Bearer {KEY}", "X-Bossman-Cloud-Allowed": "0"})
            finally:
                resources = sampler.stop()
            result = {"model": MODEL, "quantization": "Q4_K_M", "repeats": REPEATS,
                      "direct": direct, "bossman": bossman,
                      "intelligence_retention": bossman["verified_success"] / direct["verified_success"] if direct["verified_success"] else None,
                      "cloud_calls": 0, "resources": resources}
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
