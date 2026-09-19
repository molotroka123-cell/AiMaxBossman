"""Explicit start/setup only. Installed command: python -I -m bcc.telegram_companion."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from .adapters import Core, Models, Telegram, json_request
from .config import CompanionError, Settings, load
from .service import Companion
from .store import Store, single_instance


def default_config() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "Bossman" / "telegram-companion" / "config.json"


def setup(path: Path):
    """Secrets are typed locally, not supplied through argv or chat history."""
    if path.exists():
        raise CompanionError("CONFIG_EXISTS_EDIT_LOCALLY_WITH_BACKUP")
    from bcc.secrets import Vault
    from bcc.auth import _restrict_to_owner
    print("Настройка отдельного Telegram-помощника. Не используйте бот с действующим webhook approvals.")
    token = getpass.getpass("Токен бота (скрыт): ").strip()
    uid = int(input("Ваш числовой Telegram user ID (личный чат): ").strip())
    url = input("Локальная модель URL [http://127.0.0.1:8080/v1]: ").strip() or "http://127.0.0.1:8080/v1"
    model = input("Точное имя уже загруженной локальной модели: ").strip()
    core_token = getpass.getpass("Токен локального Command Center (скрыт, можно оставить пустым): ").strip()
    agent = input("ID отдельного агента для поручений [пусто = выключено]: ").strip()
    data = {"people": [{"user_id": uid, "chat_id": uid, "role": "owner", "agent_id": int(agent) if agent else None}],
            "local_url": url, "local_model": model, "core_url": "http://127.0.0.1:8800",
            "cloud_model": "", "cloud_daily_usd": 0.0, "cloud_request_usd": 0.05,
            "search_url": "", "monitor_seconds": 60}
    from .config import Person
    Settings(**{**data, "people": tuple(Person(**p) for p in data['people'])}, bot_token=token)
    if not token or token.lower().startswith(("replace", "your")):
        raise CompanionError("TELEGRAM_TOKEN_REQUIRED")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    vault = Vault(path.parent)
    secret_path = path.parent / "credentials.enc"
    for p, text in ((secret_path, vault.encrypt(json.dumps({"bot_token": token, "core_token": core_token}))),
                    (path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")):
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
        _restrict_to_owner(p)
    _restrict_to_owner(vault.path)
    print("Настройки сохранены локально. Облако и наблюдение выключены. Запустите диагностику, затем мост.")


async def diagnose(settings: Settings):
    telegram, core = Telegram(settings), Core(settings)
    results = {"scope": "CONNECTIVITY_ONLY_NOT_LIVE_OWNER_ACCEPTANCE"}
    try:
        for label, function in (("telegram", telegram.preflight), ("bossman", core.status)):
            try:
                results[label] = await function()
            except CompanionError as exc:
                results[label] = {"status": str(exc)}
        try:
            body = await json_request(core.client, "GET", settings.local_url + "/models",
                headers={"Authorization": "Bearer " + settings.local_token} if settings.local_token else {}, timeout=5)
            rows = body.get("data") if isinstance(body, dict) else None
            found = isinstance(rows, list) and any(isinstance(r, dict) and r.get('id') == settings.local_model for r in rows)
            results['local_model'] = {"status": "CATALOG_ENTRY_ONLY_NOT_INFERENCE" if found else "MODEL_NOT_LISTED"}
        except CompanionError as exc:
            results['local_model'] = {"status": str(exc)}
        results['cloud'] = {"status": "CONFIGURED_NOT_TESTED" if settings.cloud_daily_usd > 0 else "DISABLED"}
        results['end_to_end'] = "NOT_RUN: send /start, chat, /task, /confirm, /result and test restart on the owner's PC"
        return results
    finally:
        await telegram.close()
        await core.close()


async def serve(path: Path):
    settings = load(path)
    from bcc.auth import _restrict_to_owner
    with single_instance(path.parent):
        store = Store(path.parent)
        _restrict_to_owner(store.path)
        _restrict_to_owner(store.vault.path)
        telegram, core, models = Telegram(settings), Core(settings), Models(settings, path.parent)
        app = Companion(settings, store, telegram, core, models, policy_provider=lambda: load(path))
        try:
            backoff = 1
            while True:
                try:
                    await app.run()
                    return
                except CompanionError as exc:
                    print("TELEGRAM_COMPANION=" + str(exc), flush=True)
                    if str(exc) in {"AUTH_DENIED", "CONFLICT", "TELEGRAM_NOT_CONFIGURED",
                                    "WEBHOOK_CONFLICT_USE_SEPARATE_COMPANION_BOT"}:
                        raise
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
        finally:
            await telegram.close()
            await core.close()
            await models.close()
            store.close()


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Локальный Telegram-помощник Bossman; запуск отдельно от приложения.")
    parser.add_argument("--config", type=Path, default=default_config())
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--setup", action="store_true")
    mode.add_argument("--setup-console", action="store_true")
    mode.add_argument("--diagnose", action="store_true")
    mode.add_argument("--unlock-delegation", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.setup or (not args.config.exists() and not (args.setup_console or args.diagnose or args.unlock_delegation)):
            from .setup_ui import setup_browser
            setup_browser(args.config)
        elif args.setup_console:
            setup(args.config)
        elif args.diagnose:
            report = asyncio.run(diagnose(load(args.config)))
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report.get('telegram', {}).get('status') == "AUTH_AND_POLLING_CONFIG_OK_NOT_E2E" else 2
        elif args.unlock_delegation:
            # Do not change the live DB behind another process's policy state.
            with single_instance(args.config.parent):
                store = Store(args.config.parent)
                try:
                    store.put("delegation_locked", False)
                finally:
                    store.close()
            print("Делегирование разблокировано локально. Права исполнителей не изменены.")
        else:
            asyncio.run(serve(args.config))
        return 0
    except KeyboardInterrupt:
        return 0
    except CompanionError as exc:
        print("TELEGRAM_COMPANION=" + str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print("TELEGRAM_COMPANION=LOCAL_CONFIGURATION_ERROR. Проверьте локальный config.json; не отправляйте секреты в чат.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
