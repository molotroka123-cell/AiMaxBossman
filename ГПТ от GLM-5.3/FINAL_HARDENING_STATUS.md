# FINAL HARDENING STATUS (pre-dispatch)

Статус ДО Stage 13 Dispatch. Stage 13 НЕ начат намеренно — после этого прохода
нужен отдельный аудит/одобрение владельца.

Легенда: IMPLEMENTED · WIRED (подключено в проде) · UNIT · ADVERSARIAL ·
LOCAL-LIVE · CI · BLOCKED_BY_HOST.

## P0

| Пункт | Статус |
|---|---|
| Core auth — все консеквентные/операционные маршруты под Stage 6 scopes | IMPLEMENTED · WIRED · UNIT · ADVERSARIAL |
| Approval bypass — decide недостижим без scope approve (доказано счётчиком) | ADVERSARIAL |
| Никакого «localhost = auth» | IMPLEMENTED (тест loopback→deny) |
| WS /events — scope events, токен субпротоколом (не в URL) | IMPLEMENTED · ADVERSARIAL |
| AI Lab auth — все /api/lab/* admin | IMPLEMENTED · WIRED · ADVERSARIAL |
| AI Lab containment — sandbox_id, без произвольного пути хоста | IMPLEMENTED · ADVERSARIAL |
| AI Lab lease leak — release на любом исходе | IMPLEMENTED · UNIT (100× → baseline) |
| ai_lab router вообще не монтировался (getattr router) | FIXED |

## P1

| Пункт | Статус |
|---|---|
| gitops/media/shell → argv-only (create_subprocess_exec) | IMPLEMENTED · UNIT (AST-проверка) |
| Планировщик — точное имя исполняемого (не startswith) | IMPLEMENTED · UNIT |
| Dev Factory planner wiring (fake/gateway/auto) | IMPLEMENTED · WIRED · UNIT |
| Dev Factory editor — минимальный адаптер через Gateway, sandboxed | IMPLEMENTED · WIRED · UNIT |
| CI: bossman-core gate (3.11+3.12, партиции) | IMPLEMENTED (ждём первый прогон) |
| CI: секрет-канарейки помечены поштучно | IMPLEMENTED |
| CI: 30-мин висяк — pytest-timeout, тест падает и виден | IMPLEMENTED |

## HOST EXECUTION — сводка

- Найдено `create_subprocess_shell`: `toolkit/gitops.py`, `toolkit/media.py`,
  `toolkit/shell.py`, `projects/runner.py`.
- Переведено на argv-only: gitops, media, shell.
- `projects/runner.py` (kind == "cmd") — исполняет строку из ЛОКАЛЬНОГО
  registry.yaml (доверенный конфиг проекта, не модель/не репозиторий), args
  проходят через shlex.quote. Классификация: SAFE (trusted fixed developer
  command). Оставлено как есть; отмечено здесь для следующего аудита.
- В песочнице (Stage 8): shell/git идут через `sh -lc` контейнера ЕДИНСТВЕННЫМ
  аргументом; хостовый интерпретатор строку не видит.

## TOOLBOX (Stage 8) — честный статус

TOOLBOX SECURITY CONTRACT READY · EXECUTION ADAPTER: через SandboxManager +
FakeRuntime доказан контроль-плейн (переходы, аренды, egress, артефакты). Живое
исполнение в gVisor/MicroVM — BLOCKED_BY_HOST (нет runsc/KVM на раннере).
Формулировка «полностью работающий E2E» НЕ применяется.

## DEV FACTORY — честный статус

- planner: WIRED (LLMPlanner через существующий Gateway при
  BOSSMAN_DEV_FACTORY_PLANNER=gateway/auto+gateway_url);
- editor: WIRED (GatewayEditor, пишет только в одноразовую копию, без git/push);
- исполнение тестов: через Sandbox Этапа 8 (fail closed без изоляции);
- LOCAL-LIVE прогон реальной правки моделью — не выполнялся (нужен живой Gateway
  и модель): помечаем как не проверенный вживую, а не «готово».

## BLOCKED_BY_HOST

- runsc/gVisor и /dev/kvm отсутствуют на раннере → сильные рантаймы Stage 8
  протестированы только по пути ОТКАЗА (fail closed).
- Живой OpenRouter/локальная модель/Swift-сборка iOS — вне обычного раннера.

## РЕКОМЕНДАЦИЯ ПО BRANCH PROTECTION (решение владельца)

Required checks для ветки по умолчанию:
- `Bossman Core CI / pytest security (py3.12)` (и остальные группы core);
- `Bossman Core CI / compile + секреты`;
- `Command Center CI / pytest (py3.12)`;
- `Command Center CI / секреты, JS, запрещённые файлы`.

Менять правила репозитория здесь не стал — это политика владельца.

## NEXT

PRE-DISPATCH AUDIT ONLY. НЕ начинать Stage 13.
