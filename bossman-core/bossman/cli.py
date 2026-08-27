"""CLI-обёртка: запуск из терминала и по расписанию (9.8).

  bossman serve                       — поднять Core
  bossman task "текст" [--agent имя]  — поставить задачу
  bossman project plan <slug> <brief.md>
  bossman project run <slug>
  bossman project state <slug>
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

    args = p.parse_args()
    if args.cmd == "serve":
        from .api import main as serve
        serve()
    elif args.cmd == "task":
        asyncio.run(_task(args))
    elif args.cmd == "project":
        asyncio.run(_project(args))


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
        brief = Path(args.brief).read_text()
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


if __name__ == "__main__":
    main()
