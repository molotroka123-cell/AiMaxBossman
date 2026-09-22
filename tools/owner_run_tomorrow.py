#!/usr/bin/env python3
"""Owner-suite runner for the first-day run of a Bossman 1.0-RC archive.

Runs beside the shipped runtime (``app-support/owner_run_tomorrow.py``) or from a
checkout (``tools/owner_run_tomorrow.py``). It orchestrates ONLY what a machine
can do without the owner's hands:

  doctor       bossman_doctor.py --json (runtime, packages, ffmpeg, browser, ports)
  models       MAIN/FAST discovery: every OpenAI-compatible endpoint from
               BOSSMAN_MODEL_ENDPOINTS (default 127.0.0.1:8081 and :8082) is asked
               for /v1/models and one tiny completion — a preflight, not a benchmark
  media        stable-diffusion.cpp engine + model manifest validation through
               media_bootstrap.py (real sha256; missing config = OWNER_REQUIRED,
               never a failure of the whole run)
  evening      (opt-in) bundle_evening_test.py — the installed-product acceptance
  coaching     coaching_runner.py (local endpoint, or --coaching-backend mock,
               which is labelled MOCK in every output)
  diagnostics  a redacted diagnostics ZIP the owner can attach to a defect report

Every stage ends in exactly one of PASS / WARN / FAIL / OWNER_REQUIRED / NOT_RUN
with a routing hint. The runner never turns a missing key, a missing model or a
missing endpoint into a green result, and it never claims OWNER_HARDWARE_CERTIFIED:
the human scenarios in START_TOMORROW_RU.md remain the owner's work.

Second profile of the SAME runner (no separate launcher)::

  Owner-Run.cmd self-improve-mvcr plan|preflight|bootstrap|compare|mvcr|self-improve|report|run|resume|status
  Owner-Run.cmd plan | resume | stop          (short forms)

plan has no side effects; preflight adds the model-reuse plan (model_fetch.py),
Bossman coding-task readiness with a sidecar handshake and the Telegram poller
check (a live poller.lock means "already running" — a second one is never
started); bootstrap downloads only missing, explicitly named profiles and only
with --allow-download; compare runs model_bakeoff.py; mvcr stops at
WAIT_APPROVAL / PARTIAL_MISSING_DATA / BLOCKED and never submits; self-improve
runs self_improve_lab.py explore → compare → lesson, a full Bossman restart and
transfer. State lives in <run dir>/state.json: resume skips completed steps,
never repeats an external action and marks one interrupted mid-way as
UNKNOWN_OUTCOME. A STOP file (or `stop`) ends the run between steps and kills
every process tree the runner started. See docs/owner/OWNER_RUN_NEXT.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PASS, WARN, FAIL, OWNER_REQUIRED, NOT_RUN = "PASS", "WARN", "FAIL", "OWNER_REQUIRED", "NOT_RUN"
STAGES = ("doctor", "models", "media", "evening", "coaching", "diagnostics")
DEFAULT_STAGES = ("doctor", "models", "media", "coaching", "diagnostics")
DEFAULT_ENDPOINTS = "http://127.0.0.1:8081,http://127.0.0.1:8082"
ENDPOINTS_ENV = "BOSSMAN_MODEL_ENDPOINTS"
LOG_TAIL_BYTES = 200 * 1024
# Что НИКОГДА не попадает в diagnostics.zip: значения, похожие на ключи/токены.
SECRET_RX = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|(?i:token|secret|api[_-]?key|password|bearer)\s*[:=]\s*\S+"
                       r"|[A-Za-z0-9+/_-]{40,})")


def utf8_console() -> None:
    """Печать не имеет права падать на кириллице (раннеры идут под `-I`)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, io.UnsupportedOperation):
            pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _sibling(name: str) -> Path | None:
    """Скрипт рядом с этим файлом (app-support) либо в checkout (tools/, scripts/)."""
    for candidate in (HERE / name, REPO / "tools" / name, REPO / "scripts" / name):
        if candidate.is_file():
            return candidate
    return None


def _python() -> str:
    return sys.executable


def _data_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get("BCC_DATA_DIR"):
        return Path(os.environ["BCC_DATA_DIR"]).expanduser()
    try:
        from bcc.config import _data_dir as bcc_data_dir  # type: ignore
        return Path(bcc_data_dir())
    except Exception:  # noqa: BLE001 — раннер работает и без установленного bcc
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
            return base / "Bossman" / "CommandCenter"
        return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "bossman" / "command-center"


def _run(cmd: list[str], timeout: float, cwd: Path | None = None) -> tuple[int | None, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, cwd=str(cwd) if cwd else None)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        return None, (exc.stdout or "") if isinstance(exc.stdout, str) else "", f"timeout after {timeout}s"
    except OSError as exc:
        return None, "", f"{type(exc).__name__}: {exc}"


def stage_result(status: str, detail: str, *, route: str = "", **facts: Any) -> dict:
    return {"status": status, "detail": detail, "route": route, "facts": facts, "at": _now()}


# ------------------------------------------------------------------ stages

def stage_doctor(out: Path, args) -> dict:
    script = _sibling("bossman_doctor.py")
    if script is None:
        return stage_result(FAIL, "bossman_doctor.py не найден рядом с раннером",
                            route="дефект упаковки: сообщить интегратору")
    report_path = out / "doctor.json"
    code, stdout, stderr = _run([_python(), "-I", str(script), "--json", "--json-out", str(report_path),
                                 "--port", str(args.port)], timeout=120)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return stage_result(FAIL, f"доктор не оставил отчёта (exit={code}): {stderr.strip()[-300:]}",
                            route="приложить вывод консоли; это дефект доктора или упаковки")
    blocked = [c for c in report.get("checks", []) if c.get("status") == "BLOCKED"]
    warns = [c for c in report.get("checks", []) if c.get("status") == "WARN"]
    if blocked:
        return stage_result(FAIL, "BLOCKED: " + "; ".join(f"{c['name']}: {c['detail']}" for c in blocked)[:800],
                            route="не запускать сценарии владельца до устранения; приложить doctor.json",
                            blocked=[c["name"] for c in blocked], warn=[c["name"] for c in warns])
    return stage_result(WARN if warns else PASS,
                        f"{report.get('pass', 0)} PASS, {len(warns)} WARN, 0 BLOCKED",
                        route=("WARN означает отсутствующую необязательную возможность — см. doctor.json"
                               if warns else ""), warn=[c["name"] for c in warns])


def _http_json(url: str, payload: dict | None = None, timeout: float = 5.0) -> tuple[int, Any, float]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                          "Accept": "application/json"})
    t0 = time.perf_counter()
    # Loopback: прокси из окружения про локальный адрес только врёт.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310 — loopback endpoints
        body = resp.read().decode("utf-8", "replace")
        return resp.status, (json.loads(body) if body else None), time.perf_counter() - t0


def probe_endpoint(base: str, *, completion: bool = True, timeout: float = 5.0) -> dict:
    """Один OpenAI-совместимый endpoint: список моделей и крошечное завершение.

    Это предполётная проверка достижимости, НЕ измерение скорости (для этого —
    TEL-001 внутри продукта) и не сертификация модели.
    """
    base = base.rstrip("/")
    result: dict[str, Any] = {"endpoint": base, "reachable": False, "models": [], "completion": None}
    try:
        status, body, elapsed = _http_json(base + "/v1/models", timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["reachable"] = status == 200
    ids = [m.get("id") for m in (body or {}).get("data", []) if isinstance(m, dict)]
    result["models"] = [str(i) for i in ids if i]
    result["models_latency_s"] = round(elapsed, 3)
    if completion and result["models"]:
        try:
            status, body, elapsed = _http_json(base + "/v1/chat/completions", {
                "model": result["models"][0], "max_tokens": 8, "temperature": 0,
                "messages": [{"role": "user", "content": "Ответь одним словом: готов?"}]},
                timeout=max(timeout, 60.0))
            text = ""
            try:
                text = str(body["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError):
                pass
            result["completion"] = {"status": status, "latency_s": round(elapsed, 3),
                                    "text": text[:80], "usage": (body or {}).get("usage")}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
            result["completion"] = {"error": f"{type(exc).__name__}: {exc}"}
    return result


def stage_models(out: Path, args) -> dict:
    endpoints = [e.strip() for e in (args.endpoints or os.environ.get(ENDPOINTS_ENV) or DEFAULT_ENDPOINTS).split(",")
                 if e.strip()]
    probes = [probe_endpoint(e, completion=not args.no_completion) for e in endpoints]
    (out / "models.json").write_text(json.dumps(probes, indent=2, ensure_ascii=False), encoding="utf-8")
    live = [p for p in probes if p["reachable"] and p["models"]]
    answered = [p for p in live if p.get("completion") and "error" not in (p.get("completion") or {})
                and (p.get("completion") or {}).get("status") == 200]
    if not live:
        return stage_result(OWNER_REQUIRED,
                            "ни один локальный endpoint не отвечает: " + "; ".join(
                                f"{p['endpoint']} — {p.get('error', 'нет моделей')}" for p in probes),
                            route="запустить llama-server MAIN/FAST (или указать BOSSMAN_MODEL_ENDPOINTS); "
                                  "без модели сценарии 2–8 невозможны", endpoints=endpoints)
    if not args.no_completion and len(answered) < len(live):
        return stage_result(WARN, "endpoint отвечает списком моделей, но завершение не удалось: " + "; ".join(
            f"{p['endpoint']}: {(p.get('completion') or {}).get('error', 'нет ответа')}" for p in live if p not in answered),
            route="проверить, что модель загружена и ctx не исчерпан; см. models.json",
            live=[p["endpoint"] for p in live])
    return stage_result(PASS, "; ".join(
        f"{p['endpoint']}: {', '.join(p['models'][:2])}" + (
            f" ({(p.get('completion') or {}).get('latency_s')} с на 8 токенов)" if p.get("completion") else "")
        for p in live), live=[p["endpoint"] for p in live])


def stage_media(out: Path, args) -> dict:
    script = _sibling("media_bootstrap.py")
    if script is None:
        return stage_result(FAIL, "media_bootstrap.py не найден рядом с раннером",
                            route="дефект упаковки: сообщить интегратору")
    models_dir = args.media_models or os.environ.get("BOSSMAN_MEDIA_MODELS", "")
    if not models_dir:
        try:
            if str(REPO / "command-center") not in sys.path:
                sys.path.insert(0, str(REPO / "command-center"))
            from bcc.studio.providers.sdcpp import configuration  # type: ignore
            cfg = configuration()
            if cfg:
                models_dir = str(cfg["root"])
        except Exception:  # noqa: BLE001 — нет bcc или нет конфигурации
            models_dir = ""
    if not models_dir:
        return stage_result(OWNER_REQUIRED, "локальный медиадвижок не настроен (нет BOSSMAN_MEDIA_MODELS и "
                            "media/config.json)", route="Media-Setup.cmd → configure; сценарий 6 без этого "
                            "идёт только по редактированию/экспорту, не по генерации")
    report_path = out / "media-manifest.json"
    code, stdout, stderr = _run([_python(), "-I", str(script), "validate", models_dir, "--json",
                                 "--out", str(report_path)], timeout=3600)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return stage_result(FAIL, f"валидатор манифеста не оставил отчёта (exit={code}): {(stderr or stdout).strip()[-300:]}",
                            route="приложить вывод; проверить папку моделей")
    verdict = str(report.get("verdict") or report.get("status") or "").upper()
    files = report.get("files") or report.get("entries") or []
    bad = [f for f in files if isinstance(f, dict) and str(f.get("status", "")).upper() not in ("PRESENT_OK", "OK")]
    if verdict in ("OK", "PASS", "VALID") and not bad:
        return stage_result(PASS, f"манифест и {len(files)} файлов сверены по sha256", models_dir=models_dir)
    return stage_result(FAIL if any(str(f.get("status", "")).upper().endswith("BAD_HASH") for f in bad) else OWNER_REQUIRED,
                        f"{len(bad)} файлов не в порядке: " + "; ".join(
                            f"{f.get('path') or f.get('role')}: {f.get('status')}" for f in bad)[:600],
                        route="Media-Setup.cmd → plan-download / download --allow-download; "
                              "повреждённый файл (BAD_HASH) перекачать", models_dir=models_dir)


def stage_evening(out: Path, args) -> dict:
    script = _sibling("bundle_evening_test.py")
    if script is None:
        return stage_result(FAIL, "bundle_evening_test.py не найден", route="дефект упаковки")
    code, stdout, stderr = _run([_python(), "-I", str(script), *args.evening_args], timeout=3600)
    (out / "evening.log").write_text(stdout + "\n--- stderr ---\n" + stderr, encoding="utf-8")
    if code == 0:
        return stage_result(PASS, "вечерняя приёмка архива прошла (см. evening.log)")
    return stage_result(FAIL, f"вечерняя приёмка вернула код {code}", route="приложить evening.log")


def stage_coaching(out: Path, args) -> dict:
    script = _sibling("coaching_runner.py")
    if script is None:
        return stage_result(FAIL, "coaching_runner.py не найден", route="дефект упаковки")
    target = out / "coaching"
    cmd = [_python(), str(script), "--backend", args.coaching_backend, "--out", str(target)]
    if args.coaching_lessons_dir:
        cmd += ["--lessons-dir", args.coaching_lessons_dir]
    if args.coaching_endpoint:
        cmd += ["--endpoint", args.coaching_endpoint]
    code, stdout, stderr = _run(cmd, timeout=args.coaching_timeout)
    (target / "runner.log").parent.mkdir(parents=True, exist_ok=True)
    (target / "runner.log").write_text(stdout + "\n--- stderr ---\n" + stderr, encoding="utf-8")
    try:
        results = json.loads((target / "results.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return stage_result(FAIL, f"coaching runner не оставил results.json (exit={code})",
                            route="приложить coaching/runner.log")
    status = str(results.get("status") or "")
    if status == "MOCK":
        return stage_result(WARN, "COACHING_PIPELINE_TESTED (MOCK backend): числа не относятся к локальной модели; "
                            "LOCAL_LEARNING_GAIN_NOT_MEASURED; WEIGHTS_UNCHANGED",
                            route="повторить с --coaching-backend local при живом endpoint", runner_status=status)
    if status == "LOCAL_LEARNING_GAIN_NOT_MEASURED":
        return stage_result(OWNER_REQUIRED, "endpoint для coaching недоступен: LOCAL_LEARNING_GAIN_NOT_MEASURED; "
                            "WEIGHTS_UNCHANGED", route="запустить llama-server и повторить Coaching.cmd", runner_status=status)
    return stage_result(PASS if code == 0 else FAIL,
                        f"{status}; WEIGHTS_UNCHANGED; см. coaching/summary.md", runner_status=status,
                        learning_gain=results.get("learning_gain"))


def _redact(text: str) -> str:
    return SECRET_RX.sub("[REDACTED]", text)


def stage_diagnostics(out: Path, args) -> dict:
    data_dir = _data_dir(args.data_dir)
    target = out / "diagnostics.zip"
    added: list[str] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        env_names = sorted(k for k in os.environ if k.startswith(("BOSSMAN_", "BCC_", "PLAYWRIGHT_")))
        zf.writestr("environment.json", json.dumps({
            "platform": platform.platform(), "python": platform.python_version(),
            "runner": str(HERE), "data_dir": str(data_dir), "env_names_only": env_names,
            "collected_at": _now()}, indent=2, ensure_ascii=False))
        added.append("environment.json")
        for name in ("doctor.json", "models.json", "media-manifest.json", "evening.log", "report.json"):
            p = out / name
            if p.is_file():
                zf.writestr(name, _redact(p.read_text(encoding="utf-8", errors="replace")))
                added.append(name)
        for name in ("MANIFEST.json", "SHA256SUMS"):
            for base in (HERE.parent, REPO):
                p = base / name
                if p.is_file():
                    zf.writestr("bundle/" + name, p.read_text(encoding="utf-8", errors="replace")[:2_000_000])
                    added.append("bundle/" + name)
                    break
        listing = []
        if data_dir.is_dir():
            for p in sorted(data_dir.rglob("*")):
                if p.is_file():
                    try:
                        listing.append({"path": str(p.relative_to(data_dir)), "bytes": p.stat().st_size})
                    except OSError:
                        continue
                    if p.suffix.lower() in (".log", ".txt") and "log" in p.name.lower():
                        try:
                            with p.open("rb") as fh:
                                fh.seek(max(0, p.stat().st_size - LOG_TAIL_BYTES))
                                tail = fh.read().decode("utf-8", "replace")
                        except OSError:
                            continue
                        zf.writestr("logs/" + p.relative_to(data_dir).as_posix(), _redact(tail))
                        added.append("logs/" + p.relative_to(data_dir).as_posix())
        zf.writestr("data-dir-listing.json", json.dumps(listing[:5000], indent=1, ensure_ascii=False))
        added.append("data-dir-listing.json")
    return stage_result(PASS, f"{target.name}: {len(added)} файлов, секреты вырезаны по шаблону",
                        zip=str(target), files=added)


RUNNERS = {"doctor": stage_doctor, "models": stage_models, "media": stage_media,
           "evening": stage_evening, "coaching": stage_coaching, "diagnostics": stage_diagnostics}


def overall(stages: dict[str, dict]) -> str:
    statuses = {s["status"] for s in stages.values()}
    if FAIL in statuses:
        return FAIL
    if OWNER_REQUIRED in statuses:
        return OWNER_REQUIRED
    if WARN in statuses:
        return WARN
    return PASS


def render_md(report: dict) -> str:
    lines = [f"# Owner run — {report['started_at']}", "",
             f"Итог машинных стадий: **{report['overall']}**. Это предполётная проверка; "
             "сценарии 1–8 из START_TOMORROW_RU.md выполняет владелец. "
             "OWNER_HARDWARE_CERTIFIED этим отчётом не объявляется.", "",
             "| стадия | статус | что видно | куда дальше |", "|---|---|---|---|"]
    for name, s in report["stages"].items():
        lines.append(f"| {name} | {s['status']} | {s['detail'].replace('|', '/')} | {s.get('route') or '—'} |")
    lines += ["", f"Папка отчёта: `{report['out']}`", "WEIGHTS_UNCHANGED"]
    return "\n".join(lines) + "\n"


# ======================================================= profile self-improve-mvcr
#
# `Owner-Run.cmd self-improve-mvcr <стадия>` — второй профиль ТОГО ЖЕ раннера, не
# отдельный лаунчер. Стадии идут по порядку, каждая заканчивается одним статусом
# с подсказкой маршрута. Состояние — в <папка прогона>/state.json, поэтому
# повторный запуск (resume) пропускает завершённое, никогда не повторяет уже
# сделанное внешнее действие и не качает здоровые модели заново. STOP-файл (или
# `Owner-Run.cmd stop`) останавливает прогон между шагами и убивает деревья
# процессов, которые запустил сам раннер.
#
# Скрипты model_fetch.py, mvcr_prepare.py, self_improve_lab.py ищутся рядом
# (app-support/ или tools/); если их нет — стадия NOT_RUN с причиной, а не PASS.

PROFILE = "self-improve-mvcr"
BLOCKED, UNKNOWN_OUTCOME = "BLOCKED", "UNKNOWN_OUTCOME"
SI_STAGES = ("plan", "preflight", "bootstrap", "skills", "compare", "mvcr", "self-improve", "report")
SI_RUN_STAGES = SI_STAGES[1:]
SI_PHASES = ("explore", "compare", "lesson", "restart", "transfer")
SI_ACTIONS = SI_STAGES + ("run", "resume", "stop", "status")
TOP_LEVEL_ALIASES = ("stop", "resume", "plan")
# Шаги, повтор которых безопасен: они либо только читают, либо сами сверяют
# результат (model_fetch fetch докачивает и не трогает здоровое, mvcr_prepare
# только готовит пакет, перезапуск Bossman проверяется по started_at).
# Всё остальное — внешнее действие: прерванное посередине становится
# UNKNOWN_OUTCOME и повторяется только по явному --redo владельца.
IDEMPOTENT = frozenset({"preflight", "bootstrap", "skills", "compare", "mvcr", "report", "self-improve:restart"})
SEVERITY = {PASS: 0, WARN: 1, NOT_RUN: 2, OWNER_REQUIRED: 3, UNKNOWN_OUTCOME: 4, BLOCKED: 5, FAIL: 6}
KNOWN_STATUSES = frozenset(SEVERITY)
EXIT_STOPPED = 3
APP_IDENTITY = "bossman-command-center"
TOKEN_HEADER = "X-BCC-Token"
NO_DOWNLOAD = frozenset({"REUSED", "REUSE", "PRESENT_OK", "OK", "HEALTHY", "PRESENT", "VERIFIED", "SKIP",
                         "SKIPPED", "UP_TO_DATE", "INSTALLED"})
FILE_PRESENT_STATES = frozenset({"PRESENT_VALID", "PRESENT_UNPINNED", "PRESENT_SIZE_OK_NOT_HASHED", "REUSABLE"})
FILE_DOWNLOAD_STATES = frozenset({"MISSING", "PARTIAL", "PRESENT_SIZE_MISMATCH", "PRESENT_HASH_MISMATCH"})
NEEDS_DOWNLOAD_PREFIXES = ("MISSING", "DOWNLOAD", "WILL_DOWNLOAD", "FETCH", "BAD", "PARTIAL", "CORRUPT",
                           "INCOMPLETE", "ABSENT")
MVCR_FINAL = {"WAIT_APPROVAL": OWNER_REQUIRED, "PARTIAL_MISSING_DATA": OWNER_REQUIRED, "BLOCKED": BLOCKED}
STAGE_TEXT = {
    "plan": ("стадии, что будет скачано, место на диске, что требует разрешения — ничего не меняет", "—"),
    "preflight": ("доктор; эндпоинты моделей; переиспользование моделей (model_fetch plan); Bossman API и "
                  "готовность coding-задач с настоящим рукопожатием сайдкара; Telegram-поллер (второй не "
                  "запускается)", "запуск поллера — только с --telegram start"),
    "bootstrap": ("качает ТОЛЬКО отсутствующие профили через model_fetch fetch, затем verify; здоровые модели "
                  "не перекачиваются", "--allow-download и явный --profile ID"),
    "skills": ("каталог навыков Bossman доступен локальному ученику: список, выбор для задачи-багфикса, "
               "ни один навык не выдаёт прав (только чтение)", "—"),
    "compare": ("сравнение локальных моделей: те же 7 задач A–G, проверка исполнением (model_bakeoff.py), "
                "итог в compare/summary.json", "—"),
    "mvcr": ("готовит пакет MVCR (mvcr_prepare.py); ожидаемый итог WAIT_APPROVAL / PARTIAL_MISSING_DATA / "
             "BLOCKED; раннер НИКОГДА не подаёт", "подача — только владелец, вручную"),
    "self-improve": ("self_improve_lab.py: explore → compare → lesson, полный перезапуск Bossman, затем "
                     "transfer", "перезапуск Bossman; урок не вливается в продукт без владельца"),
    "report": ("русский отчёт report.md и report.json в папке прогона", "—"),
}


class StopRequested(Exception):
    """Владелец положил STOP: остановиться между шагами, не оставив сирот."""


# ---------------------------------------------------------------- helpers

def _sibling_dir(name: str) -> Path | None:
    for candidate in (HERE / name, REPO / "tools" / name):
        if candidate.is_dir():
            return candidate
    return None


def worst(statuses) -> str:
    items = [s for s in statuses if s in SEVERITY]
    return max(items, key=SEVERITY.__getitem__) if items else NOT_RUN


def _gb(n: int | None) -> str:
    return "не известно" if n is None else f"{n / 1024 ** 3:.1f} ГБ"


def _int(*values: Any) -> int | None:
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and v >= 0:
            return int(v)
    return None


def _json_from(text: str | None) -> Any:
    """JSON со stdout дочернего скрипта: весь вывод или последняя строка-объект."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def _child_status(code: int | None, payload: Any) -> tuple[str, str]:
    """(наш статус, сырой статус ребёнка). Код выхода не даёт выдать провал за успех."""
    raw = ""
    if isinstance(payload, dict):
        raw = str(payload.get("status") or payload.get("verdict") or "").strip().upper()
    if raw in KNOWN_STATUSES:
        if code not in (0, None) and raw in (PASS, WARN):
            return FAIL, f"{raw}, но код выхода {code}"
        return raw, raw
    if code == 0 and raw in ("OK", "DONE", "COMPLETED", "PASSED", "VERIFIED", "SUCCESS"):
        return PASS, raw
    if code == 0:
        return WARN, raw or ("без статуса" if payload is not None else "нет JSON на stdout")
    return FAIL, raw or ("таймаут" if code is None else f"код выхода {code}")


def _loopback_get(url: str, token: str | None = None, timeout: float = 5.0) -> tuple[int | None, Any]:
    """GET по loopback в обход прокси из окружения (он про локальный адрес только врёт)."""
    headers = {"Accept": "application/json"}
    if token:
        headers[TOKEN_HEADER] = token
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:  # noqa: S310
            body = resp.read(512_000).decode("utf-8", "replace")
            return resp.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace") or "null")
        except (ValueError, OSError):
            return exc.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


# ---------------------------------------------------------- kernel locks

def _lock_file(path: Path):
    """Неблокирующий замок ядра, как у Telegram-компаньона. None — замок занят."""
    fh = path.open("a+b")
    try:
        if fh.seek(0, 2) == 0:
            fh.write(b"0")
            fh.flush()
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _unlock_file(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    fh.close()


def lock_is_held(path: Path) -> bool:
    """Держит ли ЖИВОЙ процесс этот замок. Файл без держателя — устаревший, не живой."""
    if not path.is_file():
        return False
    fh = _lock_file(path)
    if fh is None:
        return True
    _unlock_file(fh)
    return False


# ------------------------------------------------------- process trees

def _psutil():
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:  # noqa: BLE001 — без psutil работают запасные пути
        return None


def _create_time(pid: int) -> float | None:
    ps = _psutil()
    if ps is None:
        return None
    try:
        return ps.Process(pid).create_time()
    except Exception:  # noqa: BLE001
        return None


def _same(proc, create_time: float | None) -> bool:
    try:
        return create_time is None or abs(proc.create_time() - create_time) <= 1.0
    except Exception:  # noqa: BLE001
        return False


def pid_alive(pid: int, create_time: float | None = None) -> bool:
    """Жив ли ИМЕННО этот процесс (pid плюс время создания против переиспользования pid)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    ps = _psutil()
    if ps is not None:
        try:
            proc = ps.Process(pid)
            return proc.status() != ps.STATUS_ZOMBIE and _same(proc, create_time)
        except Exception:  # noqa: BLE001
            return False
    if os.name == "nt":  # os.kill(pid, 0) на Windows убил бы процесс
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == 258
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def descendants(pid: int) -> list[dict]:
    ps = _psutil()
    if ps is None:
        return []
    try:
        return [{"pid": c.pid, "create_time": c.create_time()} for c in ps.Process(pid).children(recursive=True)]
    except Exception:  # noqa: BLE001
        return []


def kill_tree(pid: int, create_time: float | None = None, known: list[dict] | tuple = ()) -> list[int]:
    """Убить процесс, всех его потомков и ранее замеченных потомков. Вернуть убитые pid."""
    victims: set[int] = set()
    ps = _psutil()
    root_matches = True
    if ps is not None:
        procs = []
        try:
            root = ps.Process(pid)
            root_matches = _same(root, create_time)
            if root_matches:
                procs = [root] + root.children(recursive=True)
        except Exception:  # noqa: BLE001 — корень уже умер; потомков ищем по записям
            pass
        seen = {p.pid for p in procs}
        for entry in known or ():
            try:
                q = ps.Process(int(entry["pid"]))
                if q.pid not in seen and _same(q, entry.get("create_time")):
                    procs.append(q)
                    seen.add(q.pid)
            except Exception:  # noqa: BLE001
                continue
        for q in procs:
            try:
                if q.status() != ps.STATUS_ZOMBIE:
                    q.kill()
                    victims.add(q.pid)
            except Exception:  # noqa: BLE001
                continue
        try:
            ps.wait_procs(procs, timeout=10)
        except Exception:  # noqa: BLE001
            pass
    if os.name == "nt":
        if ps is None and pid_alive(pid):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)
            victims.add(pid)
    elif root_matches:
        import signal
        try:  # ребёнок запущен в своей сессии: группа = его pid, внуки в ней же
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return sorted(victims)


def _group_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


def spawn_detached(cmd: list[str], log_path: Path) -> subprocess.Popen:
    """Служба (поллер, Bossman): живёт дольше раннера, вывод — в файл."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = _group_kwargs()
    if os.name == "nt":
        kwargs["creationflags"] |= 0x00000008  # DETACHED_PROCESS
    with log_path.open("ab") as fh:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                close_fds=True, **kwargs)


# ------------------------------------------------------------ telegram

def telegram_home(config: str | None = None) -> Path:
    if config:
        return Path(config).expanduser().parent
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "Bossman" / "telegram-companion"


def telegram_poller_running(home: Path) -> bool:
    """Компаньон держит <home>/poller.lock замком ядра, пока жив (store.single_instance)."""
    return lock_is_held(home / "poller.lock")


def ensure_telegram_poller(home: Path, command: list[str], *, start: bool, log_path: Path,
                           wait_s: float = 20.0, require_config: bool = True) -> dict:
    """Никогда не второй поллер: живой замок — значит уже работает, запуск не делается."""
    if telegram_poller_running(home):
        return stage_result(PASS, "Telegram-поллер уже работает (poller.lock занят живым процессом) — второй "
                            "не запускаю", started=False, lock=str(home / "poller.lock"))
    stale = (home / "poller.lock").is_file()
    if not start:
        return stage_result(WARN, "Telegram-поллер не запущен" + (" (poller.lock без живого держателя — "
                            "устаревший)" if stale else ""),
                            route="запуск: добавить --telegram start (второй не поднимется: проверка замка)",
                            started=False, stale_lock=stale)
    if require_config and not (home / "config.json").is_file():
        return stage_result(OWNER_REQUIRED, f"нет настроек компаньона: {home / 'config.json'}",
                            route="scripts/Start-TelegramCompanion.ps1 -Mode setup (токен вводится локально)",
                            started=False)
    proc = spawn_detached(command, log_path)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if telegram_poller_running(home):
            return stage_result(PASS, f"Telegram-поллер запущен (pid {proc.pid})" + (
                "; устаревший poller.lock не мешал" if stale else ""), started=True, pid=proc.pid,
                create_time=_create_time(proc.pid), stale_lock=stale, log=str(log_path))
        if proc.poll() is not None:
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-400:]
            except OSError:
                pass
            if telegram_poller_running(home):  # другой экземпляр успел раньше
                return stage_result(PASS, "Telegram-поллер уже работает — наш экземпляр вышел сам",
                                    started=False)
            return stage_result(FAIL, f"поллер вышел с кодом {proc.returncode}: {tail.strip()[-300:]}",
                                route="приложить лог; проверить Start-TelegramCompanion.ps1 -Mode diagnose",
                                started=False, log=str(log_path))
        time.sleep(0.2)
    return stage_result(WARN, f"поллер запущен (pid {proc.pid}), но замок за {wait_s:.0f} с не занят",
                        route="смотреть лог поллера", started=True, pid=proc.pid, log=str(log_path))


# -------------------------------------------------------------- Bossman API

def _token(data_dir: Path) -> str | None:
    try:
        return (data_dir / "token").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def sidecar_handshake(ready: dict) -> tuple[bool, Any]:
    """Готовность считается доказанной только по записи о настоящем рукопожатии сайдкара."""
    for key in ("handshake", "sidecar_handshake", "sidecar_probe", "probe"):
        value = ready.get(key)
        if value is True:
            return True, {key: True}
        if isinstance(value, str) and value.strip().upper() in ("OK", "PASS", "READY", "COMPLETED"):
            return True, {key: value}
        if isinstance(value, dict):
            status = str(value.get("status") or "").strip().upper()
            if value.get("ok") is True or status in ("OK", "PASS", "READY", "COMPLETED"):
                return True, value
            return False, value
    return False, None


def bossman_identity(base_url: str, timeout: float = 5.0) -> dict | None:
    status, body = _loopback_get(base_url.rstrip("/") + "/api/identity", timeout=timeout)
    if status == 200 and isinstance(body, dict) and body.get("app") == APP_IDENTITY:
        return body
    return None


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def bossman_readiness(base_url: str, data_dir: Path) -> dict:
    base = base_url.rstrip("/")
    ident = bossman_identity(base)
    if ident is None:
        return stage_result(OWNER_REQUIRED, f"Bossman API не отвечает на {base}",
                            route="Start-Bossman.cmd, дождаться окна, затем повторить preflight")
    status, ready = _loopback_get(base + "/api/coding-tasks/readiness", _token(data_dir))
    if status in (401, 403):
        return stage_result(OWNER_REQUIRED, f"Bossman отказал в доступе ({status}): нет локального токена "
                            f"{data_dir / 'token'}", route="запустить раннер под тем же пользователем, что и Bossman; "
                            "или указать --data-dir", started_at=ident.get("started_at"))
    if status == 404:
        return stage_result(FAIL, "в этой сборке нет /api/coding-tasks/readiness",
                            route="дефект сборки: сообщить интегратору", started_at=ident.get("started_at"))
    if status != 200 or not isinstance(ready, dict):
        return stage_result(FAIL, f"readiness coding-задач ответил {status}", route="приложить логи Bossman",
                            started_at=ident.get("started_at"))
    ok, evidence = sidecar_handshake(ready)
    facts = {"started_at": ident.get("started_at"), "readiness": ready}
    if not ready.get("available"):
        return stage_result(OWNER_REQUIRED, "coding-задачи недоступны: " + str(ready.get("reason") or "без причины"),
                            route="настроить сайдкар (BOSSMAN_OPENHANDS_COMMAND) и перезапустить Bossman", **facts)
    if not ok:
        return stage_result(OWNER_REQUIRED, "Bossman говорит available, но настоящего рукопожатия сайдкара в ответе "
                            "нет — готовность не доказана", route="проверить сайдкар; нужна сборка, где readiness "
                            "делает handshake", handshake=evidence, **facts)
    return stage_result(PASS, "Bossman API отвечает; coding-задачи готовы, рукопожатие сайдкара прошло",
                        handshake=evidence, **facts)


# ------------------------------------------------------------ model_fetch

def _models_dir(args) -> Path:
    if args.models_dir:
        return Path(args.models_dir).expanduser()
    if os.environ.get("BOSSMAN_MODELS_DIR"):
        return Path(os.environ["BOSSMAN_MODELS_DIR"]).expanduser()
    return _data_dir(args.data_dir) / "models"


def _manifest(args) -> Path | None:
    if args.manifest:
        p = Path(args.manifest).expanduser()
        return p if p.is_file() else None
    return _sibling("model_profiles.json")


def fetch_cmd(args, sub: str, profiles: list[str] | None = None) -> list[str] | None:
    script, manifest = _sibling("model_fetch.py"), _manifest(args)
    if script is None or manifest is None:
        return None
    cmd = [_python(), "-I", str(script), sub, "--manifest", str(manifest), "--models-dir", str(_models_dir(args))]
    for p in (profiles if profiles is not None else (args.profile or [])):
        cmd += ["--profile", p]
    for r in args.reuse_dir or []:
        cmd += ["--reuse-dir", r]
    return cmd + ["--json"]


def fetch_missing_reason(args) -> str:
    missing = [n for n, p in (("model_fetch.py", _sibling("model_fetch.py")), ("model_profiles.json", _manifest(args)))
               if p is None]
    return "не найден: " + ", ".join(missing) + " (ни в app-support, ни в tools)"


def summarize_fetch_plan(payload: Any) -> dict:
    """Что model_fetch plan собирается сделать: переиспользовать, скачать или неясно."""
    items: list = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("profiles", "items", "entries", "plan", "models", "files"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    reuse, download, unknown = [], [], []
    for it in items:
        if not isinstance(it, dict):
            continue
        row = {"id": str(it.get("id") or it.get("profile") or it.get("name") or "?"),
               "status": str(it.get("status") or it.get("action") or it.get("state") or "").strip().upper(),
               "bytes": _int(it.get("download_bytes"), it.get("bytes_to_download"), it.get("bytes"),
                             it.get("size_bytes"), it.get("size"))}
        files = it.get("files")
        if isinstance(files, list) and files and all(isinstance(f, dict) and "state" in f for f in files):
            # The real model_fetch plan: the profile "status" is the MANIFEST
            # status (PINNED/UNPINNED/VERIFY_SOURCE_FIRST); the plan's verdict is
            # per file. Present files are never re-downloaded; an unpinned file
            # that is absent needs `pin` first (unknown size, owner decision).
            states = {str(f.get("state") or "").upper() for f in files}
            if states <= FILE_PRESENT_STATES:
                row["status"] = "REUSED"
            elif states & FILE_DOWNLOAD_STATES:
                row["status"] = "MISSING"
            else:
                row["status"] = "UNKNOWN:" + ",".join(sorted(states))
        if row["status"] in NO_DOWNLOAD:
            reuse.append(row)
        elif row["status"].startswith(NEEDS_DOWNLOAD_PREFIXES):
            download.append(row)
        else:
            unknown.append(row)
    top = payload if isinstance(payload, dict) else {}
    total = _int(top.get("download_bytes"), top.get("total_download_bytes"), top.get("bytes_to_download"),
                 top.get("required_bytes"))
    if total is None and download and all(r["bytes"] is not None for r in download):
        total = sum(r["bytes"] for r in download)
    if total is None and not download:
        total = 0
    free = _int(top.get("free_bytes"), top.get("disk_free_bytes"))
    return {"reuse": reuse, "download": download, "unknown": unknown, "download_bytes": total, "free_bytes": free}


def disk_free(path: Path) -> int | None:
    import shutil
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return None
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


# ------------------------------------------------------------------ run state

def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def load_state(run_dir: Path) -> dict | None:
    try:
        data = json.loads(_state_path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def step_keys() -> list[str]:
    keys: list[str] = []
    for stage in SI_RUN_STAGES:
        if stage == "self-improve":
            keys += [f"self-improve:{p}" for p in SI_PHASES]
        else:
            keys.append(stage)
    return keys


def next_command(state: dict | None) -> str:
    steps = (state or {}).get("steps", {})
    for key in step_keys():   # неизвестный исход важнее всего остального
        if (steps.get(key) or {}).get("status") == UNKNOWN_OUTCOME:
            return (f"Owner-Run.cmd self-improve-mvcr resume --redo {key}  (только после того, как вы "
                    "проверили, что прерванное действие не завершилось)")
    for key in step_keys():
        if key == "report":
            continue
        rec = steps.get(key)
        if not rec or rec.get("status") not in (PASS, WARN):
            stage = key.split(":")[0]
            hint = {"bootstrap": " --allow-download --profile <ID>",
                    "mvcr": " --owner-folder <папка> --facts <facts.json>"}.get(stage, "")
            return f"Owner-Run.cmd self-improve-mvcr {stage}{hint}"
    return "Owner-Run.cmd self-improve-mvcr report"


class ProfileRun:
    def __init__(self, args, run_dir: Path):
        self.args = args
        self.dir = run_dir
        self.stop_path = run_dir / "STOP"
        self.data_dir = _data_dir(args.data_dir)
        self.state = load_state(run_dir) or {"schema": "bossman.owner_run.self_improve.v1", "profile": PROFILE,
                                             "created_at": _now(), "steps": {}, "children": [],
                                             "services": [], "history": []}
        for key, default in (("steps", {}), ("children", []), ("services", []), ("history", [])):
            self.state.setdefault(key, default)
        self.current: str | None = None
        self.killed: list[dict] = []

    # --- persistence
    def save(self) -> None:
        self.state["updated_at"] = _now()
        _write_json(_state_path(self.dir), self.state)

    def log(self, message: str) -> None:
        print(f"[{PROFILE}] {message}", flush=True)

    def begin(self, action: str) -> None:
        if self.stop_path.exists():
            self.log("найден STOP от прошлой остановки — снимаю его, раз вы запустили прогон снова")
            self.stop_path.unlink()
        recovered = []
        for child in self.state.get("children", []):
            killed = kill_tree(int(child["pid"]), child.get("create_time"), child.get("descendants") or [])
            recovered.append({**child, "killed": killed})
        if recovered:
            self.state.setdefault("recovered_orphans", []).extend(recovered)
            self.log(f"прошлый раннер умер, не убрав детей: добито {sum(len(r['killed']) for r in recovered)} процессов")
        self.state["children"] = []
        self.state["runner"] = {"pid": os.getpid(), "started_at": _now(), "action": action,
                                "runner": str(Path(__file__).resolve())}
        self.state.pop("stopped", None)
        self.save()

    # --- stop and children
    def check_stop(self) -> None:
        if self.stop_path.exists():
            raise StopRequested(self.current or "между шагами")

    def mark_attempted(self) -> None:
        if self.current:
            self.state["steps"][self.current]["action_attempted"] = True
            self.save()

    def spawn(self, cmd: list[str], *, timeout: float, label: str = "") -> tuple[int | None, str, str]:
        """Дочерний процесс в своей группе; STOP и таймаут убивают всё его дерево."""
        self.check_stop()
        logs = self.dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{self.current or 'step'}-{label or Path(cmd[min(2, len(cmd) - 1)]).stem}")
        n = sum(1 for _ in logs.glob(stem + ".*.out")) + 1
        out_p, err_p = logs / f"{stem}.{n}.out", logs / f"{stem}.{n}.err"
        self.mark_attempted()
        with out_p.open("wb") as fo, err_p.open("wb") as fe:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fo, stderr=fe, **_group_kwargs())
        entry = {"pid": proc.pid, "create_time": _create_time(proc.pid), "step": self.current, "cmd": cmd,
                 "started_at": _now(), "descendants": [], "stdout": str(out_p)}
        self.state["children"].append(entry)
        self.save()
        deadline = time.monotonic() + timeout
        last_scan = 0.0
        code: int | None = None
        try:
            while True:
                code = proc.poll()
                if code is not None:
                    break
                if self.stop_path.exists():
                    killed = kill_tree(proc.pid, entry["create_time"], entry["descendants"])
                    proc.wait(timeout=30)
                    self.killed.append({"step": self.current, "pid": proc.pid, "cmd": cmd, "killed": killed,
                                        "at": _now()})
                    raise StopRequested(self.current or "")
                if time.monotonic() > deadline:
                    kill_tree(proc.pid, entry["create_time"], entry["descendants"])
                    proc.wait(timeout=30)
                    code = None
                    break
                if time.monotonic() - last_scan > 1.0:
                    last_scan = time.monotonic()
                    seen = {d["pid"] for d in entry["descendants"]}
                    fresh = [d for d in descendants(proc.pid) if d["pid"] not in seen]
                    if fresh:
                        entry["descendants"].extend(fresh)
                        self.save()
                time.sleep(0.2)
        finally:
            if proc.poll() is None:
                kill_tree(proc.pid, entry["create_time"], entry["descendants"])
                proc.wait(timeout=30)
            self.state["children"] = [c for c in self.state["children"] if c is not entry and c.get("pid") != proc.pid]
            self.save()
        out = out_p.read_text(encoding="utf-8", errors="replace")
        err = err_p.read_text(encoding="utf-8", errors="replace")
        if code is None:
            err += f"\ntimeout after {timeout}s"
        return code, out, err

    def add_service(self, name: str, result: dict) -> None:
        facts = result.get("facts") or {}
        if facts.get("pid"):
            self.state["services"].append({"name": name, "pid": facts["pid"], "create_time": facts.get("create_time"),
                                           "started_at": _now(), "step": self.current})
            self.save()

    # --- steps
    def run_step(self, key: str, fn, *, force: bool = False) -> dict:
        steps = self.state["steps"]
        rec = steps.get(key)
        idem = key in IDEMPOTENT
        redo = key in (self.args.redo or [])
        if rec and not redo:
            attempted = bool(rec.get("action_attempted"))
            if rec.get("phase") == "running" and not idem and attempted:
                rec.update(status=UNKNOWN_OUTCOME, phase="done", finished_at=_now(),
                           detail="шаг прерван посередине, а он не повторяемый: результат внешнего действия "
                                  "неизвестен" + (f" (остановлен: {rec['interrupted']})" if rec.get("interrupted") else ""),
                           route=f"проверить вручную (логи в {self.dir / 'logs'}); повторить только осознанно: "
                                 f"resume --redo {key}")
                self.state["history"].append({"step": key, "event": "unknown_outcome", "at": _now()})
                self.save()
                self.log(f"{key}: {UNKNOWN_OUTCOME} — не повторяю сам")
                return rec
            if rec.get("phase") == "done" and not idem and attempted:
                self.state["history"].append({"step": key, "event": "skipped_done", "at": _now()})
                self.save()
                self.log(f"{key}: уже выполнялось ({rec.get('status')}) — внешнее действие не повторяю")
                return rec
            if rec.get("phase") == "done" and idem and not force and rec.get("status") in (PASS, WARN):
                self.state["history"].append({"step": key, "event": "skipped_done", "at": _now()})
                self.save()
                self.log(f"{key}: уже {rec.get('status')} — пропускаю")
                return rec
        self.check_stop()
        attempt = int((rec or {}).get("attempt", 0)) + 1
        rec = {"phase": "running", "started_at": _now(), "action_attempted": False, "idempotent": idem,
               "attempt": attempt}
        if redo:
            rec["redo"] = True
        steps[key] = rec
        self.current = key
        self.state["current"] = key
        self.state["history"].append({"step": key, "event": "started", "at": _now()})
        self.save()
        self.log(f"{key} …")
        try:
            result = fn()
        except StopRequested:
            rec["interrupted"] = "STOP"
            self.save()
            raise
        except Exception as exc:  # noqa: BLE001 — шаг не роняет весь прогон
            result = stage_result(FAIL, f"шаг упал: {type(exc).__name__}: {exc}",
                                  route="дефект раннера: приложить вывод консоли и state.json")
        rec.update(result)
        rec["phase"] = "done"
        rec["finished_at"] = _now()
        self.current = None
        self.state["current"] = None
        self.state["history"].append({"step": key, "event": "finished", "status": rec["status"], "at": _now()})
        self.save()
        self.log(f"{key}: {rec['status']} — {rec['detail'][:240]}" + (f" → {rec['route']}" if rec.get("route") else ""))
        return rec

    def stage(self, stage: str, *, force: bool = False) -> None:
        if stage == "self-improve":
            for phase in SI_PHASES:
                self.run_step(f"self-improve:{phase}", lambda phase=phase: si_phase(self, phase), force=force)
            return
        self.run_step(stage, lambda: STAGE_FUNCS[stage](self), force=force)

    def on_stop(self, where: str) -> dict:
        info = {"at": _now(), "step": where, "killed": self.killed,
                "services_left_running": [s for s in self.state.get("services", [])
                                          if pid_alive(int(s["pid"]), s.get("create_time"))]}
        self.state["stopped"] = info
        self.state["current"] = None
        self.save()
        _write_json(self.dir / "stop-report.json", {"runner_acknowledged": True, **info})
        self.log(f"STOP: остановлено на «{where}»; убито процессов: "
                 f"{sum(len(k['killed']) for k in self.killed)}; отчёт: {self.dir / 'stop-report.json'}")
        return info


# ------------------------------------------------------------------ stages

def si_preflight(run: ProfileRun) -> dict:
    args = run.args
    checks: dict[str, dict] = {}
    checks["doctor"] = stage_doctor(run.dir, args)
    checks["models"] = stage_models(run.dir, args)
    cmd = fetch_cmd(args, "plan")
    if cmd is None:
        checks["model_reuse"] = stage_result(NOT_RUN, "model_fetch: " + fetch_missing_reason(args),
                                             route="дождаться сборки, где model_fetch.py едет в app-support")
    else:
        code, out, err = run.spawn(cmd, timeout=600, label="model_fetch-plan")
        summary = summarize_fetch_plan(_json_from(out))
        if code != 0 or _json_from(out) is None:
            checks["model_reuse"] = stage_result(FAIL, f"model_fetch plan: код {code}: {(err or out).strip()[-300:]}",
                                                 route="приложить logs/ из папки прогона")
        else:
            checks["model_reuse"] = stage_result(
                WARN if summary["download"] or summary["unknown"] else PASS,
                f"переиспользуется {len(summary['reuse'])}, скачать {len(summary['download'])} "
                f"({_gb(summary['download_bytes'])}), неясно {len(summary['unknown'])}",
                route="bootstrap --allow-download --profile <ID>" if summary["download"] else "", plan=summary)
        rt = fetch_cmd(args, "runtime-check")
        code, out, err = run.spawn(rt, timeout=300, label="model_fetch-runtime")
        checks["model_runtime"] = stage_result(PASS if code == 0 else WARN,
                                               f"model_fetch runtime-check: код {code}",
                                               route="" if code == 0 else "см. logs/*model_fetch-runtime*",
                                               payload=_json_from(out))
    checks["bossman"] = bossman_readiness(args.base_url, run.data_dir)
    home = telegram_home(args.telegram_config)
    command = [_python(), "-I", "-m", "bcc.telegram_companion", "--config", str(home / "config.json")]
    tg = ensure_telegram_poller(home, command, start=args.telegram == "start",
                                log_path=run.dir / "logs" / "telegram-companion.log")
    if (tg.get("facts") or {}).get("started"):
        run.mark_attempted()
        run.add_service("telegram-companion", tg)
    checks["telegram"] = tg
    status = worst(c["status"] for c in checks.values())
    bad = [(k, c) for k, c in checks.items() if c["status"] not in (PASS,)]
    return stage_result(status, "; ".join(f"{k}: {c['status']}" for k, c in checks.items()),
                        route=" | ".join(f"{k}: {c['route']}" for k, c in bad if c.get("route"))[:900],
                        checks=checks)


def si_bootstrap(run: ProfileRun) -> dict:
    args = run.args
    cmd = fetch_cmd(args, "plan")
    if cmd is None:
        return stage_result(NOT_RUN, "model_fetch: " + fetch_missing_reason(args),
                            route="дождаться сборки, где model_fetch.py и model_profiles.json едут в app-support")
    code, out, err = run.spawn(cmd, timeout=600, label="model_fetch-plan")
    payload = _json_from(out)
    if code != 0 or payload is None:
        return stage_result(FAIL, f"model_fetch plan: код {code}: {(err or out).strip()[-300:]}",
                            route="приложить logs/ из папки прогона")
    summary = summarize_fetch_plan(payload)
    wanted = summary["download"]
    if args.profile:
        wanted = [r for r in wanted if r["id"] in args.profile]
    reused = [r["id"] for r in summary["reuse"]]
    if not wanted:
        return stage_result(WARN if summary["unknown"] else PASS,
                            "ничего не качаю: все нужные модели на месте" + (f" (REUSED: {', '.join(reused)})" if reused else "")
                            + (f"; неясный статус: {', '.join(r['id'] + '=' + r['status'] for r in summary['unknown'])}"
                               if summary["unknown"] else ""),
                            route="неясные профили проверить: model_fetch.py verify" if summary["unknown"] else "",
                            downloaded=[], reused=reused, plan=summary)
    ids = [r["id"] for r in wanted]
    if not args.allow_download:
        return stage_result(OWNER_REQUIRED, f"нужно скачать {', '.join(ids)} ({_gb(summary['download_bytes'])}) — "
                            "без разрешения не качаю", route="bootstrap --allow-download "
                            + " ".join(f"--profile {i}" for i in ids), to_download=ids, reused=reused, plan=summary)
    if not args.profile:
        return stage_result(OWNER_REQUIRED, "разрешение есть, но профили не названы: качаю только явно названное",
                            route="bootstrap --allow-download " + " ".join(f"--profile {i}" for i in ids),
                            to_download=ids, plan=summary)
    need = sum(r["bytes"] or 0 for r in wanted) if all(r["bytes"] is not None for r in wanted) else None
    free = summary["free_bytes"] if summary["free_bytes"] is not None else disk_free(_models_dir(args))
    if need is not None and free is not None and need > free:
        return stage_result(BLOCKED, f"не хватает места: нужно {_gb(need)}, свободно {_gb(free)}",
                            route="освободить место или указать другой --models-dir", need=need, free=free)
    code, out, err = run.spawn(fetch_cmd(args, "fetch", ids), timeout=args.fetch_timeout, label="model_fetch-fetch")
    if code != 0:
        return stage_result(FAIL, f"model_fetch fetch: код {code}: {(err or out).strip()[-300:]}",
                            route="повторить bootstrap — докачка идемпотентна; при повторе приложить logs/",
                            fetch=_json_from(out))
    code, vout, verr = run.spawn(fetch_cmd(args, "verify", ids), timeout=3 * 3600, label="model_fetch-verify")
    if code != 0:
        return stage_result(FAIL, f"скачано, но verify не прошёл (код {code}): {(verr or vout).strip()[-300:]}",
                            route="повторить bootstrap; BAD_HASH перекачивается", fetch=_json_from(out),
                            verify=_json_from(vout))
    return stage_result(PASS, f"скачано и сверено: {', '.join(ids)}" + (f"; переиспользовано: {', '.join(reused)}"
                        if reused else ""), downloaded=ids, reused=reused, fetch=_json_from(out), verify=_json_from(vout))


def _endpoint_tag(endpoint: str, model: str) -> str:
    port = re.search(r":(\d+)", endpoint.split("//", 1)[-1])
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{port.group(1) if port else 'ep'}-{model}")[:80].strip("-")


def si_compare(run: ProfileRun) -> dict:
    args = run.args
    tool = _sibling("model_bakeoff.py")
    if tool is None:
        return stage_result(NOT_RUN, "model_bakeoff.py не найден рядом с раннером", route="дефект упаковки")
    endpoints = args.compare_endpoint or [e.strip() for e in (args.endpoints or os.environ.get(ENDPOINTS_ENV)
                                                                or DEFAULT_ENDPOINTS).split(",") if e.strip()]
    probes = [probe_endpoint(e, completion=False) for e in endpoints]
    live = [p for p in probes if p["reachable"] and p["models"]]
    if not live:
        return stage_result(OWNER_REQUIRED, "нет живых эндпоинтов для сравнения: " + ", ".join(endpoints),
                            route="запустить llama-server (MAIN/FAST/...) и повторить compare")
    target = run.dir / "compare"
    rows = []
    for probe in live:
        base = probe["endpoint"] if probe["endpoint"].endswith("/v1") else probe["endpoint"] + "/v1"
        model = probe["models"][0]
        tag = _endpoint_tag(probe["endpoint"], model)
        code, out, err = run.spawn([_python(), "-I", str(tool), "--url", base, "--tag", tag, "--out", str(target),
                                    "--model", model, "--json"], timeout=args.compare_timeout, label=f"bakeoff-{tag}")
        brief = _json_from(out)
        row = {"endpoint": probe["endpoint"], "model": model, "tag": tag, "exit": code}
        if isinstance(brief, dict) and brief.get("passed") is not None:
            row.update({k: brief.get(k) for k in ("passed", "total", "seconds", "gen_tps_median", "failed")})
        else:
            row["error"] = (err or out).strip()[-300:]
        rows.append(row)
    summary = {"schema": "bossman.owner_run.compare.v1", "at": _now(), "rows": rows,
               "note": "одинаковые 7 задач A–G, проверка исполнением; модель не выбирается автоматически",
               "weights_unchanged": True}
    target.mkdir(parents=True, exist_ok=True)
    _write_json(target / "summary.json", summary)
    ran = [r for r in rows if r.get("passed") is not None]
    detail = "; ".join(f"{r['tag']}: {r['passed']}/{r['total']} за {r['seconds']} с" if r.get("passed") is not None
                       else f"{r['tag']}: харнесс не отработал" for r in rows)
    status = FAIL if not ran else (WARN if len(ran) < len(rows) else PASS)
    return stage_result(status, detail, route="" if status == PASS else "см. logs/*bakeoff*",
                        summary=str(target / "summary.json"), rows=rows)


def _claims_submission(payload: dict) -> bool:
    raw = str(payload.get("status") or "").upper()
    return payload.get("submitted") is True or raw in ("SUBMITTED", "SENT", "FILED")


def si_mvcr(run: ProfileRun) -> dict:
    args = run.args
    script = _sibling("mvcr_prepare.py")
    if script is None:
        return stage_result(NOT_RUN, "mvcr_prepare.py не найден (ни в app-support, ни в tools)",
                            route="дождаться сборки, где mvcr_prepare.py едет в app-support")
    if not args.owner_folder or not args.facts:
        return stage_result(OWNER_REQUIRED, "не указаны --owner-folder и/или --facts",
                            route="mvcr --owner-folder <папка с документами> --facts <facts.json>")
    folder, facts = Path(args.owner_folder).expanduser(), Path(args.facts).expanduser()
    if not folder.is_dir() or not facts.is_file():
        return stage_result(OWNER_REQUIRED, f"нет папки {folder} или файла {facts}",
                            route="проверить пути; файлы остаются у владельца, раннер их не меняет")
    target = run.dir / "mvcr"
    code, out, err = run.spawn([_python(), "-I", str(script), "--owner-folder", str(folder), "--facts", str(facts),
                                "--out", str(target), "--json"], timeout=args.mvcr_timeout, label="mvcr_prepare")
    payload = _json_from(out)
    if not isinstance(payload, dict):
        return stage_result(FAIL, f"mvcr_prepare не выдал JSON (код {code}): {(err or out).strip()[-300:]}",
                            route="приложить logs/*mvcr*")
    raw = str(payload.get("status") or payload.get("final_status") or payload.get("verdict") or "").upper()
    if _claims_submission(payload):
        return stage_result(FAIL, f"mvcr_prepare сообщает о подаче ({raw}) — этого раннер не разрешает",
                            route="немедленно сообщить интегратору", mvcr=payload)
    if raw not in MVCR_FINAL:
        return stage_result(FAIL, f"неожиданный итог mvcr_prepare: {raw or 'пусто'} (код {code})",
                            route="ожидались WAIT_APPROVAL / PARTIAL_MISSING_DATA / BLOCKED", mvcr=payload)
    route = {"WAIT_APPROVAL": f"пакет готов в {target}: проверить и подать ВРУЧНУЮ (раннер не подаёт)",
             "PARTIAL_MISSING_DATA": "дополнить facts.json/папку недостающим и повторить mvcr",
             "BLOCKED": "причина в mvcr/ и logs/; устранить и повторить mvcr"}[raw]
    missing = payload.get("missing") or payload.get("missing_data") or []
    return stage_result(MVCR_FINAL[raw], f"MVCR: {raw}" + (f"; не хватает: {', '.join(map(str, missing))[:300]}"
                        if missing else ""), route=route, mvcr_status=raw, out=str(target), submitted=False)


def _case(args) -> Path | None:
    if args.case:
        p = Path(args.case).expanduser()
        return p if p.is_file() else None
    # No explicit case: the lab's built-in `sample` (its seeded repo and hidden
    # verifier are generated by the lab itself; transfer uses `sample-transfer`).
    return None


def si_phase(run: ProfileRun, phase: str) -> dict:
    args = run.args
    script = _sibling("self_improve_lab.py")
    if script is None:
        return stage_result(NOT_RUN, "self_improve_lab.py не найден (ни в app-support, ни в tools)",
                            route="дождаться сборки, где self_improve_lab.py едет в app-support")
    steps = run.state["steps"]
    for prev in SI_PHASES[:SI_PHASES.index(phase)]:
        prev_status = (steps.get(f"self-improve:{prev}") or {}).get("status")
        if prev == "explore" and prev_status == NOT_RUN:
            continue            # free exploration is optional; the fixed comparison does not depend on it
        if prev_status not in (PASS, WARN):
            return stage_result(BLOCKED, f"предыдущая фаза {prev}: {prev_status or 'не выполнялась'}",
                                route=f"сначала довести {prev}")
    if phase == "restart":
        return si_restart(run)
    if phase == "explore" and not args.explore_repo:
        return stage_result(NOT_RUN, "свободная разведка пропущена: не указан --explore-repo",
                            route="по желанию: --explore-repo <git-репозиторий внутри разрешённых корней>")
    ready = bossman_readiness(args.base_url, run.data_dir)
    if ready["status"] != PASS:
        return stage_result(BLOCKED, "Bossman/coding-задачи не готовы: " + ready["detail"], route=ready.get("route", ""))
    work = Path(args.lab_work_dir).expanduser() if args.lab_work_dir else run.dir / "lab-work"
    roots = [Path(r) for r in (((ready.get("facts") or {}).get("readiness") or {}).get("roots") or [])]
    if not any(_path_within(work, r) for r in roots):
        return stage_result(OWNER_REQUIRED, f"рабочая папка лаборатории {work} вне разрешённых корней coding "
                            f"({', '.join(map(str, roots)) or 'нет'})",
                            route="добавить папку в разрешённые корни (Настройки → терминал/код) или указать "
                                  "--lab-work-dir внутри корня, затем resume")
    work.mkdir(parents=True, exist_ok=True)
    cmd = [_python(), "-I", str(script), phase, "--base-url", args.base_url, "--data-dir", str(run.data_dir),
           "--out", str(run.dir / "self-improve"), "--work-dir", str(work), "--json"]
    case_arg = ""
    if phase == "explore":
        cmd += ["--repo", str(Path(args.explore_repo).expanduser())]
    else:
        case = _case(args) if phase != "transfer" else None
        case_arg = (str(case) if case else "sample") if phase != "transfer" else (args.transfer_case or "sample-transfer")
        cmd += ["--case", case_arg]
        if phase == "compare":
            cmd += ["--budget-minutes", str(args.budget_minutes)]
    code, out, err = run.spawn(cmd, timeout=args.budget_minutes * 60 * 6 + 900, label=f"lab-{phase}")
    payload = _json_from(out)
    status, raw = _child_status(code, payload)
    summary = ""
    if isinstance(payload, dict):
        summary = str(payload.get("summary") or payload.get("detail") or "")[:300]
    return stage_result(status, f"{phase}: {raw}" + (f" — {summary}" if summary else ""),
                        route="" if status in (PASS, WARN) else f"см. self-improve/ и logs/*lab-{phase}*",
                        case=case_arg, result=payload if isinstance(payload, dict) else None)


def _desktop_pid(data_dir: Path) -> int | None:
    try:
        data = json.loads((data_dir / "desktop.lock").read_text(encoding="utf-8"))
        return int(data.get("pid") or 0) or None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def si_restart(run: ProfileRun) -> dict:
    """Полный перезапуск Bossman; успех доказывается сменой started_at в /api/identity."""
    args = run.args
    marker = run.state.get("restart_marker") or {}
    ident = bossman_identity(args.base_url)
    if ident and marker.get("started_at") and ident.get("started_at") != marker["started_at"]:
        return stage_result(PASS, "перезапуск уже состоялся: started_at изменился", started_at=ident.get("started_at"),
                            before=marker["started_at"])
    if ident is None:
        if marker:
            return stage_result(OWNER_REQUIRED, "Bossman остановлен и не поднялся",
                                route="Start-Bossman.cmd, затем Owner-Run.cmd self-improve-mvcr resume")
        return stage_result(BLOCKED, "Bossman API не отвечает — перезапускать нечего",
                            route="Start-Bossman.cmd, затем resume")
    pid = _desktop_pid(run.data_dir)
    if not pid or not pid_alive(pid):
        run.state["restart_marker"] = {"started_at": ident.get("started_at"), "at": _now()}
        run.save()
        return stage_result(OWNER_REQUIRED, "не нашёл процесс Bossman (desktop.lock) — сам не перезапускаю",
                            route="закрыть Bossman, запустить Start-Bossman.cmd, затем "
                                  "Owner-Run.cmd self-improve-mvcr resume (перезапуск засчитается по started_at)")
    run.state["restart_marker"] = {"started_at": ident.get("started_at"), "at": _now(), "pid": pid}
    run.mark_attempted()
    telegram_before = telegram_poller_running(telegram_home(args.telegram_config))
    killed = kill_tree(pid)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and bossman_identity(args.base_url, timeout=2) is not None:
        time.sleep(0.5)
    start_cmd = json.loads(args.bossman_start_cmd) if args.bossman_start_cmd else [_python(), "-m", "bcc.desktop"]
    proc = spawn_detached(start_cmd, run.dir / "logs" / "bossman-restart.log")
    run.state["services"].append({"name": "bossman", "pid": proc.pid, "create_time": _create_time(proc.pid),
                                  "started_at": _now(), "step": run.current})
    run.save()
    deadline = time.monotonic() + args.restart_timeout
    while time.monotonic() < deadline:
        run.check_stop()
        now = bossman_identity(args.base_url, timeout=2)
        if now and now.get("started_at") != ident.get("started_at"):
            ready = bossman_readiness(args.base_url, run.data_dir)
            telegram_after = telegram_poller_running(telegram_home(args.telegram_config))
            return stage_result(PASS if ready["status"] == PASS else WARN,
                                f"Bossman перезапущен (было {ident.get('started_at')}, стало {now.get('started_at')}); "
                                f"coding-задачи: {ready['status']}", route=ready.get("route", ""), killed=killed,
                                telegram_before=telegram_before, telegram_after=telegram_after)
        time.sleep(1.0)
    return stage_result(OWNER_REQUIRED, f"Bossman остановлен, но за {args.restart_timeout:.0f} с не поднялся",
                        route="Start-Bossman.cmd вручную, затем resume; лог: logs/bossman-restart.log", killed=killed)


SKILLS_PROBE_TASK = "Fix the bug: a function returns a wrong result; reproduce it with a failing test first"


def si_skills(run: ProfileRun) -> dict:
    """Read-only: the vetted skill catalog is reachable in THIS Bossman and the
    selection for a bug-fix task returns guidance that grants nothing."""
    args = run.args
    base = args.base_url.rstrip("/")
    if bossman_identity(base) is None:
        return stage_result(OWNER_REQUIRED, f"Bossman API не отвечает на {base}",
                            route="Start-Bossman.cmd, затем Owner-Run.cmd self-improve-mvcr resume")
    token = _token(run.data_dir)
    status, catalog = _loopback_get(base + "/api/skill-catalog", token)
    if status in (401, 403):
        return stage_result(OWNER_REQUIRED, f"Bossman отказал в доступе ({status})", route="указать --data-dir")
    if status == 404:
        return stage_result(FAIL, "в этой сборке нет /api/skill-catalog", route="дефект сборки: сообщить интегратору")
    if status != 200 or not isinstance(catalog, list):
        return stage_result(FAIL, f"каталог навыков ответил {status}", route="приложить логи Bossman")
    active = [c for c in catalog if isinstance(c, dict) and str(c.get("status", "")).upper() not in
              ("QUARANTINED", "REVOKED")]
    if not active:
        return stage_result(FAIL, f"каталог навыков пуст ({len(catalog)} записей, активных 0)",
                            route="дефект сборки: каталог не попал в архив")
    from urllib.parse import quote
    status, chosen = _loopback_get(base + "/api/skill-catalog/select?q=" + quote(SKILLS_PROBE_TASK), token)
    if status != 200 or not isinstance(chosen, list) or not chosen:
        return stage_result(FAIL, f"выбор навыков для багфикса пуст или ошибка ({status})",
                            route="приложить логи Bossman", catalog=len(catalog))
    granting = [c.get("id") for c in chosen if (c.get("grants") or {}).get("tools") or
                (c.get("grants") or {}).get("permissions")]
    if granting:
        return stage_result(FAIL, "навык выдаёт права: " + ", ".join(map(str, granting)),
                            route="немедленно сообщить интегратору")
    ids = [str(c.get("id")) for c in chosen]
    return stage_result(PASS, f"навыков в каталоге: {len(active)}; для багфикса выбраны: {', '.join(ids)}",
                        catalog=[c.get("id") for c in active], selected=ids)


def si_report_stage(run: ProfileRun) -> dict:
    report = write_profile_report(run.dir, run.state)
    return stage_result(PASS, f"report.md и report.json записаны; итог прогона: {report['overall']}",
                        overall=report["overall"])


STAGE_FUNCS = {"preflight": si_preflight, "bootstrap": si_bootstrap, "skills": si_skills, "compare": si_compare,
               "mvcr": si_mvcr,
               "report": si_report_stage}


# ------------------------------------------------------------------ report

def profile_overall(state: dict) -> str:
    steps = state.get("steps", {})
    return worst([(steps.get(k) or {}).get("status", NOT_RUN) for k in step_keys() if k != "report"])


def write_profile_report(run_dir: Path, state: dict) -> dict:
    steps = state.get("steps", {})
    rows = []
    for key in step_keys():
        rec = steps.get(key) or {}
        rows.append({"step": key, "status": rec.get("status", NOT_RUN),
                     "detail": rec.get("detail", "ещё не запускался"), "route": rec.get("route", ""),
                     "finished_at": rec.get("finished_at")})
    report = {"schema": "bossman.owner_run.self_improve_report.v1", "profile": PROFILE, "generated_at": _now(),
              "run_dir": str(run_dir), "overall": profile_overall(state), "rows": rows,
              "stopped": state.get("stopped"), "services": state.get("services", []),
              "next_command": next_command(state), "weights_unchanged": True, "submitted_anything": False,
              "owner_hardware_certified": False,
              "verified_on": "автотесты с детерминированной тестовой моделью; не на железе владельца"}
    _write_json(run_dir / "report.json", report)
    lines = [f"# Owner-Run self-improve-mvcr — {report['generated_at']}", "",
             f"Итог: **{report['overall']}**. Команда подготовлена в облаке и проверена только автотестами с "
             "детерминированной тестовой моделью — НЕ на железе владельца. OWNER_HARDWARE_CERTIFIED не объявляется. "
             "Раннер ничего не подаёт (MVCR подаёт только владелец) и не меняет веса моделей.", "",
             "| шаг | статус | что видно | куда дальше |", "|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['step']} | {r['status']} | {str(r['detail']).replace('|', '/')[:400]} | "
                     f"{(r['route'] or '—').replace('|', '/')} |")
    if state.get("stopped"):
        st = state["stopped"]
        lines += ["", f"**Остановлено по STOP** {st.get('at')} на шаге «{st.get('step')}»; убито процессов: "
                      f"{sum(len(k.get('killed') or []) for k in st.get('killed') or [])}."]
    if any(r["status"] == UNKNOWN_OUTCOME for r in rows):
        lines += ["", "UNKNOWN_OUTCOME: шаг с внешним действием был прерван посередине. Раннер его НЕ повторяет. "
                      "Проверьте результат вручную и только потом: `Owner-Run.cmd self-improve-mvcr resume --redo <шаг>`."]
    services = [s for s in state.get("services", []) if pid_alive(int(s["pid"]), s.get("create_time"))]
    if services:
        lines += ["", "Оставлены работать (службы): " + ", ".join(f"{s['name']} pid {s['pid']}" for s in services)]
    lines += ["", f"Следующая команда: `{report['next_command']}`", f"Папка прогона: `{run_dir}`",
              "WEIGHTS_UNCHANGED"]
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


# ------------------------------------------------------------------- plan

def profile_plan(args, run_dir: Path) -> dict:
    """Только чтение: ничего не создаёт, не качает и не запускает, кроме `model_fetch plan`."""
    siblings = {name: (str(p) if (p := _sibling(name)) else None)
                for name in ("model_fetch.py", "model_profiles.json", "model_bakeoff.py", "mvcr_prepare.py",
                             "self_improve_lab.py", "bossman_doctor.py")}
    if args.manifest:
        siblings["model_profiles.json"] = str(_manifest(args)) if _manifest(args) else None
    cases = _case(args)
    fetch: dict[str, Any] = {"status": NOT_RUN}
    cmd = fetch_cmd(args, "plan")
    if cmd is None:
        fetch["reason"] = fetch_missing_reason(args)
    else:
        code, out, err = _run(cmd, timeout=600)
        payload = _json_from(out)
        if code == 0 and payload is not None:
            fetch = {"status": PASS, "summary": summarize_fetch_plan(payload)}
        else:
            fetch = {"status": FAIL, "reason": f"код {code}: {(err or out).strip()[-300:]}"}
    models_dir = _models_dir(args)
    need = (fetch.get("summary") or {}).get("download_bytes")
    free = (fetch.get("summary") or {}).get("free_bytes")
    if free is None:
        free = disk_free(models_dir)
    state = load_state(run_dir)
    done = {k: v.get("status") for k, v in ((state or {}).get("steps") or {}).items()}
    approvals = [f"{s}: {STAGE_TEXT[s][1]}" for s in SI_STAGES if STAGE_TEXT[s][1] != "—"]
    return {"profile": PROFILE, "stages": [{"stage": s, "does": STAGE_TEXT[s][0], "approval": STAGE_TEXT[s][1]}
                                           for s in SI_STAGES],
            "self_improve_phases": list(SI_PHASES), "fetch_plan": fetch, "models_dir": str(models_dir),
            "disk": {"need_bytes": need, "free_bytes": free,
                     "enough": None if need is None or free is None else need <= free},
            "siblings": siblings, "case": str(cases) if cases else None, "run_dir": str(run_dir),
            "state": done, "approvals": approvals, "next_command": next_command(state),
            "telegram_poller_running": telegram_poller_running(telegram_home(args.telegram_config)),
            "side_effects": "none"}


def print_plan(plan: dict) -> None:
    print(f"[{PROFILE}] ПЛАН — ничего не меняется, ничего не скачивается.")
    print("Стадии по порядку:")
    for i, s in enumerate(plan["stages"], 1):
        print(f"  {i}. {s['stage']:<12} {s['does']}" + (f"\n{'':17}разрешение: {s['approval']}"
                                                          if s["approval"] != "—" else ""))
    print("  фазы self-improve: " + " → ".join(plan["self_improve_phases"]))
    fp = plan["fetch_plan"]
    if fp["status"] == PASS:
        sm = fp["summary"]
        print("Модели (model_fetch plan): переиспользуются: " + (", ".join(r["id"] for r in sm["reuse"]) or "—"))
        print("  будет скачано (только с --allow-download --profile): "
              + (", ".join(f"{r['id']} ({_gb(r['bytes'])})" for r in sm["download"]) or "ничего"))
        if sm["unknown"]:
            print("  неясный статус: " + ", ".join(f"{r['id']}={r['status']}" for r in sm["unknown"]))
    else:
        print(f"Модели: план не получен ({fp['status']}): {fp.get('reason')}")
    d = plan["disk"]
    print(f"Диск ({plan['models_dir']}): нужно {_gb(d['need_bytes'])}, свободно {_gb(d['free_bytes'])}"
          + ("" if d["enough"] is None else ("" if d["enough"] else " — НЕ ХВАТАЕТ")))
    print("Скрипты рядом с раннером:")
    for name, path in plan["siblings"].items():
        print(f"  {name:<22} {'есть' if path else 'НЕТ → стадия будет NOT_RUN'}")
    print(f"Кейс self-improve: {plan['case'] or 'нет (нужен --case)'}")
    print(f"Telegram-поллер сейчас: {'работает (второй не будет запущен)' if plan['telegram_poller_running'] else 'не запущен'}")
    print("Требует разрешения владельца:")
    for a in plan["approvals"]:
        print(f"  - {a}")
    if plan["state"]:
        print("Уже сделано в этой папке прогона: " + ", ".join(f"{k}={v}" for k, v in plan["state"].items()))
    print(f"Папка прогона: {plan['run_dir']}")
    print("Остановить: Owner-Run.cmd stop (или создать файл STOP в папке прогона)")
    print(f"Следующая команда: {plan['next_command']}")


# ------------------------------------------------------------------- stop

def cmd_stop(run_dir: Path, wait_s: float = 60.0) -> int:
    if not run_dir.is_dir():
        print(f"[{PROFILE}] прогон ещё не начинался ({run_dir}) — останавливать нечего")
        return 0
    (run_dir / "STOP").write_text(f"STOP requested at {_now()} by pid {os.getpid()}\n", encoding="utf-8")
    runner_alive = lock_is_held(run_dir / "runner.lock")
    acknowledged = False
    if runner_alive:
        print(f"[{PROFILE}] STOP положен; жду, пока раннер остановится между шагами (до {wait_s:.0f} с)…", flush=True)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if not lock_is_held(run_dir / "runner.lock"):
                acknowledged = True
                break
            time.sleep(0.25)
    state = load_state(run_dir) or {}
    leftovers = []
    for child in state.get("children", []):
        killed = kill_tree(int(child["pid"]), child.get("create_time"), child.get("descendants") or [])
        leftovers.append({"step": child.get("step"), "pid": child["pid"], "cmd": child.get("cmd"), "killed": killed})
    if leftovers:
        state["children"] = []
        state.setdefault("stopped", {"at": _now(), "step": state.get("current")})["killed_by_stop_command"] = leftovers
        _write_json(_state_path(run_dir), state)
    previous = {}
    try:
        previous = json.loads((run_dir / "stop-report.json").read_text(encoding="utf-8")) if acknowledged else {}
    except (OSError, ValueError):
        previous = {}
    report = {**previous, "stop_command_at": _now(), "runner_was_alive": runner_alive,
              "runner_acknowledged": acknowledged, "running_step": (state.get("stopped") or {}).get("step")
              or state.get("current"), "killed_by_stop_command": leftovers,
              "services_left_running": [s for s in state.get("services", [])
                                        if pid_alive(int(s["pid"]), s.get("create_time"))]}
    _write_json(run_dir / "stop-report.json", report)
    if state:
        write_profile_report(run_dir, state)
    print(f"[{PROFILE}] остановлено: шаг «{report['running_step'] or 'между шагами'}»; "
          f"раннер {'подтвердил' if acknowledged else ('не ответил' if runner_alive else 'не работал')}; "
          f"добито сирот: {sum(len(x['killed']) for x in leftovers)}. Отчёт: {run_dir / 'stop-report.json'}")
    print(f"[{PROFILE}] продолжить: Owner-Run.cmd resume")
    return 0 if (acknowledged or not runner_alive) else 1


# ------------------------------------------------------------------- main

def profile_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="Owner-Run.cmd self-improve-mvcr",
                                description="Профиль self-improve-mvcr: " + " → ".join(SI_STAGES))
    p.add_argument("action", nargs="?", default="plan", choices=SI_ACTIONS,
                   help="стадия; run/resume — все по порядку с пропуском завершённого; stop — остановить")
    p.add_argument("--run-dir", default=None, help="папка прогона (по умолчанию <data_dir>/owner-run/self-improve-mvcr)")
    p.add_argument("--data-dir", default=None)
    p.add_argument("--port", type=int, default=int(os.environ.get("BCC_PORT", "8800")))
    p.add_argument("--base-url", default=None, help="Bossman API (по умолчанию http://127.0.0.1:<port>)")
    p.add_argument("--endpoints", default=None, help=f"эндпоинты моделей; иначе {ENDPOINTS_ENV} или {DEFAULT_ENDPOINTS}")
    p.add_argument("--no-completion", action="store_true")
    p.add_argument("--manifest", default=None, help="model_profiles.json (по умолчанию рядом с раннером)")
    p.add_argument("--models-dir", default=None, help="папка моделей (иначе BOSSMAN_MODELS_DIR или <data_dir>/models)")
    p.add_argument("--profile", action="append", default=None, help="профиль модели (можно несколько)")
    p.add_argument("--reuse-dir", action="append", default=None, help="папка с уже скачанными моделями")
    p.add_argument("--allow-download", action="store_true", help="разрешить bootstrap качать недостающее")
    p.add_argument("--fetch-timeout", type=float, default=12 * 3600)
    p.add_argument("--compare-endpoint", action="append", default=None)
    p.add_argument("--compare-timeout", type=float, default=2 * 3600)
    p.add_argument("--owner-folder", default=None)
    p.add_argument("--facts", default=None)
    p.add_argument("--mvcr-timeout", type=float, default=1800)
    p.add_argument("--case", default=None, help="кейс compare/lesson (иначе встроенный sample лаборатории)")
    p.add_argument("--transfer-case", default=None, help="НОВЫЙ аналогичный кейс transfer (иначе sample-transfer)")
    p.add_argument("--explore-repo", default=None, help="репозиторий для свободной разведки (необязательно)")
    p.add_argument("--lab-work-dir", default=None, help="где лаборатория делает чистые копии; внутри корней coding")
    p.add_argument("--budget-minutes", type=int, default=40)
    p.add_argument("--bossman-start-cmd", default=None, help='JSON-список команды запуска Bossman')
    p.add_argument("--restart-timeout", type=float, default=180)
    p.add_argument("--telegram", choices=("check", "start"), default="check")
    p.add_argument("--telegram-config", default=None)
    p.add_argument("--redo", action="append", default=None, choices=step_keys(),
                   help="осознанно повторить шаг (в т.ч. UNKNOWN_OUTCOME)")
    p.add_argument("--stop-wait", type=float, default=60.0)
    p.add_argument("--json", action="store_true", help="plan/status: печатать JSON")
    return p


def profile_main(argv: list[str]) -> int:
    args = profile_parser().parse_args(argv)
    args.base_url = (args.base_url or f"http://127.0.0.1:{args.port}").rstrip("/")
    run_dir = Path(args.run_dir).expanduser() if args.run_dir else _data_dir(args.data_dir) / "owner-run" / PROFILE
    if args.action == "plan":
        plan = profile_plan(args, run_dir)
        if args.json:
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        else:
            print_plan(plan)
        return 0
    if args.action == "status":
        state = load_state(run_dir)
        if args.json:
            print(json.dumps(state, indent=2, ensure_ascii=False))
        else:
            for key in step_keys():
                rec = ((state or {}).get("steps") or {}).get(key) or {}
                print(f"  {key:<24} {rec.get('status', NOT_RUN):<16} {str(rec.get('detail', ''))[:120]}")
            print(f"Следующая команда: {next_command(state)}")
        return 0
    if args.action == "stop":
        return cmd_stop(run_dir, args.stop_wait)
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = _lock_file(run_dir / "runner.lock")
    if lock is None:
        print(f"[{PROFILE}] в {run_dir} уже идёт прогон — второй не запускаю. Остановить: Owner-Run.cmd stop")
        return 2
    try:
        run = ProfileRun(args, run_dir)
        run.begin(args.action)
        full = args.action in ("run", "resume")
        stages = SI_RUN_STAGES if full else (args.action,)
        try:
            for stage in stages:
                run.stage(stage, force=not full)
        except StopRequested as exc:
            run.on_stop(str(exc))
            write_profile_report(run_dir, run.state)
            return EXIT_STOPPED
        report = write_profile_report(run_dir, run.state)
        touched = [k for k in step_keys() if k.split(":")[0] in stages]
        status = worst([run.state["steps"][k]["status"] for k in touched if k in run.state["steps"]])
        print(f"[{PROFILE}] итог: {status} (весь прогон: {report['overall']}); отчёт: {run_dir / 'report.md'}")
        print(f"[{PROFILE}] следующая команда: {report['next_command']}")
        return {PASS: 0, WARN: 0, FAIL: 1}.get(status, 2)
    finally:
        _unlock_file(lock)


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    # `Owner-Run.cmd self-improve-mvcr <стадия>` и короткие `stop` / `resume` / `plan`
    # идут во второй профиль этого же раннера — отдельного лаунчера нет.
    if argv and argv[0] == PROFILE:
        return profile_main(argv[1:])
    if argv and argv[0] in TOP_LEVEL_ALIASES:
        return profile_main(argv)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", action="append", choices=STAGES, default=None,
                        help="стадии (по умолчанию: doctor, models, media, coaching, diagnostics)")
    parser.add_argument("--out", default=None, help="папка отчёта (по умолчанию <data_dir>/owner-run/<время>)")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--port", type=int, default=int(os.environ.get("BCC_PORT", "8800")))
    parser.add_argument("--endpoints", default=None, help=f"через запятую; иначе {ENDPOINTS_ENV} или {DEFAULT_ENDPOINTS}")
    parser.add_argument("--no-completion", action="store_true", help="только /v1/models, без пробного завершения")
    parser.add_argument("--media-models", default=None, help="папка моделей (иначе BOSSMAN_MEDIA_MODELS/config)")
    parser.add_argument("--coaching-backend", choices=("local", "mock"), default="local")
    parser.add_argument("--coaching-endpoint", default=None)
    parser.add_argument("--coaching-lessons-dir", default=None)
    parser.add_argument("--coaching-timeout", type=float, default=3 * 3600)
    parser.add_argument("--evening-args", nargs="*", default=[])
    args = parser.parse_args(argv)

    stages = tuple(args.stage) if args.stage else DEFAULT_STAGES
    out = Path(args.out) if args.out else _data_dir(args.data_dir) / "owner-run" / _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"schema_version": 1, "started_at": _now(), "out": str(out),
                              "runner": str(Path(__file__).resolve()), "stages": {}, "weights_unchanged": True,
                              "owner_hardware_certified": False}
    for name in STAGES:
        if name not in stages:
            report["stages"][name] = stage_result(NOT_RUN, "не запускалась")
            continue
        print(f"[owner-run] {name} …", flush=True)
        try:
            result = RUNNERS[name](out, args)
        except Exception as exc:  # noqa: BLE001 — стадия не роняет весь прогон
            result = stage_result(FAIL, f"стадия упала: {type(exc).__name__}: {exc}",
                                  route="дефект раннера: приложить вывод консоли")
        report["stages"][name] = result
        print(f"[owner-run] {name}: {result['status']} — {result['detail'][:200]}", flush=True)
        (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["overall"] = overall({k: v for k, v in report["stages"].items() if v["status"] != NOT_RUN})
    report["finished_at"] = _now()
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "report.md").write_text(render_md(report), encoding="utf-8")
    print(render_md(report))
    return {PASS: 0, WARN: 0, OWNER_REQUIRED: 2, FAIL: 1}[report["overall"]]


if __name__ == "__main__":
    raise SystemExit(main())
