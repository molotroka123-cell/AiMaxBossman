"""``bossman pit master-parse`` — owner command line for the Master Parser."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from . import speed as speedmod
from .engine import (
    DEFAULT_LOCAL_URL,
    DEFAULT_MODEL,
    AlreadyRunning,
    Options,
    parse_since,
    read_status,
    resolve_settings,
    revert_run,
    run_master_parse,
)


UNCENSORED_MODEL = "bossman-community-qwen-uncensored:latest"
EXIT_OK, EXIT_CONFIG, EXIT_RUNNING, EXIT_PARTIAL, EXIT_FAILED = 0, 2, 3, 4, 5


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bossman pit master-parse",
        description="Собрать все разговоры Jeff в один корпус и обновить паспорта участников.")
    p.add_argument("command", nargs="?", default="master-parse")
    p.add_argument("--data-dir", default="")
    p.add_argument("--participant", default="", help="tg:<id>, <telegram id>, web:<id> или начало ключа")
    p.add_argument("--since", default="", help="только сообщения новее: 7d, 24h или ISO-дата")
    p.add_argument("--dry-run", action="store_true", help="ничего не записывать, только показать")
    p.add_argument("--no-llm", action="store_true", help="только детерминированный извлекатель Jeff")
    p.add_argument("--no-cloud", action="store_true", help="никогда не использовать облако")
    p.add_argument("--no-checkpoint", action="store_true", help="не обновлять сводку паспортов")
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--batch-size", type=int, default=24)
    p.add_argument("--model", default=None, help=f"локальная модель Ollama (по умолчанию {DEFAULT_MODEL})")
    p.add_argument("--profile", choices=("uncensored",), default="",
                   help="uncensored: bossman-community-qwen-uncensored:latest, --no-cloud, concurrency 1")
    p.add_argument("--narrative", action=argparse.BooleanOptionalAction, default=True,
                   help="2 мини-абзаца о переписке и личности каждого участника (по умолчанию включено)")
    p.add_argument("--speed-report", default="", metavar="PATH",
                   help="отчёт о скорости (JSON); рядом speed_report_public.json без id и текста")
    p.add_argument("--local-url", default=DEFAULT_LOCAL_URL)
    p.add_argument("--extra-root", action="append", default=[],
                   help="дополнительная копия данных Bossman (только чтение)")
    p.add_argument("--revert", default="", metavar="RUN_ID", help="отменить факты одного запуска")
    p.add_argument("--status", action="store_true", help="показать ход последнего запуска")
    p.add_argument("--json", action="store_true", help="машинный вывод (JSON)")
    return p


def _progress_printer(enabled: bool):
    last = [0.0]

    def show(status: dict) -> None:
        now = time.monotonic()
        if not enabled or (now - last[0] < 1.0 and status.get("state") == "running"):
            return
        last[0] = now
        tail = (f" · абзацы {status.get('narratives_done', 0)}/{status.get('narratives_total', 0)}"
                if status.get("phase") == "narrative" else "")
        print(f"[{status.get('phase')}] собрано новых: {status.get('collected_new', 0)} · "
              f"участники {status.get('persons_done', 0)}/{status.get('persons_total', 0)} · "
              f"сообщения {status.get('messages_done', 0)}/{status.get('messages_total', 0)} · "
              f"{status.get('rate_msgs_per_s', 0)} сообщ/с · фактов +{status.get('facts_added', 0)}{tail}",
              file=sys.stderr, flush=True)
    return show


def summary_ru(report: dict) -> str:
    t = report.get("totals", {})
    lines = [
        ("Пробный прогон (ничего не записано)" if report.get("dry_run") else "Master Parser готов")
        + (" · без модели" if report.get("use_llm") is False else "")
        + f" · запуск {report.get('run_id')}",
        f"Участников: {t.get('participants', 0)} · новых сообщений собрано: {t.get('collected_new', 0)}"
        f" · всего в корпусе: {report.get('corpus_messages', 0)}",
        f"Проанализировано: {t.get('messages_analyzed', 0)} за {report.get('analysis_seconds', 0)} с"
        f" ({t.get('messages_per_second', 0)} сообщ/с)",
        f"Фактов добавлено: {t.get('facts_added', 0)} · уже были: {t.get('facts_known', 0)}"
        f" · конфликтов на проверку: {t.get('conflicts', 0)}"
        f" · не возвращено по воле участника: {t.get('blocked_by_participant', 0)}",
    ]
    if t.get("no_memory_consent"):
        lines.append(f"Без согласия на память (не анализировались): {t['no_memory_consent']}")
    for person in report.get("participants", []):
        lines.append(f"• {person['label']}: сообщений {person['messages_total']}, "
                     f"+{len(person['facts_added'])} фактов, конфликтов {len(person['conflicts'])}"
                     + ("" if person["status"] == "OK" else f" [{person['status']}]"))
    narratives = t.get("narratives") or {}
    if any(narratives.values()):
        lines.append("Мини-абзацы: " + ", ".join(f"{name} {count}" for name, count
                                                 in narratives.items() if count))
    for person in report.get("participants", []):
        story = person.get("narrative") or {}
        if story.get("status") in {"FAILED", "REJECTED"}:
            lines.append(f"  ! {person['label']}: абзацы не получены ({story['status']}, "
                         f"{story.get('error', '')})")
        if person.get("requeued"):
            lines.append(f"  ! {person['label']}: {person['requeued']} сообщений в очереди на повтор")
    if report.get("speed") and not report.get("dry_run"):
        lines.append(speedmod.table_ru(report["speed"]))
    if report.get("revert"):
        lines.append(f"Отменить этот запуск: {report['revert']}")
    return "\n".join(lines)


def exit_code(report: dict) -> int:
    """0 all work done; 4 partial (some participant failed or was re-queued); 5 nothing succeeded."""
    if report.get("dry_run") or report.get("use_llm") is False:
        return EXIT_OK
    good = bad = 0
    for person in report.get("participants", []):
        if person["status"] in {"NO_MEMORY_CONSENT", "BLOCKED"}:
            continue
        story = (person.get("narrative") or {}).get("status")
        failed = person["status"] != "OK" or story in {"FAILED", "REJECTED"}
        worked = (person.get("messages_pending", 0) > 0 or failed
                  or story in {"OK", "FAILED", "REJECTED"})
        if not worked:
            continue
        if failed:
            bad += 1
        else:
            good += 1
    if report.get("checkpoint", {}).get("status") == "failed" and not bad:
        bad += 1
    if not bad:
        return EXIT_OK
    return EXIT_PARTIAL if good else EXIT_FAILED


def cli_main(config: Path, argv: list[str]) -> int:
    ns = build_parser().parse_args(argv)
    try:
        settings = resolve_settings(config)
    except (OSError, ValueError) as exc:
        print(f"bossman pit master-parse: PIT не настроен ({type(exc).__name__})", file=sys.stderr)
        return 2
    if ns.status:
        print(json.dumps(read_status(Path(settings.data_dir)), ensure_ascii=False, indent=2))
        return 0
    if ns.revert:
        try:
            result = revert_run(settings, ns.revert)
        except FileNotFoundError:
            print("Запуск не найден (или это был пробный прогон).", file=sys.stderr)
            return 2
        except AlreadyRunning:
            print("Master Parser уже работает — дождитесь завершения.", file=sys.stderr)
            return 3
        print(json.dumps(result, ensure_ascii=False) if ns.json else
              f"Отменено фактов: {result['removed']} (уже не было: {result['already_gone']}).")
        return 0
    try:
        since = parse_since(ns.since)
    except ValueError:
        print("--since: 7d, 24h, 30m или ISO-дата", file=sys.stderr)
        return 2
    model = ns.model or DEFAULT_MODEL
    cloud, concurrency = not ns.no_cloud, max(1, min(4, ns.concurrency))
    if ns.profile == "uncensored":   # the owner's local low-overrefusal profile: local only, serial
        model, cloud, concurrency = UNCENSORED_MODEL, False, 1
    options = Options(participant=ns.participant, since=since, dry_run=ns.dry_run,
                      use_llm=not ns.no_llm, cloud=cloud, checkpoint=not ns.no_checkpoint,
                      concurrency=concurrency, narrative=ns.narrative,
                      speed_report=ns.speed_report,
                      batch_size=max(4, min(60, ns.batch_size)), model=model,
                      local_url=ns.local_url, extra_roots=list(ns.extra_root))
    try:
        report = asyncio.run(run_master_parse(settings, options,
                                              progress=_progress_printer(not ns.json)))
    except AlreadyRunning:
        print("Master Parser уже работает — дождитесь завершения (bossman pit master-parse --status).",
              file=sys.stderr)
        return 3
    print(json.dumps(report, ensure_ascii=False) if ns.json else summary_ru(report))
    return exit_code(report)
