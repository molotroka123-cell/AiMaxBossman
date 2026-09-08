"""Пакет селекторов генератора Higgsfield: данные, а не код действий.

Пакет здесь — **эталонный, а не подтверждённый**. Он описывает интерфейс так,
как тот выглядел на момент записи, и ровно поэтому лежит отдельным документом с
версией: когда провайдер перерисует страницу, чинится документ, а не логика
адаптера.

Порядок стратегий подчиняется общему правилу `62_SELECTOR_REGISTRY`: сначала
роль/доступное имя/метка, `css` — только в хвосте и только там, где класс
безопасности это допускает. Классы берутся из доменного каталога через поле
`capability`; сам пакет назначать их не вправе.

Что это НЕ значит: что возможность работает. Пакет, проверенный только на
фикстуре, оставляет возможность в `EXPERIMENTAL` — до `VERIFIED_BROWSER` её
поднимает только свидетельство настоящего браузера на машине владельца
(`browser/capabilities.py`).
"""
from __future__ import annotations

from typing import Any

PROVIDER = "higgsfield"
PACK_VERSION = "0.1.0-unverified"
UI_REVISION = "2026-09-08"

# Имена действий. Адаптер знает только их, а не селекторы.
ACTION_IDENTITY = "account.identity.read"
ACTION_PROMPT = "generation.prompt.fill"
ACTION_ASPECT = "generation.aspect.fill"
ACTION_DURATION = "generation.duration.fill"
ACTION_PRESET = "generation.preset.fill"
ACTION_SUBMIT = "generation.submit"
ACTION_JOB_CARD = "generation.job_card"
ACTION_DOWNLOAD = "generation.result.download"

# Действия, без которых страница генерации не является страницей генерации.
REQUIRED_ACTIONS: tuple[str, ...] = (ACTION_PROMPT, ACTION_SUBMIT)
# Необязательные: их отсутствие — не дрейф интерфейса, а иной набор настроек.
OPTIONAL_ACTIONS: tuple[str, ...] = (ACTION_ASPECT, ACTION_DURATION, ACTION_PRESET)


def pack_document() -> dict[str, Any]:
    """Документ пакета в форме `selector_pack.schema.json`."""
    return {
        "provider": PROVIDER,
        "version": PACK_VERSION,
        "ui_revision": UI_REVISION,
        "locale": "en",
        "actions": [
            {"action": ACTION_IDENTITY,
             "target": "имя вошедшего аккаунта генератора",
             "capability": "account.read",
             "strategies": [
                 {"kind": "stable_attribute", "value": "data-testid=account-name"},
                 {"kind": "accessible_name", "value": "Account"},
             ]},
            {"action": ACTION_PROMPT, "target": "поле описания сцены",
             "capability": "media.generate.request",
             "strategies": [
                 {"kind": "label", "value": "Prompt"},
                 {"kind": "role", "value": "textbox|Prompt"},
                 {"kind": "stable_attribute", "value": "data-testid=prompt-input"},
                 {"kind": "css", "value": "textarea[name=prompt]"},
             ]},
            {"action": ACTION_ASPECT, "target": "поле соотношения сторон",
             "capability": "media.generate.request",
             "strategies": [
                 {"kind": "label", "value": "Aspect ratio"},
                 {"kind": "stable_attribute", "value": "data-testid=aspect-input"},
             ]},
            {"action": ACTION_DURATION, "target": "поле длительности",
             "capability": "media.generate.request",
             "strategies": [
                 {"kind": "label", "value": "Duration"},
                 {"kind": "stable_attribute", "value": "data-testid=duration-input"},
             ]},
            {"action": ACTION_PRESET, "target": "поле пресета/модели",
             "capability": "media.generate.request",
             "strategies": [
                 {"kind": "label", "value": "Preset"},
                 {"kind": "stable_attribute", "value": "data-testid=preset-input"},
             ]},
            {"action": ACTION_SUBMIT, "target": "кнопка запуска генерации",
             "capability": "media.generate.submit",
             "strategies": [
                 {"kind": "role", "value": "button|Generate"},
                 {"kind": "accessible_name", "value": "Generate"},
                 {"kind": "stable_attribute", "value": "data-testid=generate-submit"},
             ]},
            {"action": ACTION_JOB_CARD,
             "target": "карточка запущенной работы — свидетельство отправки",
             "capability": "media.generate.read",
             "strategies": [
                 {"kind": "stable_attribute", "value": "data-testid=job-card"},
                 {"kind": "role", "value": "status|Generating"},
             ]},
            {"action": ACTION_DOWNLOAD, "target": "кнопка скачивания результата",
             "capability": "media.generate.collect",
             "strategies": [
                 {"kind": "role", "value": "button|Download"},
                 {"kind": "accessible_name", "value": "Download"},
                 {"kind": "stable_attribute", "value": "data-testid=result-download"},
             ]},
        ],
    }


__all__ = [
    "ACTION_ASPECT", "ACTION_DOWNLOAD", "ACTION_DURATION", "ACTION_IDENTITY",
    "ACTION_JOB_CARD", "ACTION_PRESET", "ACTION_PROMPT", "ACTION_SUBMIT",
    "OPTIONAL_ACTIONS", "PACK_VERSION", "PROVIDER", "REQUIRED_ACTIONS",
    "UI_REVISION", "pack_document",
]
