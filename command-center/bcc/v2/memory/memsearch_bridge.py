from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(slots=True)
class MemoryHit:
    content: str
    source: str
    heading: str = ""
    score: float = 0.0
    chunk_hash: str = ""
    metadata: dict[str, Any] | None = None

class MemSearchUnavailable(RuntimeError):
    pass

class MemSearchBridge:
    """Thin CLI bridge around zilliztech/memsearch.

    Why CLI:
    - decouples BOSSMAN runtime from Milvus/ONNX native dependencies
    - works cleanly when memsearch runs inside WSL2/container
    - JSON output is stable for agent tools
    """

    def __init__(
        self,
        *,
        executable: str = "memsearch",
        provider: str = "onnx",
        model: str = "",
        milvus_uri: str = "",
        collection: str = "bossman_memory",
        base_url: str = "",
        api_key: str = "",
    ):
        self.executable = executable
        self.provider = provider
        self.model = model
        self.milvus_uri = milvus_uri
        self.collection = collection
        self.base_url = base_url
        self.api_key = api_key

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def _common(self) -> list[str]:
        args = ["--provider", self.provider, "--collection", self.collection]
        if self.model:
            args += ["--model", self.model]
        if self.milvus_uri:
            args += ["--milvus-uri", self.milvus_uri]
        if self.base_url:
            args += ["--base-url", self.base_url]
        if self.api_key:
            args += ["--api-key", self.api_key]
        return args

    async def _run(self, *args: str, timeout: int = 300) -> str:
        if not self.available():
            raise MemSearchUnavailable(
                f"`{self.executable}` not found. Install memsearch or configure a bridge."
            )
        proc = await asyncio.create_subprocess_exec(
            self.executable, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"memsearch timed out after {timeout}s")
        text = out.decode(errors="replace")
        if proc.returncode:
            raise RuntimeError(f"memsearch failed ({proc.returncode}): {text[-2000:]}")
        return text

    async def index(self, paths: list[Path], *, force: bool = False) -> str:
        args = ["index", *[str(p) for p in paths], *self._common()]
        if force:
            args.append("--force")
        return await self._run(*args, timeout=1800)

    async def search(self, query: str, *, top_k: int = 12) -> list[MemoryHit]:
        args = [
            "search", query,
            "--top-k", str(top_k),
            "--json-output",
            *self._common(),
        ]
        raw = await self._run(*args, timeout=180)
        data = json.loads(raw)
        if isinstance(data, dict):
            rows = data.get("results") or data.get("data") or []
        else:
            rows = data
        hits: list[MemoryHit] = []
        for r in rows or []:
            hits.append(MemoryHit(
                content=str(r.get("content") or r.get("text") or ""),
                source=str(r.get("source") or r.get("path") or ""),
                heading=str(r.get("heading") or ""),
                score=float(r.get("score") or 0.0),
                chunk_hash=str(r.get("chunk_hash") or r.get("hash") or ""),
                metadata=dict(r),
            ))
        return hits

    async def expand(self, chunk_hash: str) -> dict[str, Any]:
        raw = await self._run(
            "expand", chunk_hash, "--json-output", *self._common(), timeout=120
        )
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"data": data}

    async def stats(self) -> str:
        return await self._run("stats", "--collection", self.collection, timeout=60)
