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
        vault_root: str = "",
        excludes: list[str] | None = None,
    ):
        self.executable = executable
        self.provider = provider
        self.model = model
        self.milvus_uri = milvus_uri
        self.collection = collection
        self.base_url = base_url
        self.api_key = api_key
        # Корень нужен, чтобы не отдавать наружу абсолютные пути с $HOME
        self.vault_root = vault_root
        # Без передачи --exclude исключения ObsidianVault молча не действуют:
        # проверено — node_modules/README.md попадал в индекс
        self.excludes = list(excludes or [])

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
        for pattern in self.excludes:
            args += ["--exclude", pattern]
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
            raw_source = str(r.get("source") or r.get("path") or "")
            hits.append(MemoryHit(
                content=str(r.get("content") or r.get("text") or ""),
                source=self._relative(raw_source) if self.vault_root else raw_source,
                heading=str(r.get("heading") or ""),
                score=float(r.get("score") or 0.0),
                chunk_hash=str(r.get("chunk_hash") or r.get("hash") or ""),
                metadata=dict(r),
            ))
        return hits

    async def expand(self, chunk_hash: str) -> dict[str, Any]:
        """Секция целиком по хэшу чанка.

        Ненайденный хэш memsearch отдаёт кодом возврата 1, то есть `_run`
        бросил бы RuntimeError — а вызывающий код по контракту бэкенда ловит
        KeyError и падал бы целиком. Приводим к контракту здесь: это забота
        моста, а не того, кто им пользуется.
        """
        try:
            raw = await self._run(
                "expand", chunk_hash, "--json-output", *self._common(), timeout=120
            )
        except RuntimeError as exc:
            raise KeyError(chunk_hash) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KeyError(chunk_hash) from exc
        if not isinstance(data, dict):
            return {"data": data}
        # Абсолютный путь утёк бы в контекст модели вместе с $HOME
        if self.vault_root and data.get("source"):
            data["source"] = self._relative(str(data["source"]))
        return data

    async def stats(self) -> dict[str, Any]:
        """У `stats` нет `--json-output` — CLI отдаёт текст. Контракт бэкенда
        требует dict, поэтому разбираем «ключ: значение» построчно, а исходный
        текст оставляем в `raw`."""
        text = await self._run("stats", "--collection", self.collection, timeout=60)
        out: dict[str, Any] = {"raw": text.strip(), "backend": "memsearch",
                               "collection": self.collection}
        for line in text.splitlines():
            key, sep, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            if not sep or not key:
                continue
            value = value.strip()
            out[key] = int(value) if value.isdigit() else value
        return out

    def _relative(self, path: str) -> str:
        """Путь относительно корня хранилища: домашний каталог наружу не отдаём."""
        try:
            return str(Path(path).resolve().relative_to(Path(self.vault_root).resolve()))
        except (ValueError, OSError):
            return Path(path).name
