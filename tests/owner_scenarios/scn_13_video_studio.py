"""Владельческие сценарии 51–55: Video Studio из конца в конец.

Все пять идут через УСТАНОВЛЕННЫЙ продукт: `bcc.api.create_app` поднимает то же
приложение, что запускает владелец, и вся работа делается его HTTP-дверями
(`/api/video-studio/...`) с настоящим токеном. Ни один сценарий не зовёт
`render_project` в обход очереди: заявление «продукт умеет» обязано быть
измерено на том слое, которым владелец действительно пользуется.

Приёмка — ЧУЖАЯ. Артефакт проверяет независимый оракул `tools/media_roundtrip.py`
(ffprobe без подсказки расширением, полное декодирование, счёт кадров), а не
самоотчёт продукта. Самоотчёт сверяется с оракулом отдельной проверкой: если бы
они разошлись, это была бы находка, а не деталь.

* 51 — проект собирается и экспортируется; ffprobe подтверждает длительность,
  кодеки и ЧИСЛО КАДРОВ скачанных владельцем байтов;
* 52 — правка эффекта доходит до ПИКСЕЛЕЙ, а правка только манифеста — нет;
* 53 — квитанция экспорта привязана к ТЕМ САМЫМ байтам;
* 54 — отменённый посреди работы экспорт не оставляет полуфайла и не склеивается
  со следующим прогоном;
* 55 — провал объясняется ОДНОЙ человекочитаемой причиной без путей и stderr.

Зависимости модуля — только стандартная библиотека и каркас. Всё, что тянет
sqlalchemy/fastapi (`bcc.*`), импортируется ВНУТРИ функций и объявлено в реестре
как `command_center`: BL-085 — корневой CI ставит только
pytest/pytest-timeout/psutil/httpx/pyyaml, и без способности раннер отдаёт
честный вердикт вместо исполнения. `ffprobe` в реестре покрывает пару
ffmpeg+ffprobe ровно так, как её требует `media_roundtrip.binaries()`.
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import INSTALLED_PRODUCT, scenario  # noqa: E402

BASE = "/api/video-studio"


# ------------------------------------------------------------------ оснастка
def _op() -> str:
    """Идентификатор операции владельца: у каждой правки он свой."""
    return uuid.uuid4().hex


def _media_tools(ctx):
    """Бинари медиа или ЧЕСТНЫЙ тупик. Отсутствие ffmpeg — не FAIL продукта."""
    import media_roundtrip as mr  # noqa: PLC0415
    try:
        mr.binaries()
    except mr.MediaEvidenceError as exc:
        ctx.not_proven(f"пара ffmpeg/ffprobe недоступна, измерять артефакт нечем: {exc}")
    return mr


@contextlib.asynccontextmanager
async def _product(ctx, *, workers: bool = False):
    """Установленный Bossman: то же приложение, тот же токен, те же двери."""
    import httpx  # noqa: PLC0415

    from bcc.api import create_app  # noqa: PLC0415
    from bcc.auth import HEADER  # noqa: PLC0415
    from bcc.config import Settings  # noqa: PLC0415

    data = ctx.path("установка", "data")
    settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}",
                        ui_dir=ctx.path("установка", "нет-ui"))
    app = create_app(settings, announce_token=False, start_workers=workers)
    svc = app.state.svc
    await svc.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://bossman.local",
                                     headers={HEADER: svc.auth.token}) as client:
            yield svc, client
    finally:
        await svc.stop()


async def _run_task(svc, task_id: int) -> int:
    """Прогнать одну задачу очереди продукта её собственным движком."""
    import sqlalchemy as sa  # noqa: PLC0415

    from bcc.db import task_runs as runs_t  # noqa: PLC0415
    async with svc.db.session() as session:
        run_id = (await session.execute(sa.select(runs_t.c.id)
                                        .where(runs_t.c.task_id == task_id)
                                        .order_by(runs_t.c.id.desc()))).scalar()
    if run_id is None:
        raise AssertionError(f"продукт не создал ни одного run для задачи {task_id}")
    with contextlib.suppress(Exception):
        await svc.engine.execute(int(run_id))
    return int(run_id)


async def _stored_result(svc, job_id: str) -> dict:
    """Запись продукта об экспорте. `job()` намеренно прячет путь от UI."""
    import sqlalchemy as sa  # noqa: PLC0415

    from bcc.video_studio.service import jobs as jobs_t  # noqa: PLC0415
    async with svc.db.session() as session:
        value = (await session.execute(sa.select(jobs_t.c.result)
                                       .where(jobs_t.c.id == job_id))).scalar()
    return dict(value or {})


async def _tasks_count(svc) -> int:
    import sqlalchemy as sa  # noqa: PLC0415

    from bcc.db import tasks as tasks_t  # noqa: PLC0415
    async with svc.db.session() as session:
        return int((await session.execute(sa.select(sa.func.count()).select_from(tasks_t))).scalar_one())


async def _project_with_clip(client, source: Path, *, name: str) -> tuple[str, int, dict]:
    """Проект → загрузка медиа → клип на дорожке. Всё через HTTP владельца."""
    created = await client.post(BASE + "/projects", json={"name": name, "operation_id": _op()})
    if created.status_code != 200:
        raise AssertionError(f"продукт не создал проект: {created.status_code} {created.text[:200]}")
    project_id = created.json()["project"]["id"]
    upload = await client.post(BASE + "/media", params={
        "project_id": project_id, "filename": source.name,
        "expected_revision": 0, "operation_id": _op()}, content=source.read_bytes())
    if upload.status_code != 200:
        raise AssertionError(f"продукт не принял медиа: {upload.status_code} {upload.text[:200]}")
    media = upload.json()["media"]
    project = (await client.get(BASE + "/projects/" + project_id)).json()
    track = project["sequences"][0]["tracks"][0]["id"]
    placed = await client.post(BASE + "/commands", json={
        "project_id": project_id, "expected_revision": project["revision"], "operation_id": _op(),
        "command": {"type": "clip.add", "track_id": track,
                    "clip": {"id": "clip-1", "media_id": media["id"]}}})
    if placed.status_code != 200:
        raise AssertionError(f"продукт не поставил клип: {placed.status_code} {placed.text[:200]}")
    return project_id, int(placed.json()["revision"]), media


async def _export(svc, client, project_id: str, revision: int, *, options=None) -> tuple[dict, dict]:
    """Поставить экспорт в очередь продукта и дождаться его исхода."""
    payload = {"project_id": project_id, "expected_revision": revision,
               "operation_id": _op(), "options": options or {"width": 160, "height": 90}}
    queued = await client.post(BASE + "/exports", json=payload)
    if queued.status_code != 200:
        raise AssertionError(f"продукт не принял экспорт: {queued.status_code} {queued.text[:200]}")
    job = queued.json()
    await _run_task(svc, job["task_id"])
    return job, (await client.get(BASE + "/exports/" + job["job_id"])).json()


# ------------------------------------------------------------------ 51
@scenario(id="OS-51", depth=INSTALLED_PRODUCT)
def os51_project_exports_and_ffprobe_confirms(ctx) -> None:
    """Экспорт доходит до владельца, и ЧУЖОЙ ffprobe подтверждает его байты."""
    mr = _media_tools(ctx)
    ctx.reached_installed_product("bcc.api.create_app + HTTP /api/video-studio этой ветки")
    source = mr.video_fixture(ctx.path("заготовка", "исходник.mp4"), seconds=1, fps=25,
                              width=160, height=90)

    async def run():
        out = {}
        async with _product(ctx) as (svc, client):
            project_id, revision, media = await _project_with_clip(
                client, Path(source["path"]), name="Экспорт владельца")
            out["media_measured_format"] = media["metadata"]["format"]

            # ОТРИЦАТЕЛЬНЫЕ ПЕРВЫМИ: неверный экспорт не должен даже попасть в
            # очередь, и проверить это можно только пока очередь ещё пуста.
            before = await _tasks_count(svc)
            impossible = await client.post(BASE + "/exports", json={
                "project_id": project_id, "expected_revision": revision, "operation_id": _op(),
                "container": "webm", "options": {"width": 160, "height": 90,
                                                 "video_codec": "libx264"}})
            out["container_codec"] = (impossible.status_code, impossible.text[:200])
            out["tasks_after_refusal"] = await _tasks_count(svc)
            out["tasks_before_refusal"] = before
            stale = await client.post(BASE + "/exports", json={
                "project_id": project_id, "expected_revision": 0, "operation_id": _op(),
                "options": {"width": 160, "height": 90}})
            out["stale_revision"] = (stale.status_code, stale.text[:200])

            job, status = await _export(svc, client, project_id, revision)
            out["status"] = status
            out["result"] = await _stored_result(svc, job["job_id"])
            if status.get("output_url"):
                downloaded = await client.get(status["output_url"])
                out["download_code"] = downloaded.status_code
                target = ctx.path("скачано", "bossman-video.mp4")
                target.write_bytes(downloaded.content)
                out["downloaded"] = target
        return out

    got = asyncio.run(run())
    status, stored = got["status"], got["result"]
    ctx.negative("несовместимая пара контейнер+кодек отвергнута ДО очереди",
                 got["container_codec"][0] == 422
                 and "webm" in got["container_codec"][1]
                 and got["tasks_after_refusal"] == got["tasks_before_refusal"],
                 f"HTTP={got['container_codec'][0]}, задач было="
                 f"{got['tasks_before_refusal']}, стало={got['tasks_after_refusal']}")
    ctx.negative("экспорт устаревшей ревизии отвергнут",
                 got["stale_revision"][0] == 409, f"HTTP={got['stale_revision'][0]}")

    ctx.positive("продукт измерил контейнер загруженного медиа по содержимому",
                 got["media_measured_format"] == "mov,mp4,m4a,3gp,3g2,mj2",
                 f"формат={got['media_measured_format']}")
    ctx.positive("экспорт прошёл очередь продукта до completed",
                 status.get("status") == "completed" and bool(status.get("output_url")),
                 f"status={status.get('status')}, ссылка={bool(status.get('output_url'))}")
    ctx.positive("владелец скачал артефакт по выданной продуктом ссылке",
                 got.get("download_code") == 200, f"HTTP={got.get('download_code')}")

    # Независимая приёмка СКАЧАННЫХ байтов: имя ffprobe не показывается.
    expect = {"tracks": {"video": 1, "audio": 1}, "video_codec": "h264", "audio_codec": "aac",
              "width": 160, "height": 90, "duration_s": 1.0, "frames": 25, "min_bytes": 1024}
    evidence = mr.verify_artifact(got["downloaded"], expect, label="экспорт владельца")
    ctx.positive("ffprobe подтвердил контейнер, кодеки, размер, длительность и ЧИСЛО КАДРОВ",
                 evidence["passed"] and evidence["decoded_video_frames"] == "25"
                 and evidence["measured_format"] == "mov,mp4,m4a,3gp,3g2,mj2",
                 f"формат={evidence['measured_format']}, длительность={evidence['measured_duration_s']}, "
                 f"кадров={evidence['decoded_video_frames']}, дорожки={evidence['tracks']}")
    ctx.positive("скачанные байты — ровно те, что продукт объявил хешем",
                 evidence["sha256"] == stored.get("sha256"),
                 f"ffprobe-оракул и запись продукта сошлись: {evidence['sha256'][:16]}…")
    ctx.positive("самоотчёт продукта совпал с независимым замером",
                 (stored.get("verification") or {}).get("passed") is True
                 and (stored.get("verification") or {}).get("decoded_video_frames") == 25,
                 f"самоотчёт={{passed: {(stored.get('verification') or {}).get('passed')}, "
                 f"frames: {(stored.get('verification') or {}).get('decoded_video_frames')}}}")

    # Оракул обязан уметь краснеть, иначе «ffprobe подтвердил» ничего не значит.
    truncated = ctx.path("контроль", "обрезанный.mp4")
    truncated.write_bytes(got["downloaded"].read_bytes()[:2048])
    ctx.refused("обрезанный экспорт тот же оракул отвергает",
                lambda: mr.verify_artifact(truncated, expect, label="обрезанный"),
                mr.MediaEvidenceError)
    page = ctx.path("контроль", "страница.mp4")
    page.write_bytes(mr.ERROR_PAGE)
    ctx.refused("страница ошибки с именем .mp4 отвергается по ИЗМЕРЕННОМУ типу",
                lambda: mr.verify_artifact(page, expect, label="страница ошибки"),
                mr.MediaEvidenceError)


# ------------------------------------------------------------------ 52
@scenario(id="OS-52", depth=INSTALLED_PRODUCT)
def os52_effect_reaches_the_pixels(ctx) -> None:
    """Эффект обязан менять КАДР. Манифест — не доказательство правки."""
    mr = _media_tools(ctx)
    ctx.reached_installed_product("bcc.api.create_app + HTTP /api/video-studio этой ветки")
    source = mr.video_fixture(ctx.path("заготовка", "исходник.mp4"), seconds=1, fps=25,
                              width=160, height=90)

    async def command(client, project_id, revision, body):
        answer = await client.post(BASE + "/commands", json={
            "project_id": project_id, "expected_revision": revision,
            "operation_id": _op(), "command": body})
        return answer

    async def run():
        out = {}
        async with _product(ctx) as (svc, client):
            project_id, revision, _ = await _project_with_clip(
                client, Path(source["path"]), name="Пиксели владельца")

            async def export_frame(rev, tag):
                job, status = await _export(svc, client, project_id, rev)
                if status.get("status") != "completed":
                    raise AssertionError(f"{tag}: экспорт не дошёл до конца: {status.get('status')}")
                stored = await _stored_result(svc, job["job_id"])
                target = ctx.path("кадры", tag + ".mp4")
                downloaded = await client.get(status["output_url"])
                target.write_bytes(downloaded.content)
                return mr.mean_rgb(target), stored.get("sha256")

            out["базовый"], _ = await export_frame(revision, "базовый")

            renamed = await command(client, project_id, revision,
                                    {"type": "project.rename", "name": "Переименован"})
            out["rename_code"] = renamed.status_code
            revision = int(renamed.json()["revision"])
            out["только-манифест"], _ = await export_frame(revision, "только-манифест")

            off = await command(client, project_id, revision,
                                {"type": "effect.apply", "clip_id": "clip-1",
                                 "effect": {"id": "eq-1", "type": "eq", "enabled": False,
                                            "params": {"brightness": 0.35}}})
            out["disabled_code"] = off.status_code
            revision = int(off.json()["revision"])
            out["в-манифесте-выключен"], _ = await export_frame(revision, "выключен")
            project = (await client.get(BASE + "/projects/" + project_id)).json()
            clip = project["sequences"][0]["tracks"][0]["clips"][0]
            out["эффект-в-манифесте"] = [e["id"] for e in clip.get("effects", [])]

            on = await command(client, project_id, revision,
                               {"type": "effect.apply", "clip_id": "clip-1",
                                "effect": {"id": "eq-1", "type": "eq", "enabled": True,
                                           "params": {"brightness": 0.35}}})
            revision = int(on.json()["revision"])
            out["включён"], _ = await export_frame(revision, "включён")

            removed = await command(client, project_id, revision,
                                    {"type": "effect.remove", "clip_id": "clip-1",
                                     "effect_id": "eq-1"})
            revision = int(removed.json()["revision"])
            out["снят"], _ = await export_frame(revision, "снят")

            unknown = await command(client, project_id, revision,
                                    {"type": "effect.apply", "clip_id": "clip-1",
                                     "effect": {"id": "чужой", "type": "не-существует",
                                                "params": {}}})
            out["unknown_effect"] = (unknown.status_code, unknown.text[:160])
        return out

    got = asyncio.run(run())
    base, manifest_only = got["базовый"], got["только-манифест"]
    disabled, enabled, removed = got["в-манифесте-выключен"], got["включён"], got["снят"]

    ctx.positive("правка через HTTP продукта принята и подняла ревизию",
                 got["rename_code"] == 200 and got["disabled_code"] == 200,
                 f"rename={got['rename_code']}, effect.apply={got['disabled_code']}")
    ctx.positive("включённый эффект ИЗМЕНИЛ пиксели кадра",
                 sum(enabled) > sum(base) + 30,
                 f"средний RGB базовый={base} → с эффектом={enabled}")
    ctx.positive("эффект записан в манифест проекта",
                 got["эффект-в-манифесте"] == ["eq-1"],
                 f"эффекты клипа={got['эффект-в-манифесте']}")

    # Без этих трёх «правка дошла до пикселей» доказывалось бы дрожанием замера.
    ctx.negative("правка ТОЛЬКО манифеста (rename) пикселей не тронула",
                 manifest_only == base, f"базовый={base} → после rename={manifest_only}")
    ctx.negative("эффект, лежащий в манифесте ВЫКЛЮЧЕННЫМ, до пикселей не доходит",
                 disabled == base and got["эффект-в-манифесте"] == ["eq-1"],
                 f"базовый={base} → выключенный эффект={disabled}")
    ctx.negative("снятие эффекта возвращает исходные пиксели",
                 removed == base, f"после снятия={removed}")
    ctx.negative("эффект неизвестного типа отвергнут командным слоем",
                 got["unknown_effect"][0] == 422, f"HTTP={got['unknown_effect'][0]}")


# ------------------------------------------------------------------ 53
@scenario(id="OS-53", depth=INSTALLED_PRODUCT)
def os53_export_receipt_is_bound_to_the_bytes(ctx) -> None:
    """Квитанция — не печать «готово», а привязка к конкретным байтам и прогону."""
    mr = _media_tools(ctx)
    ctx.reached_installed_product("bcc.api.create_app + HTTP /api/video-studio этой ветки")
    source = mr.video_fixture(ctx.path("заготовка", "исходник.mp4"), seconds=1, fps=25,
                              width=160, height=90)

    async def run():
        import sqlalchemy as sa  # noqa: PLC0415

        from bcc.db import tasks as tasks_t, task_runs as runs_t  # noqa: PLC0415
        from bcc.video_studio.export_receipt import RECEIPT_KEY  # noqa: PLC0415
        from bcc.video_studio.service import jobs as jobs_t  # noqa: PLC0415

        out = {}
        async with _product(ctx) as (svc, client):
            project_id, revision, _ = await _project_with_clip(
                client, Path(source["path"]), name="Квитанция владельца")
            job, status = await _export(svc, client, project_id, revision)
            out["status"] = status.get("status")
            out["receipt_hidden_from_api"] = RECEIPT_KEY not in status
            async with svc.db.session() as session:
                row = dict((await session.execute(sa.select(jobs_t)
                                                  .where(jobs_t.c.id == job["job_id"]))).mappings().one())
                task = dict((await session.execute(sa.select(tasks_t)
                                                   .where(tasks_t.c.id == job["task_id"]))).mappings().one())
                run_id = int((await session.execute(sa.select(runs_t.c.id)
                                                    .where(runs_t.c.task_id == task["id"]))).scalar_one())
            video = svc.video_studio
            out["receipt_signed"] = ((row.get("result") or {}).get(RECEIPT_KEY) or {}).get("signer")
            out["gate_ok"] = await video.render_gate(task, run_id, "")
            out["gate_other_run"] = await video.render_gate(task, run_id + 1, "")
            out["gate_other_task"] = await video.render_gate({**task, "id": task["id"] + 1}, run_id, "")
            good = await client.get(status["output_url"])
            out["download_ok"] = good.status_code
            target = ctx.path("скачано", "квитанция.mp4")
            target.write_bytes(good.content)
            out["downloaded"] = target

            artifact = Path((row.get("result") or {})["path"])
            out["artifact_bytes"] = artifact.stat().st_size
            with artifact.open("ab") as handle:
                handle.write("подмена после проверки".encode())
            out["download_tampered"] = (await client.get(status["output_url"])).status_code
            out["gate_tampered"] = await video.render_gate(task, run_id, "")

            # Тот же файл, та же длина — иначе «поймали» означало бы «размер не тот».
            original = good.content
            same_size = bytearray(original)
            same_size[-1] = (same_size[-1] + 1) % 256
            artifact.write_bytes(bytes(same_size))
            out["same_size"] = artifact.stat().st_size == len(original)
            out["download_same_size"] = (await client.get(status["output_url"])).status_code
            out["gate_same_size"] = await video.render_gate(task, run_id, "")

            artifact.write_bytes(original)
            out["download_restored"] = (await client.get(status["output_url"])).status_code

            stripped = {k: v for k, v in (row.get("result") or {}).items() if k != RECEIPT_KEY}
            async with svc.db.session() as session:
                await session.execute(sa.update(jobs_t).where(jobs_t.c.id == job["job_id"])
                                      .values(result=stripped))
                await session.commit()
            out["gate_no_receipt"] = await video.render_gate(task, run_id, "")
        return out

    got = asyncio.run(run())
    ctx.positive("экспорт дошёл до конца и получил подписанную квитанцию",
                 got["status"] == "completed" and got["receipt_signed"] == "bcc.v2.verification",
                 f"подписант={got['receipt_signed']}")
    ctx.positive("гейт завершения принял ИМЕННО эту задачу и этот прогон",
                 got["gate_ok"].get("verdict") == "PASS", f"вердикт={got['gate_ok']}")
    ctx.positive("владелец скачал непорченый артефакт",
                 got["download_ok"] == 200 and got["download_restored"] == 200,
                 f"до подмены={got['download_ok']}, после восстановления={got['download_restored']}")
    ffprobe = mr.verify_artifact(got["downloaded"],
                                 {"tracks": {"video": 1, "audio": 1}, "duration_s": 1.0,
                                  "frames": 25, "min_bytes": 1024}, label="артефакт квитанции")
    ctx.positive("квитанция стоит на байтах, которые независимо декодируются",
                 ffprobe["passed"], f"кадров={ffprobe['decoded_video_frames']}")

    ctx.negative("дописанный в конец байт закрывает выдачу владельцу",
                 got["download_tampered"] == 409, f"HTTP={got['download_tampered']}")
    ctx.negative("тот же дописанный байт валит гейт завершения",
                 got["gate_tampered"].get("verdict") == "FAIL",
                 f"вердикт={got['gate_tampered'].get('verdict')}")
    ctx.negative("подмена БЕЗ изменения размера тоже поймана",
                 got["same_size"] and got["download_same_size"] == 409
                 and got["gate_same_size"].get("verdict") == "FAIL",
                 f"размер совпал={got['same_size']}, HTTP={got['download_same_size']}, "
                 f"гейт={got['gate_same_size'].get('verdict')}")
    ctx.negative("квитанция не переносится на ЧУЖОЙ прогон",
                 got["gate_other_run"].get("verdict") == "FAIL",
                 f"вердикт={got['gate_other_run'].get('verdict')}")
    ctx.negative("квитанция не переносится на ЧУЖУЮ задачу",
                 got["gate_other_task"].get("verdict") == "FAIL",
                 f"вердикт={got['gate_other_task'].get('verdict')}")
    ctx.negative("без квитанции гейт не пропускает даже верный файл",
                 got["gate_no_receipt"].get("verdict") == "FAIL",
                 f"вердикт={got['gate_no_receipt'].get('verdict')}")
    ctx.negative("квитанция не выдаётся наружу вместе со статусом задачи",
                 got["receipt_hidden_from_api"], "в ответе /exports/{id} ключа квитанции нет")


# ------------------------------------------------------------------ 54
@scenario(id="OS-54", depth=INSTALLED_PRODUCT)
def os54_cancelled_export_leaves_no_half_file(ctx) -> None:
    """Отмена посреди кодирования не публикует ничего, и следующий прогон чист.

    Автоматического «возобновления с места» у экспорта нет по устройству: каждый
    артефакт неизменяем и лежит под своим run_id, поэтому продолжать нечего —
    владелец ставит экспорт заново. Проверяется именно это: полуфайл не
    публикуется, а повторный прогон НЕ склеивается с прерванным.
    """
    import psutil  # noqa: PLC0415

    mr = _media_tools(ctx)
    ctx.reached_installed_product("bcc.api.create_app + HTTP /api/video-studio этой ветки")
    seconds, fps, width, height = 10, 25, 640, 360
    source = mr.video_fixture(ctx.path("заготовка", "длинный.mp4"), seconds=seconds, fps=fps,
                              width=width, height=height)
    options = {"width": width, "height": height}

    def ffmpeg_children() -> int:
        me = psutil.Process()
        return sum(1 for child in me.children(recursive=True)
                   if "ffmpeg" in (child.name() or "").lower())

    async def run():
        out = {}
        # Рабочий пул продукта включён: отменяется настоящая исполняющаяся работа,
        # а не задача, которую сценарий запустил бы сам.
        async with _product(ctx, workers=True) as (svc, client):
            project_id, revision, _ = await _project_with_clip(
                client, Path(source["path"]), name="Отмена владельца")
            queued = await client.post(BASE + "/exports", json={
                "project_id": project_id, "expected_revision": revision,
                "operation_id": _op(), "options": options})
            job = queued.json()
            started = time.monotonic()
            stage = None
            while time.monotonic() - started < 60:
                status = (await client.get(BASE + "/exports/" + job["job_id"])).json()
                stage = (status.get("progress") or {}).get("stage")
                if status["status"] == "running" and stage == "compiling" and ffmpeg_children():
                    break
                if status["status"] in ("completed", "failed"):
                    break
                await asyncio.sleep(0.02)
            out["stage_at_cancel"] = stage
            out["status_at_cancel"] = status["status"]
            out["ffmpeg_running"] = ffmpeg_children()
            out["seconds_to_cancel"] = round(time.monotonic() - started, 3)
            if status["status"] != "running":
                return out
            out["cancel_code"] = (await client.post(
                f"{BASE}/exports/{job['job_id']}/cancel")).status_code
            waited = time.monotonic()
            while time.monotonic() - waited < 30:
                after = (await client.get(BASE + "/exports/" + job["job_id"])).json()
                if after["status"] in ("stopped", "cancelled", "failed", "completed"):
                    break
                await asyncio.sleep(0.05)
            out["after_cancel"] = after
            drain = time.monotonic()
            while time.monotonic() - drain < 20 and ffmpeg_children():
                await asyncio.sleep(0.05)
            out["ffmpeg_after_cancel"] = ffmpeg_children()
            out["download_cancelled"] = (await client.get(
                f"{BASE}/exports/{job['job_id']}/file")).status_code
            cancelled_dir = svc.video_studio.root / "exports" / job["job_id"]
            out["published_after_cancel"] = sorted(
                p.name for p in cancelled_dir.rglob("output.*") if p.is_file())

            # Повторный экспорт того же проекта: он обязан быть ЦЕЛЫМ, а не
            # продолжением прерванного. Задачу разбирает РАБОЧИЙ ПУЛ продукта —
            # звать движок руками здесь нельзя: тот же прогон достался бы двоим.
            again = await client.post(BASE + "/exports", json={
                "project_id": project_id, "expected_revision": revision,
                "operation_id": _op(), "options": options})
            fresh_job = again.json()
            since = time.monotonic()
            fresh = {}
            while time.monotonic() - since < 180:
                fresh = (await client.get(BASE + "/exports/" + fresh_job["job_id"])).json()
                if fresh["status"] in ("completed", "failed", "stopped", "cancelled"):
                    break
                await asyncio.sleep(0.05)
            out["fresh_status"] = fresh.get("status")
            if fresh.get("output_url"):
                target = ctx.path("скачано", "повтор.mp4")
                target.write_bytes((await client.get(fresh["output_url"])).content)
                out["fresh_file"] = target
            out["fresh_dir_differs"] = fresh_job["job_id"] != job["job_id"]
        return out

    got = asyncio.run(run())
    if got.get("status_at_cancel") != "running":
        ctx.not_proven("рендер закончился раньше, чем владелец успел нажать отмену "
                       f"(статус на момент наблюдения={got.get('status_at_cancel')}, "
                       f"стадия={got.get('stage_at_cancel')}, "
                       f"окно={got.get('seconds_to_cancel')} с); прерывания не было, "
                       "и выдавать это за проверенную отмену нельзя")

    ctx.positive("отмена застала настоящую работу: кодировщик был запущен",
                 got["ffmpeg_running"] >= 1 and got["stage_at_cancel"] == "compiling",
                 f"дочерних ffmpeg={got['ffmpeg_running']}, стадия={got['stage_at_cancel']}, "
                 f"через {got['seconds_to_cancel']} с после постановки")
    ctx.positive("продукт принял отмену владельца",
                 got["cancel_code"] == 200
                 and got["after_cancel"]["status"] in ("stopped", "cancelled"),
                 f"HTTP={got['cancel_code']}, статус={got['after_cancel']['status']}")
    ctx.positive("повторный экспорт доходит до конца — способность не потеряна",
                 got["fresh_status"] == "completed" and got["fresh_dir_differs"],
                 f"статус={got['fresh_status']}, другой job={got['fresh_dir_differs']}")

    expect = {"tracks": {"video": 1, "audio": 1}, "video_codec": "h264", "width": 640,
              "height": 360, "duration_s": float(seconds), "frames": seconds * fps,
              "min_bytes": 1024}
    if got.get("fresh_file") is None:
        # Повторный прогон не дал файла: это красная проверка с названной
        # причиной, а не крушение сценария на отсутствующем ключе.
        ctx.positive("повторный прогон дал ЦЕЛЫЙ ролик, а не склейку двух", False,
                     f"повторный экспорт кончился как {got['fresh_status']}, файла нет")
    else:
        fresh = mr.verify_artifact(got["fresh_file"], expect, label="повторный экспорт")
        ctx.positive("повторный прогон дал ЦЕЛЫЙ ролик, а не склейку двух",
                     fresh["passed"] and fresh["decoded_video_frames"] == str(seconds * fps),
                     f"кадров={fresh['decoded_video_frames']} (ожидалось {seconds * fps}), "
                     f"длительность={fresh['measured_duration_s']}")

    ctx.negative("отменённый экспорт не выдал владельцу ничего",
                 got["after_cancel"].get("output_url") is None
                 and got["download_cancelled"] == 409,
                 f"ссылка={got['after_cancel'].get('output_url')}, HTTP={got['download_cancelled']}")
    ctx.negative("после отмены под экспортом нет НИ ОДНОГО опубликованного файла",
                 got["published_after_cancel"] == [],
                 f"найдено={got['published_after_cancel']}")
    ctx.negative("отмена освободила кодировщик, а не оставила его крутиться",
                 got["ffmpeg_after_cancel"] == 0,
                 f"дочерних ffmpeg после отмены={got['ffmpeg_after_cancel']}")

    # Контроль оракула: если бы прогоны ДЕЙСТВИТЕЛЬНО склеились, он бы это увидел.
    whole = got.get("fresh_file") or Path(source["path"])
    spliced = ctx.path("контроль", "склейка.mp4")
    tools = mr.binaries()
    listing = ctx.path("контроль", "список.txt")
    listing.write_text(f"file '{whole}'\nfile '{whole}'\n", encoding="utf-8")
    mr.run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y", "-f", "concat",
            "-safe", "0", "-i", str(listing), "-c", "copy", *mr.BITEXACT, str(spliced)])
    ctx.refused("склейка двух прогонов тем же оракулом отвергается",
                lambda: mr.verify_artifact(spliced, expect, label="склейка"),
                mr.MediaEvidenceError)


# ------------------------------------------------------------------ 55
@scenario(id="OS-55", depth=INSTALLED_PRODUCT)
def os55_failure_is_one_human_reason(ctx) -> None:
    """Провал объясняется одной причиной: без путей, stderr и трассировки.

    Границы утверждения названы прямо. Здесь меряется ТОЛЬКО итог НЕУДАЧИ —
    `error_detail` в ответе `GET /api/video-studio/exports/{id}`, который
    собирает `bcc/video_studio/errors.py:render_failure`. Итог УСПЕШНОГО
    экспорта этот сценарий не проверяет и зелёным его не объявляет: единого
    человеческого предложения у успеха в продукте нет, владелец получает
    словарь `verification`. Объявлять это зелёным было бы неправдой, а
    объявлять дефектом — решением за владельца.
    """
    mr = _media_tools(ctx)
    ctx.reached_installed_product("bcc.api.create_app + HTTP /api/video-studio этой ветки")
    source = mr.video_fixture(ctx.path("заготовка", "исходник.mp4"), seconds=1, fps=25,
                              width=160, height=90)

    async def run():
        out = {}
        async with _product(ctx) as (svc, client):
            # Класс 1: проект без единого клипа — экспортировать нечего.
            empty = await client.post(BASE + "/projects",
                                      json={"name": "Пустой", "operation_id": _op()})
            empty_id = empty.json()["project"]["id"]
            _, invalid = await _export(svc, client, empty_id, int(empty.json()["revision"]))
            out["invalid"] = invalid

            # Класс 2: проект изменился ПОСЛЕ постановки экспорта в очередь.
            project_id, revision, _ = await _project_with_clip(
                client, Path(source["path"]), name="Гонка владельца")
            queued = await client.post(BASE + "/exports", json={
                "project_id": project_id, "expected_revision": revision,
                "operation_id": _op(), "options": {"width": 160, "height": 90}})
            job = queued.json()
            moved = await client.post(BASE + "/commands", json={
                "project_id": project_id, "expected_revision": revision, "operation_id": _op(),
                "command": {"type": "project.rename", "name": "Сдвинут"}})
            out["moved_code"] = moved.status_code
            await _run_task(svc, job["task_id"])
            out["conflict"] = (await client.get(BASE + "/exports/" + job["job_id"])).json()
            out["root"] = str(svc.video_studio.root)
        return out

    got = asyncio.run(run())
    invalid, conflict = got["invalid"], got["conflict"]
    invalid_detail = invalid.get("error_detail") or {}
    conflict_detail = conflict.get("error_detail") or {}

    ctx.positive("продукт довёл обе неудачи до владельца как ОДНУ запись каждая",
                 invalid.get("status") == "failed" and conflict.get("status") == "failed"
                 and set(invalid_detail) == {"code", "message", "stage", "retryable", "context_id"}
                 and set(conflict_detail) == set(invalid_detail),
                 f"поля итога={sorted(invalid_detail)}")
    ctx.positive("итог назван человеческим предложением, а не кодом возврата",
                 isinstance(invalid_detail.get("message"), str)
                 and invalid_detail["message"].strip().endswith(".")
                 and len(invalid_detail["message"].split()) >= 5,
                 f"текст={invalid_detail.get('message')!r}")
    ctx.positive("итог называет стадию и одноразовый номер для журнала",
                 invalid_detail.get("stage") == "compiling"
                 and len(str(invalid_detail.get("context_id", ""))) == 32
                 and invalid_detail["context_id"] != conflict_detail.get("context_id"),
                 f"стадия={invalid_detail.get('stage')}, номера различны="
                 f"{invalid_detail.get('context_id') != conflict_detail.get('context_id')}")
    ctx.positive("разные причины названы РАЗНЫМИ кодами, а не одним «внутренняя ошибка»",
                 invalid_detail.get("code") == "INVALID_PROJECT"
                 and conflict_detail.get("code") == "REVISION_CONFLICT",
                 f"пустой проект={invalid_detail.get('code')}, "
                 f"проект изменился={conflict_detail.get('code')}")
    ctx.positive("конфликт ревизии объяснён владельцу тем, что делать дальше",
                 "queue a new export" in str(conflict_detail.get("message", "")),
                 f"текст={conflict_detail.get('message')!r}")

    # Отрицательные контроли: в итоге НЕ должно быть внутренностей хоста.
    for label, payload in (("пустой проект", invalid_detail), ("конфликт ревизии", conflict_detail)):
        text = str(payload)
        ctx.negative(f"{label}: в итоге нет пути файловой системы хоста",
                     got["root"] not in text and "/" not in str(payload.get("message", "")),
                     f"итог={text[:160]}")
        ctx.negative(f"{label}: в итоге нет вывода ffmpeg и трассировки",
                     "Traceback" not in text and "ffmpeg" not in text.lower()
                     and "\n" not in str(payload.get("message", "")),
                     f"итог={text[:160]}")
    ctx.negative("готовый файл НЕ выдан за результат изменившегося проекта",
                 conflict.get("output_url") is None,
                 f"ссылка={conflict.get('output_url')}")
    ctx.negative("правка проекта после постановки экспорта была принята — гонка настоящая",
                 got["moved_code"] == 200, f"HTTP правки={got['moved_code']}")
