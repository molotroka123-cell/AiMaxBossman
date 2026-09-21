"""Адаптер ИИ для CI: заменяет локальную модель владельца на прогоне без его машины.

Зачем. Владельческие сценарии (tools/scenario_runner.py) должны проходить хребет
целиком, включая шаг «модель». На машине владельца этот шаг обслуживает локальная
модель; в CI её нет. Этот адаптер встаёт в ТОТ ЖЕ контракт вывода/инструментов,
что и продуктовый путь, а не рядом с ним:

* провод — OpenAI-совместимый ``POST {base}/chat/completions`` с телом
  ``{"model", "messages", "tools", "max_tokens"}``: ровно то, что шлют
  ``bossman.gateway.backends.OpenRouterBackend.chat_completions`` и
  ``bcc.v2.openrouter_ext.OpenRouterClient.chat_raw``;
* ответ — ``{"choices": [{"message": {...}}], "usage": {...}}``, и
  :meth:`AIResult.as_core_message` отдаёт ровно ту форму, которую возвращает
  ``bossman.llm.chat`` (message из первого choice плюс поле ``_usage``).

Почему адаптер отдельный, а не импорт ядра. Корневой CI ставит только
``pytest pytest-timeout psutil httpx pyyaml``; ``bossman.llm`` тянет БД,
конфигурацию и агентов. Поэтому здесь повторён контракт, а не код: форму
запроса/ответа держит тест-негатив в tests/scenarios, а не вера.

Жёсткие требования владельца, закодированные типом:

* Ключ — ТОЛЬКО из ``os.environ``. Ни в коде, ни в аргументах, ни в логах,
  ни в отчётах его нет: наружу выходит лишь булево :attr:`key_present`.
* Нет ключа, 429, недоступная модель и неверный ответ — ЧЕТЫРЕ РАЗНЫХ исхода
  (``NO_KEY`` / ``RATE_LIMITED`` / ``MODEL_UNAVAILABLE`` / ``INVALID_RESPONSE``).
  Они никогда не сливаются в одну «ошибку».
* Нет синтетического запасного пути. Без ключа адаптер НЕ придумывает ответ —
  он возвращает ``NO_KEY``, и сценарий обязан стать OWNER_REQUIRED, а не PASS.
* Расходы ограничены объявленными константами :data:`MAX_CALLS_PER_RUN` и
  :data:`MAX_REQUEST_BYTES`; исчерпание — отдельный исход ``BUDGET_EXCEEDED``,
  а не молчаливое «получилось».
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import httpx

# --------------------------------------------------------------- потолок расходов
# Объявленные константы, а не «по вкусу вызывающего»: один прогон сценариев не
# может стоить больше этого, даже если сценарии зациклятся.
MAX_CALLS_PER_RUN = 8
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
REQUEST_TIMEOUT_S = 60.0

# ------------------------------------------------------------------ окружение
# Порядок важен: выделенная CI-переменная сильнее общих ключей продукта, чтобы
# прогон сценариев нельзя было случайно оплатить рабочим ключом владельца.
KEY_ENV_VARS = ("BOSSMAN_CI_AI_API_KEY", "BOSSMAN_OPENROUTER_API_KEY", "OPENROUTER_API_KEY")
BASE_URL_ENV = "BOSSMAN_CI_AI_BASE_URL"
MODEL_ENV = "BOSSMAN_CI_AI_MODEL"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "z-ai/glm-4.5-air"

# ---------------------------------------------------------------------- исходы
OK = "OK"
NO_KEY = "NO_KEY"
RATE_LIMITED = "RATE_LIMITED"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
INVALID_RESPONSE = "INVALID_RESPONSE"
TRANSPORT_ERROR = "TRANSPORT_ERROR"
PROVIDER_ERROR = "PROVIDER_ERROR"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

OUTCOMES = (OK, NO_KEY, RATE_LIMITED, MODEL_UNAVAILABLE, INVALID_RESPONSE,
            TRANSPORT_ERROR, PROVIDER_ERROR, BUDGET_EXCEEDED)

#: Четыре исхода, которые владелец потребовал НИКОГДА не сливать в один.
DISTINCT_FAILURE_OUTCOMES = (NO_KEY, RATE_LIMITED, MODEL_UNAVAILABLE, INVALID_RESPONSE)

# Грубая маскировка похожего на ключ в любых обрывках тел ответа, которые
# попадают в detail отчёта. Точное значение ключа маскируется отдельно и всегда.
_KEYLIKE = re.compile(r"\b(?:sk|pk|key|token|bearer)[-_ ]?[A-Za-z0-9_\-]{12,}\b", re.IGNORECASE)
_MODEL_HINT = re.compile(r"(?i)\bmodel\b.*\b(not|unknown|invalid|unsupported|does not exist|no endpoints)\b"
                         r"|\b(not|unknown|invalid|unsupported)\b.*\bmodel\b")


class BudgetExceeded(RuntimeError):
    """Потолок вызовов/размера исчерпан. Отдельный тип: это не ошибка сети."""


def redact(text: str, *, secret: str | None = None) -> str:
    """Убрать из текста и точное значение ключа, и всё, что на ключ похоже.

    Применяется ко ВСЕМУ, что адаптер отдаёт наружу: detail, обрывки тел,
    сообщения исключений. Пустой/короткий secret не маскируется как подстрока —
    иначе редактор искалечил бы любой текст.
    """
    out = str(text)
    if secret and len(secret) >= 8:
        out = out.replace(secret, "***")
    return _KEYLIKE.sub("***", out)


@dataclass(frozen=True)
class AIResult:
    """Исход одного вызова модели. PASS отсюда не следует никогда.

    ``message`` заполнено только при ``outcome == OK``; во всех прочих случаях
    оно None — у вызывающего нет способа принять отказ за ответ.
    """

    outcome: str
    detail: str = ""
    status_code: int | None = None
    message: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    request_bytes: int = 0
    call_index: int = 0
    model: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == OK

    @property
    def text(self) -> str:
        """Текст ответа модели; при любом отказе — пустая строка, не заглушка."""
        if not self.ok or not self.message:
            return ""
        content = self.message.get("content")
        return content if isinstance(content, str) else ""

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        """Вызовы инструментов в том же виде, в каком их читает продуктовый runner."""
        if not self.ok or not self.message:
            return []
        calls = self.message.get("tool_calls")
        return list(calls) if isinstance(calls, list) else []

    def as_core_message(self) -> dict[str, Any]:
        """Форма, которую возвращает ``bossman.llm.chat``: message + ``_usage``.

        Отказ не конвертируется: вызов на не-OK результате — ошибка
        программиста, а не повод отдать пустой ответ как настоящий.
        """
        if not self.ok or self.message is None:
            raise ValueError(f"нет ответа модели: outcome={self.outcome}")
        msg = dict(self.message)
        msg["_usage"] = {
            "prompt_tokens": int(self.usage.get("prompt_tokens") or 0),
            "completion_tokens": int(self.usage.get("completion_tokens") or 0),
            "cached_tokens": int(self.usage.get("cached_tokens") or 0),
            "cache_write_tokens": int(self.usage.get("cache_write_tokens") or 0),
        }
        return msg

    def to_report(self) -> dict[str, Any]:
        """Строка для JSON-отчёта. Ни ключа, ни заголовков, ни сырых тел."""
        return {"outcome": self.outcome, "status_code": self.status_code,
                "detail": self.detail, "request_bytes": self.request_bytes,
                "call_index": self.call_index, "model": self.model,
                "usage": dict(self.usage)}


def _validate_envelope(data: Any) -> tuple[dict[str, Any], dict[str, Any]] | str:
    """Проверка КОНТРАКТА ответа, а не «пришёл ли JSON».

    Возвращает (message, usage) либо строку-причину, почему ответ неверен.
    Именно здесь ловится «модель ответила чем угодно» — исход INVALID_RESPONSE.
    """
    if not isinstance(data, Mapping):
        return "ответ не JSON-объект"
    choices = data.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
        return "в ответе нет непустого choices"
    first = choices[0]
    if not isinstance(first, Mapping):
        return "choices[0] не объект"
    message = first.get("message")
    if not isinstance(message, Mapping):
        return "в choices[0] нет message"
    content = message.get("content")
    tool_calls = message.get("tool_calls")
    has_text = isinstance(content, str) and content != ""
    has_tools = isinstance(tool_calls, list) and bool(tool_calls)
    if not has_text and not has_tools:
        return "message без content и без tool_calls"
    if message.get("role") not in ("assistant", None):
        return f"недопустимая роль message: {message.get('role')!r}"
    usage = data.get("usage")
    return dict(message), (dict(usage) if isinstance(usage, Mapping) else {})


class CIAIProvider:
    """Клиент ИИ для прогона сценариев в CI.

    Транспорт внедряется (``transport=``) — это позволяет ОТРИЦАТЕЛЬНЫМ контролям
    проверить разбор 429/404/мусора без сети и без ключа. Внедрение транспорта
    никогда не подменяет ответ модели «правильным»: сценарий, объявивший
    ``needs_ai``, зеленеет только от настоящего вызова (см. scenario_runner).
    """

    def __init__(self, *, env: Mapping[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None,
                 base_url: str | None = None, model: str | None = None,
                 max_calls: int = MAX_CALLS_PER_RUN,
                 max_request_bytes: int = MAX_REQUEST_BYTES) -> None:
        source = os.environ if env is None else env
        # Ключ живёт ТОЛЬКО здесь, в приватном поле, и наружу не отдаётся ничем.
        self.__key = ""
        self.__key_var = ""
        for name in KEY_ENV_VARS:
            value = (source.get(name) or "").strip()
            if value:
                self.__key, self.__key_var = value, name
                break
        self.base_url = (base_url or source.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or source.get(MODEL_ENV) or DEFAULT_MODEL
        self._transport = transport
        self.max_calls = int(max_calls)
        self.max_request_bytes = int(max_request_bytes)
        self.calls: list[AIResult] = []

    # ------------------------------------------------------------------ факты
    @property
    def key_present(self) -> bool:
        """Есть ли ключ. Наружу выходит булево, значение — никогда."""
        return bool(self.__key)

    @property
    def key_source(self) -> str:
        """ИМЯ переменной окружения (не значение) — для честного отчёта."""
        return self.__key_var

    @property
    def calls_made(self) -> int:
        return len(self.calls)

    @property
    def ok_calls(self) -> int:
        """Сколько вызовов реально ответили по контракту. Основа для AI_BACKED_CI."""
        return sum(1 for c in self.calls if c.ok)

    def budget_report(self) -> dict[str, Any]:
        return {"max_calls_per_run": self.max_calls,
                "max_request_bytes": self.max_request_bytes,
                "calls_made": self.calls_made, "ok_calls": self.ok_calls,
                "key_present": self.key_present, "key_source": self.key_source,
                "base_url": self.base_url, "model": self.model}

    # ------------------------------------------------------------------ вызов
    def chat(self, messages: Sequence[Mapping[str, Any]], *,
             tools: Sequence[Mapping[str, Any]] | None = None,
             max_tokens: int = 256,
             temperature: float | None = 0.0,
             model: str | None = None) -> AIResult:
        """Один вызов chat/completions. Всегда возвращает AIResult, не бросает.

        Порядок проверок фиксирован и закрыт по отказу: бюджет → ключ → сеть →
        код ответа → контракт ответа. Раньше проверки не «смягчают» друг друга.
        """
        used_model = model or self.model
        index = len(self.calls) + 1

        payload: dict[str, Any] = {"model": used_model, "messages": [dict(m) for m in messages],
                                   "max_tokens": int(max_tokens)}
        if tools:
            payload["tools"] = [dict(t) for t in tools]
        if temperature is not None:
            payload["temperature"] = temperature
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        size = len(body)

        if index > self.max_calls:
            return self._record(AIResult(BUDGET_EXCEEDED, call_index=index, model=used_model,
                                         request_bytes=size,
                                         detail=f"исчерпан потолок вызовов: {self.max_calls}"))
        if size > self.max_request_bytes:
            return self._record(AIResult(BUDGET_EXCEEDED, call_index=index, model=used_model,
                                         request_bytes=size,
                                         detail=(f"запрос {size} Б больше потолка "
                                                 f"{self.max_request_bytes} Б")))
        if not self.__key:
            # Ключа нет — синтетического ответа НЕТ. Запрос даже не собирается в сеть.
            return self._record(AIResult(NO_KEY, call_index=index, model=used_model,
                                         request_bytes=size,
                                         detail=("ключ не задан ни в одной из переменных: "
                                                 + ", ".join(KEY_ENV_VARS))))

        headers = {"Authorization": f"Bearer {self.__key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=httpx.Timeout(REQUEST_TIMEOUT_S),
                              transport=self._transport) as client:
                response = client.post(f"{self.base_url}/chat/completions",
                                       headers=headers, content=body)
        except httpx.HTTPError as exc:
            return self._record(AIResult(TRANSPORT_ERROR, call_index=index, model=used_model,
                                         request_bytes=size,
                                         detail=self._safe(f"{type(exc).__name__}: {exc}")))

        status = response.status_code
        excerpt = self._safe(response.text[:400])
        if status == 429:
            return self._record(AIResult(RATE_LIMITED, status_code=status, call_index=index,
                                         model=used_model, request_bytes=size,
                                         detail=f"провайдер ограничил частоту: {excerpt}"))
        if status == 404 or (status in (400, 403, 422) and _MODEL_HINT.search(excerpt or "")):
            return self._record(AIResult(MODEL_UNAVAILABLE, status_code=status, call_index=index,
                                         model=used_model, request_bytes=size,
                                         detail=f"модель {used_model} недоступна: {excerpt}"))
        if status >= 400:
            return self._record(AIResult(PROVIDER_ERROR, status_code=status, call_index=index,
                                         model=used_model, request_bytes=size,
                                         detail=f"провайдер ответил {status}: {excerpt}"))
        raw = response.content
        if len(raw) > MAX_RESPONSE_BYTES:
            return self._record(AIResult(INVALID_RESPONSE, status_code=status, call_index=index,
                                         model=used_model, request_bytes=size,
                                         detail=f"ответ {len(raw)} Б больше потолка {MAX_RESPONSE_BYTES} Б"))
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._record(AIResult(INVALID_RESPONSE, status_code=status, call_index=index,
                                         model=used_model, request_bytes=size,
                                         detail=self._safe(f"ответ не разобран как JSON: {exc}")))
        checked = _validate_envelope(data)
        if isinstance(checked, str):
            return self._record(AIResult(INVALID_RESPONSE, status_code=status, call_index=index,
                                         model=used_model, request_bytes=size,
                                         detail=self._safe(checked)))
        message, usage = checked
        return self._record(AIResult(OK, status_code=status, call_index=index, model=used_model,
                                     request_bytes=size, message=message, usage=usage,
                                     detail="ответ соответствует контракту chat/completions"))

    # ------------------------------------------------------------- внутреннее
    def _safe(self, text: str) -> str:
        return redact(text, secret=self.__key)

    def _record(self, result: AIResult) -> AIResult:
        self.calls.append(result)
        return result


def probe(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Честная справка о готовности адаптера — без единого сетевого вызова."""
    provider = CIAIProvider(env=env)
    report = provider.budget_report()
    report["ready"] = provider.key_present
    report["blocker"] = "" if provider.key_present else (
        "нет ключа ИИ в окружении: сценарии с моделью дают OWNER_REQUIRED, не PASS")
    return report


def main() -> int:
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
