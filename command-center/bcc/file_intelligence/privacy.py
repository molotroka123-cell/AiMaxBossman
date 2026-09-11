"""§16 — режим модели. По умолчанию LOCAL_ONLY, и это доказывается, а не верится.

Ключевая ловушка, ради которой модуль существует. Upstream считает локальным
всё, что не входит в `is_remote_choice()` (Types.hpp:18-21) — а туда не входит
`LLMChoice::Custom`. То есть «Custom» — произвольный OpenAI-совместимый
эндпоинт, который может указывать куда угодно, — у upstream проходит как
локальный. Bossman классифицирует значение САМ и относит `Custom` и `Unset` к
UNKNOWN, потому что «мы не знаем, куда это ходит» — не то же самое, что «это
локально».

И отдельно: локальность НЕ выводится из того, что запустился локальный
исполняемый файл. Локальный процесс с удалённым эндпоинтом — обычное дело, и
именно его такое рассуждение пропускает.
"""
from __future__ import annotations

import configparser
import io
from pathlib import Path
from typing import Any

from .models import BackendMode, Denied, Refusal

#: Значения `Settings/LLMChoice`, читаемые upstream'ом (Settings.cpp:478-489).
#: Разбираются здесь заново, а не берутся у upstream, потому что вопрос
#: «локально ли это» Bossman решает своей политикой.
LOCAL_VALUES = frozenset({
    "local_3b", "local_3b_legacy", "local_4b_gemma", "local_7b", "local_7b_gemma",
})
REMOTE_VALUES = frozenset({
    "remote", "remote_openai", "remote_gemini", "remote_custom",
})
#: `Custom` намеренно ЗДЕСЬ, а не в LOCAL: это пользовательский
#: OpenAI-совместимый эндпоинт, и его адрес — не свойство названия.
UNKNOWN_VALUES = frozenset({"unset", "custom", ""})


def classify(raw_value: Any) -> BackendMode:
    """Значение настройки → режим. Незнакомое значение — UNKNOWN, не LOCAL.

    Новый вариант, добавленный upstream'ом, приедет сюда как UNKNOWN и будет
    остановлен политикой, вместо того чтобы по умолчанию сойти за локальный.
    """
    text = str(raw_value or "").strip().lower()
    if text in LOCAL_VALUES:
        return BackendMode.LOCAL
    if text in REMOTE_VALUES:
        return BackendMode.REMOTE
    return BackendMode.UNKNOWN


def read_backend_mode(config_path: Path | None) -> tuple[BackendMode, str]:
    """Прочитать режим из INI сайдкара. Возвращает (режим, что прочитали).

    Отсутствующий или нечитаемый конфиг — UNKNOWN. Это не «наверное, по
    умолчанию локально»: настройки, которую не удалось прочитать, у нас нет.
    """
    if config_path is None or not Path(config_path).is_file():
        return BackendMode.UNKNOWN, ""
    try:
        text = Path(config_path).read_text(encoding="utf-8", errors="replace")
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        parser.read_file(io.StringIO(text))
        value = parser.get("Settings", "LLMChoice", fallback="")
    except Exception:
        return BackendMode.UNKNOWN, ""
    return classify(value), str(value)


def assert_processing_permitted(mode: BackendMode, *,
                                remote_approved: bool = False) -> None:
    """§16 — fail-closed. Только доказанно локальный режим проходит молча.

    REMOTE без явного согласия владельца и UNKNOWN в любом случае — отказ.
    Разница между ними сохранена в причине: «владелец не разрешал удалённую
    обработку» и «мы не смогли доказать, где обрабатывается» — разные проблемы
    и чинятся по-разному.
    """
    if mode is BackendMode.LOCAL:
        return
    if mode is BackendMode.REMOTE:
        if remote_approved:
            return
        raise Denied(Refusal.REMOTE_BACKEND_NOT_APPROVED,
                     "the sidecar is configured for a remote model and the owner has "
                     "not approved remote processing of file names or contents")
    raise Denied(Refusal.BACKEND_UNKNOWN,
                 "the sidecar's model backend could not be proven local; File "
                 "Intelligence does not send file data to an unproven destination")
