"""``bossman pit setup|status|doctor|start|stop`` — owner launch surface.

The owner launch contract (docs/v1.7): PIT starts from the existing ``bossman``
CLI, secrets stay out of argv/logs/config, diagnostics are secret-free and
fail closed.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from pathlib import Path

from bcc.telegram_companion.config import CompanionError, Person
from bcc.telegram_companion.store import single_instance

from .config import (
    DEFAULT_FREE_CHAT_MODELS,
    config_path,
    credentials_path,
    default_data_dir,
    load,
    looks_like_repo,
    pit_home,
    save_setup,
)
from .photo_runtime import build_photo_services, photo_runtime_status
from .runtime import STOP_FLAG, ParticipantRuntime, StopRequested

COMMANDS = ("setup", "status", "doctor", "start", "stop")


def _resolve_path(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(prog="bossman pit")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("command", choices=COMMANDS)
    ns, _ = parser.parse_known_args(argv)
    data_dir = Path(ns.data_dir) if ns.data_dir else default_data_dir()
    return config_path(data_dir)


def _resolve_data_dir(path: Path) -> Path:
    """config.json sits at <data_dir>/pit-v1.7/config.json."""
    return path.parent.parent


# -- setup ----------------------------------------------------------------------
def cmd_setup(path: Path) -> int:
    """Local secure setup. Tokens are typed, never passed through argv."""
    if path.exists():
        raise CompanionError("CONFIG_EXISTS_EDIT_LOCALLY_WITH_BACKUP")
    print("Настройка PIT-режима Jeff (Bossman 1.7). Секреты не попадают в config.json.")
    token = getpass.getpass("Токен Telegram-бота PIT (скрыт): ").strip()
    if not token:
        raise CompanionError("TELEGRAM_TOKEN_REQUIRED")
    owner = input("Числовой Telegram ID участника (allowlist): ").strip()
    if not owner.isdigit():
        raise CompanionError("TELEGRAM_ID_REQUIRED")
    print(f"Бесплатные модели через запятую [{', '.join(DEFAULT_FREE_CHAT_MODELS)}]:")
    raw_models = input("Модели (Enter = по умолчанию): ").strip()
    models = [model.strip() for model in raw_models.split(",") if model.strip()] \
        or list(DEFAULT_FREE_CHAT_MODELS)
    provider_key = getpass.getpass("Ключ провайдера (скрыт, OpenRouter): ").strip()
    core_token = getpass.getpass("Токен локального Command Center (скрыт, можно пусто): ").strip()
    search_url = input("SearXNG URL [пусто = keyless-фолбэк]: ").strip()
    save_setup(
        path,
        people=[Person(user_id=int(owner), chat_id=int(owner), role="owner")],
        chat_models=models,
        provider_base_url="https://openrouter.ai/api/v1",
        core_url="http://127.0.0.1:8800",
        search_url=search_url,
        allowlist_open=True,
        bot_token=token,
        provider_key=provider_key,
        core_token=core_token,
    )
    print(f"Готово. Конфигурация: {path}")
    print("Проверка: bossman pit doctor. Запуск: bossman pit start.")
    return 0


# -- doctor -----------------------------------------------------------------------
async def _doctor_checks(path: Path) -> tuple[list[dict], bool]:
    from bcc.providers import build_adapter
    from bcc.telegram_companion.adapters import Telegram

    checks: list[dict] = []
    data_dir = _resolve_data_dir(path)
    home = pit_home(data_dir)

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail[:200]})

    add("data_root_outside_repo", not looks_like_repo(home),
        "runtime data must never live inside a Git checkout")
    if not path.is_file() or not credentials_path(data_dir).is_file():
        add("config", False, "config/credentials отсутствуют; запусти bossman pit setup")
        return checks, False
    try:
        settings = load(path)
        add("config", True, f"participants={len(settings.people)}, models={len(settings.chat_models)}")
    except (ValueError, TypeError, CompanionError, OSError) as exc:
        add("config", False, str(exc))
        return checks, False

    add("allowlist", len(settings.people) >= 1)
    add("bot_token_present", bool(settings.bot_token))

    transport_ok = False
    try:
        from .runtime import _transport_settings
        transport = Telegram(_transport_settings(settings))
        try:
            await transport.preflight()
            transport_ok = True
        finally:
            import contextlib
            with contextlib.suppress(Exception):
                await transport.close()
    except CompanionError as exc:
        add("telegram_auth", False, str(exc))
    if transport_ok:
        add("telegram_auth", True)

    route_ok = False
    try:
        adapter = build_adapter("openai_compat", settings.provider_base_url,
                                api_key=settings.provider_key or None)
        probe = ParticipantRuntime.__new__(ParticipantRuntime)
        probe.settings = settings
        probe.home = pit_home(data_dir)
        probe.adapter = adapter
        probe.local_adapter = build_adapter("openai_compat", settings.local_url) \
            if settings.local_url else None
        # The catalog probe follows the same resource-arbitration contract as
        # the live runtime.  Keep the diagnostic object structurally aligned
        # with ParticipantRuntime.__init__ so doctor cannot report a false
        # integration failure before it reaches the provider catalog.
        from .resources import LocalCapacityGuard
        probe.capacity_guard = LocalCapacityGuard()
        probe.catalog = {}
        probe.catalog_checked_at = 0.0
        endpoints = await ParticipantRuntime.refresh_catalog(probe)
        local_ok = any(endpoint.local for endpoint in endpoints.values())
        rows = [{"model": model, "status": "ZERO_COST_OK" if model in endpoints else "NOT_ELIGIBLE"}
                for model in settings.chat_models]
        route_ok = bool(endpoints) or bool(settings.local_models)
        detail = json.dumps(rows, ensure_ascii=False)
        if settings.local_models:
            detail += f"; local={'OK' if local_ok else 'DOWN'}"
        add("free_route", route_ok, detail)
    except Exception as exc:  # noqa: BLE001 — doctor must not crash on a broken provider
        add("free_route", False, str(exc))

    if settings.search_url:
        add("web", await _probe_web(settings), "searxng configured")
    else:
        add("web", await _probe_web(settings), "keyless fallback")

    media = photo_runtime_status(build_photo_services(core_token="").config)
    # Photo ANALYSIS is the live 1.7 product surface: when AI Max media is
    # enabled it must have a vision route. Photo EDIT is owner-deferred until
    # a local Qwen image model is registered in Bossman Studio (AI Max phase);
    # its absence is an honest capability gap, not a broken runtime, so it is
    # reported in the detail without failing the doctor.
    media_ok = True
    if media["ai_max_media_ready"] and not media["vision_configured"]:
        media_ok = False
    add("ai_max_media", media_ok, json.dumps(media, ensure_ascii=False))

    add("participant_tool_perimeter", _tool_perimeter(), "location/device/computer denied")
    ok = all(item["status"] == "PASS" for item in checks)
    return checks, ok


def with_suppressed_close(client) -> None:
    import contextlib
    with contextlib.suppress(Exception):
        close = getattr(client, "close", None)
        if close is not None:
            close()


async def _probe_web(settings) -> bool:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            if settings.search_url:
                response = await client.get(settings.search_url.rstrip("/") + "/search",
                                            params={"q": "ping", "format": "json"})
            else:
                response = await client.get("https://html.duckduckgo.com/html/",
                                            params={"q": "ping"})
            return 200 <= response.status_code < 300
    except Exception:
        return False


def _tool_perimeter() -> bool:
    from .policy import TelegramToolPolicy
    policy = TelegramToolPolicy()
    denied = ("geo.current", "geolocation.read", "location.current", "device.info",
              "computer.control", "shell.exec", "admin.console")
    return all(not policy.allows(name) for name in denied)


def cmd_doctor(path: Path) -> int:
    checks, ok = asyncio.run(_doctor_checks(path))
    print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 2


# -- status --------------------------------------------------------------------------
def cmd_status(path: Path) -> int:
    data_dir = _resolve_data_dir(path)
    home = pit_home(data_dir)
    report: dict = {"surface": "bossman-pit", "secret_free": True,
                    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    report["config_present"] = path.is_file()
    report["process"] = "RUNNING" if _is_running(home) else "STOPPED"
    if path.is_file():
        try:
            settings = load(path)
            personalities = home / "personalities"
            participants = len(list(personalities.iterdir())) if personalities.is_dir() else 0
            facts = 0
            if personalities.is_dir():
                for person_dir in personalities.iterdir():
                    marker = person_dir / "facts.jsonl"
                    if marker.is_file():
                        facts += sum(1 for line in marker.read_text(encoding="utf-8").splitlines()
                                     if line.strip())
            report["allowlist"] = len(settings.people)
            report["chat_models"] = len(settings.chat_models)
            report["pit_storage"] = {"participants": participants, "facts": facts, "root": str(home)}
            report["web"] = "SEARXNG" if settings.search_url else "KEYLESS_FALLBACK"
            report["local_models"] = list(settings.local_models) or "NONE"
            report["route_stats"] = _route_stats(home)
            transport_error = _store_state(home, "transport_error")
            if transport_error:
                report["last_transport_error"] = transport_error
        except (ValueError, TypeError, CompanionError, OSError) as exc:
            report["config_error"] = str(exc)[:200]
    report["media"] = photo_runtime_status(build_photo_services(core_token="").config)
    report["queue"] = {"pending": _queue_pending(home)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["config_present"] else 2


def _is_running(home: Path) -> bool:
    """Probe the kernel-owned poller lock without disturbing a live process."""
    lock_path = home / "poller.lock"
    if not lock_path.exists():
        return False
    try:
        file = lock_path.open("a+b")
    except OSError:
        return False
    try:
        file.seek(0)                     # lock byte 0, where the runtime holds it
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        import fcntl
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(file, fcntl.LOCK_UN)
            return False
        except OSError:
            return True
    except OSError:
        return True
    finally:
        try:
            file.close()
        except OSError:
            pass


def _route_stats(home: Path) -> dict:
    """Secret-free aggregates of the internal route log (no message content)."""
    log_path = home / "logs" / "route_log.jsonl"
    if not log_path.is_file():
        return {}
    rows = []
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines()[-500:]:
            if line.strip():
                rows.append(json.loads(line))
    except (OSError, ValueError):
        return {}
    if not rows:
        return {}
    per_model: dict[str, dict] = {}
    latencies = []
    for row in rows:
        entry = per_model.setdefault(row.get("model", "?"),
                                     {"ok": 0, "fail": 0, "latency_ms": []})
        if row.get("ok"):
            entry["ok"] += 1
            latency = row.get("latency_ms")
            if type(latency) is int and latency >= 0:
                entry["latency_ms"].append(latency)
                latencies.append(latency)
        else:
            entry["fail"] += 1
    summary = {}
    for model, entry in per_model.items():
        lat = sorted(entry["latency_ms"])
        summary[model] = {
            "ok": entry["ok"], "fail": entry["fail"],
            "p50_ms": lat[len(lat) // 2] if lat else None,
            "p95_ms": lat[int(len(lat) * 0.95)] if lat else None,
        }
    return {"turns": len(rows), "by_model": summary}


def _queue_pending(home: Path) -> int:
    import sqlite3
    db_path = home / "companion.sqlite3"
    if not db_path.is_file():
        return 0
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        count = db.execute("SELECT count(*) FROM inbox WHERE phase IN ('pending','processing')").fetchone()[0]
        db.close()
        return int(count)
    except (sqlite3.Error, OSError):
        return -1


def _store_state(home: Path, key: str):
    import sqlite3
    db_path = home / "companion.sqlite3"
    if not db_path.is_file():
        return None
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        db.close()
        if not row:
            return None
        from bcc.secrets import Vault
        value = Vault(home).decrypt(row[0])
        return json.loads(value) if value else None
    except Exception:
        return None


# -- start / stop ------------------------------------------------------------------------
def _keep_system_awake():
    """Windows: hold an awake state while the participant bot is alive.

    The owner runs Jeff on a laptop that also sleeps on idle; a Telegram bot
    that dies every time the machine naps is not a product. While this context
    is held the system cannot sleep (the display still may). It is released on
    any exit path, so stop/restart keeps working as before.
    """
    import contextlib

    if os.name != "nt":
        return contextlib.nullcontext()

    import ctypes

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    class _Awake:
        def __enter__(self):
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED)

        def __exit__(self, *exc):
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            return False

    return _Awake()


def cmd_start(path: Path) -> int:
    from bcc.telegram_companion.store import single_instance
    settings = load(path)
    home = pit_home(_resolve_data_dir(path))
    try:
        lock = single_instance(home)
    except CompanionError as exc:
        print(f"bossman pit: {exc}", file=sys.stderr)
        return 3
    (home / STOP_FLAG).unlink(missing_ok=True)
    with lock, _keep_system_awake():
        runtime = ParticipantRuntime(settings)
        try:
            asyncio.run(runtime.run())
            return 0
        except KeyboardInterrupt:
            return 0
        except StopRequested:
            return 0
        except CompanionError as exc:
            print(f"bossman pit: {exc}", file=sys.stderr)
            return 2
        finally:
            (home / STOP_FLAG).unlink(missing_ok=True)
            import contextlib
            with contextlib.suppress(Exception):
                asyncio.run(runtime.close())


def cmd_stop(path: Path) -> int:
    home = pit_home(_resolve_data_dir(path))
    if _is_running(home):
        (home / STOP_FLAG).write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                      encoding="utf-8")
        print("Сигнал остановки записан; PIT завершит цикл опроса в течение ~25 секунд.")
        return 0
    (home / STOP_FLAG).unlink(missing_ok=True)
    print("PIT сейчас не запущен.")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "pit":
        argv = argv[1:]
    if not argv:
        argv = ["status"]
    command = argv[0]
    path = _resolve_path(argv)
    if command == "setup":
        return cmd_setup(path)
    if command == "doctor":
        return cmd_doctor(path)
    if command == "status":
        return cmd_status(path)
    if command == "start":
        return cmd_start(path)
    if command == "stop":
        return cmd_stop(path)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
