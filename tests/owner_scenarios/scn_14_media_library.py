"""Владельческие сценарии 56–60: медиатека, Image Studio и очередь студий.

Тот же принцип, что и в 51–55: меряется ШИПОВАЯ дверь, а не удобная функция.
Прошлая волна уже ошиблась ровно на этом — заявила дефект по одному внутреннему
слою, не посмотрев, каким путём продукт пользуется на самом деле. Поэтому здесь
в каждом сценарии прямо названо, ЧТО измерено:

* 56 — HTTP `POST /api/images/assets/import` (BL-091), а не `measured_image_type`;
* 57 — HTTP `POST /api/video-studio/media`, а не `media.file_kind`;
* 58 — HTTP `GET /api/video-studio/media/{id}/file`, а не `ReadVerifier.resolve`;
* 59 — HTTP `GET /api/studio/models` — витрина, из которой владелец выбирает
  модель. Второй рубеж (`governance.reserve`) проверяется рядом, но отдельно,
  потому что в цепочке `dispatch.generate` он стоит ПОСЛЕ проверки ключа;
* 60 — HTTP очередь Image Studio с НАСТОЯЩИМ фоновым тиком продукта.

Зависимости модуля — только стандартная библиотека и каркас; всё продуктовое
импортируется внутри функций и объявлено в реестре как `command_center`
(BL-085). `ffprobe` объявлен там, где сценарий действительно меряет медиа.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import INSTALLED_PRODUCT, scenario  # noqa: E402

VIDEO = "/api/video-studio"
IMAGES = "/api/images"
STUDIO = "/api/studio"

#: Сохранённая браузером страница ошибки — то, что владелец приносит чаще всего.
ERROR_PAGE = (b"<!doctype html><html><head><title>502 Bad Gateway</title></head>"
              b"<body><h1>502 Bad Gateway</h1></body></html>\n")
#: Плейлист: не контейнер, а ссылка наружу под видом медиа.
PLAYLIST = b"#EXTM3U\n#EXT-X-VERSION:3\nhttps://example.invalid/segment-0.ts\n"


def _op() -> str:
    return uuid.uuid4().hex


def _media_tools(ctx):
    """Бинари медиа или ЧЕСТНЫЙ тупик: отсутствие ffmpeg — не дефект продукта."""
    import media_roundtrip as mr  # noqa: PLC0415
    try:
        mr.binaries()
    except mr.MediaEvidenceError as exc:
        ctx.not_proven(f"пара ffmpeg/ffprobe недоступна, измерять нечем: {exc}")
    return mr


@contextlib.asynccontextmanager
async def _product(ctx, *, workers: bool = False):
    """Установленный Bossman: то же приложение и тот же токен, что у владельца."""
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


# ------------------------------------------------------------------ 56
@scenario(id="OS-56", depth=INSTALLED_PRODUCT)
def os56_image_import_is_measured_by_bytes(ctx) -> None:
    """BL-091: тип импортируемой картинки решают БАЙТЫ, а не имя и не заголовок.

    Измерена шиповая дверь — HTTP `POST /api/images/assets/import`
    (`command-center/bcc/features/images.py:357`). Функция `measured_image_type`
    отдельно не проверяется: зелёная функция при дырявом эндпоинте ничего
    владельцу не даёт.
    """
    mr = _media_tools(ctx)
    ctx.reached_installed_product("bcc.api.create_app + HTTP /api/images этой ветки")
    png = Path(mr.image_fixture(ctx.path("заготовка", "кадр.png"), width=64, height=48)["path"])
    jpeg = ctx.path("заготовка", "кадр.jpg")
    tools = mr.binaries()
    mr.run([tools["ffmpeg"], "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", str(png),
            "-frames:v", "1", *mr.BITEXACT, str(jpeg)])

    def payload(name: str, raw: bytes) -> dict:
        return {"filename": name, "data_base64": base64.b64encode(raw).decode()}

    async def run():
        out = {}
        async with _product(ctx) as (svc, client):
            # ОТРИЦАТЕЛЬНЫЕ ПЕРВЫМИ: библиотека ещё пуста, и «ничего не осело»
            # проверяется по её размеру, а не по вере в отказ.
            bad = {
                "страница ошибки с именем .png": payload("screenshot.png", ERROR_PAGE),
                "jpeg под именем .png": payload("photo.png", jpeg.read_bytes()),
                "png под именем .jpg": payload("frame.jpg", png.read_bytes()),
                "html со встроенным <svg> под именем .svg": payload(
                    "logo.svg", b"<!doctype html><html><body>"
                                b'<svg xmlns="http://www.w3.org/2000/svg"></svg></body></html>'),
                "пустой файл": payload("empty.png", b""),
                "исполняемый файл под именем .png": payload("tool.png", b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64),
            }
            out["refusals"] = {}
            for label, body in bad.items():
                answer = await client.post(IMAGES + "/assets/import", json=body)
                out["refusals"][label] = (answer.status_code, answer.text[:200])
            out["library_after_refusals"] = (await client.get(IMAGES + "/assets")).json()["total"]

            # ПОЛОЖИТЕЛЬНАЯ ПОЛОВИНА: настоящий PNG проходит и остаётся собой.
            good = await client.post(IMAGES + "/assets/import",
                                     json={**payload("моя-картинка.png", png.read_bytes()),
                                           "title": "Импорт владельца"})
            out["good"] = (good.status_code, good.json() if good.status_code == 200 else good.text[:200])
            if good.status_code == 200:
                asset = good.json()
                fetched = await client.get(f"{IMAGES}/assets/{asset['id']}/file")
                out["fetch_code"] = fetched.status_code
                out["fetch_type"] = fetched.headers.get("content-type", "")
                target = ctx.path("скачано", "библиотека.png")
                target.write_bytes(fetched.content)
                out["fetched"] = target
            out["library_total"] = (await client.get(IMAGES + "/assets")).json()["total"]
            # Заявленный тип содержимого двери не подсказывает: у импорта его нет
            # вовсе, тип берётся ТОЛЬКО из байтов.
            declared = await client.post(IMAGES + "/assets/import",
                                         json=payload("screenshot.png", ERROR_PAGE),
                                         headers={"content-type": "application/json"})
            out["declared_type_ignored"] = declared.status_code
        return out

    got = asyncio.run(run())
    for label, (code, text) in got["refusals"].items():
        ctx.negative(f"{label} отвергнут дверью импорта",
                     code == 422, f"HTTP={code}, ответ={text}")
    ctx.negative("ни один отказ не оставил строки в библиотеке владельца",
                 got["library_after_refusals"] == 0,
                 f"картинок после {len(got['refusals'])} отказов={got['library_after_refusals']}")
    ctx.negative("страница ошибки не проходит и при «правильном» заголовке запроса",
                 got["declared_type_ignored"] == 422, f"HTTP={got['declared_type_ignored']}")

    code, asset = got["good"]
    ctx.positive("настоящий PNG принят той же дверью — способность не потеряна",
                 code == 200 and isinstance(asset, dict), f"HTTP={code}")
    ctx.positive("в библиотеке записан ИЗМЕРЕННЫЙ тип, а не обещание имени",
                 asset.get("mime_type") == "image/png" and asset.get("model_alias") == "import",
                 f"mime_type={asset.get('mime_type')}")
    ctx.positive("продукт отдаёт картинку обратно тем же измеренным типом",
                 got["fetch_code"] == 200 and got["fetch_type"].startswith("image/png"),
                 f"HTTP={got['fetch_code']}, Content-Type={got['fetch_type']}")
    ctx.positive("в библиотеке ровно одна картинка — та, что владелец принёс",
                 got["library_total"] == 1, f"всего={got['library_total']}")

    evidence = mr.verify_artifact(got["fetched"], {"tracks": {"video": 1}, "video_codec": "png",
                                                   "width": 64, "height": 48,
                                                   "sha256": mr.digest(png), "min_bytes": 64},
                                  label="картинка из библиотеки")
    ctx.positive("ffprobe независимо подтвердил: вернулись те же байты того же типа",
                 evidence["passed"] and evidence["measured_format"] == "png_pipe",
                 f"измеренный формат={evidence['measured_format']}, байт={evidence['bytes']}")


# ------------------------------------------------------------------ 57
@scenario(id="OS-57", depth=INSTALLED_PRODUCT)
def os57_media_upload_is_measured_by_bytes(ctx) -> None:
    """Контейнер загружаемого медиа решают байты; плейлист и ссылка запрещены.

    Измерена шиповая дверь — HTTP `POST /api/video-studio/media`
    (`command-center/bcc/features/video_studio.py:169`), то есть весь путь
    загрузка → `MediaLibrary.import_file` → `file_kind` → `probe`.
    """
    mr = _media_tools(ctx)
    ctx.reached_installed_product("bcc.api.create_app + HTTP /api/video-studio этой ветки")
    video = Path(mr.video_fixture(ctx.path("заготовка", "ролик.mp4"), seconds=1, fps=25,
                                  width=160, height=90)["path"])
    png = Path(mr.image_fixture(ctx.path("заготовка", "кадр.png"), width=64, height=48)["path"])

    async def run():
        out = {}
        async with _product(ctx) as (svc, client):
            created = await client.post(VIDEO + "/projects",
                                        json={"name": "Медиатека", "operation_id": _op()})
            project_id = created.json()["project"]["id"]

            async def upload(name, content, revision, content_type=None):
                headers = {"content-type": content_type} if content_type else None
                return await client.post(VIDEO + "/media", params={
                    "project_id": project_id, "filename": name,
                    "expected_revision": revision, "operation_id": _op()},
                    content=content, headers=headers)

            # ОТРИЦАТЕЛЬНЫЕ ПЕРВЫМИ: ревизия проекта ещё 0, и «правка не прошла»
            # доказуема ею же.
            bad = {
                "страница ошибки с именем .mp4": ("scene.mp4", ERROR_PAGE),
                "плейлист m3u8 под именем .mp4": ("clip.mp4", PLAYLIST),
                "ссылка наружу вместо файла": ("evil.mp4", b"https://example.invalid/video"),
                "пустая загрузка": ("empty.mp4", b""),
                "имя с выходом из каталога": ("../../secret", video.read_bytes()),
            }
            out["refusals"] = {}
            for label, (name, content) in bad.items():
                answer = await upload(name, content, 0, content_type="video/mp4")
                out["refusals"][label] = (answer.status_code, answer.text[:200])
            out["revision_after_refusals"] = (await client.get(
                VIDEO + "/projects/" + project_id)).json()["revision"]
            out["leftovers"] = [p.name for p in (svc.video_studio.root / "uploads").glob("*.upload")]

            good = await upload(video.name, video.read_bytes(), 0)
            out["good_code"] = good.status_code
            out["media"] = good.json()["media"] if good.status_code == 200 else good.text[:200]
            revision = good.json()["revision"] if good.status_code == 200 else 0

            # Имя обещает mp4, заголовок обещает видео, а внутри картинка:
            # решает содержимое, и оно сохраняется как картинка.
            disguised = await upload("clip.mp4", png.read_bytes(), revision,
                                     content_type="video/mp4")
            out["disguised_code"] = disguised.status_code
            out["disguised"] = (disguised.json()["media"] if disguised.status_code == 200
                                else disguised.text[:200])
            out["root"] = svc.video_studio.root
        return out

    got = asyncio.run(run())
    for label, (code, text) in got["refusals"].items():
        ctx.negative(f"{label} отвергнут дверью загрузки",
                     code == 422, f"HTTP={code}, ответ={text}")
    ctx.negative("ни один отказ не сдвинул ревизию проекта",
                 got["revision_after_refusals"] == 0,
                 f"ревизия={got['revision_after_refusals']}")
    ctx.negative("после отказов не осталось ни одного недогруженного файла",
                 got["leftovers"] == [], f"найдено={got['leftovers']}")

    # Ответ двери может оказаться не объектом (отказ): проверка обязана в этом
    # случае ПОКРАСНЕТЬ с внятным текстом, а не упасть по TypeError.
    media = got["media"] if isinstance(got["media"], dict) else {}
    disguised = got["disguised"] if isinstance(got["disguised"], dict) else {}
    ctx.positive("настоящий ролик принят — способность не потеряна",
                 got["good_code"] == 200 and bool(media),
                 f"HTTP={got['good_code']}, ответ={str(got['media'])[:120]}")
    ctx.positive("продукт САМ измерил контейнер и дорожки, не поверив имени",
                 media.get("metadata", {}).get("format") == "mov,mp4,m4a,3gp,3g2,mj2"
                 and media.get("has_video") and media.get("has_audio"),
                 f"формат={media.get('metadata', {}).get('format')}, "
                 f"видео={media.get('has_video')}, звук={media.get('has_audio')}")
    ctx.positive("файл лёг в хранилище под именем собственного хеша",
                 bool(media) and Path(media["relative_path"]).stem == media["sha256"]
                 and (got["root"] / media["relative_path"]).is_file(),
                 f"путь={media.get('relative_path')}")
    ctx.positive("картинка под именем .mp4 сохранена КАК КАРТИНКА: решает содержимое",
                 got["disguised_code"] == 200
                 and disguised.get("metadata", {}).get("format") == "png_pipe"
                 and str(disguised.get("relative_path", "")).endswith(".png"),
                 f"HTTP={got['disguised_code']}, измеренный формат="
                 f"{disguised.get('metadata', {}).get('format')}, "
                 f"путь={disguised.get('relative_path')}")

    if not media:
        ctx.positive("ffprobe независимо подтвердил содержимое сохранённого медиа",
                     False, "дверь загрузки не приняла настоящий ролик: проверять нечего")
        return
    stored = got["root"] / media["relative_path"]
    evidence = mr.verify_artifact(stored, {"tracks": {"video": 1, "audio": 1},
                                           "duration_s": 1.0, "frames": 25, "min_bytes": 1024},
                                  label="медиа в библиотеке")
    ctx.positive("ffprobe независимо подтвердил содержимое сохранённого медиа",
                 evidence["passed"] and evidence["sha256"] == media["sha256"],
                 f"кадров={evidence['decoded_video_frames']}, формат={evidence['measured_format']}")


# ------------------------------------------------------------------ 58
@scenario(id="OS-58", depth=INSTALLED_PRODUCT)
def os58_verified_read_refuses_changed_media(ctx) -> None:
    """Файл, изменившийся между проверкой и использованием, владельцу не отдают.

    Измерена шиповая дверь — HTTP `GET /api/video-studio/media/{id}/file`
    (`command-center/bcc/features/video_studio.py:177`), то есть весь путь
    `resolve_for_read` → `ReadVerifier.resolve` → потоковая выдача. Проверка
    НИКОГДА не кешируется: это доказывается чередованием хороших и плохих
    чтений одного и того же пути.
    """
    mr = _media_tools(ctx)
    ctx.reached_installed_product("bcc.api.create_app + HTTP /api/video-studio этой ветки")
    video = Path(mr.video_fixture(ctx.path("заготовка", "ролик.mp4"), seconds=1, fps=25,
                                  width=160, height=90)["path"])
    foreign = Path(mr.video_fixture(ctx.path("заготовка", "чужой.mp4"), seconds=1, fps=25,
                                    width=160, height=90, tone_hz=880)["path"])

    async def run():
        out = {}
        async with _product(ctx) as (svc, client):
            created = await client.post(VIDEO + "/projects",
                                        json={"name": "Чтение", "operation_id": _op()})
            project_id = created.json()["project"]["id"]
            upload = await client.post(VIDEO + "/media", params={
                "project_id": project_id, "filename": video.name,
                "expected_revision": 0, "operation_id": _op()}, content=video.read_bytes())
            media = upload.json()["media"]
            stored = svc.video_studio.root / media["relative_path"]
            original = stored.read_bytes()

            async def read():
                answer = await client.get(f"{VIDEO}/media/{media['id']}/file",
                                          params={"project_id": project_id})
                return answer.status_code, answer.content

            out["first"] = await read()
            out["declared_sha"] = media["sha256"]

            # 1. Правка на месте БЕЗ изменения размера: метаданные не спасут.
            same_size = bytearray(original)
            same_size[-1] = (same_size[-1] + 1) % 256
            with stored.open("r+b") as handle:
                handle.write(bytes(same_size))
            out["same_size_size_equal"] = stored.stat().st_size == len(original)
            out["same_size"] = (await read())[0]

            # 2. Восстановление тех же байтов снова открывает чтение: значит,
            # отказ был про СОДЕРЖИМОЕ, а не про «этот файл занесён в чёрный список».
            stored.write_bytes(original)
            out["restored"] = await read()

            # 3. Обрезание.
            with stored.open("r+b") as handle:
                handle.truncate(len(original) // 2)
            out["truncated"] = (await read())[0]
            stored.write_bytes(original)

            # 4. Подмена имени: файл удалён и создан заново ЧУЖИМИ байтами.
            stored.unlink()
            stored.write_bytes(foreign.read_bytes())
            out["swapped"] = (await read())[0]
            stored.write_bytes(original)

            # 5. Ссылка НА МЕСТЕ САМОГО медиа — единственный путь, по которому
            # чтение действительно пошло бы. Ссылка рядом ничего не доказывает:
            # её никто не открывает, и «отказ» на ней был бы выдуманным.
            out["symlink_made"] = False
            stored.unlink()
            with contextlib.suppress(OSError, NotImplementedError):
                stored.symlink_to(foreign)
                out["symlink_made"] = stored.is_symlink()
            if out["symlink_made"]:
                out["symlink_foreign"] = (await read())[0]
                stored.unlink()
                # И та же ссылка на ПРАВИЛЬНЫЕ байты: отказ обязан быть про
                # ссылку, а не про содержимое.
                twin = ctx.path("подмена", "двойник.mp4")
                twin.write_bytes(original)
                stored.symlink_to(twin)
                out["symlink_identical"] = (await read())[0]
                stored.unlink()
            stored.write_bytes(original)
            out["after_all"] = await read()
        return out

    got = asyncio.run(run())
    first_code, first_bytes = got["first"]
    restored_code, restored_bytes = got["restored"]
    final_code, final_bytes = got["after_all"]

    import hashlib  # noqa: PLC0415
    served = hashlib.sha256(first_bytes).hexdigest()
    ctx.positive("неизменённое медиа владелец читает целиком и получает ИМЕННО его",
                 first_code == 200 and served == got["declared_sha"],
                 f"HTTP={first_code}, байт={len(first_bytes)}, хеш выданного совпал="
                 f"{served == got['declared_sha']}")
    ctx.positive("те же байты после восстановления читаются снова",
                 restored_code == 200 and restored_bytes == first_bytes,
                 f"HTTP={restored_code}, совпало={restored_bytes == first_bytes}")
    ctx.positive("после всех подмен и восстановления выдача снова открыта",
                 final_code == 200 and final_bytes == first_bytes,
                 f"HTTP={final_code}")

    ctx.negative("правка на месте БЕЗ изменения размера закрывает выдачу",
                 got["same_size_size_equal"] and got["same_size"] == 422,
                 f"размер совпал={got['same_size_size_equal']}, HTTP={got['same_size']}")
    ctx.negative("обрезанное медиа не отдаётся",
                 got["truncated"] == 422, f"HTTP={got['truncated']}")
    ctx.negative("подмена файла по тому же пути чужими байтами не отдаётся",
                 got["swapped"] == 422, f"HTTP={got['swapped']}")
    if got.get("symlink_made"):
        ctx.negative("ссылка НА МЕСТЕ медиа не открывается, даже указывая на верные байты",
                     got["symlink_foreign"] == 422 and got["symlink_identical"] == 422,
                     f"чужие байты HTTP={got['symlink_foreign']}, "
                     f"те же байты HTTP={got['symlink_identical']}")


# ------------------------------------------------------------------ 59
@scenario(id="OS-59", depth=INSTALLED_PRODUCT)
def os59_studio_showcase_never_sells_paid_as_free(ctx) -> None:
    """BL-084 на ВИТРИНЕ: ноль в поле владельца не делает модель бесплатной.

    Какой слой шиповой. Владелец выбирает модель из ответа
    `GET /api/studio/models` (`command-center/bcc/features/studio.py:35-36` →
    `command-center/bcc/studio/runtime.py:89`) — это и есть витрина, и она
    меряется здесь. Второй рубеж, `governance.reserve`, в цепочке
    `command-center/bcc/studio/dispatch.py:55` стоит ПОСЛЕ проверки ключа
    владельца (строки 52–54), поэтому в живой цепочке без ключа до него не
    доходит; он вызывается отдельно и назван отдельно, а не выдан за витрину.

    Про ключ. Признак «ключ настроен» — предусловие, а не предмет проверки:
    во ВСЕХ трёх замерах он один и тот же, меняется только политика, поэтому
    решает именно `free_only`. Значение берётся из окружения; если у владельца
    ключа нет, на время сценария ставится собранная из кусков заглушка, которая
    никуда не отправляется, и окружение восстанавливается.
    """
    ctx.reached_installed_product("bcc.api.create_app + HTTP /api/studio этой ветки")
    paid = "openrouter:" + "minimax/" + "hailuo-3-max"
    #: Оба имени одной переменной: расходящиеся значения сами по себе закрывают
    #: витрину, и тогда замер говорил бы не о бюджете, а о конфликте имён.
    variables = ("OPENROUTER" + "_API_KEY", "BOSSMAN_" + "OPENROUTER" + "_API_KEY")
    placeholder = "sk-" + "or-" + "v1-" + uuid.uuid4().hex

    async def run():
        from bcc.studio import catalog  # noqa: PLC0415
        from bcc.studio import governance as gov  # noqa: PLC0415
        from bcc.studio.runtime import StudioError  # noqa: PLC0415

        out = {"catalog": {"paid": catalog.declared_free(paid),
                           "free": catalog.declared_free("comfyui:*"),
                           "unknown": catalog.declared_free("openrouter:" + uuid.uuid4().hex)}}
        saved_env = {name: os.environ.get(name) for name in variables}
        try:
            async with _product(ctx) as (svc, client):
                async def showcase(policy):
                    stored = await client.put(STUDIO + "/policy", json=policy)
                    if stored.status_code != 200:
                        raise AssertionError(
                            f"политика не сохранена: {stored.status_code} {stored.text[:160]}")
                    rows = (await client.get(STUDIO + "/models")).json()["items"]
                    return {row["id"]: row for row in rows}

                base = {"enabled": True, "cloud_budget_usd": 10, "per_job_usd": 10,
                        "download_hosts": []}
                # 0. Ключа нет вовсе — это рубеж УЧЁТНЫХ ДАННЫХ, а не бюджета.
                for name in variables:
                    os.environ.pop(name, None)
                out["no_key"] = (await showcase({**base, "free_only": False,
                                                 "prices": {paid: 0.1}}))[paid]["configured"]
                # Дальше ключ ОДИН И ТОТ ЖЕ во всех замерах: меняется политика.
                os.environ[variables[0]] = saved_env[variables[0]] or placeholder
                # 1. free_only + ноль владельца напротив ПЛАТНОЙ модели.
                out["zero"] = (await showcase({**base, "free_only": True,
                                               "prices": {paid: 0}}))[paid]
                # 2. free_only + честная цена — та же модель, тот же ключ.
                out["paid_under_free_only"] = (await showcase({**base, "free_only": True,
                                                               "prices": {paid: 0.1}}))[paid]
                # 3. Владелец снял free_only: способность не потеряна.
                honest = await showcase({**base, "free_only": False, "prices": {paid: 0.1}})
                out["honest"] = honest[paid]
                out["mock"] = honest.get("mock:image", {})
                out["budget"] = (await client.get(STUDIO + "/budget")).json()
                # Второй рубеж — отдельно и назван отдельно.
                await gov.save_policy(svc, {"enabled": True, "free_only": True,
                                            "cloud_budget_usd": 10, "per_job_usd": 10,
                                            "prices": {paid: 0}})
                try:
                    await gov.reserve(svc, paid, 1, 1, [])
                    out["reserve"] = "ПРОПУСТИЛ"
                except StudioError as exc:
                    out["reserve"] = str(exc)
                out["budget_after_reserve"] = (await gov.budget_status(svc))["committed_upper_bound_usd"]
        finally:
            for name, value in saved_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        return out

    got = asyncio.run(run())
    ctx.positive("каталог поставки умеет подтверждать БЕСПЛАТНЫЙ тариф",
                 got["catalog"]["free"] is True, "declared_free('comfyui:*') is True")
    ctx.positive("витрина умеет говорить «доступна»: локальная модель предложена",
                 got["mock"].get("available") is True,
                 f"mock:image available={got['mock'].get('available')}")
    ctx.positive("с честной ценой и снятым free_only та же модель настроена",
                 got["honest"].get("configured") is True,
                 f"configured={got['honest'].get('configured')}")
    ctx.positive("дневной счётчик расходов остался нулевым: витрина ничего не купила",
                 got["budget"]["committed_upper_bound_usd"] == 0
                 and got["budget_after_reserve"] == 0,
                 f"committed={got['budget']['committed_upper_bound_usd']} → "
                 f"{got['budget_after_reserve']}")

    ctx.negative("каталог НЕ подтверждает бесплатность платной модели",
                 got["catalog"]["paid"] is False, "declared_free(платная) is False")
    ctx.negative("о неизвестной каталогу модели бесплатность не утверждается",
                 got["catalog"]["unknown"] is None, "declared_free(неизвестная) is None")
    ctx.negative("ноль владельца при free_only НЕ выставляет платную модель на витрину",
                 got["zero"].get("configured") is False and got["zero"].get("available") is False,
                 f"configured={got['zero'].get('configured')}, available={got['zero'].get('available')}")
    ctx.negative("платная модель при free_only не предлагается и с честной ценой",
                 got["paid_under_free_only"].get("configured") is False,
                 f"configured={got['paid_under_free_only'].get('configured')}")
    ctx.negative("без ключа владельца модель не предлагается — рубежи не спутаны",
                 got["no_key"] is False, f"configured без ключа={got['no_key']}")
    ctx.negative("второй рубеж отказал по НАЗВАННОЙ причине, а не молча",
                 "free_only" in str(got["reserve"]), f"отказ резерва: {got['reserve']}")


# ------------------------------------------------------------------ 60
@scenario(id="OS-60", depth=INSTALLED_PRODUCT)
def os60_queue_cancel_and_status_are_honest(ctx) -> None:
    """Долгая работа не блокирует владельца, а очередь не врёт про её исход.

    Прогон идёт с ВКЛЮЧЁННЫМИ рабочими циклами продукта: задачи разбирает
    настоящий фоновый тик Image Studio, а не сценарий. Артефакт здесь не
    меряется намеренно — генерация картинок в этой поставке идёт через
    детерминированный мок (это уже честно записано в OS-16). Предмет проверки —
    поведение ОЧЕРЕДИ.
    """
    ctx.reached_installed_product("bcc.api.create_app + фоновый тик /api/images этой ветки")

    async def run():
        out = {}
        async with _product(ctx, workers=True) as (svc, client):
            first = (await client.post(IMAGES + "/jobs",
                                       json={"prompt": "Отменяемая работа", "count": 1})).json()
            second = (await client.post(IMAGES + "/jobs",
                                        json={"prompt": "Соседняя работа", "count": 1})).json()
            out["queued"] = (first["status"], second["status"])
            cancelled = await client.post(f"{IMAGES}/jobs/{first['id']}/cancel")
            out["cancel_code"] = cancelled.status_code
            out["cancel_body"] = cancelled.json()

            started = time.monotonic()
            neighbour = {}
            while time.monotonic() - started < 60:
                neighbour = (await client.get(f"{IMAGES}/jobs/{second['id']}")).json()
                if neighbour["status"] in ("completed", "failed", "cancelled"):
                    break
                await asyncio.sleep(0.05)
            out["neighbour"] = neighbour
            out["seconds_to_finish"] = round(time.monotonic() - started, 3)
            # Дать очереди ещё один полный виток: отменённая работа не должна
            # ожить ни на следующем тике, ни после соседней.
            await asyncio.sleep(2.0)
            out["cancelled_after_wait"] = (await client.get(f"{IMAGES}/jobs/{first['id']}")).json()
            assets = (await client.get(IMAGES + "/assets")).json()
            out["assets"] = [(a["id"], a["source_job_id"]) for a in assets["items"]]
            out["retry"] = (await client.post(f"{IMAGES}/jobs/{first['id']}/retry")).json()
            out["after_retry"] = (await client.get(f"{IMAGES}/jobs/{first['id']}")).json()
            out["idle"] = (await client.get(f"{IMAGES}/jobs/{uuid.uuid4().int % 10**6 + 10**6}")).status_code
            # Владелец не заблокирован: пока очередь работает, продукт отвечает.
            out["overview"] = (await client.get(IMAGES + "/overview")).status_code
            out["listing"] = (await client.get(IMAGES + "/jobs")).json()["total"]
        return out

    got = asyncio.run(run())
    cancelled_now = got["cancelled_after_wait"]
    ctx.positive("обе работы встали в очередь, а не выполнились в ответе владельцу",
                 got["queued"] == ("queued", "queued"), f"статусы={got['queued']}")
    ctx.positive("соседняя работа дошла до конца САМА, фоновым циклом продукта",
                 got["neighbour"]["status"] == "completed",
                 f"статус={got['neighbour']['status']} за {got['seconds_to_finish']} с")
    ctx.positive("результат ровно один и принадлежит той работе, что его сделала",
                 len(got["assets"]) == 1 and got["assets"][0][1] == got["neighbour"]["id"],
                 f"артефакты={got['assets']}, соседняя работа={got['neighbour']['id']}")
    ctx.positive("повтор создаёт НОВУЮ работу в очереди",
                 got["retry"]["id"] != cancelled_now["id"] and got["retry"]["status"] == "queued",
                 f"новая работа={got['retry']['id']} вместо {cancelled_now['id']}")
    ctx.positive("продукт отвечает владельцу, пока очередь занята",
                 got["overview"] == 200 and got["listing"] >= 3,
                 f"обзор HTTP={got['overview']}, работ в списке={got['listing']}")

    ctx.negative("отменённая работа не ожила на следующем витке очереди",
                 cancelled_now["status"] == "cancelled",
                 f"статус через 2 с после отмены={cancelled_now['status']}")
    ctx.negative("отменённая работа не выдала ни одного артефакта",
                 all(source != cancelled_now["id"] for _, source in got["assets"]),
                 f"артефакты={got['assets']}")
    ctx.negative("отменённая работа не показывает прогресс завершения",
                 (cancelled_now.get("progress") or 0) < 1.0,
                 f"progress={cancelled_now.get('progress')}")
    ctx.negative("повтор не воскресил старую работу",
                 got["after_retry"]["status"] == "cancelled",
                 f"статус исходной после повтора={got['after_retry']['status']}")
    ctx.negative("несуществующая работа — честный 404, а не пустой успех",
                 got["idle"] == 404, f"HTTP={got['idle']}")
