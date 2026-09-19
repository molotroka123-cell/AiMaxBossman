"""Машиночитаемый код отказа называет НАСТОЯЩУЮ причину, а не одну на всех.

BL-104. HTTP-обёртка `features/video_studio.guarded` выдавала код
`derivative_not_prepared` ЛЮБОМУ `RuntimeError` подсистемы. Комментарий прямо
объявлял допущение: «RuntimeError здесь — наш собственный текст… `prepared_file`
этим сообщает „производная ещё не подготовлена“». Допущение неверно: тот же тип
бросают ещё десяток мест, среди них «revision conflict» и «output changed after
independent verification».

Видимый владельцу ТЕКСТ был верен всегда — он проходит насквозь. Врал
машиночитаемый код, которым пользуются автоматика и агенты: на обнаруженную
подмену артефакта им советовали «запусти prepare», то есть починить не то.

Найдено владельческим сценарием OS-55 при прогоне табло из восьмидесяти строк.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi import HTTPException

from bcc.features import video_studio as feature
from bcc.video_studio import service as service_module
from bcc.video_studio.model import Conflict, DerivativeNotPrepared, MissingObject


def _guard(error: BaseException):
    async def raising():
        raise error

    async def run():
        with pytest.raises(HTTPException) as caught:
            await feature.guarded(raising())
        return caught.value
    return asyncio.run(run())


def test_only_the_derivative_case_gets_the_derivative_code():
    failure = _guard(DerivativeNotPrepared("queue analysis action prepare"))
    assert failure.status_code == 409
    assert failure.detail["code"] == "derivative_not_prepared"
    assert "prepare" in failure.detail["message"]


@pytest.mark.parametrize("message", [
    "revision conflict",
    "output changed after independent verification",
    "insufficient disk space",
    "operation id reused with different payload",
])
def test_every_other_runtime_failure_is_not_called_a_missing_derivative(message):
    """Совет «запусти prepare» на подмену артефакта — это починить не то."""
    failure = _guard(RuntimeError(message))
    assert failure.status_code == 409
    assert failure.detail["code"] != "derivative_not_prepared", message
    assert failure.detail["code"] == "media_operation_failed"
    # Текст владельцу не потерян: он и раньше был верен, и остаётся.
    assert message in failure.detail["message"]


def test_the_classified_errors_keep_their_own_codes():
    """Обратная сторона: общий код не съел уже существующую классификацию."""
    assert _guard(Conflict("revision moved")).detail["code"] == "revision_conflict"
    assert _guard(MissingObject("нет объекта")).detail["code"] == "missing_object"
    assert _guard(MissingObject("нет объекта")).status_code == 404


def test_the_product_really_raises_the_dedicated_type():
    """Тип завели не для теста: продукт бросает ИМЕННО его.

    Без этой проверки можно было бы объявить класс, никогда его не бросать и
    получить зелёный набор при неизменившемся поведении продукта.
    """
    source = inspect.getsource(service_module.VideoService.prepared_file)
    assert source.count("DerivativeNotPrepared(") == 2, source
    assert "raise RuntimeError(" not in source, source


def test_the_dedicated_type_is_still_a_runtime_error():
    """Обёртка ловит `RuntimeError`; выпади тип из иерархии — отказ станет 500."""
    assert issubclass(DerivativeNotPrepared, RuntimeError)
