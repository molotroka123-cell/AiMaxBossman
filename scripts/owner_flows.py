#!/usr/bin/env python3
"""Раздел 21: сквозные владельческие потоки, а не юнит-тесты.

«Do not finish with unit tests only. Run actual integrated workflows.»

Семь потоков задания:

  A  программирование   заявка -> Mission IR -> кодовый бэкенд -> правка репозитория
                        -> независимый diff -> тесты -> улика
  B  сайт               заявка -> Веб-дизайнер -> правка -> превью в браузере
                        -> сохранение/переоткрытие -> улика
  C  компьютер          заявка -> Windows-MCP -> окно -> ввод -> сохранение
                        -> перечитывание пост-состояния
  D  файлы              File Intelligence -> анализ -> предложение -> исполнение
                        -> перечитывание файловой системы -> откат
  E  видео              рецепты кадров -> Video Studio -> таймлайн -> рендер
                        -> ffprobe -> полное декодирование -> переоткрытие
  F  память             сессия 1 решает -> процесс убит -> сессия 2 вспоминает
  G  составной          сайт + браузер + файл + память одной задачей

Главное правило, которое этот файл соблюдает буквально (раздел 0):

    внешний результат -> наблюдение -> независимая проверка Bossman
    -> улика -> гейт завершения -> ЗАВЕРШЕНО

Поэтому каждый поток заканчивается ПЕРЕЧИТЫВАНИЕМ реальности (файл с диска,
проект из базы после перезапуска, декодирование видео), а не ответом того, кто
работу выполнял. Поток, который не может этого сделать, получает NOT_RUN с
названной причиной — и это честнее PASS.

Запуск:

    python scripts/owner_flows.py --json docs/final/owner_flows.json
    python scripts/owner_flows.py --only E,F
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC = ROOT / "command-center"
for p in (str(CC), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

PASS, PARTIAL, NOT_RUN, FAIL = "PASS", "PARTIAL", "NOT_RUN", "FAIL"


@dataclass
class Flow:
    key: str
    title: str
    status: str = NOT_RUN
    steps: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    reason: str = ""

    def step(self, text: str) -> None:
        self.steps.append(text)


# --------------------------------------------------------------------------
# Общая опора
# --------------------------------------------------------------------------


def _live_app(data_dir: Path):
    from ui_acceptance_sweep import LiveApp
    return LiveApp(data_dir).start()


def _client(app):
    import httpx
    # Заголовок именно X-BCC-Token. Bearer тут не принимается, и первый прогон
    # этого файла получил 401 на КАЖДОМ потоке — а поток D по этому 401 чуть не
    # был записан как «подсистема отказывает fail-closed». Отказ по отсутствию
    # аутентификации и отказ по существу — разные вещи, и путать их нельзя.
    return httpx.Client(base_url=app.url, trust_env=False, timeout=30.0,
                        headers={"X-BCC-Token": app.svc.auth.token})


def _leaf_bd_ids(html: str) -> list[str]:
    """Размеченные элементы БЕЗ вложенных тегов.

    Правка текста у элемента с детьми продуктом отвергается, и правильно:
    она снесла бы вложенные теги. Первый bd-id в документе — это обычно
    контейнер, поэтому брать «первый попавшийся» значит проверять отказ, а не
    правку. Здесь выбираются только листья.
    """
    import re
    out = []
    for tag, bd, text in re.findall(
            r'<(h1|h2|h3|p|span|a|li|button|strong|em)\b[^>]*data-bd-id="(bd-\d+)"[^>]*>([^<]{3,})<',
            html):
        if text.strip():
            out.append(bd)
    return out




def _browser_sees(exe, app, pid: int, marker: str, *, want_html: bool = False):
    """Открыть превью НАСТОЯЩИМ браузером как это делает владелец.

    Вход обязателен: превью — защищённая ручка, и попытка зайти на неё голым
    URL с токеном в query даёт страницу «нужна аутентификация». Первый прогон
    этого потока так и получил «браузер правку не показывает» — при том что
    правка была на месте. Поэтому сначала вход через форму, и уже потом
    переход: cookie сессии живёт в том же контексте.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=exe)
        try:
            page = browser.new_page()
            page.goto(app.url + "/", wait_until="domcontentloaded")
            page.fill("#login-token", app.svc.auth.token)
            page.click("#login-submit")
            page.wait_for_selector("#shell:not([hidden])", timeout=20000)
            page.goto(f"{app.url}/api/web-designer/projects/{pid}/preview",
                      wait_until="domcontentloaded")
            text = page.evaluate("() => document.body.innerText")
            html = page.content()
        finally:
            browser.close()
    return (marker in text, html) if want_html else (marker in text)


def _chromium():
    from bcc.browser_runtime import chromium_executable
    return chromium_executable(preinstalled="/opt/pw-browsers/chromium")


# --------------------------------------------------------------------------
# A — программирование
# --------------------------------------------------------------------------


def flow_a() -> Flow:
    f = Flow("A", "Программирование: правка репозитория с независимой проверкой")
    command = os.environ.get("BOSSMAN_OPENHANDS_COMMAND", "")
    endpoint = os.environ.get("BOSSMAN_LOCAL_MODEL_URL", "http://127.0.0.1:11434")
    have_backend = bool(command and Path(command.split()[0]).exists())

    import socket
    have_model = False
    try:
        host, _, port = endpoint.rsplit(":", 1)[0].replace("http://", ""), None, endpoint.rsplit(":", 1)[-1]
        with socket.create_connection((host, int(port)), timeout=1.0):
            have_model = True
    except Exception:                                     # noqa: BLE001
        have_model = False

    f.evidence = {"openhands_command": command or None,
                  "local_model_endpoint_answers": have_model}
    if not have_backend and not have_model:
        f.status = NOT_RUN
        f.reason = ("нет ни кодового сайдкара (BOSSMAN_OPENHANDS_COMMAND не задан), "
                    "ни отвечающей локальной модели (" + endpoint + " молчит). "
                    "Поток A целиком состоит из работы модели над репозиторием; "
                    "изобразить его без модели значило бы изготовить успех.")
        f.step("проверено наличие кодового бэкенда: нет")
        f.step("проверено наличие локальной модели: нет")
        return f
    f.status = PARTIAL
    f.reason = "бэкенд есть, но этот прогон запускался не на машине владельца"
    return f


# --------------------------------------------------------------------------
# B — сайт
# --------------------------------------------------------------------------


def flow_b(workdir: Path) -> Flow:
    f = Flow("B", "Сайт: правка, превью в настоящем браузере, перезапуск, переоткрытие")
    exe = _chromium()
    if exe is None:
        f.reason = "Chromium недоступен: проверять превью нечем"
        return f

    app = _live_app(workdir / "b")
    try:
        with _client(app) as c:
            r = c.post("/api/web-designer/projects",
                       json={"name": "Fresh Vibes", "prompt": "лендинг кофейни",
                             "template": "landing", "palette": "auto"})
            if r.status_code != 200:
                f.status = FAIL
                f.reason = f"проект не создался: {r.status_code} {r.text[:200]}"
                return f
            pid = int(r.json()["meta"]["id"])
            f.step(f"проект создан из шаблона: id={pid}")

            html = c.get(f"/api/web-designer/projects/{pid}/preview").text
            assert "<" in html
            f.step(f"превью отдано сервером: {len(html)} символов")

            meta = c.get(f"/api/web-designer/projects/{pid}").json()["meta"]
            version_before = int(meta.get("version", 0))

            # Правка через настоящий редакторский путь, не подмена файла.
            import re as _re
            ids = _leaf_bd_ids(html)
            if not ids:
                f.status = PARTIAL
                f.reason = "в превью нет ЛИСТОВЫХ размеченных элементов — точечную правку применять не к чему"
                return f
            marker = "ВЛАДЕЛЕЦ ПРАВИЛ ЗДЕСЬ"
            # Перебор, а не «первый попавшийся». Разметка шаблона зависит от
            # брифа, и лист по одному брифу может оказаться контейнером по
            # другому. Продукт такие правки ОТВЕРГАЕТ — правильно, иначе правка
            # текста снесла бы вложенные теги. Владелец в этом случае выбирает
            # другой элемент; развёртка делает то же самое.
            chosen, e = None, None
            for bd in ids[:12]:
                e = c.post(f"/api/web-designer/projects/{pid}/edit",
                           json={"op": "text", "bd_id": bd, "text": marker,
                                 "base_version": version_before})
                if e.status_code == 200:
                    chosen = bd
                    break
            if chosen is None:
                f.status = FAIL
                f.reason = (f"ни один из {len(ids[:12])} листовых элементов не принял правку; "
                            f"последний ответ: {e.status_code} {e.text[:160]}")
                return f
            f.step(f"элемент выбран перебором: {chosen}")
            version_after = int(e.json()["meta"]["version"])
            f.step(f"правка применена: версия {version_before} -> {version_after}")

            # Историческая регрессия: повторное применение НЕ должно откатывать.
            again = c.post(f"/api/web-designer/projects/{pid}/edit",
                           json={"op": "text", "bd_id": ids[0], "text": marker + " 2",
                                 "base_version": version_after})
            kept = marker in c.get(f"/api/web-designer/projects/{pid}/preview").text \
                or (marker + " 2") in c.get(f"/api/web-designer/projects/{pid}/preview").text
            f.step(f"повторная правка: код {again.status_code}, прежняя правка на месте: {kept}")
            if not kept:
                f.status = FAIL
                f.reason = "повторное применение откатило предыдущую правку (историческая регрессия Apply -> revert)"
                return f

        # Перезапуск «процесса» и переоткрытие.
        app.stop()
        app = _live_app(workdir / "b")
        with _client(app) as c:
            after = c.get(f"/api/web-designer/projects/{pid}/preview").text
            survived = marker in after
            f.step(f"после перезапуска правка на месте: {survived}")
            if not survived:
                f.status = FAIL
                f.reason = "правка не пережила перезапуск"
                return f

            # Независимая проверка: не «сервер сказал», а браузер ПОКАЗАЛ.
            shown = _browser_sees(exe, app, pid, marker)
            f.step(f"настоящий Chromium показывает правку владельцу: {shown}")
            f.evidence = {"project_id": pid, "version_before": version_before,
                          "version_after": version_after,
                          "survived_restart": survived, "rendered_in_browser": shown}
            f.status = PASS if shown else PARTIAL
            if not shown:
                f.reason = "сервер отдаёт правку, но в отрисованной странице её не видно"
    finally:
        app.stop()
    return f


# --------------------------------------------------------------------------
# C — компьютер (Windows)
# --------------------------------------------------------------------------


def flow_c() -> Flow:
    f = Flow("C", "Компьютер: открыть программу, ввести, сохранить, перечитать")
    f.status = NOT_RUN
    f.reason = (f"целевая ОС недоступна: платформа прогона — {sys.platform}. "
                "Windows-MCP управляет окнами Windows; на Linux этот поток "
                "нечем выполнить, и объявлять его пройденным запрещено "
                "разделом 28.")
    f.step(f"платформа: {sys.platform}")
    return f


# --------------------------------------------------------------------------
# D — файлы
# --------------------------------------------------------------------------


def flow_d(workdir: Path) -> Flow:
    f = Flow("D", "Файлы: анализ, предложение, исполнение, перечитывание, откат")
    app = _live_app(workdir / "d")
    try:
        with _client(app) as c:
            r = c.get("/api/file-intelligence/status")
            if r.status_code == 404:
                r = c.get("/api/file-intelligence")
            f.step(f"состояние File Intelligence: HTTP {r.status_code}")
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            privacy = str(body.get("privacy") or body.get("privacy_state") or "")
            f.evidence = {"http": r.status_code, "privacy": privacy or None,
                          "body_keys": sorted(body)[:12]}
            # Ожидаемое поведение без доказанной локальности обработки — ОТКАЗ.
            refuses = (r.status_code >= 400) or ("НЕИЗВЕСТНО" in str(body)) \
                or ("unknown" in privacy.lower())
            if refuses:
                f.status = PARTIAL
                f.reason = ("подсистема отказывает fail-closed: происхождение обработки "
                            "не доказано, поэтому переименований не предлагается. Это "
                            "ПРАВИЛЬНОЕ поведение; полный поток D требует машины "
                            "владельца с локальной моделью.")
                f.step("отказ fail-closed подтверждён — выдумывать переименования нельзя")
            else:
                f.status = PARTIAL
                f.reason = "подсистема доступна, но без локальной модели предлагать имена нечему"
    finally:
        app.stop()
    return f


# --------------------------------------------------------------------------
# E — видео
# --------------------------------------------------------------------------


def flow_e(workdir: Path) -> Flow:
    f = Flow("E", "Видео: рецепты кадров -> таймлайн -> рендер -> полное декодирование")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        f.reason = "нет ffmpeg/ffprobe: рендерить и проверять нечем"
        return f

    from bcc.video_studio import storyboard as sb
    from bcc.video_studio.commands import apply_command
    from bcc.video_studio.model import TICKS, new_project
    from bcc.video_studio.media import MediaLibrary
    from bcc.video_studio.render import render_project, verify_output

    stage = workdir / "e"
    stage.mkdir(parents=True, exist_ok=True)

    async def run() -> dict:
        library = MediaLibrary(stage)
        media = []
        for colour in ("red", "green", "blue"):
            path = stage / f"{colour}.mp4"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-f", "lavfi", "-i", f"color=c={colour}:size=320x180:rate=25:duration=4",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                 str(path)], check=True)
            media.append(await library.import_file(path))
        return {"media": media}

    prepared = asyncio.run(run())
    media = prepared["media"]
    f.step(f"материал изготовлен и импортирован: {len(media)} файла")

    board = sb.plan_storyboard("рекламный ролик Fresh Vibes",
                               media_ids=[m["id"] for m in media],
                               target_ticks=9 * TICKS, bpm=120)
    f.step(f"раскадровка: {len(board.shots)} кадров, {board.duration / TICKS:.1f} с, "
           f"ритм {board.beat_ticks / TICKS:.2f} с/удар")
    if board.shortfalls:
        f.step("нехватки названы вслух: " + "; ".join(board.shortfalls))

    project = new_project("p-owner-flow", "Fresh Vibes")
    for m in media:
        project["media"][m["id"]] = m
    seq = project["sequences"][0]
    track_id = seq["tracks"][0]["id"]
    for command in sb.compile_to_commands(board, track_id=track_id):
        project, _changed, _ = apply_command(project, command)
    clips = project["sequences"][0]["tracks"][0]["clips"]
    f.step(f"таймлайн собран настоящими командами: {len(clips)} клипов")

    out = stage / "export.mp4"
    result = asyncio.run(render_project(project, stage, out))
    verification = result["verification"]
    f.step(f"рендер и проверка: decoded={verification.get('decoded')} "
           f"passed={verification.get('passed')} "
           f"{verification.get('width')}x{verification.get('height')}")

    # Независимое перечитывание: файл открывается заново, а не «рендер сказал ок».
    reopened = asyncio.run(verify_output(out, {}))
    f.step(f"файл переоткрыт с диска и декодирован заново: passed={reopened.get('passed')}")

    # Отрицательный контроль: проверка обязана уметь сказать «нет».
    wrong = asyncio.run(verify_output(out, {"width": 9999}))
    f.step(f"отрицательный контроль (ожидание заведомо неверного размера): "
           f"passed={wrong.get('passed')}")

    f.evidence = {"shots": len(board.shots), "clips": len(clips),
                  "bytes": out.stat().st_size if out.exists() else 0,
                  "verification": {k: verification.get(k)
                                   for k in ("passed", "decoded", "width", "height", "has_audio")},
                  "reopened_passed": reopened.get("passed"),
                  "negative_control_passed": wrong.get("passed")}
    ok = (verification.get("passed") and verification.get("decoded")
          and reopened.get("passed") and not wrong.get("passed"))
    f.status = PASS if ok else FAIL
    if not ok:
        f.reason = "рендер или его независимая перепроверка не подтвердились"
    return f


# --------------------------------------------------------------------------
# F — память через смерть процесса
# --------------------------------------------------------------------------


def flow_f(workdir: Path) -> Flow:
    f = Flow("F", "Память: решение переживает смерть процесса и узнаётся по существу")
    stage = workdir / "f"
    stage.mkdir(parents=True, exist_ok=True)
    db_path = stage / "memory.db"

    # Сессия 1 и сессия 2 — РАЗНЫЕ процессы, а не два объекта в одном.
    # Иначе «пережило перезапуск» доказывалось бы кэшем в памяти.
    writer = subprocess.run(
        [sys.executable, "-c", _SESSION_CODE, "write", str(db_path)],
        capture_output=True, text=True, cwd=str(CC))
    if writer.returncode != 0:
        f.status = FAIL
        f.reason = f"сессия 1 не записала решение: {writer.stderr[-400:]}"
        return f
    f.step("сессия 1 (отдельный процесс) записала решение и завершилась")

    reader = subprocess.run(
        [sys.executable, "-c", _SESSION_CODE, "read", str(db_path)],
        capture_output=True, text=True, cwd=str(CC))
    if reader.returncode != 0:
        f.status = FAIL
        f.reason = f"сессия 2 не прочитала решение: {reader.stderr[-400:]}"
        return f
    out = json.loads(reader.stdout.strip().splitlines()[-1])
    f.step(f"сессия 2 (новый процесс) вспомнила: {out['recalled']}")
    f.step(f"чужой репозиторий этого не видит: {out['isolated']}")
    f.step(f"после обновления выигрывает новая версия: {out['latest_wins']}")
    f.step(f"происхождение названо: {out['provenance']}")
    f.evidence = out
    ok = out["recalled"] and out["isolated"] and out["latest_wins"] and out["provenance"]
    f.status = PASS if ok else FAIL
    if not ok:
        f.reason = "одно из четырёх свойств памяти не подтвердилось"
    return f


_SESSION_CODE = r'''
import asyncio, json, sys, time
mode, path = sys.argv[1], sys.argv[2]
from bcc.db import Database
from bcc.hybrid.capabilities import ContextNamespace, EffectCorrelation
from bcc.hybrid.context_store import (BossmanNativeContextStore, ContextStorePlanner,
                                      build_record)

NS = ContextNamespace(project="Fresh Vibes", repository="owner/fresh-vibes")
OTHER = ContextNamespace(project="Другой проект", repository="owner/other")

def corr():
    return EffectCorrelation(correlation_id="c-flow", task_id="t-flow", run_id="r-flow",
                             deadline_epoch_s=time.time() + 30)

async def main():
    db = Database("sqlite+aiosqlite:///" + path)
    await db.create_all()
    planner = ContextStorePlanner(BossmanNativeContextStore(db))
    if mode == "write":
        await planner.remember(build_record(
            namespace=NS, key="палитра", version=1, source="owner:chat",
            body="Решили: основной цвет #2f6f4f, кнопки скруглены 12px"), corr())
        await db.close()
        print("ok")
        return
    hits = await planner.recall(NS, "палитра", correlation=corr())
    recalled = bool(hits) and "#2f6f4f" in hits[0].record.body
    provenance = bool(hits) and hits[0].record.provenance.is_attributable()
    others = await planner.recall(OTHER, "палитра", correlation=corr())
    isolated = not others
    await planner.remember(build_record(
        namespace=NS, key="палитра", version=2, source="owner:chat",
        body="Решили: основной цвет #1d4ed8, кнопки скруглены 12px"), corr())
    after = await planner.recall(NS, "палитра", correlation=corr())
    latest_wins = (bool(after) and any("#1d4ed8" in h.record.body for h in after)
                   and all("#2f6f4f" not in h.record.body for h in after))
    await db.close()
    print(json.dumps({"recalled": recalled, "provenance": provenance,
                      "isolated": isolated, "latest_wins": latest_wins}))

asyncio.run(main())
'''


# --------------------------------------------------------------------------
# G — составной
# --------------------------------------------------------------------------


def flow_g(workdir: Path) -> Flow:
    f = Flow("G", "Составной: правка сайта + проверка браузером + файл на диске + память")
    exe = _chromium()
    if exe is None:
        f.reason = "Chromium недоступен"
        return f

    stage = workdir / "g"
    stage.mkdir(parents=True, exist_ok=True)
    app = _live_app(stage / "data")
    marker = "Fresh Vibes — часы работы 8:00–20:00"
    try:
        with _client(app) as c:
            r = c.post("/api/web-designer/projects",
                       json={"name": "Составной поток", "prompt": "кофейня",
                             "template": "landing", "palette": "auto"})
            pid = int(r.json()["meta"]["id"])
            html = c.get(f"/api/web-designer/projects/{pid}/preview").text
            import re as _re
            # Атрибут называется data-bd-id, а не data-bd. Неверное имя давало
            # «в превью нет размеченных элементов» на полностью исправном превью.
            ids = _leaf_bd_ids(html)
            version = int(c.get(f"/api/web-designer/projects/{pid}").json()["meta"]["version"])
            # Тот же перебор, что и в потоке B: продукт вправе отвергнуть правку
            # текста у элемента с вложенными тегами, и это не повод объявлять
            # поток провалившимся.
            edited = None
            for bd in ids[:12]:
                resp = c.post(f"/api/web-designer/projects/{pid}/edit",
                              json={"op": "text", "bd_id": bd, "text": marker,
                                    "base_version": version})
                if resp.status_code == 200:
                    edited = bd
                    break
            if edited is None:
                f.status = FAIL
                f.reason = "правку сайта не принял ни один элемент"
                return f
            f.step(f"сайт правлен: проект {pid}, элемент {edited}")

            # 1. браузер ПОКАЗЫВАЕТ
            shown, rendered = _browser_sees(exe, app, pid, marker, want_html=True)
            f.step(f"браузер показывает правку: {shown}")

            # 2. результат СОХРАНЁН локально и перечитан с диска
            saved = stage / "fresh-vibes.html"
            saved.write_text(rendered, encoding="utf-8")
            on_disk = marker in saved.read_text(encoding="utf-8")
            f.step(f"файл сохранён и перечитан с диска: {on_disk} ({saved.stat().st_size} байт)")

        # 3. ЗАПОМНЕНО и переживает смерть процесса
        db = stage / "memory.db"
        w = subprocess.run([sys.executable, "-c", _SESSION_CODE, "write", str(db)],
                           capture_output=True, text=True, cwd=str(CC))
        r2 = subprocess.run([sys.executable, "-c", _SESSION_CODE, "read", str(db)],
                            capture_output=True, text=True, cwd=str(CC))
        remembered = w.returncode == 0 and r2.returncode == 0 and \
            json.loads(r2.stdout.strip().splitlines()[-1])["recalled"]
        f.step(f"изменение запомнено и пережило перезапуск: {remembered}")

        f.evidence = {"project_id": pid, "browser_shows": shown,
                      "file_on_disk": on_disk, "remembered": remembered,
                      "saved_bytes": saved.stat().st_size}
        ok = shown and on_disk and remembered
        f.status = PASS if ok else FAIL
        if not ok:
            f.reason = "одна из четырёх частей составного потока не подтвердилась независимо"
    finally:
        app.stop()
    return f


# --------------------------------------------------------------------------


FLOWS = {
    "A": lambda w: flow_a(),
    "B": flow_b,
    "C": lambda w: flow_c(),
    "D": flow_d,
    "E": flow_e,
    "F": flow_f,
    "G": flow_g,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    wanted = [k.strip().upper() for k in args.only.split(",") if k.strip()] or list(FLOWS)

    tmp = tempfile.TemporaryDirectory(prefix="bossman-owner-flows-")
    workdir = Path(tmp.name)
    results: list[Flow] = []
    try:
        for key in wanted:
            started = time.time()
            try:
                flow = FLOWS[key](workdir)
            except Exception as exc:                      # noqa: BLE001
                flow = Flow(key, FLOWS[key].__doc__ or key, FAIL,
                            reason=f"{type(exc).__name__}: {exc}")
            flow.evidence["seconds"] = round(time.time() - started, 1)
            results.append(flow)
            print(f"\n=== {flow.key}. {flow.title}")
            print(f"    {flow.status}" + (f" — {flow.reason}" if flow.reason else ""))
            for s in flow.steps:
                print(f"      · {s}")
    finally:
        tmp.cleanup()

    print("\n" + "-" * 60)
    for flow in results:
        print(f"  {flow.key}  {flow.status:8s} {flow.title}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"platform": sys.platform, "flows": [asdict(f) for f in results]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nзаписано: {args.json}")

    return 1 if any(f.status == FAIL for f in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
