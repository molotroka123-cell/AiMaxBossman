"""Обнаружение локальных моделей (раздел 6 ТЗ: «discover running endpoints»).

Две части:
1. Опрос известных локальных портов OpenAI-совместимых серверов — что запущено
   прямо сейчас и какие модели отдаёт /models.
2. Скан диска на файлы моделей (*.gguf) в типичных каталогах — что установлено,
   даже если сервер не запущен.

Только чтение: ничего не запускает и не скачивает.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from .providers import OpenAICompatAdapter, ProviderError

# Известные локальные раннеры и их порты по умолчанию.
KNOWN_ENDPOINTS: list[tuple[str, str]] = [
    ("llama.cpp / llama-swap", "http://127.0.0.1:8080/v1"),
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("vLLM", "http://127.0.0.1:8000/v1"),
    ("LiteLLM", "http://127.0.0.1:4000/v1"),
    ("SGLang", "http://127.0.0.1:30000/v1"),
    ("text-generation-webui", "http://127.0.0.1:5000/v1"),
]

PROBE_TIMEOUT = 2.5     # локальный сервер отвечает мгновенно; дольше — значит его нет

# Каталоги для поиска весов; расширяется через env BCC_MODELS_DIRS (пути через :)
DEFAULT_MODEL_DIRS = ["/opt/bossman/models", "/models", "~/models", "~/.cache/lm-studio/models"]
MODEL_FILE_GLOBS = ["*.gguf", "*.GGUF"]
MAX_FILES = 200


async def _probe(label: str, base_url: str, transport: Any = None) -> dict:
    adapter = OpenAICompatAdapter(base_url=base_url, transport=transport)
    t0 = time.perf_counter()
    try:
        models = await asyncio.wait_for(adapter.list_models(), timeout=PROBE_TIMEOUT)
        return {"label": label, "base_url": base_url, "ok": True,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "models": models[:50]}
    except (ProviderError, asyncio.TimeoutError) as exc:
        return {"label": label, "base_url": base_url, "ok": False,
                "detail": str(exc) if not isinstance(exc, asyncio.TimeoutError)
                else f"не ответил за {PROBE_TIMEOUT} с", "models": []}


def _scan_files(dirs: list[str] | None = None) -> list[dict]:
    """Файлы весов на диске: путь и размер. Не рекурсивно глубже 3 уровней."""
    roots = dirs if dirs is not None else (
        os.environ.get("BCC_MODELS_DIRS", "").split(":") if os.environ.get("BCC_MODELS_DIRS")
        else DEFAULT_MODEL_DIRS)
    found: list[dict] = []
    for root in roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for pattern in MODEL_FILE_GLOBS:
            for depth in ("", "*/", "*/*/"):
                try:
                    for f in base.glob(depth + pattern):
                        if f.is_file():
                            found.append({"path": str(f),
                                          "size_gb": round(f.stat().st_size / 1e9, 2)})
                            if len(found) >= MAX_FILES:
                                return sorted(found, key=lambda x: x["path"])
                except OSError:
                    continue
    return sorted(found, key=lambda x: x["path"])


async def discover(extra_urls: list[str] | None = None,
                   known_providers: list[dict] | None = None,
                   endpoints: list[tuple[str, str]] | None = None,
                   model_dirs: list[str] | None = None,
                   transport: Any = None) -> dict:
    """Полный проход: параллельный опрос endpoint'ов + скан диска.

    known_providers — уже зарегистрированные провайдеры: их base_url помечаются,
    чтобы UI не предлагал добавить дубль.
    """
    targets = list(endpoints if endpoints is not None else KNOWN_ENDPOINTS)
    for url in extra_urls or []:
        url = url.strip().rstrip("/")
        if url and url not in [u for _, u in targets]:
            targets.append(("указан вручную", url))

    results = await asyncio.gather(*(_probe(label, url, transport) for label, url in targets))

    registered = {(p.get("base_url") or "").rstrip("/") for p in known_providers or []}
    for r in results:
        r["registered"] = r["base_url"].rstrip("/") in registered

    files = await asyncio.to_thread(_scan_files, model_dirs)
    return {
        "endpoints": sorted(results, key=lambda r: (not r["ok"], r["label"])),
        "files": files,
        "online": sum(1 for r in results if r["ok"]),
        "scanned_dirs": model_dirs if model_dirs is not None else (
            os.environ.get("BCC_MODELS_DIRS", "").split(":") if os.environ.get("BCC_MODELS_DIRS")
            else DEFAULT_MODEL_DIRS),
    }
