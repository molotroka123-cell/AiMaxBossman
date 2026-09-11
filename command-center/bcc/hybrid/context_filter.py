"""Что НЕЛЬЗЯ класть в память проекта, и почему это решается до записи.

Память, в которую попал секрет, — это секрет, у которого появилась вторая
копия, живущая дольше исходной и уезжающая туда, куда исходную никто не
посылал. Зеркало в сторонний контекст-стор делает это буквально: строка
уходит в чужой процесс, на чужой диск, иногда по сети.

Поэтому фильтр стоит ПЕРЕД записью в оба стора, а не перед отправкой в
зеркало. Если бы он стоял только перед зеркалом, родная память Bossman
осталась бы местом, где секрет лежит в открытом виде, и первая же выгрузка
памяти вынесла бы его наружу.

Два разных исхода, а не один:

* ОТКАЗ (`SecretMaterialRefused`) — источник сам по себе является секретом:
  `.env`, приватный ключ, файл учётных данных. Здесь нечего редактировать:
  запись целиком состоит из того, чего в памяти быть не должно;
* РЕДАКТИРОВАНИЕ — секрет найден ВНУТРИ полезного текста. Токен заменяется
  меткой, а сам факт замены попадает в provenance записи. Молчаливая замена
  запрещена: читающий обязан видеть, что текст неполон, иначе он примет
  «...» за часть решения.

Ни один найденный секрет не попадает ни в исключение, ни в лог, ни в метку:
сообщение называет ВИД находки и её место, но не значение. Диагностика,
печатающая секрет, — это тот же самый дефект под другим именем.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Pattern, Tuple

#: Пути, само существование которых в записи означает «это учётные данные».
#: Сопоставление идёт по ИМЕНИ файла и по сегментам пути, а не по подстроке:
#: подстрока `.env` находится и в `docs/environment.md`.
_CREDENTIAL_BASENAMES = frozenset({
    ".env", ".env.local", ".env.production", ".env.development", ".envrc",
    ".netrc", "_netrc", ".pgpass", ".htpasswd", "credentials", "credentials.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "service-account.json",
    ".npmrc", ".pypirc", ".dockercfg", ".git-credentials",
})
_CREDENTIAL_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".docker"})

#: Формы секретов, у которых есть узнаваемая структура. Список намеренно
#: короткий и конкретный: широкая эвристика «строка длиннее 32 символов»
#: вырезала бы хэши коммитов и пути, то есть ровно то, ради чего память и
#: существует, и приучила бы читать «[REDACTED]» как шум.
_SECRET_PATTERNS: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("private_key_block", re.compile(
        r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----",
        re.DOTALL)),
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("url_with_password", re.compile(
        r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s/@]+@[^\s]+")),
    # `KEY=value`, `"password": "value"` и `password = 'value'` — три записи
    # одного и того же. Кавычка ПОСЛЕ имени ключа обязательна в необязательном
    # виде: без неё JSON-форма (`"client_secret": "..."`) не совпадала, и
    # фильтр молча пропускал самый частый способ записать секрет в заметку.
    ("assigned_secret", re.compile(
        r"(?i)\b([a-z0-9_.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token"
        r"|refresh[_-]?token|client[_-]?secret|private[_-]?key))\b"
        r"[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{6,})")),
)

#: Метка ровно одна и она говорит, ЧТО было убрано. `[REDACTED]` без вида
#: находки не даёт читателю понять, потерял ли он ключ или абзац текста.
_MARK = "[REDACTED:{kind}]"


class SecretMaterialRefused(ValueError):
    """Запись целиком состоит из учётных данных — редактировать нечего."""


@dataclass(frozen=True)
class FilterResult:
    body: str
    redactions: Tuple[str, ...]

    @property
    def was_redacted(self) -> bool:
        return bool(self.redactions)


def _path_is_credential_material(source: str) -> str:
    """Имя находки, если путь сам по себе — учётные данные; иначе пустая строка."""
    cleaned = source.strip().replace("\\", "/")
    if not cleaned:
        return ""
    segments = [seg for seg in cleaned.split("/") if seg not in ("", ".")]
    if not segments:
        return ""
    basename = segments[-1].casefold()
    if basename in _CREDENTIAL_BASENAMES:
        return f"credential_file:{basename}"
    # `.env.<что угодно>` — тот же файл под другим суффиксом окружения.
    if basename.startswith(".env"):
        return "credential_file:.env"
    if basename.endswith((".pem", ".p12", ".pfx", ".key", ".keystore", ".jks")):
        return f"credential_file:{basename.rsplit('.', 1)[-1]}"
    for segment in segments[:-1]:
        if segment.casefold() in _CREDENTIAL_DIRS:
            return f"credential_dir:{segment.casefold()}"
    return ""


def scrub_for_memory(body: str, *, source: str = "") -> FilterResult:
    """Привести текст к виду, пригодному для долгой памяти, или отказать.

    `source` — происхождение текста (путь, URL, имя инструмента). Он
    проверяется ПЕРВЫМ: файл учётных данных отвергается целиком, даже если
    внутри него не нашлось ни одного узнаваемого шаблона. Обратное тоже верно:
    отсутствие пути не делает текст безопасным, поэтому шаблоны проверяются
    всегда.
    """
    if type(body) is not str:
        raise TypeError("context body must be a string")

    refusal = _path_is_credential_material(source)
    if refusal:
        raise SecretMaterialRefused(
            f"refusing to remember {refusal}: a credential file has no redacted form")

    found: List[str] = []
    cleaned = body
    for kind, pattern in _SECRET_PATTERNS:
        if kind == "assigned_secret":
            def _mask(match: re.Match) -> str:
                found.append(kind)
                return f"{match.group(1)}={_MARK.format(kind=kind)}"
            cleaned, hits = pattern.subn(_mask, cleaned)
        else:
            cleaned, hits = pattern.subn(_MARK.format(kind=kind), cleaned)
            found.extend([kind] * hits)

    if found and not cleaned.replace(_MARK.format(kind=found[0]), "").strip():
        # После вычистки не осталось ничего, кроме меток: запись была секретом,
        # а не текстом, содержавшим секрет.
        raise SecretMaterialRefused(
            "refusing to remember a record that is nothing but secret material")

    # Порядок стабилен и дубликаты схлопнуты: provenance читают люди.
    ordered = tuple(sorted(set(found)))
    return FilterResult(body=cleaned, redactions=ordered)


__all__ = ["FilterResult", "SecretMaterialRefused", "scrub_for_memory"]
