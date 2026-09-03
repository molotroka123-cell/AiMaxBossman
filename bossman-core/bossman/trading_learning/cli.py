"""CLI пайплайна: python -m bossman.trading_learning.cli <команда>.

Каждая команда — отдельная точка входа из ТЗ. Команды, чья технология
недоступна, честно печатают BLOCKED и возвращают код 3 — так их видно в CI и
нельзя перепутать с успехом (0) или ошибкой запуска (2).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from .safety import OwnerApproval, OwnerApprovalRequired, utcnow

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_BLOCKED = 3


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _approval(args) -> OwnerApproval:
    """Одобрение владельца из аргументов. Без --approved-by работы не будет."""
    if not getattr(args, "approved_by", ""):
        raise OwnerApprovalRequired("--approved-by is required (a person, not an agent)")
    return OwnerApproval(subject=args.source, stage="historical_analysis",
                         granted_by=args.approved_by, granted_at=utcnow())


# ----------------------------------------------------------------- команды
def cmd_ingest_video(args) -> int:
    from .ingest import ingest_video
    record = ingest_video(args.source, approval=_approval(args), notes=args.notes)
    _emit(record.as_dict())
    return EXIT_OK if record.evidence_class != "BLOCKED" else EXIT_BLOCKED


def cmd_extract_audio(args) -> int:
    from .adapters import extract_audio
    result = extract_audio(args.source, args.out)
    _emit(result.as_dict())
    return EXIT_OK if result.ok else EXIT_BLOCKED


def cmd_transcribe(args) -> int:
    from .adapters import transcribe
    result = transcribe(args.source)
    _emit(result.as_dict())
    return EXIT_OK if result.ok else EXIT_BLOCKED


def cmd_extract_frames(args) -> int:
    from .frames import extract_frames
    marks = [float(x) for x in args.timestamps.split(",")] if args.timestamps else None
    result = extract_frames(args.source, args.out, timestamps=marks,
                            every_seconds=args.every, max_frames=args.max_frames)
    payload = result.as_dict()
    payload["frames"] = [f.as_dict() for f in (result.payload or [])]
    _emit(payload)
    return EXIT_OK if result.ok else EXIT_BLOCKED


def cmd_chart_ocr(args) -> int:
    from .adapters import chart_ocr
    result = chart_ocr(args.frames.split(",") if args.frames else [])
    _emit(result.as_dict())
    return EXIT_OK if result.ok else EXIT_BLOCKED


def cmd_extract_claims(args) -> int:
    from .claims import Segment, dedupe, extract_claims
    raw = json.loads(open(args.segments, encoding="utf-8").read())
    segments = [Segment(float(s["start"]), float(s["end"]), s["text"],
                        s.get("frame_ref", ""), s.get("channel", "transcript")) for s in raw]
    collected = (datetime.fromisoformat(args.collected_at).astimezone(timezone.utc)
                 if args.collected_at else None)
    claims = dedupe(extract_claims(segments, source_id=args.source_id,
                                   video_hash=args.video_hash, asset=args.asset,
                                   venue=args.venue, timeframe=args.timeframe,
                                   collected_at=collected))
    _emit({"count": len(claims), "claims": [c.as_dict() for c in claims]})
    return EXIT_OK


def cmd_normalize_strategy(args) -> int:
    _emit({"status": "NEEDS_INPUT",
           "message": ("normalize_strategy требует claim'ов и явных зон уровней; "
                       "используйте bossman.trading_learning.strategy.normalize_strategy "
                       "из кода или API — CLI не выдумывает зоны за владельца")})
    return EXIT_ERROR


def cmd_verify_claims(args) -> int:
    _emit({"status": "BLOCKED",
           "reason": ("нет источника исторических рыночных данных в окружении: "
                      "верификатору нечего противопоставить claim'ам"),
           "missing": ["market data provider (READ_ONLY, owner-approved)"]})
    return EXIT_BLOCKED


def cmd_compile_backtest(args) -> int:
    _emit({"status": "NEEDS_INPUT",
           "message": "compile_backtest принимает StrategyRule; см. trading_benchmark"})
    return EXIT_ERROR


def cmd_run_backtest(args) -> int:
    _emit({"status": "BLOCKED", "reason": "нет исторических данных (см. verify_claims)",
           "missing": ["market data provider (READ_ONLY, owner-approved)"]})
    return EXIT_BLOCKED


def cmd_paper_trade(args) -> int:
    _emit({"status": "BLOCKED",
           "reason": ("paper trading прогоняется только по реальным историческим данным; "
                      "источника данных нет, а торговать по фикстуре и называть это "
                      "результатом запрещено"),
           "trading_execution": "OFF", "paper_trading_only": True})
    return EXIT_BLOCKED


def cmd_lesson_builder(args) -> int:
    _emit({"status": "NEEDS_INPUT",
           "message": "lesson_builder собирает урок из прогона; сначала нужен run_backtest"})
    return EXIT_ERROR


def cmd_trading_benchmark(args) -> int:
    from .benchmark import BenchmarkMode, run_benchmark
    modes = ([BenchmarkMode(m.strip().upper()) for m in args.modes.split(",")]
             if args.modes else None)
    report = run_benchmark(modes)
    _emit(report.as_dict())
    # NOT_READY — это НЕ ошибка выполнения, это честный вердикт. Но и не успех.
    return EXIT_OK if report.verdict == "READY" else EXIT_BLOCKED


def cmd_status(args) -> int:
    from .routes import pipeline_status
    _emit(pipeline_status())
    return EXIT_OK


def cmd_seed(args) -> int:
    from .seed import seed_report
    _emit(seed_report())
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading_learning",
                                     description="K1MBA Trading Learning Lab (paper/replay only)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest_video"); p.add_argument("source")
    p.add_argument("--approved-by", default=""); p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_ingest_video)

    p = sub.add_parser("extract_audio"); p.add_argument("source"); p.add_argument("out")
    p.set_defaults(func=cmd_extract_audio)

    p = sub.add_parser("transcribe"); p.add_argument("source")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("extract_frames"); p.add_argument("source"); p.add_argument("out")
    p.add_argument("--timestamps", default=""); p.add_argument("--every", type=float, default=5.0)
    p.add_argument("--max-frames", dest="max_frames", type=int, default=40)
    p.set_defaults(func=cmd_extract_frames)

    p = sub.add_parser("chart_ocr"); p.add_argument("--frames", default="")
    p.set_defaults(func=cmd_chart_ocr)

    p = sub.add_parser("extract_claims"); p.add_argument("segments")
    p.add_argument("--source-id", dest="source_id", required=True)
    p.add_argument("--video-hash", dest="video_hash", required=True)
    p.add_argument("--asset", required=True); p.add_argument("--venue", required=True)
    p.add_argument("--timeframe", required=True)
    p.add_argument("--collected-at", dest="collected_at", default="")
    p.set_defaults(func=cmd_extract_claims)

    for name, func in (("normalize_strategy", cmd_normalize_strategy),
                       ("verify_claims", cmd_verify_claims),
                       ("compile_backtest", cmd_compile_backtest),
                       ("run_backtest", cmd_run_backtest),
                       ("paper_trade", cmd_paper_trade),
                       ("lesson_builder", cmd_lesson_builder)):
        sub.add_parser(name).set_defaults(func=func)

    p = sub.add_parser("trading_benchmark"); p.add_argument("--modes", default="")
    p.set_defaults(func=cmd_trading_benchmark)

    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("seed").set_defaults(func=cmd_seed)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 — CLI печатает причину, а не трейс
        _emit({"status": "ERROR", "error": type(exc).__name__, "message": str(exc)})
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
