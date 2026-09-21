#!/usr/bin/env python3
"""Поставить Bossman так, как его ставит владелец, и вернуть путь к установке.

    python scripts/installed_product_install.py --out dist/installed --json install.json

Ставится НЕ рабочая копия. Колёса собираются из одноразового git-worktree на
HEAD (`tools/build_local_bundle.clean_source_snapshot`), поэтому:

* в колесо попадает ровно то, что закоммичено, а не то, что лежит в дереве;
* `command-center/setup.py` во время сборки читает git и кладёт в колесо
  `bcc/_build.json` с настоящим SHA и `source_dirty: false`. Из-за этого
  установленный продукт УМЕЕТ назвать свой коммит, а `/api/identity` и
  интерфейс показывают «установленная сборка», а не «рабочий чекаут». Экспорт
  `git archive` этого не даёт: в распакованном архиве нет `.git`, личность
  источника выходит пустой, и установка на вид перестаёт отличаться от
  чекаута.

Скрипт НИЧЕГО не проверяет и никакого вердикта не выносит: приёмку делает
`tools/installed_product_chain.py` уже установленным интерпретатором. Разделение
умышленное — установщик, который сам себя принимает, не проверка.

Коды выхода: 0 — установлено, 1 — установить не удалось.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bundle_module():
    """Сборка колёс уже написана. Второй её экземпляр неизбежно разойдётся."""
    spec = importlib.util.spec_from_file_location(
        "build_local_bundle", ROOT / "tools" / "build_local_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(cmd: list, *, timeout: int = 2400, **kw) -> subprocess.CompletedProcess:
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    done = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, **kw)
    if done.returncode:
        detail = (done.stderr or done.stdout or "").strip()
        raise SystemExit(f"не отработало ({done.returncode}): {' '.join(str(c) for c in cmd)}\n"
                         + detail[-8000:])
    return done


def venv_python(env_dir: Path) -> Path:
    return env_dir / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")


def install(out: Path, *, browser: bool, allow_dirty: bool) -> dict:
    bundle = _bundle_module()
    sha = bundle._source_sha()
    dirty = bundle._source_dirty()
    if dirty and not allow_dirty:
        raise SystemExit("дерево грязное: установка из него не является уликой для коммита. "
                         "Закоммитьте правки или запустите с --allow-dirty (диагностика).")
    out.mkdir(parents=True, exist_ok=True)
    wheels = out / "wheels"
    print(f"источник {sha} (dirty={dirty}) -> {out}", flush=True)
    if dirty:
        bundle.build_wheels(wheels)
    else:
        with bundle.clean_source_snapshot(sha) as snapshot:
            bundle.build_wheels(wheels, snapshot)
    built = sorted(wheels.glob("*.whl"))

    env_dir = out / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    python = venv_python(env_dir)
    env = dict(os.environ)
    # Ничто из окружения не имеет права подсунуть установке чужой код: именно
    # это и отличает поставку от рабочей копии.
    for key in ("PYTHONPATH", "PYTHONHOME", "BCC_UI_DIR", "BCC_DATA_DIR", "DATABASE_URL"):
        env.pop(key, None)
    _run([python, "-m", "pip", "install", "--upgrade", "pip", "wheel"], env=env, cwd=out)
    # `pip install <wheel>` — это НЕ editable: колесо распаковывается в
    # site-packages, и рабочая копия после установки продукту не нужна вовсе.
    _run([python, "-m", "pip", "install", *bundle.pip_requirements(built)], env=env, cwd=out)
    _run([python, "-m", "pip", "check"], env=env, cwd=out)
    if browser:
        # Браузер ставится В УСТАНОВКУ, а не в раннер: страницу открывает сам
        # установленный продукт своим же playwright.
        _run([python, "-m", "playwright", "install", "chromium"], env=env, cwd=out)

    listing = json.loads(_run([python, "-m", "pip", "list", "--format=json"],
                              env=env, cwd=out).stdout)
    report = {
        "schema_version": 1,
        "source_sha": sha,
        "source_dirty": bool(dirty),
        "python": str(python),
        "prefix": str(env_dir.resolve()),
        "wheels": [{"имя": w.name, "байт": w.stat().st_size} for w in built],
        "браузер_в_установке": bool(browser),
        "пакетов_в_установке": len(listing),
        "когда": datetime.now(timezone.utc).isoformat(),
    }
    (out / "installed-product-install.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="куда ставить")
    parser.add_argument("--json", type=Path, help="куда положить отчёт об установке")
    parser.add_argument("--no-browser", action="store_true",
                        help="не ставить Chromium в установку (страница проверена не будет)")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="диагностика: установка из грязного дерева, НЕ улика для коммита")
    args = parser.parse_args(argv)
    report = install(args.out.resolve(), browser=not args.no_browser,
                     allow_dirty=args.allow_dirty)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    print("INSTALLED_PRODUCT_PYTHON=" + report["python"], flush=True)
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write("INSTALLED_PRODUCT_PYTHON=" + report["python"].replace("\\", "/") + "\n")
            stream.write("INSTALLED_PRODUCT_SHA=" + report["source_sha"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
