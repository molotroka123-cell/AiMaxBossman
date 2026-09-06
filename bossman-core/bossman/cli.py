"""CLI-обёртка: запуск из терминала и по расписанию (9.8).

  bossman serve                       — поднять Core
  bossman task "текст" [--agent имя]  — поставить задачу
  bossman project plan <slug> <brief.md>
  bossman project run <slug>
  bossman project state <slug>
  bossman models list --provider openrouter|zai   — каталог облачного провайдера
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
    pm.add_argument("--provider", required=True, choices=["openrouter", "zai"])

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
    """
    from .gateway.backends import build_backend
    from .gateway.config import (OPENROUTER_KEY_ENV, ZAI_KEY_ENV, load_env_file,
                                 openrouter_backend_config, zai_backend_config)
    load_env_file()                       # ключ владельца лежит в .env
    factory, key_env = ({"openrouter": (openrouter_backend_config, OPENROUTER_KEY_ENV),
                         "zai": (zai_backend_config, ZAI_KEY_ENV)})[args.provider]
    backend = build_backend(factory())
    try:
        listing = await backend.list_models()
    finally:
        await backend.close()
    if not listing.ok:
        print(f"{args.provider}: {listing.reason}", file=sys.stderr)
        if listing.status == "unavailable":
            print(f"задайте {key_env} в bossman-core/.env", file=sys.stderr)
        return 1
    for model_id in listing.models:
        print(model_id)
    return 0


if __name__ == "__main__":
    main()
