"""Приёмочная полоса MCP судит правильно — иначе она хуже, чем её отсутствие.

Стенд решает, попадёт ли сторонний коннектор в реестр адаптеров. Если он
засчитывает отказ за успех, он не гейт, а штамп. Поэтому проверяется именно
СУДЕЙСКАЯ логика, и к каждому отказу есть обратный контроль: законный случай
обязан по-прежнему проходить.

Отдельно проверен дефект, который стенд совершил на первом же прогоне против
настоящего сервера: ответ JSON-RPC без `error`, а внутри `content` — «### Error:
браузер не установлен». Первая редакция засчитала это как «valid request PASS»,
то есть сам приёмочный стенд сделал ровно то, что раздел 2 задания запрещает:
превратил успех транспорта в успех операции.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from mcp_acceptance import (  # noqa: E402
    FAIL, NOT_RUN, PASS, _judge_call, _schema_is_usable, StdioServer)


# --------------------------------------------------------------------------
# Успех транспорта ≠ успех операции
# --------------------------------------------------------------------------


def test_a_json_rpc_error_is_a_failure():
    verdict, detail = _judge_call({"error": {"code": -32601, "message": "нет метода"}})
    assert verdict == FAIL and "нет метода" in detail


def test_is_error_true_is_a_failure_even_though_the_transport_succeeded():
    verdict, detail = _judge_call(
        {"result": {"isError": True,
                    "content": [{"type": "text", "text": "браузер не установлен"}]}})
    assert verdict == FAIL and "браузер не установлен" in detail


@pytest.mark.parametrize("text", [
    "### Error\nError: Browser \"chrome-for-testing\" is not installed",
    "Error: connection refused",
    "Traceback (most recent call last):\n  File ...",
])
def test_an_error_hidden_in_the_body_of_a_successful_reply_is_a_failure(text):
    """Главный тест файла — ровно тот дефект, который стенд сам и совершил.

    `isError` не выставлен, ошибки JSON-RPC нет, а операция не состоялась.
    Засчитать это успехом — значит написать гейт, который пропускает
    неработающий коннектор.
    """
    verdict, detail = _judge_call({"result": {"content": [{"type": "text", "text": text}]}})
    assert verdict == FAIL
    assert "ОТКАЗ В ТЕЛЕ УСПЕШНОГО ОТВЕТА" in detail


def test_a_genuinely_successful_call_still_passes():
    """Обратный контроль: строгость не имеет права съесть законный случай.

    Без него «починка» вида «всегда FAIL» прошла бы за исправление и сделала
    бы стенд бесполезным в другую сторону.
    """
    verdict, detail = _judge_call(
        {"result": {"content": [{"type": "text", "text": "# Заголовок\n\nтело"}]}})
    assert verdict == PASS and "Заголовок" in detail


def test_the_word_error_inside_ordinary_text_is_not_a_failure():
    """Второй обратный контроль: поиск подстроки не должен ловить обычный текст.

    Документ, в котором просто написано слово «error», — это удачное
    извлечение, а не отказ. Поэтому признак привязан к НАЧАЛУ сообщения.
    """
    verdict, _ = _judge_call({"result": {"content": [{
        "type": "text",
        "text": "# Руководство\n\nВ разделе про Error handling описано..."}]}})
    assert verdict == PASS


def test_a_result_that_is_not_an_object_is_a_failure():
    assert _judge_call({"result": "просто строка"})[0] == FAIL


# --------------------------------------------------------------------------
# Схема инструмента обязана быть пригодной для ВЫЗОВА
# --------------------------------------------------------------------------


def test_a_usable_schema_is_accepted():
    ok, note = _schema_is_usable({"type": "object",
                                  "properties": {"uri": {"type": "string"}}})
    assert ok and "1 параметр" in note


@pytest.mark.parametrize("schema,expected", [
    ({}, "type"),
    ({"type": "string"}, "type"),
    ({"type": "object"}, "properties"),
    ({"type": "object", "properties": []}, "properties не объект"),
    ({"type": "object", "properties": {"x": {"description": "без типа"}}}, "нет типа"),
    ("не объект вовсе", "не объект"),
])
def test_a_schema_nobody_can_build_a_call_from_is_refused(schema, expected):
    """«Формально валидная JSON Schema» и «по ней можно собрать вызов» — разное.

    `{}` — валидная схема, описывающая что угодно. Модель по ней аргументов не
    соберёт, а сервер потом откажет; выяснится это в рабочем пути, а не здесь.
    """
    ok, note = _schema_is_usable(schema)
    assert not ok and expected in note


def test_alternative_type_declarations_are_accepted():
    """Обратный контроль к предыдущему: anyOf/enum/$ref — законные способы
    объявить тип, и придирка к одному только ключу `type` отвергала бы
    исправные схемы."""
    for prop in ({"anyOf": [{"type": "string"}]}, {"enum": ["a", "b"]},
                 {"$ref": "#/$defs/x"}, {"oneOf": [{"type": "integer"}]}):
        ok, _ = _schema_is_usable({"type": "object", "properties": {"p": prop}})
        assert ok, prop


# --------------------------------------------------------------------------
# Клиент не имеет права подвесить Bossman
# --------------------------------------------------------------------------


def test_a_server_that_never_answers_hits_our_deadline_not_theirs():
    """Крайний срок принадлежит КЛИЕНТУ. Молчащий коннектор обязан кончиться
    таймаутом у нас, а не ждать вечно доброй воли сервера."""
    server = StdioServer([sys.executable, "-c",
                          "import time; time.sleep(30)"])
    try:
        server.start()
        with pytest.raises(TimeoutError):
            server.request("initialize", timeout=0.5)
    finally:
        server.stop()
    assert not server.alive, "стенд обязан гасить за собой процесс"


def test_a_server_that_dies_is_reported_not_awaited():
    server = StdioServer([sys.executable, "-c", "raise SystemExit(3)"])
    try:
        server.start()
        with pytest.raises(Exception):
            server.request("initialize", timeout=5)
    finally:
        server.stop()


def test_garbage_on_the_wire_is_an_error_not_a_parsed_reply():
    """Сервер, пишущий не-JSON, не должен превращаться в «пустой ответ»."""
    from mcp_acceptance import McpError
    server = StdioServer([sys.executable, "-c",
                          "import sys; print('это не json'); sys.stdout.flush(); "
                          "import time; time.sleep(5)"])
    try:
        server.start()
        with pytest.raises(McpError, match="не разбирается"):
            server.request("initialize", timeout=5)
    finally:
        server.stop()


def test_the_client_matches_the_reply_to_its_own_id():
    """Чужое сообщение в потоке не должно быть принято за ответ.

    Это и есть защита от «дубля ответа»: стенд ждёт СВОЙ id, а всё остальное
    пропускает.
    """
    script = (
        "import sys, json\n"
        "sys.stdin.readline()\n"
        # сначала чужое уведомление и ответ с ЧУЖИМ id, потом настоящий
        "print(json.dumps({'jsonrpc':'2.0','method':'notifications/x','params':{}}))\n"
        "print(json.dumps({'jsonrpc':'2.0','id':999,'result':{'чужой':True}}))\n"
        "print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'наш':True}}))\n"
        "sys.stdout.flush()\n"
        "import time; time.sleep(5)\n")
    server = StdioServer([sys.executable, "-c", script])
    try:
        server.start()
        reply = server.request("initialize", timeout=5)
        assert reply["id"] == 1 and reply["result"] == {"наш": True}
    finally:
        server.stop()
