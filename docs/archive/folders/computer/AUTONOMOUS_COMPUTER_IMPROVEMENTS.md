# Autonomous Computer-Control Improvements

Владение агента: computer connection/control/observation, visual state, OS capability
discovery, remote connection, operator reliability, verification, recovery.
Память/модели/контекст — НЕ трогал (ими занимается параллельный агент).

Аудит показал зрелую базу, которую НЕ нужно переписывать: канонический цикл
`observe → plan → policy → approval → execute → observe → verify` уже есть в
`computer_operator/manager.py`; типизированные действия (`ActionKind`),
типизированные постусловия (`ExpectedState`) и `Verifier` — есть; защита от
устаревшего состояния через `generation` — есть (до действия И после аппрува);
owner takeover (`pause`/`take_control`/`resume`) с инвалидацией — есть;
эксклюзивная аренда рабочего стола с heartbeat и `emergency_lock` — есть;
запуск приложений — argv-only + allowlist. Поэтому изменения — ТОЧЕЧНЫЕ,
аддитивные, закрывающие реальные дыры.

---

## IMP-1 — Loop / no-progress guard (защита от «слепого кликера»)

PROBLEM=
Цикл менеджера повторял НЕИЗМЕННОЕ действие против НЕИЗМЕННОГО состояния, пока не
исчерпается replan-бюджет (`max_replans`, по умолчанию 20). Заблокированная модалка,
уехавший элемент или неверный локатор давали до 20 бессмысленных кликов.
Детекции «то же действие / то же состояние / нет перехода» не было вообще
(`grep loop_detect|repeat|oscillat|no_progress` → ничего).

OLD_DESIGN=
Единственный тормоз — счётчики `replans_used`/`max_steps`. Никакого понятия
«прогресса»: одинаковые шаги неотличимы от разных.

NEW_DESIGN=
`computer_operator/loop_guard.py` — детерминированный детектор на подписях:
* `action_signature` = sha1(kind, target, text, args) — без изменчивых id;
* `state_signature` = sha1(foreground app/title/url + отпечаток UI/DOM-дерева + summary).
  **Скриншот в подпись НЕ входит** — пиксельный шум (курсор, часы) делал бы каждое
  состояние «новым» и глушил детектор;
* детектирует `repeat` (то же действие против того же состояния),
  `no_progress` (before==after), `verify_loop` (подряд не проходит верификацию),
  `oscillation` (A,B,A,B).
Вшит в `manager._run_loop` **после policy и approval, до исполнения**, поэтому
может только ЗАБЛОКИРОВАТЬ действие и никогда — разрешить. Срабатывание тратит
replan, эмитит событие `loop_guard` (оператор видит застревание) и уходит на
перепланирование. `take_control`/`resume` сбрасывают историю: после вмешательства
человека прежние подписи не значат ничего.

WHY_BETTER=
Раньше «застряли» обнаруживалось только исчерпанием бюджета — 20 реальных действий
по живому рабочему столу. Теперь — на 3-м. Причина застревания типизирована и видна
оператору вместо общего «replan budget».

TEST=
`tests/test_computer_loop_guard.py` (11) — подписи, все 4 вида детекции, отсутствие
ложных срабатываний при реальном прогрессе, сброс после takeover.
`tests/test_computer_loop_guard_manager.py` (3) — интеграция: упрямый планировщик +
неизменное состояние ⇒ действие исполнено ≤4 раз вместо 20, задача падает с
«loop guard», событие эмитится, takeover чистит историю.

LATENCY_IMPACT=
`LoopGuard.check` p50 0.107 ms / p95 0.139 ms (UI-дерево на 200 элементов);
`state_signature` p95 0.133 ms. На фоне реального клика+скриншота (десятки–сотни мс)
— пренебрежимо. Хот-путь не удлиняется: проверка O(1) по ограниченной истории.

RELIABILITY_IMPACT=
Устраняет runaway-клики по живому десктопу — главный риск «универсального оператора».

SECURITY_IMPACT=
Строго fail-safe: guard стоит ПОСЛЕ policy/approval и может только запретить.
Доказано тестом порядка индексов в `manager.py` (policy < approval < guard < execute).

ROLLBACK=
Убрать блок `gv=guard.check(...)` из `_run_loop` и импорт; модуль самодостаточен.

---

## IMP-2 — Честная витрина возможностей (capability discovery)

PROBLEM=
Не было способа спросить «что этот хост РЕАЛЬНО умеет». Каждый backend имел приватный
`supports()`, но наружу поверхность не публиковалась. Планировщик мог предлагать
действия, невозможные на хосте, и узнавать об этом только по ошибке исполнения.

OLD_DESIGN=
Только `ActionRouter`, перебирающий backends в момент исполнения.

NEW_DESIGN=
`computer_operator/capabilities.py`: канонический словарь `computer.<domain>.<verb>`
→ `ActionKind`, и `CapabilityRegistry.probe()`, который опрашивает РЕАЛЬНЫЕ backends
безопасным пробным действием (никогда не исполняется). Правила честности:
* поддержано ⇔ конкретный существующий backend подтвердил — иначе `supported=False`
  с причиной;
* неизвестная capability → deny-by-default;
* упавший `supports()` НЕ считается поддержкой (fail-closed);
* проба идёт `source="planner"`, поэтому vision-адаптер её не перехватывает и
  пиксельный fallback не выдаёт себя за структурную поддержку;
* цель пробы подбирается под вид действия (для `APP_LAUNCH` — реальное
  allowlisted-имя), иначе получалось ЛОЖНОЕ «не поддерживается».

WHY_BETTER=
«Не врать о возможностях» становится проверяемым свойством, а не обещанием.
На этом headless-хосте реестр честно возвращает 1/13: поддержан только
`computer.application.launch` (argv+allowlist работают на Linux), а весь
mouse/keyboard/window — `unsupported` с причиной, потому что десктопа нет.

TEST=
`tests/test_computer_capabilities.py` (9) — нет backends ⇒ ничего не поддержано;
только реальные виды действий помечаются supported; неизвестная capability отклонена;
сломанный backend не считается поддержкой; проба никогда не исполняет действие;
vision-only не подделывает структурную поддержку; проба не ослабляет allowlist
(`/bin/sh` по-прежнему отвергается и в `supports`, и в `execute`).

LATENCY_IMPACT=
`probe()` 13 capability p95 0.090 ms; вызывается вне хот-пути (при старте/по запросу).

RELIABILITY_IMPACT=
Планировщику можно отдавать только реально доступное — меньше заведомо провальных шагов.

SECURITY_IMPACT=
Deny-by-default для неизвестных; проба безопасна (не исполняет); allowlist не ослаблен.

ROLLBACK=
Удалить `capabilities.py` — модуль ни на что не влияет, пока его не вызвали.

---

## Что НЕ делал сознательно
* Не переписывал наблюдатель/планировщик/адаптеры: канонический цикл, verifier,
  generation-защита и takeover уже реализованы и покрыты тестами.
* Не строил второй remote-канал: аутентификация/скоупы/сессии уже есть в
  Stage 6 `remote_client` (device-токены, `require_scope`, revoke). Дублировать
  его = нарушить инвариант «одна authority».
* Не трогал memory/model/context — зона параллельного агента.

## Честный live-статус на ЭТОМ хосте
`LIVE_COMPUTER_TEST = SKIP_HOST` — headless Linux: `DISPLAY` не задан, X11-сокета нет,
`pywinauto`/`pyautogui` отсутствуют, десктопные backends недоступны. Реестр это и
показывает (1/13). Фабриковать «живой» прогон нельзя.
