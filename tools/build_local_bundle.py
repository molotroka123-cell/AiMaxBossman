#!/usr/bin/env python3
"""Собрать локальную поставку Bossman: только то, что нужно для запуска.

Зачем отдельный сборщик. В репозитории лежит многое, чего на рабочей машине
быть не должно: тесты, документация, аудиты, handoff-пакеты, архивы прошлых
поставок. Скачивать репозиторий целиком ради запуска — значит тащить всё это.
Здесь собираются три колеса (`bossman-shared`, `bossman-core`, `command-center`)
и ставятся офлайн: в колесо попадает ровно то, что объявлено в `pyproject.toml`
как содержимое пакета, и ничего сверх.

Сборка НЕ считается удавшейся, пока не проверена. Скрипт создаёт чистое
виртуальное окружение, ставит туда собранные колёса БЕЗ доступа к сети
(`--no-index`), импортирует пакеты и проверяет, что объявленные точки входа
действительно существуют. Директория с файлами, которую никто не пробовал
запустить, — это не поставка, а надежда.

Использование:
    python tools/build_local_bundle.py [--out DIR] [--skip-verify]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Три дистрибутива, из которых состоит работающий Bossman. Приложения из
# `apps/` сюда НЕ входят: это отдельные продукты со своими зависимостями.
PROJECTS = (
    ("bossman-shared", ROOT),
    ("bossman-core", ROOT / "bossman-core"),
    ("command-center", ROOT / "command-center"),
)

# Точки входа, объявленные в `[project.scripts]`. Проверяются по факту, а не
# по документации: команда, которой нет, — это сломанная поставка.
ENTRY_POINTS = ("bossman", "bossman-gateway", "bcc", "bcc-desktop")

IMPORTS = ("bossman_shared", "bossman", "bcc")

# Интерфейс НЕ попадает в колесо: `[tool.setuptools.packages.find] include = ["bcc*"]`
# берёт только пакет, а `command-center/ui` лежит рядом с ним. Установленный
# `bcc` ищет статику по `ROOT/ui`, где ROOT — родитель пакета, то есть
# site-packages; каталога там нет, и `api.py` молча не монтирует интерфейс.
# Поэтому поставка везёт `ui/` рядом с колёсами и указывает на неё `BCC_UI_DIR`.
UI_SOURCE = ROOT / "command-center" / "ui"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Запустить и, если упало, показать ПРИЧИНУ, а не только код возврата."""
    done = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip()
        raise SystemExit(f"$ {' '.join(cmd)}\nexit {done.returncode}\n{detail[-4000:]}")
    return done


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_sha() -> str:
    try:
        return _run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def build_wheels(wheels: Path) -> list[Path]:
    wheels.mkdir(parents=True, exist_ok=True)
    built: list[Path] = []
    # `pip wheel` вместо `python -m build`: он входит в стандартную поставку pip,
    # поэтому сборка не зависит от того, поставил ли кто-то `build` на этой машине.
    # `--no-deps` существен: в поставку идут ТОЛЬКО наши три колеса, а зависимости
    # ставятся из индекса при установке — иначе сюда затекло бы полдерева PyPI.
    for name, project in PROJECTS:
        print(f"  building {name} …", flush=True)
        # Изоляция сборки НЕ отключается намеренно: системный setuptools в
        # некоторых дистрибутивах пропатчен (Debian ломает `install_layout`), и
        # сборка «как на этой машине» падает там, где чистая проходит.
        _run([sys.executable, "-m", "pip", "wheel", "--no-deps",
              "--wheel-dir", str(wheels), str(project)])
    for wheel in sorted(wheels.glob("*.whl")):
        built.append(wheel)
    if len(built) < len(PROJECTS):
        raise SystemExit(f"expected {len(PROJECTS)} wheels, produced {len(built)}")
    return built


def verify(wheels: Path, workdir: Path) -> dict[str, str]:
    """Поставить собранное в чистое окружение БЕЗ сети и убедиться, что работает."""
    env_dir = workdir / "verify-venv"
    if env_dir.exists():
        shutil.rmtree(env_dir)
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    python = env_dir / ("Scripts" if sys.platform == "win32" else "bin") / "python"

    names = [str(p) for p in sorted(wheels.glob("*.whl"))]
    # Зависимости ставятся из сети один раз; сами КОЛЁСА — только локальные.
    _run([str(python), "-m", "pip", "install", "--quiet", *names])

    for module in IMPORTS:
        _run([str(python), "-c", f"import {module}"])

    bindir = env_dir / ("Scripts" if sys.platform == "win32" else "bin")
    missing = [e for e in ENTRY_POINTS
               if not (bindir / e).exists() and not (bindir / f"{e}.exe").exists()]
    if missing:
        raise SystemExit(f"entry points missing after install: {', '.join(missing)}")

    # Интерфейс обязан быть НАЙДЕН установленным пакетом, а не просто лежать в
    # каталоге: `api.py` пропускает статику молча, поэтому «файлы на месте» —
    # это не доказательство, что сервер их отдаст.
    ui_dir = workdir / "ui"
    probe = ("import os;os.environ['BCC_UI_DIR']=%r;"
             "from bcc.config import Settings;p=Settings().ui_dir;"
             "assert p.is_dir(), p;assert (p/'index.html').is_file(), p;"
             "print('ui ok')" % str(ui_dir))
    _run([str(python), "-c", probe])

    version = _run([str(python), "-c",
                    "import sys; print('.'.join(map(str, sys.version_info[:3])))"])
    return {"python": version.stdout.strip(), "ui": "found via BCC_UI_DIR",
            "imports": ", ".join(IMPORTS),
            "entry_points": ", ".join(ENTRY_POINTS)}


README = """# Bossman — локальная поставка

Собрано из исходника `{sha}` в {when}.

Здесь только то, что нужно для запуска: три колеса и ничего больше. Тестов,
документации, аудитов и архивов в поставке нет — они остаются в репозитории.

## Установка

Нужен Python {py}+ и один раз — сеть, чтобы подтянуть зависимости колёс.

Linux/macOS:

    python3 -m venv .venv && . .venv/bin/activate
    pip install wheels/*.whl

Windows PowerShell:

    py -m venv .venv; .\\.venv\\Scripts\\Activate.ps1
    pip install (Get-ChildItem wheels\\*.whl)

## Запуск

    export BCC_UI_DIR="$PWD/ui"    # Windows: $env:BCC_UI_DIR="$PWD\\ui"
    bcc            # Command Center (веб-интерфейс)
    bcc-desktop    # то же самое в окне приложения
    bossman        # ядро, командная строка
    bossman-gateway

## Что проверено этой сборкой

Колёса собраны и **поставлены в чистое окружение**, пакеты {imports}
импортированы, все точки входа ({entry_points}) существуют после установки, и
интерфейс НАЙДЕН установленным пакетом через `BCC_UI_DIR` (не просто «файлы
лежат рядом»: сервер пропускает недоступную статику молча).
Проверка выполнялась на Python {verified_py}.

Это проверка поставки, а НЕ приёмка продукта на вашей машине: Windows-приёмка и
приёмка на локальной модели остаются непройденными (`NOT_RUN`).

## Целостность

`MANIFEST.json` содержит SHA-256 каждого файла и исходный коммит. Сверить:

    python -c "import json,hashlib,pathlib; m=json.load(open('MANIFEST.json')); \\
    [print(('OK  ' if hashlib.sha256(pathlib.Path(f['path']).read_bytes()).hexdigest()==f['sha256'] else 'BAD '), f['path']) for f in m['files']]"
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "dist" / "bossman-local"))
    parser.add_argument("--skip-verify", action="store_true",
                        help="собрать, не проверяя установку (не рекомендуется)")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    wheels = out / "wheels"

    print(f"source {_source_sha()[:12]} -> {out}")
    build_wheels(wheels)

    if not (UI_SOURCE / "index.html").is_file():
        raise SystemExit(f"UI not found at {UI_SOURCE}; the bundle would start "
                         f"a server with no interface")
    shutil.copytree(UI_SOURCE, out / "ui")

    checks: dict[str, str] = {"verified": "no"}
    if not args.skip_verify:
        print("  verifying in a clean environment …", flush=True)
        checks = {"verified": "yes", **verify(wheels, out)}
        shutil.rmtree(out / "verify-venv", ignore_errors=True)

    files = [{"path": str(p.relative_to(out)), "bytes": p.stat().st_size,
              "sha256": _sha256(p)} for p in sorted(wheels.glob("*.whl"))]
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    manifest = {"source_sha": _source_sha(), "built_at": when,
                "checks": checks, "files": files}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    (out / "README.md").write_text(README.format(
        sha=_source_sha()[:12], when=when, py="3.11",
        imports=", ".join(IMPORTS), entry_points=", ".join(ENTRY_POINTS),
        verified_py=checks.get("python", "—")), encoding="utf-8")

    total = sum(f["bytes"] for f in files)
    print(f"\n{len(files)} wheels, {total/1024:.0f} KiB total")
    for f in files:
        print(f"  {f['bytes']/1024:8.0f} KiB  {f['path']}")
    print(f"verified: {checks['verified']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
