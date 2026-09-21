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
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — loopback endpoints
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


def main(argv: list[str] | None = None) -> int:
    utf8_console()
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
