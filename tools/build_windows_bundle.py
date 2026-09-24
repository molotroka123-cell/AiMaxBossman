#!/usr/bin/env python3
"""Build ONE downloadable Bossman application for Windows.

The owner downloads one archive, unzips it and starts Bossman. No repository
clone, no system Python, no separate wheel picking, no hunting for Chromium or
ffmpeg. Everything the shipped product needs is inside the archive.

    BOSSMAN-Windows-x64-<short sha>/
      Start-Bossman.cmd      open the Command Center
      Evening-Test.cmd       exact-SHA owner acceptance for THIS build
      runtime/               embeddable CPython + every Python dependency
      browser/               Chromium used by the shipped browser path
      media/                 ffmpeg.exe, ffprobe.exe
      icons/                 the canonical application icon
      LICENSES/              notices for everything redistributed
      MANIFEST.json          exact source SHA, contents, measured checks
      SHA256SUMS             hash of every shipped file

Honesty rules this file obeys:

* the archive records what it actually contains. A prerequisite that could not
  be bundled is written into MANIFEST.json as ``required_downloads`` with the
  reason, and the first-run launcher fetches it — the owner is never told to go
  and find a binary by hand;
* nothing is stamped with a commit unless the tree is clean and still that
  commit when the build finishes;
* build-time acquisition failures fail the build. A bundle missing Chromium is
  not quietly relabelled as a bundle that never wanted Chromium;
* with ``tools/windows_bundle_lock.json`` present (OA-03) every input is
  pinned before it is read: the embeddable CPython zip and the FFmpeg release
  asset are hashed against the lock before extraction, third-party packages
  come from a wheelhouse pip downloaded in hash-checking mode and are
  installed with ``--no-index --require-hashes``, the Bossman wheels are
  installed ``--no-deps`` on top, and the EMBEDDED interpreter is asked which
  distributions it actually sees. Without the lock the build still runs, says
  ``BOSSMAN_BUILD_INPUTS=UNLOCKED`` and writes ``build_inputs.locked=false``;
  the freeze aggregator refuses to release such an archive;
* the ``release`` profile (the default) fails on a missing FFmpeg instead of
  writing it into ``required_downloads``: a standalone product does not ask
  the owner's first run to fetch its media tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from terminal_launchers import TERMINAL_LAUNCHERS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import windows_bundle_lock as lockmod  # noqa: E402

# python.org publishes an immutable embeddable build per patch release. The
# version is taken from the interpreter doing the build, so the wheels that pip
# resolves here are the wheels that runtime can import — no guessing a tag.
EMBED_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"

BUNDLE_DIRS = ("runtime", "browser", "media", "icons", "LICENSES", "app-support")

ICON_FILES = ("bossman.ico", "icon-1024.png", "icon-512.png", "icon-256.png",
              "icon-192.png", "icon-128.png", "icon-64.png", "icon-48.png",
              "icon-32.png", "icon-16.png")

# Shipped next to the runtime so the owner's evening test needs no checkout.
SUPPORT_SCRIPTS = (
    (ROOT / "tools" / "studio_live_owner.py", "studio_live_owner.py"),
    (ROOT / "tools" / "verify_installed_product.py", "verify_installed_product.py"),
    (ROOT / "scripts" / "bossman_doctor.py", "bossman_doctor.py"),
    (ROOT / "tools" / "bundle_evening_test.py", "bundle_evening_test.py"),
    # Отчёт о машине владельца и определитель железа, на который он опирается.
    # Доктор в архиве есть, но про железо, ускорение и камеру не говорит ничего,
    # а target_hardware_acceptance.py написан ровно для машины владельца и до
    # сих пор существовал только в клоне репозитория. Здесь он едет как
    # БИБЛИОТЕКА определения CPU/GPU/памяти; собственный его прогон по-прежнему
    # требует клона, потому что три его проверки указывают на scripts/.
    (ROOT / "tools" / "owner_machine_report.py", "owner_machine_report.py"),
    (ROOT / "scripts" / "target_hardware_acceptance.py", "target_hardware_acceptance.py"),
    # OA-04: the owner runners CI drives from the checkout ship in the archive
    # too, so «Evening-Test.cmd --full» and the target-hardware acceptance run
    # from the ZIP alone. installed_ui_sweep.py finds its driver beside itself;
    # target_hardware_acceptance.py resolves its three checks beside itself.
    (ROOT / "tools" / "installed_ui_sweep.py", "installed_ui_sweep.py"),
    (ROOT / "scripts" / "ui_acceptance_sweep.py", "ui_acceptance_sweep.py"),
    (ROOT / "tools" / "live_openrouter_owner.py", "live_openrouter_owner.py"),
    # 1.0-RC (2026-09-21): владельческий прогон завтрашнего дня, настройка
    # локального медиадвижка, coaching runner и A/B пресетов едут в архиве —
    # владелец запускает прогон, а не собирает зависимости руками.
    (ROOT / "tools" / "owner_run_tomorrow.py", "owner_run_tomorrow.py"),
    (ROOT / "tools" / "media_bootstrap.py", "media_bootstrap.py"),
    (ROOT / "tools" / "media_ab_preset.py", "media_ab_preset.py"),
    (ROOT / "tools" / "coaching_runner.py", "coaching_runner.py"),
    # 2026-09-23: проверка Coding path из архива (локальный сайдкар +
    # детерминированная тестовая модель) и загрузчик модельных профилей.
    (ROOT / "tools" / "coding_path_owner.py", "coding_path_owner.py"),
    (ROOT / "tools" / "model_fetch.py", "model_fetch.py"),
    # HW-10 MVČR: пакет до WAIT_APPROVAL, без отправки/подписи/оплаты.
    (ROOT / "tools" / "mvcr_prepare.py", "mvcr_prepare.py"),
    # Профиль `Owner-Run.cmd self-improve-mvcr compare`: бейк-офф A–G лаборатории
    # 2026-09-22, перенесённый в поставку (только stdlib, проверка исполнением).
    (ROOT / "tools" / "model_bakeoff.py", "model_bakeoff.py"),
    # Лаборатория самоулучшения (фазы compare/lesson/transfer) — тоже раннер,
    # поэтому под общей проверкой UTF-8 консоли.
    (ROOT / "tools" / "self_improve_lab.py", "self_improve_lab.py"),
    # Jev (browser fast path + decision provider) — только shadow и выключен по
    # умолчанию; раннер без флагов проверяет лишь ключ и конфиг (exit 3).
    (ROOT / "tools" / "jev_shadow_owner.py", "jev_shadow_owner.py"),
    # Bossman 1.5 economy lane: public YouTube ingest + free-first worker swarm.
    (ROOT / "tools" / "youtube_trader_ingest.py", "youtube_trader_ingest.py"),
    (ROOT / "tools" / "youtube_trader_ingest_auto.py", "youtube_trader_ingest_auto.py"),
    (ROOT / "tools" / "youtube_trader_ingest_batch.py", "youtube_trader_ingest_batch.py"),
    (ROOT / "tools" / "worker_client.py", "worker_client.py"),
    (ROOT / "tools" / "distill_recorder.py", "distill_recorder.py"),
    (ROOT / "tools" / "v15_economy_orchestrator.py", "v15_economy_orchestrator.py"),
    (ROOT / "tools" / "v15_provider_pool.py", "v15_provider_pool.py"),
    (ROOT / "tools" / "bossman_15_self_improve.py", "bossman_15_self_improve.py"),
    (ROOT / "tools" / "bossman_15_owner_ctl.py", "bossman_15_owner_ctl.py"),
    (ROOT / "tools" / "bossman_15_learning_compile.py", "bossman_15_learning_compile.py"),
    (ROOT / "tools" / "bossman_15_ling_coder.py", "bossman_15_ling_coder.py"),
    # Same bounded evolution engine used by the product API; no second daemon.
    (ROOT / "tools" / "bossman_evolve.py", "bossman_evolve.py"),
    # Four-clip Bossfield preflight/editor; generation remains governed by Studio.
    (ROOT / "tools" / "bossfield_owner_run.py", "bossfield_owner_run.py"),
)
# Данные, которые раннеры читают рядом с собой (не исполняемые скрипты).
SUPPORT_DATA = (
    (ROOT / "tools" / "model_profiles.json", "model_profiles.json"),
    (ROOT / "config" / "evolution" / "owner-v1.1.json", "config/evolution/owner-v1.1.json"),
    (ROOT / "config" / "evolution" / "local-champions.json", "config/evolution/local-champions.json"),
    (ROOT / "config" / "v1.5" / "economy-orchestrator.json", "config/v1.5/economy-orchestrator.json"),
    (ROOT / "config" / "v1.5" / "provider-pool.json", "config/v1.5/provider-pool.json"),
    (ROOT / "config" / "v1.5" / "self-improvement.json", "config/v1.5/self-improvement.json"),
    (ROOT / "config" / "evolution" / "owner-v1.5-self-improve.json", "config/evolution/owner-v1.5-self-improve.json"),
)

# Профиль `Owner-Run.cmd self-improve-mvcr` вызывает эти файлы рядом с раннером.
# Они ОБЯЗАТЕЛЬНЫ: пропавший роняет сборку громко и списком, а не превращается в
# архив, где стадия молча стала бы NOT_RUN у владельца. Каталог кейсов копируется
# целиком и тоже обязан быть непустым.
SELF_IMPROVE_REQUIRED = (
    (ROOT / "tools" / "model_fetch.py", "model_fetch.py"),
    (ROOT / "tools" / "model_profiles.json", "model_profiles.json"),
    (ROOT / "tools" / "mvcr_prepare.py", "mvcr_prepare.py"),
    (ROOT / "tools" / "self_improve_lab.py", "self_improve_lab.py"),
)
SELF_IMPROVE_CASES_SOURCE = ROOT / "tools" / "self_improve_cases"
SELF_IMPROVE_CASES_TARGET = "self_improve_cases"
# Документы профиля — по тем же относительным путям, что и в репозитории.
SELF_IMPROVE_DOCS = ("docs/owner/OWNER_RUN_NEXT.md",)

# Пак задач coaching (5 обучающих + 5 holdout) едет каталогом рядом с раннером.
COACHING_PACK_SOURCE = ROOT / "tests" / "coaching_pack"
COACHING_PACK_TARGET = "coaching-pack"

# Комплект владельческого GUI-прогона. В README комплекта владельцу обещан
# путь `app-support/owner-final-run/`; пока этого контракта не было, наличие
# документов в GitHub НЕ означало их наличия в скачанном архиве.
#
# Список явный, а не глоб, и проверяется в обе стороны: пропавший файл роняет
# сборку, и добавленный мимо списка — тоже. Глоб молча увёз бы случайный файл
# и так же молча не заметил бы пропажу нужного, а обнаружил бы это владелец,
# распаковав архив без инструкции.
OWNER_RUN_SOURCE = ROOT / "docs" / "v8" / "owner-final-run"
OWNER_RUN_FILES = (
    "README_RU.md",
    "START_PROMPT_RU.md",
    "OPENCODE_OWNER_RUN_RU.md",
    "OPENCODE_LOCAL_SETUP_RU.md",
    "PREPARATION_MEMORY_RU.md",
    "PREPARATION_FILE_CHECKS.json",
    "BUG_REPORT_TEMPLATE_RU.md",
    "CONVERGENCE_NOTE_RU.md",
    "FABLE_ASTER_HANDOFF_RU.md",
    "RUN_CHECKPOINT.template.json",
    "check_package.py",
)


# Keep the current owner protocol at its documented relative paths in the ZIP.
# This is an explicit allowlist: no private reports, secrets or arbitrary tests
# are copied. These files enter the existing MANIFEST.json/SHA256SUMS inventory.
OWNER_ACCEPTANCE_FILES = (
    "INSTALL.md", "OWNER_ACCEPTANCE.md", "KNOWN_LIMITATIONS.md", "owner-acceptance.ps1",
    "START_TOMORROW_RU.md", "docs/owner/ROLLBACK_RU.md", "docs/owner/JEV_TOMORROW.md",
    "tests/owner_hardware/README.md", "tests/owner_hardware/manifest.json",
    "tests/owner_hardware/HOTSPOTS_AND_HOTFIX_PLAYBOOK.md",
    "tests/owner_hardware/MODEL_STACK_2026-09-20.md",
    "tests/owner_hardware/CLOUD_STACK_2026-09-20.md",
    "tests/owner_scenarios/OWNER_HARDWARE_FIRST_RUN.md",
)


# The eight accepted OSS directions (docs/oss/README.md), stated per archive:
# what is inside the ZIP, what the owner configures, what needs weights, what
# needs a separate service. VERIFIED is never claimed by the build: it is a
# separate acceptance on the owner's machine, named per direction.
OSS_DIRECTIONS = (
    {"direction": "llama.cpp", "surface": "Модели → провайдер openai_compat", "distribution": None,
     "model_required": True, "external_service_required": True,
     "verify_by": "русский ответ, JSON по схеме, задача с инструментом через настроенный сервер"},
    {"direction": "Docling", "surface": "Локальные инструменты → Документ → текст", "distribution": "docling-slim",
     "model_required": False, "external_service_required": False,
     "verify_by": "известная фраза из PDF с текстовым слоем; сканы без OCR — NO_TEXT"},
    {"direction": "Qdrant", "surface": "Локальные инструменты → Поиск по заметкам", "distribution": "qdrant-client",
     "model_required": True, "external_service_required": True,
     "verify_by": "заранее записанный факт найден через локальный сервер эмбеддингов"},
    {"direction": "faster-whisper", "surface": "Локальные инструменты → Запись → текст", "distribution": "faster-whisper",
     "model_required": True, "external_service_required": False,
     "verify_by": "короткий WAV расшифрован с локальными весами CTranslate2"},
    {"direction": "SearXNG", "surface": "Веб-поиск", "distribution": None,
     "model_required": False, "external_service_required": True,
     "verify_by": "реальный запрос с ссылками через BOSSMAN_WEB_SEARXNG_URL"},
    {"direction": "ComfyUI", "surface": "Изображения → ComfyUI (local text-to-image)", "distribution": None,
     "model_required": True, "external_service_required": True,
     "verify_by": "открывающийся PNG 512×512; «Стоп» и «Повторить»"},
    {"direction": "UI-TARS", "surface": "Computer Operator → планировщик uitars", "distribution": None,
     "model_required": True, "external_service_required": True,
     "verify_by": "безобидный клик по координатам визуальной модели через локальный маршрут"},
    {"direction": "GrapesJS", "surface": "Веб-дизайн → Конструктор блоков", "distribution": "bossman-command-center",
     "asset": "ui/vendor/grapesjs/grapes.min.js",
     "model_required": False, "external_service_required": False,
     "verify_by": "правка заголовка, сохранение, переоткрытие после перезапуска"},
)

START_CMD = r"""@echo off
setlocal
rem Bossman — one-download Windows application. Everything is beside this file.
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
"%BOSSMAN_HOME%runtime\python.exe" -m bcc.desktop %*
exit /b %ERRORLEVEL%
"""

EVENING_CMD = r"""@echo off
setlocal
rem Owner evening acceptance for exactly this build. No repository required.
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
"%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\bundle_evening_test.py" %*
exit /b %ERRORLEVEL%
"""

MACHINE_CMD = r"""@echo off
setlocal
rem Seven separated stages about THIS machine. No repository required.
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
"%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\owner_machine_report.py" %*
exit /b %ERRORLEVEL%
"""

OWNER_RUN_CMD = r"""@echo off
setlocal
rem Owner-suite runner: doctor -> MAIN/FAST discovery -> media manifest -> coaching -> diagnostics.
rem Machine stages only; the owner scenarios are in START_TOMORROW_RU.md. Never claims certification.
rem Profile: Owner-Run.cmd self-improve-mvcr STAGE, where STAGE is plan, preflight, bootstrap, skills, compare, mvcr, self-improve, report, run, resume or status.
rem Short forms: Owner-Run.cmd plan / resume / stop. See docs\owner\OWNER_RUN_NEXT.md.
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
"%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\owner_run_tomorrow.py" %*
exit /b %ERRORLEVEL%
"""

MEDIA_SETUP_CMD = r"""@echo off
setlocal
rem Local media engine (stable-diffusion.cpp) setup: validate | plan-download | download | configure.
rem Downloads only with --allow-download; a missing engine never blocks Bossman itself.
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
"%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\media_bootstrap.py" %*
exit /b %ERRORLEVEL%
"""

COACHING_CMD = r"""@echo off
setlocal
rem Coaching runner: 5 training + 5 holdout coding tasks against the local endpoint.
rem Prints WEIGHTS_UNCHANGED; without a reachable endpoint reports LOCAL_LEARNING_GAIN_NOT_MEASURED.
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
if "%~1"=="" (
  "%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\coaching_runner.py" --backend local --out "%LOCALAPPDATA%\Bossman\CommandCenter\owner-run\coaching"
) else (
  "%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\coaching_runner.py" %*
)
exit /b %ERRORLEVEL%
"""

V15_OWNER_CMD = r"""@echo off
setlocal
rem Bossman 1.5 owner control: same authenticated API as the UX page.
rem Usage: Bossman-1.5.cmd quick-test ^| start ^| status ^| stop
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
if "%~1"=="" (
  "%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\bossman_15_owner_ctl.py" status
) else (
  "%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\bossman_15_owner_ctl.py" %*
)
exit /b %ERRORLEVEL%
"""

DIAGNOSTICS_CMD = r"""@echo off
setlocal
rem Redacted diagnostics ZIP for a defect report (doctor, manifests, log tails; secrets cut by pattern).
set "BOSSMAN_HOME=%~dp0"
call "%BOSSMAN_HOME%app-support\_env.cmd"
if errorlevel 1 exit /b 1
"%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\owner_run_tomorrow.py" --stage doctor --stage diagnostics %*
exit /b %ERRORLEVEL%
"""

# One place decides the environment, so the two launchers cannot drift apart.
ENV_CMD = r"""@echo off
rem Sourced by the launchers. Points the product at the BUNDLED prerequisites.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PLAYWRIGHT_BROWSERS_PATH=%BOSSMAN_HOME%browser"
set "PATH=%BOSSMAN_HOME%runtime\Scripts;%BOSSMAN_HOME%media;%PATH%"
if not exist "%BOSSMAN_HOME%runtime\python.exe" (
  echo [bossman] runtime is missing - the archive did not unzip completely.
  exit /b 1
)
if exist "%BOSSMAN_HOME%app-support\first-run.py" (
  "%BOSSMAN_HOME%runtime\python.exe" "%BOSSMAN_HOME%app-support\first-run.py"
  if errorlevel 1 exit /b 1
)
exit /b 0
"""


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=kwargs.pop("timeout", 1800), **kwargs)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"failed ({result.returncode}): {' '.join(cmd)}\n{detail[-4000:]}")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ----------------------------------------------------------------- layout

def bundle_name(sha: str) -> str:
    return f"BOSSMAN-Windows-x64-{sha[:12]}"


def launcher_files() -> dict[str, str]:
    """Path -> contents for every launcher shipped in the bundle."""
    return {
        "Start-Bossman.cmd": START_CMD,
        "Evening-Test.cmd": EVENING_CMD,
        "Machine-Report.cmd": MACHINE_CMD,
        "Owner-Run.cmd": OWNER_RUN_CMD,
        "Bossman-1.5.cmd": V15_OWNER_CMD,
        "Media-Setup.cmd": MEDIA_SETUP_CMD,
        "Coaching.cmd": COACHING_CMD,
        "Collect-Diagnostics.cmd": DIAGNOSTICS_CMD,
        "app-support/_env.cmd": ENV_CMD,
        # Bossman 1.2 terminal: Bossman-CLI.cmd (bossman chat) and bossman.cmd
        # (headless, for Claude Code). They start the installed `bossman`
        # entry point (bossman.cli), not an app-support script.
        **TERMINAL_LAUNCHERS,
    }


def manifest_for(*, sha: str, when: str, files: list[dict], contents: dict,
                 checks: dict, required_downloads: list[dict]) -> dict:
    """The claim the archive makes about itself, in one place.

    ``downloads_required_for_bossman_app`` counts what the OWNER must fetch:
    the archive itself, plus anything the first run still has to acquire.
    """
    return {
        "schema_version": 1,
        "artifact": bundle_name(sha),
        "source_sha": sha,
        "source_dirty": False,
        "built_at": when,
        "platform": "windows-x64",
        "contents": contents,
        "checks": checks,
        "required_downloads": required_downloads,
        "downloads_required_for_bossman_app": 1 + len(required_downloads),
        "repository_clone_required": False,
        "system_python_required": False,
        "files": files,
    }


# ------------------------------------------------------------- acquisition

def fetch(url: str, target: Path) -> Path:
    """Download with the standard library; no extra build dependency."""
    from urllib.request import urlopen
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"  fetching {url}", flush=True)
    with urlopen(url, timeout=900) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)
    return target


def fetch_pinned(url: str, target: Path, expected_sha256: str | None) -> Path:
    """Download, then refuse to hand back a file whose digest is not the locked one.

    The check happens here, before any caller extracts or runs the download:
    a wrong FFmpeg or a wrong CPython never reaches the archive.
    """
    archive = fetch(url, target)
    if expected_sha256:
        found = sha256_file(archive)
        if found != expected_sha256:
            archive.unlink(missing_ok=True)
            raise RuntimeError(f"digest mismatch for {url}: lock says {expected_sha256}, "
                               f"downloaded {found}; the build stops before extraction")
        print(f"  digest verified {found[:16]}…", flush=True)
    return archive


def install_runtime(runtime: Path, work: Path, lock: dict | None = None) -> dict:
    """Embeddable CPython, with site-packages switched on.

    Locked: the exact patch and zip digest from the lock (the build interpreter
    only has to share the minor version, wheels are tagged cp3XY). Unlocked:
    the version of the interpreter doing the build, as before.
    """
    if lock:
        version = lock["python"]["version"]
        if tuple(int(part) for part in version.split(".")[:2]) != sys.version_info[:2]:
            raise RuntimeError(f"the lock pins CPython {version}; the build interpreter is "
                               f"{sys.version_info.major}.{sys.version_info.minor}")
        archive = fetch_pinned(lock["python"]["embeddable_url"], work / f"python-{version}-embed.zip",
                               lock["python"]["embeddable_sha256"])
    else:
        version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        archive = fetch(EMBED_URL.format(v=version), work / f"python-{version}-embed.zip")
    runtime.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(runtime)
    # The embeddable build ships an isolated path file with `import site`
    # commented out; without it nothing in Lib/site-packages is importable.
    pth = next(iter(sorted(runtime.glob("python*._pth"))), None)
    if pth is None:
        raise RuntimeError("embeddable runtime has no ._pth file")
    text = pth.read_text(encoding="utf-8")
    if "#import site" in text:
        text = text.replace("#import site", "import site")
    elif "import site" not in text:
        text += "\nimport site\n"
    if "Lib\\site-packages" not in text:
        text = text.replace("\nimport site", "\nLib\\site-packages\nimport site")
    pth.write_text(text, encoding="utf-8")
    (runtime / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    return {"python_version": version, "embeddable_sha256": sha256_file(archive),
            "pinned": bool(lock)}


def pip_requirements(wheels: list[Path]) -> list[str]:
    """The wheels to install, with the extras the shipped product needs.

    The bundled doctor reported ``computer-operator`` BLOCKED on a complete
    archive: Windows automation, PDF, MCP and OTIO live in the ``runtime`` extras,
    and installing the bare wheels leaves them out. The archive
    promises the owner has nothing left to install, so the extra is part of the
    build — not a line in a README telling them to run pip.
    """
    requirements = []
    for wheel in wheels:
        extras = "[runtime]" if wheel.name.startswith(("bossman_core-", "bossman-core-", "bossman_command_center-")) else ""
        requirements.append(f"{wheel}{extras}")
    return requirements


def console_shim(spec: str) -> str:
    """A relocatable ``Scripts`` entry point for the EMBEDDED runtime.

    ``pip install --target`` does not produce usable console scripts: the
    wrappers it writes land beside the packages and hard-code the path of the
    interpreter that ran the build, so on the owner's machine they point at a
    Python that is not there. The archive therefore writes its own, and they
    resolve the runtime relative to themselves — the owner may unzip anywhere.
    """
    module, _, attribute = spec.partition(":")
    first, *rest = attribute.split(".") if attribute else ["main"]
    call = "obj" + "".join(f".{part}" for part in rest)
    code = f"import sys; from {module} import {first} as obj; sys.exit({call}())"
    return ("@echo off\r\n"
            "rem Generated by tools/build_windows_bundle.py — do not edit by hand.\r\n"
            f'"%~dp0..\\python.exe" -c "{code}" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n")


def console_scripts(site_packages: Path) -> dict[str, str]:
    """Every console entry point the installed distributions declare."""
    import configparser
    found: dict[str, str] = {}
    for info in sorted(site_packages.glob("*.dist-info/entry_points.txt")):
        parser = configparser.ConfigParser()
        parser.read_string(info.read_text(encoding="utf-8"))
        if parser.has_section("console_scripts"):
            found.update(dict(parser.items("console_scripts")))
    return found


def install_packages(runtime: Path, wheels: Path, lock: dict | None = None,
                     work: Path | None = None) -> dict:
    """Bossman wheels and every third-party dependency, into the runtime.

    Locked: pip downloads exactly the files the lock hashes (``--require-hashes``
    refuses anything else), installs them from that wheelhouse with
    ``--no-index`` (no resolver, no index), then the Bossman wheels go on top
    with ``--no-deps`` — their dependencies are the locked set by construction.
    Unlocked: the old ``pip install --upgrade --target`` resolution.
    """
    site_packages = runtime / "Lib" / "site-packages"
    built = sorted(wheels.glob("*.whl"))
    if not built:
        raise RuntimeError("no Bossman wheels to install")
    pip = [sys.executable, "-m", "pip"]
    if lock:
        wheelhouse = (work or wheels.parent) / "wheelhouse"
        wheelhouse.mkdir(parents=True, exist_ok=True)
        requirements = str(lock["_txt"])
        run([*pip, "download", "--no-deps", "--require-hashes", "--dest", str(wheelhouse),
             "--requirement", requirements])
        # --no-build-isolation: the lock's few source distributions (the
        # PyAutoGUI family ships no wheels) are built by the build runner's
        # PINNED setuptools/wheel (windows_bundle_build_tools.txt), never by a
        # backend pip would fetch from an index behind the lock's back.
        run([*pip, "install", "--no-index", "--find-links", str(wheelhouse), "--require-hashes",
             "--no-deps", "--no-build-isolation", "--target", str(site_packages),
             "--requirement", requirements])
        run([*pip, "install", "--no-index", "--no-deps", "--target", str(site_packages),
             *[str(wheel) for wheel in built]])
    else:
        run([*pip, "install", "--upgrade", "--target", str(site_packages), *pip_requirements(built)])
    listing = run([*pip, "list", "--path", str(site_packages), "--format=json"]).stdout

    entry_points = console_scripts(site_packages)
    # The wrappers pip left behind belong to the build machine's interpreter.
    # Shipping them would hand the owner commands that cannot run.
    for stale in ("bin", "Scripts"):
        shutil.rmtree(site_packages / stale, ignore_errors=True)
    scripts = runtime / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name, spec in sorted(entry_points.items()):
        (scripts / f"{name}.cmd").write_text(console_shim(spec), encoding="utf-8")
    return {"packages": json.loads(listing), "bossman_wheels": [w.name for w in built],
            "console_scripts": sorted(entry_points), "pinned": bool(lock),
            "resolver": "none: wheelhouse in pip hash-checking mode" if lock else "pip at build time"}


def normalize_bytecode(runtime: Path) -> dict:
    """Hash-based .pyc for the shipped packages: same sources, same bytes.

    pip compiles timestamp-based bytecode, and the timestamp is the moment the
    file was extracted on the build runner — two builds of one lock differed
    in every .pyc. ``unchecked-hash`` bytecode (PEP 552) depends only on the
    source, and a shipped runtime whose sources never change loses nothing.
    """
    python = runtime / "python.exe"
    site_packages = runtime / "Lib" / "site-packages"
    done = subprocess.run([str(python), "-I", "-m", "compileall", "-q", "-f",
                           "--invalidation-mode", "unchecked-hash", str(site_packages)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=1800)
    if done.returncode:
        # Some vendored files never compiled anywhere; say so, ship as is.
        print("  bytecode normalisation incomplete: " + (done.stderr or done.stdout or "")[-800:], flush=True)
    return {"normalized": done.returncode == 0, "mode": "unchecked-hash"}


def verify_runtime(runtime: Path, wheels: Path, lock: dict | None = None) -> dict:
    """Ask the EMBEDDED interpreter, in isolated mode, what it can import and sees.

    The build machine's Python is not the one the owner runs. The product
    modules must import from the runtime, and — when locked — the set of
    installed distributions must be exactly the lock plus the Bossman wheels:
    nothing missing, nothing extra, no other version.
    """
    python = runtime / "python.exe"
    probe = (
        "import importlib.metadata as m, json, re, sys\n"
        "import bcc, bossman, bossman_shared, playwright\n"
        "norm = lambda n: re.sub(r'[-_.]+', '-', n).lower()\n"
        "seen = {}\n"
        "for d in m.distributions():\n"
        "    name = norm(d.metadata['Name'])\n"
        "    seen.setdefault(name, set()).add(d.version)\n"
        "print(json.dumps({'distributions': {k: sorted(v) for k, v in seen.items()},"
        " 'bcc': bcc.__file__, 'executable': sys.executable, 'isolated': bool(sys.flags.isolated)}))\n"
    )
    done = subprocess.run([str(python), "-I", "-c", probe], cwd=str(runtime.parent),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if done.returncode:
        raise RuntimeError("the embedded interpreter cannot import the product: " + (done.stderr or "")[-1500:])
    payload = json.loads(done.stdout.strip().splitlines()[-1])
    if not Path(payload["bcc"]).resolve().is_relative_to(runtime.resolve()):
        raise RuntimeError(f"the embedded interpreter imported bcc from outside the runtime: {payload['bcc']}")
    seen = {name: versions for name, versions in payload["distributions"].items()}
    duplicated = sorted(name for name, versions in seen.items() if len(versions) > 1)
    if duplicated:
        raise RuntimeError(f"two versions of one distribution in the runtime: {duplicated}")
    result = {"verified_by_embedded_python": True, "distribution_count": len(seen),
              "isolated": payload["isolated"]}
    if lock:
        expected = dict(lock["_pins"])
        for wheel in sorted(wheels.glob("*.whl")):
            name, version = wheel.name.split("-")[:2]
            expected[lockmod.normalize(name)] = version
        actual = {name: versions[0] for name, versions in seen.items()}
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        wrong = sorted(name for name in set(expected) & set(actual) if expected[name] != actual[name])
        if missing or extra or wrong:
            raise RuntimeError("the embedded runtime is not the locked set: "
                               f"missing={missing} extra={extra} other_version={wrong}")
        result["matches_lock"] = True
    return result


def install_browser(browser: Path, runtime: Path, lock: dict | None = None) -> dict:
    """Chromium, resolved by the Playwright that is actually shipped.

    The locked Playwright version fixes the Chromium revision it installs;
    the lock names the directory that revision produced, and a different one
    means a different browser than the one accepted.
    """
    browser.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(browser))
    python = runtime / "python.exe"
    run([str(python), "-m", "playwright", "install", "chromium"], env=env)
    found = sorted(p.name for p in browser.glob("chromium-*"))
    if not found:
        raise RuntimeError("Playwright reported success but installed no Chromium")
    expected = (lock or {}).get("chromium") or {}
    pinned = bool(expected.get("directories"))
    if pinned and found != sorted(expected["directories"]):
        raise RuntimeError(f"the locked Playwright installed {found}, the lock names {expected['directories']}")
    return {"chromium_dirs": found, "pinned": pinned}


def install_media(media: Path, work: Path, ffmpeg_zip: str | None,
                  lock: dict | None = None) -> tuple[dict, list[dict]]:
    """ffmpeg + ffprobe. Missing is reported, never silently dropped.

    Locked: the immutable release asset from the lock, digest-checked before
    a single member is extracted; the URL given on the command line is ignored
    and said so. Unlocked: the URL as supplied.
    """
    media.mkdir(parents=True, exist_ok=True)
    expected_sha256 = None
    if lock:
        if ffmpeg_zip and ffmpeg_zip != lock["ffmpeg"]["url"]:
            print(f"  ignoring --ffmpeg-zip {ffmpeg_zip}: the lock pins {lock['ffmpeg']['asset']}", flush=True)
        ffmpeg_zip = lock["ffmpeg"]["url"]
        expected_sha256 = lock["ffmpeg"]["sha256"]
    if not ffmpeg_zip:
        return ({"ffmpeg": None, "ffprobe": None}, [{
            "component": "ffmpeg",
            "reason": "no --ffmpeg-zip supplied to the build",
            "acquired_by": "first run of Start-Bossman.cmd",
        }])
    archive = fetch_pinned(ffmpeg_zip, work / "ffmpeg.zip", expected_sha256)
    notices = []
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            name = Path(member).name.lower()
            if name in {"ffmpeg.exe", "ffprobe.exe"}:
                with zf.open(member) as src, (media / name).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            # Preserve upstream licence texts, including dependency notices.
            # Flattening them could replace distinct libraries' LICENSE files.
            parts = Path(member).parts
            is_notice = name.startswith(("license", "licence", "copying", "readme")) or any(
                part.lower() in {"licenses", "licences"} for part in parts)
            if is_notice and not member.endswith("/"):
                if Path(member).is_absolute() or ".." in parts or any(":" in p for p in parts):
                    raise RuntimeError(f"unsafe media notice path: {member}")
                target = media.parent / "LICENSES" / "ffmpeg" / member
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                notices.append(target.relative_to(media.parent).as_posix())
    missing = [n for n in ("ffmpeg.exe", "ffprobe.exe") if not (media / n).exists()]
    if missing:
        raise RuntimeError(f"{ffmpeg_zip} contained no {', '.join(missing)}")
    return ({"ffmpeg": "media/ffmpeg.exe", "ffprobe": "media/ffprobe.exe",
             "source": ffmpeg_zip, "sha256": sha256_file(archive),
             "pinned": expected_sha256 is not None,
             "license_files": notices}, [])


def install_icons(icons: Path) -> dict:
    icons.mkdir(parents=True, exist_ok=True)
    source = ROOT / "command-center" / "ui" / "icons"
    shipped = {}
    for name in ICON_FILES:
        origin = source / name
        if not origin.exists():
            raise RuntimeError(f"canonical icon missing from the checkout: {name}")
        shutil.copyfile(origin, icons / name)
        shipped[name] = sha256_file(icons / name)
    return shipped


def oss_directions_for(packages: list[dict]) -> list[dict]:
    """BUNDLED / CONFIGURED / MODEL_REQUIRED / EXTERNAL_SERVICE_REQUIRED / VERIFIED per direction.

    ``bundled`` is read from what was actually installed into the runtime, not
    from the extras the build asked for. ``configured`` is always the owner's
    act after unzipping; ``verified`` is never true in a manifest.
    """
    installed = {lockmod.normalize(item.get("name", "")): item.get("version") for item in packages}
    rows = []
    for spec in OSS_DIRECTIONS:
        distribution = spec["distribution"]
        bundled = distribution is None and not spec["external_service_required"] or (
            distribution is not None and lockmod.normalize(distribution) in installed)
        rows.append({
            "direction": spec["direction"], "surface": spec["surface"],
            "BUNDLED": bundled,
            "bundled_distribution": (f"{distribution}=={installed[lockmod.normalize(distribution)]}"
                                     if distribution and lockmod.normalize(distribution) in installed else None),
            "CONFIGURED": "by the owner after unzipping; a card that says «Настроено» is configuration, not a done task",
            "MODEL_REQUIRED": spec["model_required"],
            "EXTERNAL_SERVICE_REQUIRED": spec["external_service_required"],
            "VERIFIED": False,
            "verify_by": spec["verify_by"],
        })
    return rows


def install_owner_run(target: Path) -> dict:
    """Кладёт комплект владельческого прогона и сверяет его в обе стороны.

    Возвращает имена и хэши — они попадут в MANIFEST.json вместе с остальным
    содержимым, и в SHA256SUMS как обычные файлы поставки. То есть комплект
    зафиксирован ДО сборки, а не описан после неё.
    """
    declared = set(OWNER_RUN_FILES)
    missing = sorted(n for n in declared if not (OWNER_RUN_SOURCE / n).is_file())
    if missing:
        raise RuntimeError(
            "комплект владельческого прогона неполон, архив собирать нельзя: "
            + ", ".join(missing)
            + f" (ожидались в {OWNER_RUN_SOURCE})")
    present = {p.name for p in OWNER_RUN_SOURCE.iterdir() if p.is_file()}
    extra = sorted(present - declared)
    if extra:
        raise RuntimeError(
            "в комплекте владельческого прогона есть файлы мимо контракта: "
            + ", ".join(extra)
            + " — объявите их в OWNER_RUN_FILES или уберите из "
            + str(OWNER_RUN_SOURCE))

    target.mkdir(parents=True, exist_ok=True)
    shipped: dict[str, str] = {}
    for name in OWNER_RUN_FILES:
        origin = OWNER_RUN_SOURCE / name
        shutil.copyfile(origin, target / name)
        shipped[name] = sha256_file(origin)
    return {"path": "app-support/owner-final-run", "files": shipped}



def install_owner_acceptance(home: Path) -> None:
    """Ship the complete HW-01..HW-13 protocol, never a hardware PASS claim."""
    missing = [name for name in OWNER_ACCEPTANCE_FILES if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError("owner acceptance input missing: " + ", ".join(missing))
    try:
        protocol = json.loads((ROOT / "tests/owner_hardware/manifest.json").read_text(encoding="utf-8"))
        cases = protocol["cases"]
        expected = {f"HW-{number:02d}" for number in range(1, 14)}
        if (not isinstance(cases, list) or len(cases) != len(expected)
                or {case["id"] for case in cases} != expected
                or any(not isinstance(case.get("checks"), list) or not case["checks"]
                       or any(not isinstance(check, str) or not check.strip() for check in case["checks"])
                       for case in cases)):
            raise ValueError("expected HW-01..HW-13 with nonempty checks")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("owner acceptance manifest is invalid: expected HW-01..HW-13") from exc
    for name in OWNER_ACCEPTANCE_FILES:
        target = home / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)


def install_support(support: Path) -> dict:
    support.mkdir(parents=True, exist_ok=True)
    install_owner_acceptance(support.parent)
    for origin, name in SUPPORT_SCRIPTS:
        if not origin.exists():
            raise RuntimeError(f"support script missing: {origin}")
        shutil.copyfile(origin, support / name)
    for origin, name in SUPPORT_DATA:
        if not origin.exists():
            raise RuntimeError(f"support data missing: {origin}")
        (support / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, support / name)
    install_coaching_pack(support / COACHING_PACK_TARGET)
    return install_owner_run(support / "owner-final-run")


def self_improve_inputs() -> list[str]:
    """Каждый обязательный вход профиля self-improve-mvcr, как путь в checkout."""
    names = [str(origin.relative_to(ROOT)).replace("\\", "/") for origin, _ in SELF_IMPROVE_REQUIRED]
    names.append(str(SELF_IMPROVE_CASES_SOURCE.relative_to(ROOT)).replace("\\", "/") + "/**")
    return names + list(SELF_IMPROVE_DOCS)


def install_self_improve(home: Path) -> dict:
    """Скрипты, профили моделей, кейсы и OWNER_RUN_NEXT.md профиля self-improve-mvcr.

    Все входы обязательны; недостающие перечисляются разом, и сборка падает.
    """
    missing = [str(origin) for origin, _ in SELF_IMPROVE_REQUIRED if not origin.is_file()]
    cases = sorted(p for p in SELF_IMPROVE_CASES_SOURCE.rglob("*") if p.is_file()) \
        if SELF_IMPROVE_CASES_SOURCE.is_dir() else []
    if not cases:
        missing.append(f"{SELF_IMPROVE_CASES_SOURCE} (нет ни одного кейса)")
    missing += [str(ROOT / name) for name in SELF_IMPROVE_DOCS if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError("профиль self-improve-mvcr неполон, архив собирать нельзя: " + ", ".join(missing))
    support = home / "app-support"
    support.mkdir(parents=True, exist_ok=True)
    shipped: dict[str, str] = {}
    for origin, name in SELF_IMPROVE_REQUIRED:
        shutil.copyfile(origin, support / name)
        shipped[f"app-support/{name}"] = sha256_file(origin)
    for origin in cases:
        rel = origin.relative_to(SELF_IMPROVE_CASES_SOURCE).as_posix()
        target = support / SELF_IMPROVE_CASES_TARGET / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        shipped[f"app-support/{SELF_IMPROVE_CASES_TARGET}/{rel}"] = sha256_file(origin)
    for name in SELF_IMPROVE_DOCS:
        target = home / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
        shipped[name] = sha256_file(ROOT / name)
    return {"profile": "self-improve-mvcr", "files": shipped}


def install_coaching_pack(target: Path) -> dict:
    """Пак coaching (train/holdout JSON) — файл в файл, без чужих файлов."""
    if not COACHING_PACK_SOURCE.is_dir():
        raise RuntimeError(f"coaching pack missing: {COACHING_PACK_SOURCE}")
    shipped: dict[str, str] = {}
    for split in ("train", "holdout"):
        files = sorted((COACHING_PACK_SOURCE / split).glob("*.json"))
        if len(files) < 5:
            raise RuntimeError(f"coaching pack {split}: expected at least 5 tasks, found {len(files)}")
        (target / split).mkdir(parents=True, exist_ok=True)
        for origin in files:
            shutil.copyfile(origin, target / split / origin.name)
            shipped[f"{split}/{origin.name}"] = sha256_file(origin)
    return {"path": "app-support/coaching-pack", "files": shipped}


def write_licenses(licenses: Path, contents: dict) -> None:
    licenses.mkdir(parents=True, exist_ok=True)
    (licenses / "README.md").write_text(
        "# Third-party components redistributed in this archive\n\n"
        f"* CPython {contents['runtime'].get('python_version', 'embeddable')} — "
        "Python Software Foundation License, https://docs.python.org/3/license.html\n"
        "* Python dependencies under `runtime/Lib/site-packages` — each distribution "
        "keeps its own `*.dist-info/METADATA` and licence files; see MANIFEST.json "
        "for the exact list and versions.\n"
        "* Chromium (via Playwright) under `browser/` — BSD-3-Clause and the licences "
        "listed in the browser directory.\n"
        "* ffmpeg/ffprobe under `media/`, when bundled — the licence of the build named "
        "in MANIFEST.json `contents.media.source`. Check that build's terms before "
        "redistributing this archive outside your organisation.\n",
        encoding="utf-8")


# ------------------------------------------------------------------ assemble

def assemble(out: Path, sha: str, *, wheels: Path, work: Path,
             ffmpeg_zip: str | None, lock: dict | None = None) -> tuple[dict, list[dict]]:
    for name in BUNDLE_DIRS:
        (out / name).mkdir(parents=True, exist_ok=True)
    contents: dict = {}
    contents["runtime"] = install_runtime(out / "runtime", work, lock)
    contents["runtime"].update(install_packages(out / "runtime", wheels, lock, work))
    contents["runtime"]["bytecode"] = normalize_bytecode(out / "runtime")
    contents["runtime"]["embedded_check"] = verify_runtime(out / "runtime", wheels, lock)
    contents["browser"] = install_browser(out / "browser", out / "runtime", lock)
    media, required = install_media(out / "media", work, ffmpeg_zip, lock)
    contents["media"] = media
    contents["icons"] = install_icons(out / "icons")
    contents["owner_run"] = install_support(out / "app-support")
    contents["self_improve"] = install_self_improve(out)
    for name, body in launcher_files().items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.replace("\n", "\r\n"), encoding="utf-8")
    write_licenses(out / "LICENSES", contents)
    return contents, required


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--ffmpeg-zip", default=os.environ.get("BOSSMAN_FFMPEG_ZIP") or None,
                        help="URL of a Windows ffmpeg zip containing ffmpeg.exe and ffprobe.exe")
    parser.add_argument("--zip", action="store_true", help="also write the .zip archive")
    parser.add_argument("--profile", choices=("release", "diagnostic"), default="release",
                        help="release (default) fails on anything the first run would have to fetch")
    args = parser.parse_args(argv)

    if os.name != "nt":
        print("BOSSMAN_WINDOWS_BUNDLE=UNSUPPORTED_HOST "
              f"(needs Windows, this is {platform.system()})", file=sys.stderr)
        return 2

    import build_local_bundle as local

    sha = local._source_sha()
    if local._source_dirty():
        print("BOSSMAN_WINDOWS_BUNDLE=FAIL dirty source tree", file=sys.stderr)
        return 2

    out_root = args.out.resolve()
    out = out_root / bundle_name(sha)
    if out.exists():
        raise SystemExit(f"output already exists, choose another --out: {out}")
    out.mkdir(parents=True)
    work = out_root / "_work"
    work.mkdir(parents=True, exist_ok=True)

    lock = lockmod.load()
    print(f"building {out.name} from {sha} "
          f"({'inputs locked: ' + lock['recorded_at'] if lock else 'BOSSMAN_BUILD_INPUTS=UNLOCKED'})", flush=True)
    wheels = work / "wheels"
    with local.clean_source_snapshot(sha) as snapshot:
        # Locked: the Bossman wheels too are built by the pinned build tools,
        # not by a setuptools pip would resolve at build time.
        local.build_wheels(wheels, snapshot, build_isolation=not lock)

    contents, required = assemble(out, sha, wheels=wheels, work=work, ffmpeg_zip=args.ffmpeg_zip, lock=lock)
    if args.profile == "release" and required:
        raise SystemExit("BOSSMAN_WINDOWS_BUNDLE=FAIL release profile: the archive would still have to fetch "
                         + ", ".join(str(item.get("component")) for item in required)
                         + " on first run; supply --ffmpeg-zip or record the lock")

    if local._source_sha() != sha or local._source_dirty():
        raise SystemExit("source changed during the build; artifact is not exact-SHA evidence")

    when = datetime.now(timezone.utc).isoformat()
    build_inputs = build_inputs_for(lock, args.profile, contents)
    files = [{"path": p.relative_to(out).as_posix(), "bytes": p.stat().st_size,
              "sha256": sha256_file(p)}
             for p in sorted(out.rglob("*")) if p.is_file()]
    manifest = manifest_for(sha=sha, when=when, files=files, contents=contents,
                            checks={"installed_acceptance": "NOT_RUN"},
                            required_downloads=required)
    manifest["build_inputs"] = build_inputs
    manifest["oss_directions"] = oss_directions_for(contents["runtime"].get("packages", []))
    (out / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "SHA256SUMS").write_text(
        "".join(f"{f['sha256']}  {f['path']}\n" for f in files), encoding="utf-8")
    (out / "README.md").write_text(
        f"# BOSSMAN for Windows\n\nBuild `{sha}`, {when}.\n\n"
        "Unzip anywhere, then double-click **Start-Bossman.cmd**.\n"
        "There is nothing else to install: the Python runtime, every dependency, "
        "the browser and the media tools are inside this folder.\n\n"
        "**Evening-Test.cmd** runs the owner acceptance for exactly this build and "
        "prints the build SHA, platform, doctor state and the final verdict.\n\n"
        "`app-support/owner-final-run/` holds the owner's own run: start with "
        "`README_RU.md` there. Those files are listed in MANIFEST.json, and "
        "Evening-Test.cmd fails if any of them is missing or altered.\n\n"
        "The current offline first-run protocol is in `OWNER_ACCEPTANCE.md` and "
        "`tests/owner_hardware/manifest.json` (HW-01..HW-13). "
        "`owner-acceptance.ps1` checks the installed core; it does not certify all hardware cases.\n\n"
        "Your data lives in `%LOCALAPPDATA%\\Bossman\\CommandCenter` and survives "
        "replacing this folder with a newer build.\n",
        encoding="utf-8")

    total = sum(f["bytes"] for f in files)
    print(f"BOSSMAN_WINDOWS_BUNDLE=BUILT files={len(files)} bytes={total} "
          f"required_downloads={len(required)}", flush=True)
    print(f"BOSSMAN_BUILD_INPUTS={'LOCKED' if build_inputs['locked'] else 'UNLOCKED'} "
          f"profile={args.profile}", flush=True)

    if args.zip:
        archive = out_root / f"{out.name}.zip"
        write_archive(out, archive, commit_time(sha))
        print(f"BOSSMAN_WINDOWS_ARCHIVE={archive} bytes={archive.stat().st_size}", flush=True)
    return 0


def build_inputs_for(lock: dict | None, profile: str, contents: dict) -> dict:
    """What the manifest says about how its inputs were chosen."""
    if not lock:
        return {"locked": False, "profile": profile, "lock": None,
                "note": "inputs resolved at build time; not a release candidate until recorded in "
                        "tools/windows_bundle_lock.json"}
    return {
        "locked": True, "profile": profile,
        "lock": {"recorded_at": lock["recorded_at"], "requirements_sha256": lock["requirements"]["sha256"],
                 "requirements": lock["requirements"]["count"],
                 "python": lock["python"]["version"], "ffmpeg": lock["ffmpeg"]["asset"],
                 "chromium": (lock.get("chromium") or {}).get("directories")},
        "known_differences_between_builds": [
            "*.dist-info/direct_url.json of the three Bossman wheels carries the build-time wheel path",
            "bytecode is unchecked-hash and identical for identical sources; files compileall could not "
            "compile keep pip's timestamp-based .pyc",
            "MANIFEST.json built_at and the zip entry order are the only build-time values in the archive",
        ],
    }


def commit_time(sha: str) -> int:
    """The commit's own timestamp: the one build-time-free clock the archive has."""
    try:
        return int(run(["git", "-C", str(ROOT), "show", "-s", "--format=%ct", sha]).stdout.strip())
    except (RuntimeError, ValueError):
        return 315532800  # 1980-01-01, the zip epoch


def write_archive(out: Path, archive: Path, timestamp: int) -> None:
    """One zip, entries sorted, every entry stamped with the commit time.

    ``zipfile.write`` copies the file's mtime — the extraction moment on the
    build runner — into each entry; two builds of one commit then differ in
    every header. Stamping the commit time keeps the zip a function of its
    contents.
    """
    stamp = datetime.fromtimestamp(max(timestamp, 315532800), timezone.utc).timetuple()[:6]
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(out.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(str(Path(out.name) / path.relative_to(out)).replace(os.sep, "/"), date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            with path.open("rb") as stream:
                zf.writestr(info, stream.read())


if __name__ == "__main__":
    raise SystemExit(main())
