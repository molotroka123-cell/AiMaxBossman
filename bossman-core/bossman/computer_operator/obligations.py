"""Обязательства цели и независимая проверка их исхода (AT-01).

Планировщик закрывает задачу словом COMPLETE. Постусловие, которое он к нему
прикладывает, читается по ЭКРАНУ — по тому же экрану, который прочитал он сам,
— поэтому «создай отчёт report.txt» честно закрывалось фразой, совпавшей с
заголовком окна. Требование «хотя бы один подтверждённый изменяющий шаг» это
чинило лишь наполовину: любая посторонняя мутация (открыл блокнот, что-то
напечатал) уже считалась уликой для ЛЮБОЙ цели.

Здесь обязательство именное. Из цели извлекается КОНКРЕТНЫЙ внешний результат
(этот путь, это содержимое), и на завершении он перечитывается независимо от
модели и от экрана — из файловой системы. Плюс привязка к попытке: файл,
существовавший до начала с тем же содержимым, доказывает прошлое, а не эту
работу, поэтому он не улика.

Модуль намеренно чистый: файловый доступ идёт через порт `probe`, поэтому цикл
оператора тестируется без настоящего диска, а прод читает настоящий.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Путь с расширением: Windows (C:\dir\file.txt, \\server\share\f.doc) и POSIX.
# Кавычки любые, включая типографские — владелец пишет цель как говорит.
_QUOTES = "\"'«»“”‘’`"
_PATH = re.compile(
    r"(?:(?<=[\s\"'«»“”‘’`(])|^)"
    r"((?:[A-Za-z]:[\\/]|\\\\|~[\\/]|\.{1,2}[\\/]|/)?"
    r"(?:[^\s\\/:*?\"<>|]+[\\/])*"
    r"[^\s\\/:*?\"<>|]+\.[A-Za-z0-9]{1,8})"
)
# «с текстом X», «со словами X», «containing X», «with the text X» — до конца
# фразы или до закрывающей кавычки.
_CONTENT = re.compile(
    r"(?:с\s+(?:текстом|содержимым|содержанием)|со\s+словами|"
    r"with\s+(?:the\s+)?(?:text|content)|containing)\s*[:\-]?\s*"
    r"([" + _QUOTES + r"])(.+?)\1",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class FileEffect:
    """Именованный внешний результат В ФАЙЛОВОЙ СИСТЕМЕ."""
    path: str
    contains: str | None = None

    def key(self) -> str:
        return self.path


@dataclass(frozen=True, slots=True)
class ScreenEffect:
    """Обещанный результат, который виден на ЭКРАНЕ, а не в файловой системе.

    «Включи тёмную тему», «поставь галочку», «открой вкладку настроек» — внешний
    эффект без единого пути. Раньше такие цели не давали ни одного файлового
    обязательства, и решение падало обратно на слабое правило «была какая-то
    подтверждённая мутация»: открыл блокнот, что-то напечатал — цель закрыта.

    Улика здесь другой природы и слабее файловой: экран читает тот же
    наблюдатель, которым пользуется планировщик. Поэтому она НЕ засчитывается
    сама по себе — она лишь требует, чтобы экран ПОСЛЕ попытки отличался от
    экрана ДО неё и содержал обещанное. Совпадение «до» и «после» означает, что
    ничего не изменилось, чем бы планировщик это ни называл.
    """
    text: str

    def key(self) -> str:
        return f"screen:{self.text.lower()}"


@dataclass(frozen=True, slots=True)
class UnknownEffect:
    """Цель обещает внешний результат, но КАКОЙ именно — извлечь не удалось.

    Это самый опасный случай и раньше он был самым тихим: пустой список
    обязательств означал «проверять нечего», и завершение решалось слабым
    правилом. Теперь отсутствие извлечённого обязательства — само по себе
    обязательство, которое нечем закрыть: цель, обещающая эффект, но не
    называющая его проверяемо, не может быть закрыта машиной.
    """
    reason: str

    def key(self) -> str:
        return f"unknown:{self.reason}"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Независимое чтение состояния мира. `exists=False` — цели нет."""
    exists: bool
    digest: str | None = None
    text: str | None = None


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_obligations(goal: str) -> tuple[FileEffect, ...]:
    """Какие ИМЕННО внешние результаты обещает эта цель.

    Пусто — не «всё разрешено»: пустой кортеж означает лишь, что именных
    обязательств из текста не извлечено, и решение остаётся за прежним
    правилом (`goal_requires_external_effect` + подтверждённый изменяющий шаг).
    Расширять извлечение можно, ослаблять проверку — нельзя.
    """
    text = goal or ""
    contains = None
    match = _CONTENT.search(text)
    if match is not None:
        contains = match.group(2).strip() or None
        # Требуемое содержимое из цели вырезается: путь ищется по остатку,
        # иначе «с текстом "см. report.txt"» породил бы фантомное обязательство.
        text = text[: match.start()] + " " + text[match.end() :]
    seen: dict[str, Any] = {}
    for raw in _PATH.findall(text):
        path = raw.strip("".join(_QUOTES) + ".,;:!?)")
        if not path or path.endswith((".", "/", "\\")):
            continue
        # Голое «слово.слово» без разделителя пути и без явного расширения
        # файла — это чаще предложение, чем путь. Требуем либо разделитель,
        # либо расширение из известного набора.
        if not re.search(r"[\\/]", path) and not re.search(
                r"\.(txt|md|csv|json|xml|html?|log|ini|cfg|ya?ml|docx?|xlsx?|pptx?|pdf|"
                r"png|jpe?g|gif|svg|zip|rar|7z|mp[34]|wav|py|js|ts|sh|bat|ps1)$", path, re.I):
            continue
        effect = FileEffect(path=path, contains=contains)
        seen.setdefault(effect.key(), effect)
    if seen:
        return tuple(seen.values())
    # Файлового обязательства не извлеклось. Это НЕ означает «проверять нечего»:
    # цель уже признана обещающей внешний результат, значит результат есть, и
    # вопрос лишь в том, назвали ли его проверяемо.
    screen = _screen_effect(goal or "")
    if screen is not None:
        return (screen,)
    return (UnknownEffect(reason="из цели не извлечён проверяемый результат"),)


# Обещание, видимое на экране: кавычки вокруг того, что должно там оказаться,
# либо явный «переключи/включи/поставь ...». Намеренно узко: широкая эвристика
# здесь означала бы придуманное обязательство, а не найденное.
_SCREEN = re.compile(
    r"(?:выбер[иь]|включ[иь]|выключ[иь]|переключ[иь]|поставь|отмет[ьи]|введ[иь]|"
    r"select|enable|disable|toggle|check|set)\b[^" + _QUOTES + r"]{0,40}"
    r"([" + _QUOTES + r"])(.+?)\1",
    re.IGNORECASE | re.DOTALL,
)


def _screen_effect(goal: str):
    match = _SCREEN.search(goal)
    if match is None:
        return None
    wanted = match.group(2).strip()
    return ScreenEffect(text=wanted) if wanted else None


def file_probe(root: str | Path | None = None):
    """Настоящее чтение с диска. Независимо от модели и от экрана."""
    base = Path(root).resolve() if root is not None else None

    def probe(effect: FileEffect) -> ProbeResult:
        try:
            path = Path(effect.path).expanduser()
            if not path.is_absolute() and base is not None:
                path = base / path
            if not path.is_file():
                return ProbeResult(False)
            data = path.read_bytes()
        except OSError:
            # Прочитать нечем — это НЕ «файла нет» и НЕ «файл есть». Отказ
            # честнее любой из догадок, поэтому отдаём отсутствие улики.
            return ProbeResult(False)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return ProbeResult(True, digest_bytes(data), text)

    return probe


def screen_text(observation) -> str:
    """Текст экрана для проверки экранных обязательств: сводка плюс дерево UI."""
    if observation is None:
        return ""
    tree = getattr(observation, "ui_tree", None)
    blob = getattr(observation, "summary", "") or ""
    if tree is not None:
        import json as _json
        blob = f"{blob}\n{_json.dumps(tree, ensure_ascii=False, default=str)}"
    return blob.lower()


def unsatisfied(obligations, probe, before: dict[str, ProbeResult] | None,
                screens: tuple[str, str] | None = None):
    """Обязательства, у которых НЕТ улики этой попытки. Пусто — все закрыты.

    Улика обязана быть тройной:
    * результат существует СЕЙЧАС (свежее независимое чтение);
    * его содержимое соответствует тому, что просили;
    * он отличается от состояния ДО попытки — иначе доказано прошлое.
    """
    missing = []
    before_screen, after_screen = screens or ("", "")
    for effect in obligations:
        if isinstance(effect, UnknownEffect):
            # Нечем закрыть по построению: цель обещает результат и не называет
            # его. Машина не имеет права додумать, что именно проверять.
            missing.append((effect, effect.reason))
            continue
        if isinstance(effect, ScreenEffect):
            wanted = effect.text.lower()
            if wanted not in after_screen:
                missing.append((effect, "на экране этого нет"))
            elif before_screen == after_screen:
                missing.append((effect, "экран не изменился за попытку"))
            elif wanted in before_screen:
                missing.append((effect, "было на экране до попытки"))
            continue
        after = probe(effect)
        if not after.exists:
            missing.append((effect, "не создан"))
            continue
        if effect.contains is not None:
            if after.text is None or effect.contains.lower() not in after.text.lower():
                missing.append((effect, "содержимое не соответствует запрошенному"))
                continue
        prior = (before or {}).get(effect.key())
        if prior is not None and prior.exists and prior.digest == after.digest:
            missing.append((effect, "существовал до начала и не изменился"))
    return tuple(missing)


def snapshot(obligations, probe) -> dict[str, ProbeResult]:
    """Состояние обещанных результатов ДО попытки — привязка улики к попытке."""
    return {effect.key(): probe(effect) for effect in obligations
            if isinstance(effect, FileEffect)}
