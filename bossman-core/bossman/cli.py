"""CLI-обёртка: запуск из терминала и по расписанию (9.8).

  bossman serve                       — поднять Core
  bossman task "текст" [--agent имя]  — поставить задачу
  bossman project plan <slug> <brief.md>
  bossman project run <slug>
  bossman project state <slug>
  bossman models list --provider <имя> | --all [--json]  — каталог провайдера
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(prog="bossman")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve")

    pt = sub.add_parser("task")
    pt.add_argument("text")
    pt.add_argument("--agent")
    pt.add_argument("--source", default="cli")

    pp = sub.add_parser("project")
    pp.add_argument("action", choices=["plan", "run", "state"])
    pp.add_argument("slug")
    pp.add_argument("brief", nargs="?")

    pm = sub.add_parser("models")
    pm.add_argument("action", choices=["list"])
    # Перечень провайдеров берётся из реестра шлюза, а не переписывается
    # здесь второй раз: два списка расходятся, и расходятся молча.
    from .gateway.config import AVAILABLE_PROVIDERS
    pm.add_argument("--provider", choices=sorted(AVAILABLE_PROVIDERS))
    pm.add_argument("--all", action="store_true", dest="list_all",
                    help="опросить всех провайдеров, у которых есть ключ")
    pm.add_argument("--json", action="store_true", dest="as_json")

    args = p.parse_args()
    if args.cmd == "serve":
        from .api import main as serve
        serve()
    elif args.cmd == "task":
        asyncio.run(_task(args))
    elif args.cmd == "project":
        asyncio.run(_project(args))
    elif args.cmd == "models":
        sys.exit(asyncio.run(_models(args)))


async def _task(args) -> None:
    from . import db, runner
    row = await db.fetchrow(
        "INSERT INTO tasks (agent, source, text) VALUES ($1,$2,$3) RETURNING id",
        args.agent, args.source, args.text)
    await runner.enqueue(row["id"])
    print(f"задача #{row['id']} поставлена")
    await db.close()


async def _project(args) -> None:
    from . import db
    if args.action == "plan":
        if not args.brief:
            sys.exit("нужен путь к brief.md")
        from .projects.planner import plan_project
        # brief.md владельца — utf-8; без явной кодировки Windows читает его
        # как cp1251 и в модель уезжает кракозябра вместо задания.
        brief = Path(args.brief).read_text(encoding="utf-8")
        await db.execute(
            """INSERT INTO projects (slug, title, brief) VALUES ($1,$1,$2)
               ON CONFLICT (slug) DO UPDATE SET brief=excluded.brief, updated_at=now()""",
            args.slug, brief)
        info = await plan_project(args.slug, brief)
        print(f"план готов: {info} — утвердить: POST /projects/{args.slug}/approve")
    elif args.action == "run":
        from .projects.runner import run_project
        await run_project(args.slug)
    elif args.action == "state":
        from .projects.plan import State
        print(json.dumps(State(args.slug).data, ensure_ascii=False, indent=1))
    await db.close()


async def _models(args) -> int:
    """Каталог провайдера в терминал. Нет ключа — не падение, а внятный отказ.

    Провайдер импортируется здесь, а не в начале модуля: `bossman task` не
    обязан тянуть за собой ни gateway, ни httpx-клиента облака.

    `--all` опрашивает только тех, у кого ключ задан. Показывать «ошибку» у
    каждого ненастроенного провайдера значило бы утопить настоящий отказ в
    восьми ожидаемых.
    """
    import json as _json

    from .gateway.backends import build_backend
    from .gateway.config import (AVAILABLE_PROVIDERS, load_env_file,
                                 load_provider_config)
    load_env_file()                       # ключ владельца лежит в .env

    # `getattr` со значением по умолчанию, а не `args.list_all`: эту функцию
    # зовут не только из разбора аргументов, и вызывающий, знающий про один
    # провайдер, не обязан знать про флаги, появившиеся позже.
    list_all = bool(getattr(args, "list_all", False))
    as_json = bool(getattr(args, "as_json", False))
    provider = getattr(args, "provider", None)
    if not provider and not list_all:
        print("укажите --provider <имя> или --all", file=sys.stderr)
        return 2
    names = sorted(AVAILABLE_PROVIDERS) if list_all else [provider]

    async def catalogue(name: str) -> dict:
        config = load_provider_config(name)
        backend = build_backend(config)
        try:
            listing = await backend.list_models()
        finally:
            await backend.close()
        return {"provider": name, "status": listing.status,
                "reason": listing.reason, "models": listing.models,
                "key_env": config.api_key_env}

    if list_all:
        # Ненастроенный провайдер отсеивается ДО сети: спрашивать облако,
        # ключа к которому нет, — это ожидание таймаута ради заранее
        # известного ответа.
        names = [n for n in names
                 if (cfg := load_provider_config(n)).api_key_env is None
                 or cfg.resolved_api_key()]
        if not names:
            print("ни у одного провайдера нет ключа; задайте их в "
                  "bossman-core/.env", file=sys.stderr)
            return 1

    results = await asyncio.gather(*(catalogue(name) for name in names))

    if as_json:
        print(_json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if all(r["status"] == "ok" for r in results) else 1

    failed = False
    for row in results:
        if row["status"] != "ok":
            failed = True
            print(f"{row['provider']}: {row['reason']}", file=sys.stderr)
            if row["status"] == "unavailable" and row["key_env"]:
                print(f"задайте {row['key_env']} в bossman-core/.env",
                      file=sys.stderr)
            continue
        if len(results) > 1:
            print(f"# {row['provider']} ({len(row['models'])})")
        for model_id in row["models"]:
            print(model_id)
    return 1 if failed else 0


if __name__ == "__main__":
    main()
