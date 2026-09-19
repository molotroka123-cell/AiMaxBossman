#!/usr/bin/env python3
"""Замер отзывчивости против бюджета, зафиксированного ДО замера.

«Летает» — не вердикт, а ощущение, пока у него нет чисел и границы. Граница
лежит в `tools/responsiveness_budget.json` и зафиксирована 18.09 до первого
запуска этого файла. Здесь она только ЧИТАЕТСЯ: поднять число можно лишь
правкой того файла И `tests/test_responsiveness_budget.py`, то есть видимым
коммитом, а не по итогу неудачного прогона.

Что меряется настоящим браузером на живом приложении:

* навигация между разделами, p50 и p95 — от нажатия до появления содержимого,
  а не время ответа ручки;
* галерея на 100 и на 1000 объектах — до первой отрисованной карточки;
* 50 циклов открыть/закрыть — рост RSS ВСЕГО дерева процессов;
* прогон на выдержку (`--soak-seconds`) — рост RSS дерева и CPU покоя.

Чего этот файл НЕ меряет и не притворяется, что меряет:

* холодный и тёплый старт до интерактивного UI — только установленный архив
  на машине владельца, здесь `OWNER_REQUIRED`;
* режим `--mode reference` (Linux, из исходников, приложение в этом же
  процессе) даёт `REFERENCE_ONLY`: это НЕ доказательство об установленном
  архиве Windows, и дерево процессов там другое.

Строка без замера получает `INSUFFICIENT_EVIDENCE`, а не `PASS`.

Коды выхода: 0 — ни одного FAIL; 1 — есть FAIL; 2 — FAIL нет, но есть
строки без улик.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CC = REPO / "command-center"
BUDGET_FILE = REPO / "tools" / "responsiveness_budget.json"

INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
OWNER_REQUIRED = "OWNER_REQUIRED"
REFERENCE = "REFERENCE_ONLY"
REFERENCE_OVER = "REFERENCE_ONLY_OVER_BUDGET"

# Покой — это покой. Первый часовой прогон считал CPU за весь прогон, в котором
# проба делала переход каждые две секунды (1734 перехода за час): получилась
# стоимость РАБОТЫ, а не покоя, 22.4 % при пределе 2. Ошибка была в
# измерителе, поэтому бюджет остался прежним, а мерить стали правильно: после
# нагрузки проба замолкает и наблюдает дерево в тишине.
IDLE_WINDOW_SECONDS = 180.0

# Разделы, по которым ходит замер навигации. Берутся не из фантазии, а из
# реестра страниц UI — тем же способом, что и обход §23.
NAV_SAMPLE = 20


def load_budget(path: Path = BUDGET_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("fixed_before_measurement"):
        raise SystemExit("бюджет не помечен как зафиксированный до замера — "
                         "замер по нему ничего не доказывает")
    return data


class Line:
    """Одна строка бюджета: предел, замер, вердикт и чем он подтверждён."""

    def __init__(self, key: str, spec: dict) -> None:
        self.key = key
        self.limit = float(spec["limit"])
        self.means = spec["means"]
        self.where = spec["where"]
        self.measured: float | None = None
        self.verdict = INSUFFICIENT
        self.detail = "замер не проводился"

    def owner_required(self, why: str) -> None:
        self.verdict, self.detail = OWNER_REQUIRED, why

    def observe(self, value: float, *, reference: bool, detail: str = "") -> None:
        self.measured = value
        within = value <= self.limit
        if reference:
            # Справочность означает «не доказывает установленный архив
            # Windows», а не «не считается». Превышение обязано быть видно в
            # строке вердикта, а не только в пояснении.
            self.verdict = REFERENCE if within else REFERENCE_OVER
        else:
            self.verdict = "PASS" if within else "FAIL"
        self.detail = (f"{value:.1f} против предела {self.limit:.1f}"
                       f"{' — В БЮДЖЕТЕ' if within else ' — ВЫШЕ БЮДЖЕТА'}"
                       + (f"; {detail}" if detail else ""))

    def as_dict(self) -> dict:
        return {"metric": self.key, "limit": self.limit, "measured": self.measured,
                "verdict": self.verdict, "detail": self.detail, "where": self.where}


def idle_window_for(soak_seconds: float) -> float:
    """Сколько наблюдать дерево в тишине после нагрузки."""
    if soak_seconds <= 0:
        return 0.0
    return max(IDLE_WINDOW_SECONDS, soak_seconds * 0.05)


def overall_verdict(verdicts: list[str]) -> str:
    """Один итог по строкам. Порядок строгости, а не порядок перечисления."""
    for state in ("FAIL", INSUFFICIENT, REFERENCE_OVER, REFERENCE, OWNER_REQUIRED):
        if state in verdicts:
            return state
    return "PASS"


def _tree_rss_mib(root_pid: int | None = None) -> float:
    """RSS всего дерева, а не главного процесса.

    Одно число по главному процессу не отвечает на вопрос «летает ли»:
    воркеры, встроенный браузер и ffmpeg живут отдельными процессами и едят
    ту же память владельца.
    """
    import psutil
    root = psutil.Process(root_pid or os.getpid())
    total = 0
    for proc in [root, *root.children(recursive=True)]:
        try:
            total += proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total / (1024 * 1024)


def _tree_cpu_seconds(root_pid: int | None = None) -> float:
    import psutil
    root = psutil.Process(root_pid or os.getpid())
    total = 0.0
    for proc in [root, *root.children(recursive=True)]:
        try:
            times = proc.cpu_times()
            total += times.user + times.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def _seed_gallery(data_dir: Path, db_url: str, count: int, offset: int = 0) -> None:
    """Кладёт `count` настоящих записей галереи в настоящую схему."""
    import hashlib

    import sqlalchemy as sa
    from bcc.studio.tables import runs

    media = data_dir / "seeded"
    media.mkdir(parents=True, exist_ok=True)
    payload = media / "seed.png"
    if not payload.exists():
        payload.write_bytes(b"\x89PNG\r\n\x1a\n" + b"seeded gallery asset" * 32)
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()

    engine = sa.create_engine(db_url.replace("sqlite+aiosqlite", "sqlite"))
    try:
        with engine.begin() as conn:
            conn.execute(runs.insert(), [{
                "id": f"seed-{offset + n:05d}", "job_id": None, "legacy_asset_id": None,
                "surface": "image", "model": "mock-image:seed",
                # Форма provenance — та же, что пишет runtime: галерея читает
                # `plane.prompt`, и запись без него обрушила бы страницу, а
                # проба записала бы это как медленную галерею. Так и вышло на
                # первом прогоне: обе строки дали FAIL по TimeoutError.
                "provenance": {
                    "plane": {"prompt": f"замер отзывчивости #{offset + n}",
                              "settings": {}, "media": [], "collection_id": None},
                    "settings_resolved": {}, "provider": "mock-image",
                    "model": "mock-image:seed", "mock": True,
                    "inputs": [], "provider_request_id": None, "cost_usd": 0.0,
                    "output": {"sha256": digest, "bytes": payload.stat().st_size,
                               "mime": "image/png"},
                    "harness": {"repository_sha": "NOT_CAPTURED:responsiveness-probe"},
                },
                "file_path": str(payload), "sha256": digest,
                "file_bytes": payload.stat().st_size, "mime": "image/png",
                "favorite": False, "deleted": False, "collection_id": None,
            } for n in range(count)])
    finally:
        engine.dispose()


def _load(name: str, path: Path):
    """Грузит соседний скрипт, не оставляя следа в `sys.path`.

    Модуль регистрируется в `sys.modules` ДО исполнения: без этого
    `@dataclass` внутри него падает на разрешении собственных аннотаций.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    before = list(sys.path)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = before
    return module


def _routes() -> list[str]:
    """Маршруты берутся из реестра страниц, как в обходе §23."""
    module = _load("ui_acceptance_sweep", REPO / "scripts" / "ui_acceptance_sweep.py")
    src = CC / "ui" / "pages" / "index.js"
    routes = module.page_routes(src.read_text(encoding="utf-8"))
    return [r for r in routes if r]


class Walker:
    """Ходит по разделам, НИКОГДА не «переходя» в тот, где уже стоит.

    Это не удобство, а условие честного замера: `location.hash`, выставленный
    в текущее значение, не перерисовывает страницу вовсе, и ожидание либо
    зависает, либо засчитывается по старому экрану. Первый прогон этой пробы
    упёрся ровно в это — 50 циклов закончились на `routes[0]`, а следующим
    шагом был снова `routes[0]`.
    """

    def __init__(self, page, routes: list[str]) -> None:
        if len(routes) < 2:
            raise SystemExit("для замера навигации нужно хотя бы два раздела")
        self.page = page
        self.routes = routes
        self.index = 0

    def step(self) -> float:
        """Один переход: от смены раздела до появления содержимого.

        Меряется не ответ ручки, а то, что видит владелец. Приём: перед
        переходом содержимое `#view` помечается, и ожидание кончается, когда
        метки в нём нет, скелета нет и дети есть. Так переход нельзя
        засчитать по старому экрану.
        """
        self.index = (self.index + 1) % len(self.routes)
        return self.goto(self.routes[self.index])

    def goto(self, route: str, selector: str | None = None) -> float:
        current = self.page.evaluate(
            "() => location.hash.replace(/^#\\/?/, '')")
        if current == route:
            raise RuntimeError(f"переход в текущий раздел {route!r} ничего не "
                               "перерисовывает и замером не является")
        self.page.evaluate("""r => {
          const view = document.querySelector('#view');
          for (const child of view.children) child.setAttribute('data-probe-stale','1');
          location.hash = '#/' + r;
        }""", route)
        started = time.perf_counter()
        if selector:
            self.page.wait_for_selector(selector, timeout=25000)
        else:
            self.page.wait_for_function("""() => {
              const view = document.querySelector('#view');
              if (!view || !view.childElementCount) return false;
              if (view.querySelector('.skeleton')) return false;
              return !view.querySelector('[data-probe-stale]');
            }""", timeout=25000)
        return (time.perf_counter() - started) * 1000

    def park(self, avoid: str) -> None:
        """Встать на раздел, заведомо отличный от следующего измеряемого."""
        for route in self.routes:
            if route != avoid:
                current = self.page.evaluate(
                    "() => location.hash.replace(/^#\\/?/, '')")
                if current != route:
                    self.goto(route)
                return


def measure(mode: str, soak_seconds: float, budget: dict) -> dict:
    reference = mode == "reference"
    lines = {k: Line(k, v) for k, v in budget["budgets"].items()}

    for key in ("cold_start_to_interactive_s", "warm_start_to_interactive_s"):
        lines[key].owner_required(
            "старт меряется только на установленном архиве Windows на машине "
            "владельца: из исходников стартует интерпретатор, а не продукт")

    sys.path.insert(0, str(CC))
    sweep = _load("ui_acceptance_sweep", REPO / "scripts" / "ui_acceptance_sweep.py")

    from bcc.browser_runtime import chromium_executable
    from playwright.sync_api import sync_playwright

    exe = chromium_executable(preinstalled="/opt/pw-browsers/chromium")
    if exe is None:
        raise SystemExit("Chromium не найден — отзывчивость мерить нечем. "
                         "Подделывать числа здесь нельзя.")

    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="responsiveness-") as tmp:
        data_dir = Path(tmp) / "data"
        app = sweep.LiveApp(data_dir).start()
        try:
            routes = _routes()
            notes.append(f"разделов в обходе: {len(routes)}")
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=exe, headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(app.url + "/", wait_until="domcontentloaded")
                page.fill("#login-token", app.svc.auth.token)
                page.click("#login-submit")
                page.wait_for_selector("#shell:not([hidden])", timeout=30000)

                walker = Walker(page, routes)
                samples = [walker.step() for _ in range(NAV_SAMPLE)]
                lines["navigation_p50_ms"].observe(
                    statistics.median(samples), reference=reference,
                    detail=f"{len(samples)} переходов по {len(routes)} разделам")
                ordered = sorted(samples)
                p95 = ordered[min(len(ordered) - 1,
                                  int(round(0.95 * (len(ordered) - 1))))]
                lines["navigation_p95_ms"].observe(
                    p95, reference=reference,
                    detail=f"худший {max(samples):.0f} мс, лучший {min(samples):.0f} мс")

                rss_before = _tree_rss_mib()
                for _ in range(50):
                    walker.step()
                page.wait_for_timeout(500)
                rss_after = _tree_rss_mib()
                growth = ((rss_after - rss_before) / max(rss_before, 1e-9)) * 100
                lines["open_close_cycles_rss_growth_pct"].observe(
                    max(growth, 0.0), reference=reference,
                    detail=f"50 циклов, дерево {rss_before:.1f} → {rss_after:.1f} MiB")

                studio_route = "images?studio=1"
                seeded = 0
                for count, key in ((100, "gallery_100_first_paint_ms"),
                                   (1000, "gallery_1000_first_paint_ms")):
                    _seed_gallery(data_dir, str(app.settings.database_url),
                                  count - seeded, seeded)
                    seeded = count
                    walker.park(avoid=studio_route)
                    page.wait_for_timeout(200)
                    try:
                        lines[key].observe(
                            walker.goto(studio_route, selector="article.studio-card"),
                            reference=reference,
                            detail=f"{count} записей в базе, страница показывает 60")
                    except Exception as exc:  # noqa: BLE001
                        lines[key].verdict = "FAIL"
                        lines[key].detail = (
                            f"{count} записей: карточка не появилась за 25 с — "
                            f"{type(exc).__name__}")
                notes.append(
                    "галерея постраничная по 60 карточек: пара 100/1000 меряет "
                    "стоимость запроса и подсчёта, а не размер DOM")

                if soak_seconds > 0:
                    rss0 = _tree_rss_mib()
                    cpu0 = _tree_cpu_seconds()
                    t0 = time.monotonic()
                    turns = 0
                    while time.monotonic() - t0 < soak_seconds:
                        walker.step()
                        turns += 1
                        page.wait_for_timeout(2000)
                    loaded = time.monotonic() - t0
                    cpu_loaded = _tree_cpu_seconds()

                    # Тишина: ни одного перехода, страница просто открыта.
                    # Именно это и называется «в покое» — фоновые тики
                    # очереди, опрос состояния и сам браузер.
                    quiet = idle_window_for(soak_seconds)
                    idle_started = time.monotonic()
                    page.wait_for_timeout(int(quiet * 1000))
                    idle_elapsed = time.monotonic() - idle_started
                    rss1, cpu1 = _tree_rss_mib(), _tree_cpu_seconds()

                    lines["soak_rss_growth_pct"].observe(
                        max(((rss1 - rss0) / max(rss0, 1e-9)) * 100, 0.0),
                        reference=reference,
                        detail=(f"{loaded / 60:.0f} мин нагрузки ({turns} переходов) "
                                f"плюс {idle_elapsed / 60:.0f} мин покоя, "
                                f"дерево {rss0:.1f} → {rss1:.1f} MiB"))
                    lines["soak_idle_cpu_pct_of_one_core"].observe(
                        ((cpu1 - cpu_loaded) / max(idle_elapsed, 1e-9)) * 100,
                        reference=reference,
                        detail=f"{idle_elapsed / 60:.1f} мин тишины после нагрузки")
                    under_load = ((cpu_loaded - cpu0) / max(loaded, 1e-9)) * 100
                    notes.append(
                        f"CPU дерева ПОД НАГРУЗКОЙ {under_load:.1f} % одного ядра "
                        f"при переходе раз в 2 с — величина без бюджета, "
                        f"приводится рядом, чтобы её не путали с покоем")
                else:
                    notes.append("прогон на выдержку не запускался (--soak-seconds 0): "
                                 "обе строки выдержки остаются без улик")

                browser.close()
        finally:
            app.stop()

    overall = overall_verdict([line.verdict for line in lines.values()])
    return {
        "verdict": overall,
        "mode": mode,
        "budget_fixed_at": budget["fixed_at"],
        "reference_only": reference,
        "lines": [line.as_dict() for line in lines.values()],
        "notes": notes,
        "refusal_rules": budget["refusal_rules"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("reference", "installed"), default="reference")
    parser.add_argument("--soak-seconds", type=float, default=0.0)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    report = measure(args.mode, args.soak_seconds, load_budget())

    for line in report["lines"]:
        print(f"{line['verdict']:<21} {line['metric']}\n     {line['detail']}")
    for note in report["notes"]:
        print(f"     примечание: {note}")
    print(f"RESPONSIVENESS_BUDGET={report['verdict']} mode={report['mode']} "
          f"fixed_at={report['budget_fixed_at']}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")

    if report["verdict"] == "FAIL":
        return 1
    return 2 if any(l["verdict"] == INSUFFICIENT for l in report["lines"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
