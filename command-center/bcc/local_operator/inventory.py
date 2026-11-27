"""What the owner's local Ollama really has installed — read-only.

`probe()` asks Ollama itself (`/api/version`, `/api/tags`, `/api/show`) and returns one `LocalModel` per installed
model with the capabilities Ollama reports for it (`completion`, `tools`, `vision`, `thinking`, `embedding`).
Nothing is guessed from the model name: a model whose `/api/show` has no `capabilities` list is reported with
`capabilities_source = "unknown"` and is NOT treated as tool-capable (it can still chat).

`:cloud` / `-cloud` tags are listed but marked `cloud=True`: Ollama forwards their prompts to ollama.com, so for
privacy, price and the local-only rule they are cloud models (same rule as bcc.provider_governance).
Embedding-only models are listed with `chat=False`.

Read-only: no pull, no load, no generation. The only effect on Ollama is the metadata requests.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

#: Ollama listens on 11434; on a WSL2 host 11434 can be held by a silent forwarder and Ollama moves to 11435
#: (same reasoning as bcc.discovery.KNOWN_ENDPOINTS). Override: BOSSMAN_OLLAMA_URLS="http://127.0.0.1:11434,…".
DEFAULT_URLS: tuple[str, ...] = ("http://127.0.0.1:11434", "http://127.0.0.1:11435")
TIMEOUT_S = 5.0
SHOW_CONCURRENCY = 4
_CLOUD_TAG = re.compile(r"(?i)[:-]cloud\b")
_PARAMS = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMBT])?\s*$", re.I)
_SCALE = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12, None: 1.0}


def ollama_urls(env: dict | None = None) -> tuple[str, ...]:
    raw = (env if env is not None else os.environ).get("BOSSMAN_OLLAMA_URLS", "")
    urls = tuple(u.strip().rstrip("/") for u in raw.split(",") if u.strip())
    return urls or DEFAULT_URLS


def parameter_count(text: Any) -> float | None:
    """"30.5B" -> 30.5e9; "" / "unknown" -> None (never 0: an unknown size is not a small model)."""
    m = _PARAMS.match(str(text or ""))
    if not m:
        return None
    return float(m.group(1)) * _SCALE[(m.group(2) or "").upper() or None]


@dataclass(frozen=True)
class LocalModel:
    name: str
    size_bytes: int = 0
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    context_length: int | None = None
    capabilities: tuple[str, ...] = ()
    capabilities_source: str = "unknown"      # "ollama" | "unknown"
    cloud: bool = False
    show_error: str = ""

    @property
    def tools(self) -> bool:
        return self.capabilities_source == "ollama" and "tools" in self.capabilities

    @property
    def vision(self) -> bool:
        return self.capabilities_source == "ollama" and "vision" in self.capabilities

    @property
    def thinking(self) -> bool:
        return self.capabilities_source == "ollama" and "thinking" in self.capabilities

    @property
    def chat(self) -> bool:
        """Can answer a chat turn. Unknown capabilities still chat; only an explicit embedding-only list cannot."""
        if self.capabilities_source != "ollama":
            return True
        return "completion" in self.capabilities

    @property
    def params(self) -> float | None:
        return parameter_count(self.parameter_size)

    def caps(self) -> dict[str, bool]:
        """Registry `models.caps` shape (vision, tools, reasoning)."""
        return {"tools": self.tools, "vision": self.vision, "reasoning": self.thinking}

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["capabilities"] = list(self.capabilities)
        d.update(tools=self.tools, vision=self.vision, thinking=self.thinking, chat=self.chat)
        return d


@dataclass
class Inventory:
    ok: bool
    base_url: str = ""
    version: str = ""
    models: list[LocalModel] = field(default_factory=list)
    error: str = ""
    tried: list[str] = field(default_factory=list)

    @property
    def v1_url(self) -> str:
        return f"{self.base_url}/v1" if self.base_url else ""

    def public(self) -> dict[str, Any]:
        return {"ok": self.ok, "base_url": self.base_url, "version": self.version, "error": self.error,
                "tried": list(self.tried), "models": [m.public() for m in self.models]}


def _context_length(model_info: Any) -> int | None:
    if not isinstance(model_info, dict):
        return None
    for key, value in model_info.items():
        if str(key).endswith(".context_length") and isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def parse_model(tag: dict, show: dict | None, show_error: str = "") -> LocalModel:
    """One `/api/tags` entry plus its `/api/show` body (None when show failed)."""
    name = str(tag.get("name") or tag.get("model") or "").strip()
    details = dict(tag.get("details") or {})
    caps_raw = (show or {}).get("capabilities")
    if isinstance(caps_raw, list) and all(isinstance(c, str) for c in caps_raw):
        caps, source = tuple(sorted({c.strip().lower() for c in caps_raw if c.strip()})), "ollama"
    else:
        caps, source = (), "unknown"
    if isinstance((show or {}).get("details"), dict):
        details = {**details, **show["details"]}
    size = tag.get("size")
    return LocalModel(
        name=name,
        size_bytes=int(size) if isinstance(size, int) and not isinstance(size, bool) else 0,
        family=str(details.get("family") or ""),
        parameter_size=str(details.get("parameter_size") or ""),
        quantization=str(details.get("quantization_level") or ""),
        context_length=_context_length((show or {}).get("model_info")),
        capabilities=caps, capabilities_source=source,
        cloud=bool(_CLOUD_TAG.search(name)),
        show_error=show_error)


async def _probe_one(client: httpx.AsyncClient, base: str) -> Inventory:
    version = ""
    try:
        v = await client.get(f"{base}/api/version")
        if v.status_code == 200:
            version = str((v.json() or {}).get("version") or "")
    except (httpx.HTTPError, ValueError):
        pass   # version is informational; /api/tags decides whether Ollama is there
    try:
        r = await client.get(f"{base}/api/tags")
    except httpx.HTTPError as exc:
        return Inventory(ok=False, base_url=base, error=f"{type(exc).__name__}: {exc}"[:300])
    if r.status_code != 200:
        return Inventory(ok=False, base_url=base, error=f"/api/tags ответил {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        return Inventory(ok=False, base_url=base, error="/api/tags вернул не JSON — это не Ollama")
    tags = body.get("models") if isinstance(body, dict) else None
    if not isinstance(tags, list):
        return Inventory(ok=False, base_url=base, error="/api/tags без списка models — это не Ollama")
    sem = asyncio.Semaphore(SHOW_CONCURRENCY)

    async def show(tag: dict) -> LocalModel:
        name = str(tag.get("name") or tag.get("model") or "")
        async with sem:
            try:
                s = await client.post(f"{base}/api/show", json={"model": name})
                if s.status_code != 200:
                    return parse_model(tag, None, f"/api/show ответил {s.status_code}")
                return parse_model(tag, s.json())
            except (httpx.HTTPError, ValueError) as exc:
                return parse_model(tag, None, f"/api/show: {type(exc).__name__}")

    models = await asyncio.gather(*(show(t) for t in tags if isinstance(t, dict) and (t.get("name") or t.get("model"))))
    models = sorted(models, key=lambda m: m.name)
    return Inventory(ok=True, base_url=base, version=version, models=list(models))


async def probe(urls: tuple[str, ...] | None = None, *, transport: Any = None,
                timeout: float = TIMEOUT_S) -> Inventory:
    """First URL whose `/api/tags` answers like Ollama wins. Errors are kept per URL, never hidden."""
    urls = urls or ollama_urls()
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=timeout, transport=transport, trust_env=False) as client:
        for base in urls:
            inv = await _probe_one(client, base)
            inv.tried = list(urls)
            if inv.ok:
                return inv
            errors.append(f"{base}: {inv.error}")
    return Inventory(ok=False, error="Ollama не отвечает — " + "; ".join(errors), tried=list(urls))


__all__ = ["DEFAULT_URLS", "Inventory", "LocalModel", "ollama_urls", "parameter_count", "parse_model", "probe"]
