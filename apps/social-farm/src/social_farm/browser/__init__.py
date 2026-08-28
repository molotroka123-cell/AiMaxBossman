"""Изолированный браузерный резерв.

Это адаптер совместимости, разрешённый владельцем аккаунта, а не средство
обхода защиты. Здесь нет и не появится: прохождения капчи автоматом, антидетекта
и подмены отпечатка браузера, обхода ограничений частоты, создания аккаунтов,
перебора учётных данных, массовой незапрошенной рассылки. Для этого нет кода, и
статический скан в `tests/unit/test_independence.py` следит, чтобы он не
появился.

Что здесь есть:

* `isolation` — свой каталог контекста на аккаунт, маркер владельца, права 0700;
* `worker` — отдельный процесс, привязанный к ОДНОМУ аккаунту;
* `states` — автомат браузерной сессии дословно из спецификации;
* `selectors` — версионированные пакеты селекторов; семантика первой, `css` и
  `xpath` только в хвосте и никогда на разрушающих действиях;
* `fingerprint` — отпечаток цели, снимаемый непосредственно перед действием;
* `secrets` — ссылка вместо значения и три линии редакции;
* `challenge` — распознавание проверок, которые обязан пройти человек;
* `session` — сборка всего перечисленного в одно действие;
* `capabilities` — что браузерный путь умеет и почему считается, что умеет.

Playwright импортируется лениво: приложение обязано работать без него.
"""
from __future__ import annotations

from .audit import BrowserAuditRecord, BrowserAuditSink, InMemoryAuditSink
from .capabilities import (BrowserCapabilityLedger, BrowserCapabilityRecord,
                           BrowserCapabilityState, Evidence, FailureKind,
                           PromotionRefused)
from .challenge import Challenge, ChallengeKind, detect_challenge
from .config import BrowserConfig
from .dom import (BrowserUnavailable, DomError, DomPort, FixtureDom, FixtureElement,
                  FixturePage, PlaywrightDom, playwright_available)
from .fingerprint import (FINGERPRINT_ALGORITHM, TargetDescriptor, fingerprint_of,
                          normalize_text, target_fingerprint)
from .isolation import AccountContextRoot, CrossAccountViolation, account_slug
from .secrets import (MappingSecretResolver, Redactor, SecretNotFound, SecretRef,
                      SecretResolver, redact_secrets)
from .selectors import (SelectorAction, SelectorPack, SelectorPackError,
                        SelectorRegistry, Strategy, load_pack, validate_pack_document)
from .session import (AccountBrowserSession, BrokenUi, IdentityMismatch, PageSnapshot,
                      ResolvedTarget)
from .states import (SPEC_MACHINE_YAML, BrowserState, BrowserTransitionError,
                     allowed_transitions, can_transition, check_transition)
from .worker import (BrowserWorkerHandle, BrowserWorkerPool, SecretInTransit,
                     WorkerRequest, WorkerResponse, guard_account, guard_payload)

__all__ = [
    "FINGERPRINT_ALGORITHM", "SPEC_MACHINE_YAML", "AccountBrowserSession",
    "AccountContextRoot", "BrokenUi", "BrowserAuditRecord", "BrowserAuditSink",
    "BrowserCapabilityLedger", "BrowserCapabilityRecord", "BrowserCapabilityState",
    "BrowserConfig", "BrowserState", "BrowserTransitionError", "BrowserUnavailable",
    "BrowserWorkerHandle", "BrowserWorkerPool", "Challenge", "ChallengeKind",
    "CrossAccountViolation", "DomError", "DomPort", "Evidence", "FailureKind",
    "FixtureDom", "FixtureElement", "FixturePage", "IdentityMismatch",
    "InMemoryAuditSink", "MappingSecretResolver", "PageSnapshot", "PlaywrightDom",
    "PromotionRefused", "Redactor", "ResolvedTarget", "SecretInTransit",
    "SecretNotFound", "SecretRef", "SecretResolver", "SelectorAction", "SelectorPack",
    "SelectorPackError", "SelectorRegistry", "Strategy", "TargetDescriptor",
    "WorkerRequest", "WorkerResponse", "account_slug", "allowed_transitions",
    "can_transition", "check_transition", "detect_challenge", "fingerprint_of",
    "guard_account", "guard_payload", "load_pack", "normalize_text",
    "playwright_available", "redact_secrets", "target_fingerprint",
    "validate_pack_document",
]
