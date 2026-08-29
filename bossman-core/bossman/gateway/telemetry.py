from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock


@dataclass
class GatewayMetrics:
    started_at: float = field(default_factory=time.time)
    requests_total: int = 0
    errors_total: int = 0
    inflight: int = 0
    queued: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    backend_requests: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    alias_requests: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    latencies_ms: list[float] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def begin(self, alias: str) -> float:
        with self._lock:
            self.requests_total += 1
            self.inflight += 1
            self.alias_requests[alias] += 1
        return time.perf_counter()

    def end(self, started: float, backend: str | None, usage: dict | None = None, error: bool = False) -> None:
        elapsed = (time.perf_counter() - started) * 1000
        with self._lock:
            self.inflight = max(0, self.inflight - 1)
            if backend:
                self.backend_requests[backend] += 1
            if error:
                self.errors_total += 1
            if usage:
                self.tokens_prompt += int(usage.get("prompt_tokens") or 0)
                self.tokens_completion += int(usage.get("completion_tokens") or 0)
            self.latencies_ms.append(elapsed)
            if len(self.latencies_ms) > 2000:
                del self.latencies_ms[:1000]

    def snapshot(self) -> dict:
        with self._lock:
            lat = sorted(self.latencies_ms)
            def pct(q: float) -> float:
                if not lat:
                    return 0.0
                return round(lat[min(len(lat)-1, int((len(lat)-1)*q))], 2)
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "inflight": self.inflight,
                "queued": self.queued,
                "prompt_tokens": self.tokens_prompt,
                "completion_tokens": self.tokens_completion,
                "latency_ms": {"p50": pct(.50), "p95": pct(.95), "p99": pct(.99)},
                "backend_requests": dict(self.backend_requests),
                "alias_requests": dict(self.alias_requests),
                "process": process_resources(),
            }


def process_resources() -> dict:
    data = {"pid": os.getpid()}
    try:
        import psutil  # optional
        p = psutil.Process()
        m = p.memory_info()
        data.update({"rss_mb": round(m.rss/1024/1024, 1), "cpu_percent": p.cpu_percent(interval=None)})
        vm = psutil.virtual_memory()
        data.update({"system_ram_total_mb": round(vm.total/1024/1024), "system_ram_available_mb": round(vm.available/1024/1024)})
    except Exception:
        pass
    return data
