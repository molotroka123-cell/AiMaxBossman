"""CLI: python tools/doc_intake <command> ...  (see README_RU.md)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import (DEFAULT_BACKEND, DEFAULT_MAX_BYTES, ENV_BACKEND, REPO_ROOT, default_data_dir, default_inbox,
                     default_library_root, lower_priority)
from .fsutil import LockBusy
from .library import ADDED, DUPLICATE, REFUSED, Library, LibraryError, Outcome, Settings
from .memory import MemorySink
from .rules import RulesError, load_rules


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="doc_intake", description="Приём документов → библиотека знаний → память Bossman")
    ap.add_argument("--data-dir", help="папка данных Bossman (по умолчанию BCC_DATA_DIR или %%LOCALAPPDATA%%\\Bossman\\CommandCenter)")
    ap.add_argument("--library-root", help="корень библиотеки (по умолчанию <data dir>\\library или BOSSMAN_LIBRARY_ROOT)")
    ap.add_argument("--vault", help="vault памяти (по умолчанию — тот, что отдаёт /api/memory/config)")
    ap.add_argument("--write-folder", help="папка записи внутри vault (по умолчанию из /api/memory/config)")
    ap.add_argument("--backend", default=os.environ.get(ENV_BACKEND, DEFAULT_BACKEND), help="адрес backend (только loopback)")
    ap.add_argument("--no-memory", action="store_true", help="не писать в память (сухой прогон: только библиотека)")
    ap.add_argument("--max-mb", type=float, default=DEFAULT_MAX_BYTES / 1048576, help="лимит размера файла, МБ (200)")
    ap.add_argument("--max-pages", type=int, default=2000, help="сколько страниц PDF разбирать максимум")
    ap.add_argument("--note-chars", type=int, default=20000, help="максимум знаков в одной заметке")
    ap.add_argument("--docling", action="store_true", help="PDF через Docling (если установлен): таблицы в Markdown")
    ap.add_argument("--ocr", action="store_true", help="OCR изображений (если установлен RapidOCR)")
    ap.add_argument("--json", action="store_true", help="машинный вывод JSON")
    ap.add_argument("--notify", action="store_true",
                    help="короткое уведомление владельцу в пульт (имя файла, категория, статус; без содержимого)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("intake", help="принять файл(ы) сразу")
    p.add_argument("files", nargs="+")
    p.add_argument("--tags", default="", help="метки через запятую")
    p.add_argument("--low-priority", action="store_true", help="понизить приоритет процесса")

    p = sub.add_parser("watch", help="следить за папкой приёма")
    p.add_argument("--inbox", help="папка приёма (по умолчанию <data dir>\\inbox-docs или BOSSMAN_DOC_INBOX)")
    p.add_argument("--interval", type=float, default=15.0)
    p.add_argument("--settle", type=float, default=5.0, help="секунд без изменений, прежде чем брать файл")
    p.add_argument("--once", action="store_true", help="один проход и выход (для планировщика)")

    p = sub.add_parser("list", help="список библиотеки")
    p.add_argument("--category")
    p = sub.add_parser("show", help="карточка документа")
    p.add_argument("id")
    p = sub.add_parser("search", help="поиск по библиотеке (метаданные + текст)")
    p.add_argument("text", nargs="+")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("retag", help="изменить метки")
    p.add_argument("id")
    p.add_argument("--add", default="")
    p.add_argument("--remove", default="")
    p.add_argument("--set", dest="set_to", default=None)
    p = sub.add_parser("move-category", help="перенести в другую категорию")
    p.add_argument("id")
    p.add_argument("category")
    p = sub.add_parser("reparse", help="разобрать заново из копии в библиотеке")
    p.add_argument("id")
    p.add_argument("--recategorize", action="store_true")
    p = sub.add_parser("reindex", help="дописать заметки в память и переиндексировать")
    p.add_argument("ids", nargs="*")
    p.add_argument("--all", action="store_true", help="включая записи, принятые с --no-memory")
    p.add_argument("--rewrite", action="store_true", help="переписать заметки в vault заново")
    sub.add_parser("rules", help="где лежат правила категорий и какие категории есть")
    return ap


def _build(args) -> tuple[Library, Path]:
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    root = Path(args.library_root).expanduser() if args.library_root else default_library_root(data_dir)
    settings = Settings(library_root=root, max_bytes=int(args.max_mb * 1048576), max_pdf_pages=args.max_pages,
                        note_chars=args.note_chars, use_docling=args.docling, use_ocr=args.ocr)
    memory = MemorySink(enabled=not args.no_memory, vault_root=args.vault, write_folder=args.write_folder,
                        backend=args.backend, data_dir=data_dir)
    return Library(settings, memory), data_dir


def _notifier(enabled: bool):
    if not enabled:
        return None
    if str(REPO_ROOT / "tools") not in sys.path:
        sys.path.append(str(REPO_ROOT / "tools"))
    import pult_note  # existing owner-only pult helper; plain text send()

    def notify(outcome: Outcome) -> None:
        if outcome.status == ADDED and outcome.entry:
            e = outcome.entry
            pages = f", {e['pages']} стр." if e.get("pages") else ""
            text = (f"📚 Библиотека: принят «{e['original_name']}» → {e['category']}{pages}; "
                    f"разбор {e['parse_status']}; память: {e['memory'].get('state')}")
        elif outcome.status == DUPLICATE:
            text = f"📚 Библиотека: «{Path(outcome.path).name}» уже есть (дубликат)"
        else:
            text = f"📚 Библиотека: «{Path(outcome.path).name}» не принят — {outcome.reason[:200]}"
        pult_note.send(text)
    return notify


def _out(args, payload, human: str) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=1) if args.json else human)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    try:
        lib, data_dir = _build(args)
        notify = _notifier(args.notify)
        if args.cmd == "intake":
            if args.low_priority:
                lower_priority()
            outcomes = lib.intake_many(args.files, tags=[t for t in args.tags.split(",") if t.strip()])
            for outcome in outcomes:
                if notify:
                    try:
                        notify(outcome)
                    except Exception as exc:  # noqa: BLE001 - notification is best effort
                        print(f"уведомление не отправлено: {type(exc).__name__}", file=sys.stderr)
            _out(args, [o.to_dict() for o in outcomes], "\n".join(o.line() for o in outcomes))
            return 0 if all(o.status in (ADDED, DUPLICATE) for o in outcomes) else (
                2 if any(o.status == REFUSED for o in outcomes) else 1)
        if args.cmd == "watch":
            from .watch import watch
            lower_priority()
            inbox = Path(args.inbox).expanduser() if args.inbox else default_inbox(data_dir)
            return watch(lib, inbox, interval=args.interval, settle=args.settle, once=args.once, notify=notify)
        if args.cmd == "list":
            items = lib.entries(args.category)
            lines = [f"{e['id']}  {e['category']:<9} {e['parse_status']:<11} "
                     f"{(str(e.get('pages') or '') + ' стр.') if e.get('pages') else '':>9}  "
                     f"{(e.get('memory') or {}).get('state', ''):<8}  {e['title']}" for e in items]
            _out(args, items, "\n".join(lines) or "библиотека пуста")
            return 0
        if args.cmd == "show":
            entry = lib.get(args.id)
            _out(args, entry, json.dumps(entry, ensure_ascii=False, indent=1))
            return 0
        if args.cmd == "search":
            hits = lib.search(" ".join(args.text), limit=args.limit)
            lines = []
            for hit in hits:
                lines.append(f"{hit['id']}  [{hit['category']}] {hit['title']}")
                lines += [f"    {s['where'] or '—'}: {s['text']}" for s in hit["snippets"]]
            _out(args, hits, "\n".join(lines) or "ничего не найдено")
            return 0
        if args.cmd == "retag":
            entry = lib.retag(args.id, add=args.add.split(","), remove=args.remove.split(","),
                              set_to=args.set_to.split(",") if args.set_to is not None else None)
            _out(args, entry, f"{entry['id']}: метки {', '.join(entry['tags']) or '—'}")
            return 0
        if args.cmd == "move-category":
            entry = lib.move_category(args.id, args.category)
            _out(args, entry, f"{entry['id']}: категория {entry['category']} → {entry['file']}")
            return 0
        if args.cmd == "reparse":
            entry = lib.reparse(args.id, recategorize=args.recategorize)
            _out(args, entry, f"{entry['id']}: разбор {entry['parse_status']} ({entry['parser']}), "
                              f"{len(entry['notes'])} заметок, категория {entry['category']}")
            return 0
        if args.cmd == "reindex":
            result = lib.reindex(args.ids or None, include_disabled=args.all, rewrite=args.rewrite)
            _out(args, result, f"переписано {result['rewritten']}, индекс: {'OK' if result['indexed'] else 'НЕТ'} "
                               f"({result['detail'][:200]}), ждут: {result['pending']}")
            return 0 if result["indexed"] else 3
        if args.cmd == "rules":
            rules, path = load_rules(lib.root)
            names = [c["name"] for c in rules["categories"]] + [rules.get("default", "other")]
            _out(args, {"path": str(path), "categories": names}, f"правила: {path}\nкатегории: {', '.join(names)}")
            return 0
    except (LibraryError, RulesError, LockBusy, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ошибка файловой системы: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 1
