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
import ipaddress
import json
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlsplit

from .providers import OpenAICompatAdapter, ProviderError

# Известные локальные раннеры и их порты по умолчанию.
#
# Ollama слушает 11434 — но только если этот порт свободен. На машине с WSL2
# 11434 часто занимает форвардер svchost: он ПРИНИМАЕТ соединение и молчит, а
# Ollama уходит на 11435. Поэтому альтернативный порт опрашивается всегда.
KNOWN_ENDPOINTS: list[tuple[str, str]] = [
    ("llama.cpp / llama-swap", "http://127.0.0.1:8080/v1"),
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("Ollama (запасной порт)", "http://127.0.0.1:11435/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("vLLM", "http://127.0.0.1:8000/v1"),
    ("LiteLLM", "http://127.0.0.1:4000/v1"),
    ("SGLang", "http://127.0.0.1:30000/v1"),
    ("text-generation-webui", "http://127.0.0.1:5000/v1"),
]

PROBE_TIMEOUT = 2.5     # локальный сервер отвечает мгновенно; дольше — значит его нет
PORT_TIMEOUT = 1.0      # TCP-рукопожатие на localhost укладывается в миллисекунды
RESOLVE_TIMEOUT = 2.0   # DNS для extra_urls: имя, которое не резолвится быстро, не зондируем

# F-017: extra_urls приходят из HTTP-тела и зондируются нашим клиентом — это
# SSRF-поверхность. Назначение discovery — локальные серверы моделей, поэтому
# петля и RFC1918 разрешены, а вот link-local (169.254.x — metadata облаков),
# multicast, unspecified и не-http(s) не зондируются никогда.
ALLOWED_SCHEMES = frozenset({"http", "https"})
BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal", "metadata", "instance-data",
    "instance-data.ec2.internal", "metadata.azure.com",
})
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

# Каталоги для поиска весов; расширяется через env BCC_MODELS_DIRS.
# Разделитель — os.pathsep: ':' на Unix, ';' на Windows. Резать по ':' нельзя,
# иначе путь `C:\Users\...\models` распадётся на `C` и `\Users\...\models`.
DEFAULT_MODEL_DIRS = ["/opt/bossman/models", "/models", "~/models",
                      "~/.cache/lm-studio/models", "~/.ollama/models"]
# Windows-раскладка. Ни `/opt/bossman/models`, ни `/models` там не существуют,
# а `~/.cache/lm-studio` — это Linux-путь LM Studio; на машине разработчика
# скан по DEFAULT_MODEL_DIRS находил ровно ноль моделей и выглядел как «моделей
# нет», хотя Ollama и LM Studio стояли и работали.
WINDOWS_MODEL_DIRS = [r"%USERPROFILE%\.ollama\models",
                      r"%APPDATA%\LM Studio\models",
                      "~/models"]
MODEL_FILE_GLOBS = ["*.gguf", "*.GGUF"]
MODEL_SCAN_DEPTHS = ("", "*/", "*/*/")
MAX_FILES = 200

# Ollama держит веса не файлами `*.gguf`, а блобами по хешу; имена моделей
# лежат отдельно, в манифестах. Скан по маске такое хранилище не видит вообще.
OLLAMA_MODEL_MEDIA = "application/vnd.ollama.image.model"


def default_model_dirs() -> list[str]:
    """Каталоги весов по умолчанию для ТЕКУЩЕЙ ОС.

    Считается при вызове, а не один раз при импорте: так тест может подменить
    `os.name` и проверить вторую платформу, не запуская её.
    """
    if os.name == "nt":
        return list(WINDOWS_MODEL_DIRS)
    return list(DEFAULT_MODEL_DIRS)


def expand_dir(raw: str) -> Path:
    """Раскрыть `%VAR%`/`$VAR` и `~` в пути каталога.

    `%APPDATA%` без expandvars — это несуществующий каталог с процентами в
    имени, а не путь: скан по нему молча ничего не находит.
    """
    return Path(os.path.expandvars(str(raw))).expanduser()


def model_dirs_from_env() -> list[str]:
    """Каталоги весов из окружения либо значения по умолчанию."""
    raw = os.environ.get("BCC_MODELS_DIRS", "")
    if not raw.strip():
        return default_model_dirs()
    return [p.strip() for p in raw.split(os.pathsep) if p.strip()]


def _address_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Почему адрес нельзя зондировать (None — можно)."""
    if ip.version == 6:
        # IPv4, завёрнутый в IPv6 (::ffff:169.254.169.254, NAT64, 6to4, Teredo),
        # проверяется как IPv4 — иначе metadata-адрес проходит «в обёртке».
        inner = ip.ipv4_mapped
        if inner is None and ip in _NAT64:
            inner = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if inner is None and ip.sixtofour is not None:
            inner = ip.sixtofour
        if inner is None and ip.teredo is not None:
            inner = ip.teredo[1]
        if inner is not None:
            ip = inner
    if ip.is_unspecified:
        return "unspecified-адрес"
    if ip.is_link_local:
        return "link-local (metadata-диапазон)"
    if ip.is_multicast:
        return "multicast"
    if ip.is_loopback:
        return None               # петля — назначение discovery; проверяется ДО
                                  # is_reserved: у IPv6 ::1 лежит в «::/8»
    if ip.is_reserved:
        return "зарезервированный диапазон"
    return None


async def _reject_reason(url: str) -> str | None:
    """F-017: причина, по которой URL из extra_urls НЕ зондируется (None — можно).

    Проверяются схема, userinfo, известные metadata-имена и КАЖДЫЙ адрес, в
    который резолвится хост: имя, ведущее в 169.254.x.x, — тот же SSRF.
    Ожидание DNS ограничено RESOLVE_TIMEOUT; нерезолвящееся имя отклоняется
    (fail-closed: нельзя доказать, что адрес не запрещён).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "некорректный URL"
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return f"схема {scheme or '(нет)'!r} — допустимы только http и https"
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        return "userinfo (логин:пароль@) в адресе не допускается"
    try:
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "некорректный порт"
    if not host:
        return "в адресе нет хоста"
    host = host.lower().rstrip(".")
    if host in BLOCKED_HOSTNAMES:
        return f"{host} — служебный metadata-хост"
    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address]
    try:
        addrs = [ipaddress.ip_address(host.split("%", 1)[0])]
    except ValueError:
        if host == "localhost" or host.endswith(".localhost"):
            addrs = [ipaddress.IPv4Address("127.0.0.1")]      # RFC 6761, без DNS
        else:
            try:
                infos = await asyncio.wait_for(
                    asyncio.to_thread(socket.getaddrinfo, host, port or 80,
                                      type=socket.SOCK_STREAM),
                    timeout=RESOLVE_TIMEOUT)
            except (asyncio.TimeoutError, OSError, UnicodeError):
                return f"имя {host} не разрешилось в адрес"
            addrs = []
            for info in infos:
                try:
                    addrs.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
                except (ValueError, IndexError, TypeError):
                    return f"имя {host} разрешилось в непонятный адрес"
            if not addrs:
                return f"имя {host} не разрешилось в адрес"
    for ip in addrs:
        why = _address_reason(ip)
        if why:
            return f"адрес {ip} — {why}"
    return None


async def _port_state(base_url: str) -> bool | None:
    """Открыт ли TCP-порт: True — принимает, False — закрыт, None — не выяснено."""
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    if not host:
        return None
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port),
                                           timeout=PORT_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        return None          # фильтр или очень медленная сеть — утверждать нечего
    except OSError:
        return False         # в том числе ConnectionRefusedError: порт свободен
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def _diagnose(base_url: str, detail: str, timed_out: bool) -> str:
    """Отличить «сервера нет» от «порт занят чужим процессом».

    Раньше оба случая давали один и тот же текст «не ответил за 2.5 с», и это
    уводило от причины: на боевой машине владельца 11434 держал форвардер WSL2,
    который принимает соединение и молчит, а живая Ollama слушала 11435.
    """
    state = await _port_state(base_url)
    port = urlparse(base_url).port
    if state is False:
        return f"сервер не запущен: порт {port} закрыт"
    if state is True and timed_out:
        return (f"порт {port} занят другим процессом: соединение принято, но ответа "
                f"как от OpenAI-совместимого API нет за {PROBE_TIMEOUT} с — "
                f"проверьте, кто слушает порт, и запустите модель на свободном")
    if state is True:
        return f"на порту {port} кто-то есть, но /models вернул: {detail}"
    if not timed_out and detail.startswith("нет связи"):
        # HTTP-клиент сам не смог установить TCP-соединение (ConnectError), но
        # сырой сокет-проб не смог это подтвердить: на машинах, где фильтр
        # роняет SYN даже на loopback, закрытый порт неотличим от медленной
        # сети. Честный ответ — «нет связи», без ложного «не запущен».
        return (f"нет связи с портом {port}: соединение на уровне TCP не "
                f"установилось (порт закрыт или отфильтрован)")
    return detail


async def _probe(label: str, base_url: str, transport: Any = None) -> dict:
    adapter = OpenAICompatAdapter(base_url=base_url, transport=transport)
    t0 = time.perf_counter()
    try:
        models = await asyncio.wait_for(adapter.list_models(), timeout=PROBE_TIMEOUT)
        return {"label": label, "base_url": base_url, "ok": True,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "models": models[:50]}
    except (ProviderError, asyncio.TimeoutError) as exc:
        timed_out = isinstance(exc, asyncio.TimeoutError)
        detail = f"не ответил за {PROBE_TIMEOUT} с" if timed_out else str(exc)
        if transport is None:      # с MockTransport настоящего сокета нет
            detail = await _diagnose(base_url, detail, timed_out)
        return {"label": label, "base_url": base_url, "ok": False,
                "detail": detail, "models": []}


def _scan_ollama(base: Path) -> list[dict]:
    """Модели Ollama из манифестов: `blobs/` + `manifests/` вместо `*.gguf`."""
    manifests = base / "manifests"
    if not manifests.is_dir():
        return []
    found: list[dict] = []
    for path in sorted(manifests.rglob("*")):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        layers = data.get("layers")
        if not isinstance(layers, list):
            continue
        size = next((int(l.get("size") or 0) for l in layers
                     if isinstance(l, dict) and l.get("mediaType") == OLLAMA_MODEL_MEDIA), 0)
        if not size:
            continue
        # .../manifests/registry.ollama.ai/library/qwen2.5/7b -> qwen2.5:7b
        parts = path.relative_to(manifests).parts
        name = f"{parts[-2]}:{parts[-1]}" if len(parts) >= 2 else path.name
        found.append({"path": str(path), "name": name, "runner": "ollama",
                      "size_gb": round(size / 1e9, 2)})
        if len(found) >= MAX_FILES:
            break
    return found


def _dedup_key(path: str) -> str:
    """Ключ «это тот же самый файл».

    normcase важен не для красоты: на регистронезависимой ФС (NTFS, APFS)
    маски `*.gguf` и `*.GGUF` возвращают ОДИН и тот же файл, и модель попадала
    в список дважды — с одинаковым размером, как будто весов на диске вдвое
    больше. Один и тот же каталог, указанный в BCC_MODELS_DIRS дважды (или
    через `~` и абсолютным путём), давал тот же эффект.
    """
    return os.path.normcase(os.path.abspath(path))


def _scan_files(dirs: list[str] | None = None) -> list[dict]:
    """Файлы весов на диске: путь и размер. Не рекурсивно глубже 3 уровней."""
    roots = dirs if dirs is not None else model_dirs_from_env()
    found: list[dict] = []
    seen: set[str] = set()

    def add(entry: dict) -> None:
        key = _dedup_key(entry["path"])
        if key in seen:
            return
        seen.add(key)
        found.append(entry)

    for root in roots:
        base = expand_dir(root)
        if not base.is_dir():
            continue
        if (base / "manifests").is_dir() and (base / "blobs").is_dir():
            for entry in _scan_ollama(base):
                add(entry)
            if len(found) >= MAX_FILES:
                return sorted(found, key=lambda x: x["path"])[:MAX_FILES]
        for pattern in MODEL_FILE_GLOBS:
            for depth in MODEL_SCAN_DEPTHS:
                try:
                    for f in base.glob(depth + pattern):
                        if not f.is_file():
                            continue
                        add({"path": str(f),
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
    rejected: list[dict] = []
    for url in extra_urls or []:
        url = str(url).strip().rstrip("/")
        if not url or url in [u for _, u in targets] or url in {r["base_url"] for r in rejected}:
            continue
        why = await _reject_reason(url)
        if why is not None:
            # F-017: отказ виден в ответе честно (rejected=True), но в сеть не уходит
            rejected.append({"label": "указан вручную", "base_url": url, "ok": False,
                             "rejected": True, "detail": f"адрес отклонён: {why}",
                             "models": []})
            continue
        targets.append(("указан вручную", url))

    results = list(await asyncio.gather(
        *(_probe(label, url, transport) for label, url in targets))) + rejected

    registered = {(p.get("base_url") or "").rstrip("/") for p in known_providers or []}
    for r in results:
        r["registered"] = r["base_url"].rstrip("/") in registered

    files = await asyncio.to_thread(_scan_files, model_dirs)
    return {
        "endpoints": sorted(results, key=lambda r: (not r["ok"], r["label"])),
        "files": files,
        "online": sum(1 for r in results if r["ok"]),
        "scanned_dirs": model_dirs if model_dirs is not None else model_dirs_from_env(),
    }
