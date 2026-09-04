"""web_research: сборка фичи — включение, хуки, фоновая уборка.

Пакет `bcc/features/web_research/` — это ОДИН модуль в терминах `load_features`
(`pkgutil.iter_modules` отдаёт и пакеты, `importlib.import_module` импортирует
этот файл и берёт отсюда `FEATURE`). Разбиение на файлы нужно не для красоты:
между ними нет циклических зависимостей (`config` ← `ledger`/`net`/`sources`/
`render` ← `tools`/`gate` ← `api` ← этот файл), и каждый слой проверяется
отдельно от остальных.

Главное свойство этого файла — то, чего он НЕ делает при выключенном флаге.
`setup()` выходит НЕМЕДЛЕННО: ни одной регистрации инструмента, ни одного
хука движка, ни подмены транспорта OSIRIS, ни единого обращения к диску.
Импорт пакета при этом происходит всегда (его делает `load_features`), поэтому
импорт обязан быть безобидным — и он безобиден: `config` только читает
переменные окружения, остальные файлы при импорте не делают вовсе ничего.
«Выключено» здесь означает «приложение ведёт себя ровно как до модуля», а не
«работает, но тихо».

Чего этот файл НЕ делает и делать не должен:

  * **не решает за владельца, что считать испорченной настройкой.** Мусор в
    переменной окружения — это отказ ФИЧИ (её не будет), а не отказ
    приложения: испорченный потолок бюджета веб-поиска не повод не запустить
    командный центр. Причина уходит событием и видна в `GET /api/web`;
  * **не ставит фичу по частям.** Проверки идут до единого изменения чужого
    состояния, и первая же неудача означает, что не поставлено НИЧЕГО.
    Частично установленная фича хуже отсутствующей: она выглядит
    установленной, а её половина молчит;
  * **не ходит наружу из `tick()`.** Фоновая работа здесь — только уборка
    своего: реестры старше суток, ролловер суточного счётчика и сырьё, на
    которое не ссылается ни одно наблюдение. Ни одного сетевого вызова.

Про порядок в `setup()`. Проверки идут от самой дешёвой и самой
принципиальной к установке: флаг → настройка → флаг OSIRIS → движок → и
только потом изменения в чужом состоянии (`osiris.PARSERS`,
`store(svc).adapter`, реестр инструментов, хук движка). Любая перестановка
означает, что при отказе на шаге N в системе остались изменения от шагов
1..N-1 — то есть частично установленная фича, а её никто не отключит.
"""
from __future__ import annotations

from typing import Any

from .. import Feature, osiris
from . import api, config, gate, ledger, net, sources, tools

# Пометка на объекте сервисов: `setup()` в тестах и при перезапуске зовут
# дважды, а `engine.add_hook` не идемпотентен — второй такой же хук означал бы
# две корректирующие попытки вместо одной и вдвое больший счёт за прогон.
# Пометка живёт на `svc`, а не в модульном множестве по `id()`: идентификатор
# объекта переиспользуется после сборки мусора, и в тестах с десятком сервисов
# это дало бы «уже установлено» для чужого.
_SETUP_ATTR = "web_research_installed"

# Уборка сырья перебирает все файлы наблюдений, поэтому она стоит не в каждом
# тике: раз в час достаточно для сирот, которые и появляются-то редко (файл
# сырья пишется ПОСЛЕ успешного извлечения, то есть уже под наблюдение), а
# каждые пятнадцать минут это лишний проход по хранилищу на машине владельца.
PRUNE_EVERY_TICKS = 4
TICK_SECONDS = 900.0

_ticks = 0


async def _emit(svc: Any, kind: str, **data: Any) -> None:
    bus = getattr(svc, "bus", None)
    if bus is not None:
        await bus.emit(kind, **data)


async def setup(svc: Any) -> None:
    """Установка фичи. При выключенном флаге — немедленный выход и ничего больше."""
    if not config.enabled():
        return
    if getattr(svc, _SETUP_ATTR, False):
        return

    # Испорченная настройка — отказ ФИЧИ, а не приложения. `check_env` зовётся
    # первым делом, как и обещано в проекте, но его исключение не выпускается
    # наружу: `setup()` зовут из `Services.start()`, и упасть здесь значило бы
    # не запустить весь командный центр из-за опечатки в потолке бюджета.
    try:
        config.check_env()
    except config.ConfigError as exc:
        await _emit(svc, "web.setup_refused", code="bad_config", reason=str(exc))
        return

    if not osiris.enabled():
        await _emit(svc, "web.setup_refused", code="osiris_disabled",
                    reason=(f"нужен {config.OSIRIS_FLAG}=1: без слоя происхождения "
                            f"результат нельзя ни проверить, ни стереть"))
        return

    engine = getattr(svc, "engine", None)
    if engine is None or not callable(getattr(engine, "add_hook", None)):
        # Без движка ставить инструменты некуда, а хук — единственное, что
        # ловит модель, напечатавшую вызов текстом. Половина фичи не ставится.
        await _emit(svc, "web.setup_refused", code="no_engine",
                    reason="в сервисах нет движка задач: инструменты ставить некуда")
        return

    # Дальше — изменения в чужом состоянии, и только после того, как всё
    # проверено. Оба вызова идемпотентны сами по себе: парсеры кладутся в
    # `osiris.PARSERS` по имени, а адаптер подменяется, только если текущий —
    # штатный `HttpFetchAdapter` (подменённый в тесте стенд не затаптывается).
    sources.install_parsers()
    net.install_adapter(svc)
    tools.register(svc)
    engine.add_hook("gate_completion", gate.make_gate(svc))
    setattr(svc, _SETUP_ATTR, True)

    ready = sources.readiness(svc)
    await _emit(svc, "web.ready", code=ready.get("code"),
                backends_ready=ready.get("backends_ready"),
                general_web=bool(ready.get("general_web")))


async def tick(svc: Any) -> None:
    """Фоновая уборка своего. Наружу не ходит ни при каких условиях.

    Три дела и ни одного лишнего: реестры прогонов старше суток, ролловер
    суточного счётчика (иначе `_daily.json` неделями показывал бы вчерашнее
    число, и владелец читал бы его как сегодняшнее) и сырьё-сироты.

    Ошибка тика не должна убивать петлю — её и так ловит `Services._feature_tick`,
    но здесь она ещё и превращается в событие: молча пропущенная уборка это
    растущий диск, о котором владелец узнает последним.
    """
    global _ticks
    if not config.both_enabled():
        return
    _ticks += 1
    try:
        ledgers = ledger.Ledger.gc(svc)
        rolled = ledger.daily_rollover(svc)
        pruned = (api.prune_raw(svc) if _ticks % PRUNE_EVERY_TICKS == 0
                  else {"removed": 0, "freed_bytes": 0, "pointers_removed": 0})
    except Exception as exc:                   # noqa: BLE001 — диск бывает любым
        await _emit(svc, "web.tick_failed", error=f"{exc.__class__.__name__}: {exc}")
        return
    if ledgers or rolled or pruned["removed"] or pruned["pointers_removed"]:
        await _emit(svc, "web.gc", ledgers=ledgers, daily_rollover=rolled,
                    raw_removed=pruned["removed"], freed_bytes=pruned["freed_bytes"],
                    pointers_removed=pruned["pointers_removed"])


FEATURE = Feature(name="web_research", router=api.router, setup=setup, tick=tick,
                  tick_seconds=TICK_SECONDS)
