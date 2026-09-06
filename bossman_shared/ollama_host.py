"""Как читать ``OLLAMA_HOST`` — одно правило на весь репозиторий.

Живёт в ``bossman_shared``, потому что его нужны ТРИ независимых потребителя:
Stage-3 Gateway (bossman-core), harness локального A/B (tools/) и корневые
тесты, у которых нет и не должно быть зависимостей Core/Command Center.
Своя копия этого правила в harness уже однажды разъехалась с Gateway.

Чистые строки: ни сети, ни файлов, ни сторонних пакетов.
"""
from __future__ import annotations

# Адреса, на которых Ollama СЛУШАЕТ, но по которым к ней нельзя ПОДКЛЮЧИТЬСЯ.
# `OLLAMA_HOST=0.0.0.0:11434` — документированный способ открыть Ollama наружу,
# и это же значение видит клиент. На Linux подключение к 0.0.0.0 случайно
# срабатывает (ядро трактует его как localhost), на Windows — нет
# (WSAEADDRNOTAVAIL), то есть локальная модель просто «пропадает».
_WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]", "*"}


def normalize_ollama_host(host: str) -> str:
    """``OLLAMA_HOST`` в том виде, в каком его можно набрать клиентом.

    Принимает всё, что принимает сама Ollama: ``host:port``, ``:port``,
    ``http://host:port``, wildcard-адрес прослушивания. Возвращает '' , если
    строка пуста или неразбираема — вызывающий остаётся на своём умолчании,
    а не уходит на заведомо нерабочий адрес.
    """
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    scheme, sep, rest = host.partition("://")
    if not sep:
        scheme, rest = "http", host
    if rest in _WILDCARD_HOSTS:                    # адрес прослушивания без порта
        rest = "127.0.0.1"
    elif rest.startswith(":"):                     # ":11434" — только порт
        rest = f"127.0.0.1{rest}"
    authority, slash, path = rest.partition("/")
    name, colon, port = authority.rpartition(":")
    if not colon:                                  # без порта: весь authority — хост
        name, port = authority, ""
    if name in _WILDCARD_HOSTS or not name:
        name = "127.0.0.1"
    authority = f"{name}:{port}" if port else name
    resolved = f"{scheme}://{authority}{slash}{path}".rstrip("/")
    # Пути запросов Gateway уже содержат ``/v1`` (например ``/v1/chat/completions``),
    # поэтому база бэкенда обязана остаться корнем сервиса. Операторы иногда
    # пишут /v1 в OpenAI-совместимом URL — нормализуем, а не делаем /v1/v1/….
    return resolved[:-3] if resolved.endswith("/v1") else resolved
