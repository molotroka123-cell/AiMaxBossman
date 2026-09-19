"""Импорт картинки меряет ТИП ПО БАЙТАМ, а не читает его из имени файла.

Дефект найден настоящим HTTP-запросом, а не чтением: страница ошибки 502,
сохранённая как `screenshot.png`, принималась (200), ложилась в базу с
`mime_type='image/png'` и отдавалась с `Content-Type: image/png`. Тело при
этом начиналось с `<!doctype html><html><head><title>502 Ba`.

Причина была в одной строке: `mime_type=allowed[suffix]` — тип брался из
расширения имени, которое присылает клиент.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.features import images as images_module

ROOT = Path(__file__).resolve().parents[2]

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>'
ERROR_PAGE = b"<!doctype html><html><head><title>502 Bad Gateway</title></head></html>"


# --- положительная половина: настоящие картинки принимаются ------------------
# Без неё «всё отвергается» тоже проходило бы проверки ниже.

@pytest.mark.parametrize("raw,expected", [
    (PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"),
    (WEBP, "image/webp"), (SVG, "image/svg+xml"),
])
def test_real_images_are_measured_correctly(raw, expected):
    assert images_module.measured_image_type(raw) == expected


# --- собственно дефект -------------------------------------------------------

def test_an_error_page_is_not_an_image():
    """Тот самый случай: страница 502, названная screenshot.png."""
    assert images_module.measured_image_type(ERROR_PAGE) is None


def test_an_html_page_containing_an_svg_tag_is_still_not_an_svg():
    """Подпись ищется структурно, а не поиском подстроки `<svg`.

    Иначе страницу можно было бы протащить, упомянув svg где-нибудь внутри.
    """
    sneaky = b"<!doctype html><html><body><svg></svg></body></html>"
    assert images_module.measured_image_type(sneaky) is None


@pytest.mark.parametrize("raw", [b"", b"not an image at all", b"%PDF-1.7\n%..."])
def test_junk_is_refused(raw):
    assert images_module.measured_image_type(raw) is None


def test_a_png_renamed_to_jpg_is_measured_as_png():
    """Мера не зависит от имени вовсе — имя сюда не передаётся."""
    assert images_module.measured_image_type(PNG) == "image/png"


# --- контроль пролога SVG ----------------------------------------------------

def test_an_svg_behind_a_comment_or_prolog_is_still_an_svg():
    """Пара к отказам выше: законный SVG с прологом проходить обязан."""
    framed = (b"\xef\xbb\xbf" + b'<?xml version="1.0"?>' + b"<!-- \xd0\xbf\xd1\x80 -->"
              + b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert images_module.measured_image_type(framed) == "image/svg+xml"


def test_the_route_no_longer_takes_the_type_from_the_filename():
    """Структурный след дефекта: строка `mime_type=allowed[suffix]` ушла."""
    source = (ROOT / "command-center" / "bcc" / "features" / "images.py").read_text(encoding="utf-8")
    assert "mime_type=allowed[suffix]" not in source, (
        "тип снова берётся из расширения имени — это и был дефект")
    assert "mime_type=measured," in source


# --- СКВОЗНОЕ через настоящую ручку -----------------------------------------
# Проверок чистой функции выше НЕ ДОСТАТОЧНО, и это выяснилось мутацией:
# я отключил в маршруте сравнение измеренного типа с именем, и все тесты
# остались зелёными. Функция меряла верно — маршрут её ответ игнорировал.
# Ниже проверяется сам отказ, по-настоящему, через HTTP.

import base64  # noqa: E402


async def test_an_error_page_named_png_is_refused_by_the_route(env):
    """Тот самый дефект, целиком: 502-страница под именем screenshot.png."""
    res = await env.client.post("/api/images/assets/import", json={
        "filename": "screenshot.png",
        "data_base64": base64.b64encode(ERROR_PAGE).decode(),
        "title": "битая загрузка",
    })
    assert res.status_code == 422, (
        f"страница ошибки принята как картинка: {res.status_code} {res.text[:200]}")
    assert "опознан" in res.text or "содержимое" in res.text


async def test_a_real_png_is_still_accepted(env):
    """Положительная половина. Без неё отказ выше давала бы и сломанная ручка."""
    res = await env.client.post("/api/images/assets/import", json={
        "filename": "sample.png",
        "data_base64": base64.b64encode(PNG).decode(),
        "title": "настоящий png",
    })
    assert res.status_code == 200, res.text
    assert res.json()["mime_type"] == "image/png"


async def test_a_png_body_under_a_jpg_name_is_refused(env):
    """Несовпадение измеренного типа и имени — отдельный отказ.

    Именно эту ветку мутация и погасила, оставив набор зелёным.
    """
    res = await env.client.post("/api/images/assets/import", json={
        "filename": "photo.jpg",
        "data_base64": base64.b64encode(PNG).decode(),
        "title": "png под чужим именем",
    })
    assert res.status_code == 422, (
        f"PNG принят под именем .jpg: {res.status_code} {res.text[:200]}")
    assert "image/png" in res.text and "image/jpeg" in res.text
