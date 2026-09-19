#!/usr/bin/env python3
"""Отчёт о машине владельца — семь РАЗДЕЛЁННЫХ стадий, без общего «готово».

Зачем отдельный инструмент. `scripts/target_hardware_acceptance.py` написан
ровно для машины владельца, но запускается только из клона репозитория: три
его проверки указывают на `scripts/...`, а сам файл в Windows-архив не
кладётся. Владелец, скачавший приложение одним файлом, запустить его не может.
Доктор же (`bossman_doctor.py`) в архив входит, но про железо, ускорение и
камеру не говорит ничего. Между двумя инструментами остаётся дыра ровно там,
где у владельца вопросы: «моя ли это машина», «работает ли ускорение»,
«сколько это на самом деле занимает памяти».

Здесь эта дыра закрывается для УСТАНОВЛЕННОГО продукта. Клон не нужен: файл
кладётся в `app-support/` рядом с рантаймом, откуда и берёт соседей.

Семь стадий никогда не сливаются в одно число, потому что путать их дорого:
запущенное приложение не означает загруженную модель, загруженная модель не
означает работающее ускорение, а работающее ускорение не означает, что камера
откалибрована.

    1 app              приложение запускается и пишет в свой каталог данных
    2 hardware         это ли машина целевого класса
    3 model_server     отвечает ли локальный сервер модели
    4 model_loaded     загружена ли в него хоть одна модель
    5 acceleration     считает ли модель на GPU, а не на процессоре
    6 camera           установлено ли приложение камеры и что говорит калибровка
    7 speed_memory     фактическая память и фактическая скорость ответа

Чего отчёт НЕ делает, и это его свойство, а не недоделка:

  * не объявляет аппаратную приёмку. Вердикт `TARGET_HARDWARE_READY` может
    выдать только `target_hardware_acceptance.py`, запущенный НА целевой
    машине; здесь такого значения нет вовсе;
  * не открывает камеру. Раздел про Face SDK запрещает фоновое наблюдение и
    биометрическое хранилище по умолчанию, поэтому стадия камеры читает только
    объявленное состояние приложения и НИ РАЗУ не обращается к устройству;
  * не выдумывает чисел. Стадия, которую нельзя измерить на этой машине,
    получает `NOT_MEASURED` и точное имя недостающего условия — «не измерено»
    и «ноль» здесь разные ответы;
  * ничего не скачивает и не отправляет наружу.

    python tools/owner_machine_report.py
    python tools/owner_machine_report.py --json owner-machine.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PASS, WARN, NOT_MEASURED, BLOCKED = "PASS", "WARN", "NOT_MEASURED", "BLOCKED"
TIMEOUT = 5.0


def _neighbours() -> None:
    """Соседние модули — и в архиве (всё в app-support), и в клоне (scripts/)."""
    for candidate in (HERE, HERE.parent / "scripts", HERE.parent):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def _stage(name: str, status: str, detail: str, *, proves: str = "",
           does_not_prove: str = "", facts: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"stage": name, "status": status, "detail": detail, "proves": proves,
            "does_not_prove": does_not_prove, "facts": facts or {}}


def _get_json(url: str, timeout: float = TIMEOUT) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8", "replace"))


def model_endpoint() -> str:
    host = os.environ.get("OLLAMA_HOST", "").strip() or "http://127.0.0.1:11434"
    return host if "://" in host else f"http://{host}"


# --- 1 ------------------------------------------------------------------
def stage_app() -> dict[str, Any]:
    data_dir = os.environ.get("BCC_DATA_DIR", "").strip()
    if not data_dir:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME") \
            or str(Path.home() / ".local" / "share")
        data_dir = str(Path(base) / "Bossman" / "CommandCenter")
    target = Path(data_dir)
    facts: dict[str, Any] = {"data_dir": str(target), "exists": target.exists()}
    identity = HERE.parent / "MANIFEST.json"
    if identity.is_file():
        try:
            facts["build_sha"] = json.loads(identity.read_text(encoding="utf-8")).get("source_sha")
        except (OSError, ValueError):
            facts["build_sha"] = None
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".owner-machine-report"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _stage("app", BLOCKED, f"каталог данных недоступен на запись: {exc}",
                      does_not_prove="ничего: без своего каталога приложение не сохранит работу",
                      facts=facts)
    free = shutil.disk_usage(target).free / (1024 ** 3)
    facts["free_gib"] = round(free, 1)
    status = WARN if free < 5 else PASS
    return _stage("app", status, f"{target} доступен на запись, свободно {facts['free_gib']} ГиБ",
                  proves="приложению есть куда писать состояние владельца",
                  does_not_prove="что приложение запущено и что модель подключена",
                  facts=facts)


# --- 2 ------------------------------------------------------------------
def stage_hardware() -> dict[str, Any]:
    _neighbours()
    try:
        from target_hardware_acceptance import probe as hardware_probe
    except ImportError as exc:
        return _stage("hardware", NOT_MEASURED,
                      f"определитель железа недоступен рядом с этим файлом ({exc})",
                      does_not_prove="ничего о машине")
    facts = hardware_probe()
    if facts.get("on_target"):
        return _stage("hardware", PASS,
                      f"машина опознана как целевая: {facts.get('cpu')}, {facts.get('ram_gib')} ГиБ",
                      proves="класс машины совпадает с целевым",
                      does_not_prove="ПРИЁМКУ на целевом железе: её выдаёт только "
                                     "target_hardware_acceptance.py своим прогоном",
                      facts=facts)
    return _stage("hardware", WARN,
                  "машина НЕ целевого класса: " + "; ".join(facts.get("mismatch", [])),
                  proves="что измерения этой машины нельзя переносить на машину владельца",
                  does_not_prove="что приложение неисправно — оно работает и здесь",
                  facts=facts)


# --- 3 ------------------------------------------------------------------
def stage_model_server() -> dict[str, Any]:
    url = model_endpoint()
    try:
        body = _get_json(url.rstrip("/") + "/api/tags")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _stage("model_server", NOT_MEASURED,
                      f"локальный сервер модели на {url} не отвечает ({type(exc).__name__})",
                      does_not_prove="что модели нет: облачный провайдер настраивается отдельно",
                      facts={"endpoint": url,
                             "next_step": "запустите Ollama или укажите OLLAMA_HOST"})
    names = [m.get("name", "") for m in body.get("models", []) if isinstance(m, dict)]
    return _stage("model_server", PASS, f"{url} отвечает",
                  proves="сервер модели поднят и доступен приложению",
                  does_not_prove="что в него загружена модель и что она считает на GPU",
                  facts={"endpoint": url, "models": names[:10], "model_count": len(names)})


# --- 4 ------------------------------------------------------------------
def stage_model_loaded(server: dict[str, Any]) -> dict[str, Any]:
    if server["status"] != PASS:
        return _stage("model_loaded", NOT_MEASURED, "сервер модели не отвечал — загрузку не спросить",
                      does_not_prove="что модель не загружена",
                      facts={"depends_on": "model_server"})
    names = server["facts"].get("models", [])
    if not names:
        return _stage("model_loaded", WARN, "сервер отвечает, но не загружено ни одной модели",
                      does_not_prove="что модель нельзя загрузить",
                      facts={"next_step": "ollama pull <модель>", "models": []})
    return _stage("model_loaded", PASS, f"загружено моделей: {len(names)} ({', '.join(names[:3])})",
                  proves="в сервере есть веса, которые приложение может выбрать",
                  does_not_prove="качество ответов и сохранность интеллекта: это отдельный гейт "
                                 "Intelligence Preservation",
                  facts={"models": names})


# --- 5 ------------------------------------------------------------------
def stage_acceleration(loaded: dict[str, Any]) -> dict[str, Any]:
    """Ускорение — это НЕ наличие GPU в системе, а факт счёта на нём.

    Единственный честный признак, доступный без запуска задачи владельца:
    сервер модели сам сообщает, сколько весов лежит в видеопамяти. Ноль в
    `size_vram` при ненулевом размере модели означает счёт на процессоре,
    каким бы мощным ни был GPU в списке устройств.
    """
    if loaded["status"] != PASS:
        return _stage("acceleration", NOT_MEASURED, "модель не загружена — ускорять нечего",
                      does_not_prove="что ускорение не работает",
                      facts={"depends_on": "model_loaded"})
    url = model_endpoint()
    try:
        body = _get_json(url.rstrip("/") + "/api/ps")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _stage("acceleration", NOT_MEASURED,
                      f"сервер не сообщил размещение весов ({type(exc).__name__})",
                      does_not_prove="что счёт идёт на процессоре",
                      facts={"endpoint": url})
    running = [m for m in body.get("models", []) if isinstance(m, dict)]
    if not running:
        return _stage("acceleration", NOT_MEASURED,
                      "ни одна модель сейчас не в памяти — задайте ей вопрос и повторите",
                      does_not_prove="размещение весов при работе",
                      facts={"next_step": "спросите модель в Command Center, затем повторите отчёт"})
    detail, facts, on_gpu = [], {}, False
    for model in running:
        size = int(model.get("size") or 0)
        vram = int(model.get("size_vram") or 0)
        share = (vram / size) if size else 0.0
        on_gpu = on_gpu or vram > 0
        name = model.get("name", "?")
        facts[name] = {"size_bytes": size, "size_vram_bytes": vram,
                       "share_on_gpu": round(share, 3)}
        detail.append(f"{name}: {round(share * 100)}% весов в видеопамяти")
    if on_gpu:
        return _stage("acceleration", PASS, "; ".join(detail),
                      proves="сервер держит веса в видеопамяти, то есть ускоритель задействован",
                      does_not_prove="скорость на целевом Radeon: это измеряется на самой машине",
                      facts=facts)
    return _stage("acceleration", WARN, "; ".join(detail) + " — счёт идёт на процессоре",
                  proves="ускоритель НЕ задействован, какой бы GPU ни числился в системе",
                  does_not_prove="что ускорение невозможно: обычно это сборка сервера или драйвер",
                  facts=facts)


# --- 6 ------------------------------------------------------------------
def stage_camera() -> dict[str, Any]:
    """Камера НЕ открывается. Читается только объявленное состояние приложения."""
    try:
        import ai_webcam_vision  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    if not installed:
        return _stage("camera", NOT_MEASURED, "приложение камеры не установлено в этом рантайме",
                      does_not_prove="что камера неисправна",
                      facts={"next_step": "установите приложение ai-webcam-vision"})
    port = os.environ.get("AI_WEBCAM_PORT", "8830").strip()
    base = f"http://127.0.0.1:{port}"
    try:
        ready = _get_json(base + "/readyz")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _stage("camera", NOT_MEASURED,
                      f"сервис камеры не запущен на {base} ({type(exc).__name__})",
                      does_not_prove="состояние калибровки",
                      facts={"endpoint": base,
                             "next_step": "запустите приложение камеры из Command Center"})
    return _stage("camera", PASS if ready.get("ready") else WARN,
                  f"HTTP-сервис камеры отвечает: ready={ready.get('ready')}",
                  proves="сервис поднят и принимает запросы",
                  does_not_prove="ни исправность устройства, ни выполненную калибровку: "
                                 "их сообщает защищённая проверка состояния, а камера "
                                 "этим отчётом НЕ открывается",
                  facts={"endpoint": base, "readyz": ready})


# --- 7 ------------------------------------------------------------------
def stage_speed_memory(loaded: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    _neighbours()
    try:
        from target_hardware_acceptance import total_ram_gib
        facts["ram_gib"] = total_ram_gib()
    except (ImportError, OSError):
        facts["ram_gib"] = None
    try:
        import psutil  # type: ignore

        memory = psutil.virtual_memory()
        facts["ram_available_gib"] = round(memory.available / (1024 ** 3), 1)
        facts["ram_used_percent"] = memory.percent
    except Exception:  # noqa: BLE001 — psutil необязателен в архиве
        facts["ram_available_gib"] = None
    if loaded["status"] != PASS:
        return _stage("speed_memory", NOT_MEASURED,
                      f"память: {facts.get('ram_gib')} ГиБ всего; скорость не измерена — "
                      "нет загруженной модели",
                      does_not_prove="скорость этой машины",
                      facts=facts)
    name = (loaded["facts"].get("models") or ["?"])[0]
    url = model_endpoint().rstrip("/") + "/api/generate"
    payload = json.dumps({"model": name, "prompt": "ping", "stream": False,
                          "options": {"num_predict": 16, "temperature": 0}}).encode("utf-8")
    request = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        facts["error"] = type(exc).__name__
        return _stage("speed_memory", NOT_MEASURED,
                      f"модель {name} не ответила на пробный запрос ({facts['error']})",
                      does_not_prove="скорость модели", facts=facts)
    elapsed = time.monotonic() - started
    tokens = int(body.get("eval_count") or 0)
    nanos = int(body.get("eval_duration") or 0)
    facts.update({"model": name, "wall_seconds": round(elapsed, 3), "eval_tokens": tokens})
    if tokens and nanos:
        facts["tokens_per_second"] = round(tokens / (nanos / 1e9), 1)
    detail = (f"{name}: {facts.get('tokens_per_second', '?')} токенов/с на 16 токенах, "
              f"{facts['wall_seconds']} с от запроса до ответа; "
              f"память {facts.get('ram_gib')} ГиБ всего")
    return _stage("speed_memory", PASS, detail,
                  proves="модель отвечает на этой машине и с этой скоростью НА ЭТОЙ ЗАДАЧЕ",
                  does_not_prove="скорость на задачах владельца: 16 токенов — это проба связи, "
                                 "а не замер производительности",
                  facts=facts)


def build_report() -> dict[str, Any]:
    app = stage_app()
    hardware = stage_hardware()
    server = stage_model_server()
    loaded = stage_model_loaded(server)
    acceleration = stage_acceleration(loaded)
    camera = stage_camera()
    speed = stage_speed_memory(loaded)
    stages = [app, hardware, server, loaded, acceleration, camera, speed]
    blocked = [s["stage"] for s in stages if s["status"] == BLOCKED]
    pending = [s["stage"] for s in stages if s["status"] in (WARN, NOT_MEASURED)]
    return {
        "type": "bossman.owner_machine_report",
        "schema": 1,
        "platform": platform.platform(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "BLOCKED" if blocked else ("OWNER_ACTION_REQUIRED" if pending else "ALL_STAGES_MEASURED"),
        "hardware_acceptance": "NOT_CLAIMED_BY_THIS_REPORT",
        "hardware_acceptance_note": "аппаратную приёмку выдаёт только "
                                    "scripts/target_hardware_acceptance.py, запущенный "
                                    "на самой машине владельца",
        "blocked": blocked,
        "needs_owner_action": pending,
        "stages": stages,
    }


def render(report: dict[str, Any]) -> str:
    lines = [f"BOSSMAN_OWNER_MACHINE={report['status']}", ""]
    for number, stage in enumerate(report["stages"], start=1):
        lines.append(f"{number}. {stage['stage']:<14} {stage['status']:<13} {stage['detail']}")
        if stage["does_not_prove"]:
            lines.append(f"   не доказывает: {stage['does_not_prove']}")
        step = stage["facts"].get("next_step")
        if step:
            lines.append(f"   дальше: {step}")
    lines += ["", f"аппаратная приёмка: {report['hardware_acceptance']}",
              f"  {report['hardware_acceptance_note']}"]
    return "\n".join(lines)


def utf8_console() -> None:
    """Печать не имеет права падать на кириллице.

    Раннеры приёмки запускаются с `-I`, а `-I` подразумевает `-E`: PYTHONUTF8
    и PYTHONIOENCODING игнорируются. На Windows поток получает кодировку
    локали, и первая же русская строка роняет процесс UnicodeEncodeError —
    так и закончился прогон 132 на настоящей Windows. Требование касается и
    вывода без русских строк: печатаемый путь проходит через имя пользователя
    Windows, а оно вполне может быть кириллическим. `errors='replace'`
    оставляет печать живой и там, где UTF-8 недоступен.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path, help="куда положить отчёт")
    parser.add_argument("--quiet", action="store_true", help="только строка состояния")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"BOSSMAN_OWNER_MACHINE={report['status']}" if args.quiet else render(report))
    # Код выхода сообщает, СОСТАВЛЕН ли отчёт, а не годна ли машина: вердикт
    # живёт в поле status. Иначе «не измерено» превратилось бы в «сломано».
    return 1 if report["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
