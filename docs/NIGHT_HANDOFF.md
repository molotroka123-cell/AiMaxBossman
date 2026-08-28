# NIGHT HANDOFF — BOSSMAN V2 (обновляется на каждом этапе)

> Оборвалась сессия? Новая читает ЭТОТ файл + `data/night_tasks.json` +
> `docs/V2_ORCHESTRATION_STATE.md` и продолжает. LOCKED-решения —
> `docs/v2-pack/CLAUDE_START_HERE.md`. Отчёты — `docs/V2_IMPLEMENTATION_REPORT.md`,
> `docs/V2_FINAL_SCORECARD.md`.

## Главная цель
BOSSMAN V2 — проверяемый multi-agent AI control plane. Реальные функции, не мокапы.

## Ветка и состояние
- Ветка (push): `claude/bossman-control-v03-43igbk`. Всё запушено.
- Тесты: **110 pytest passed** (`cd command-center && python -m pytest -q`;
  если pytest-timeout плагин мешает — `timeout 200 python -u -m pytest -q`).
- Пак пользователя внедрён (`bcc/v2/*`, `.agents/skills/*`, `docs/v2-pack/*`).

## Сделано (backend всех 15 функций + ядро)
- Ядро: Worker Pool (BCC_WORKERS=3) + Hard Cancel; 6 хуков engine; загрузчик
  фич `bcc/features/`; 12 V2-таблиц + миграции; permissions.
- 15 функций как feature-модули с тестами (см. V2_FINAL_SCORECARD):
  01 missions, 02 router, 03 governor, 04 benchlab, 05 forks, 06 agentmap,
  07 terminal(+opencode PARTIAL), 08 review_gate, 09 browser, 10 skills(+mcp),
  11 nl_orchestra, 12 resources, 13 kpi(в missions), 14 healing, openrouter.
- VRAM-аудит vs OpenCode (~105 МБ idle, 0 VRAM) — в V2_CURRENT_STATE_AUDIT.
- Отчёты: scorecard + implementation report.

## В работе / осталось
1. **UI-страницы функций** — строит субагент (ui/pages/*.js) поверх готовых API.
   Реестр: ui/pages/index.js (FEATURE_PAGES). После завершения — интегрировать,
   `node --check`, зарегистрировать в app.js (PAGES.push уже есть).
2. **Browser QA** — поднять сервер, Chromium /opt/pw-browsers/chromium, пройти
   viewport'ы 320/375/390/430/1440, скриншоты в docs/V2_PROOFS/shots.
3. **OpenCode полный цикл** — на машине с `opencode serve` (сейчас PARTIAL).
4. **Кросс-сценарии §39-41 на реальных моделях** — компоненты готовы+mock.
5. Финал: обновить scorecard по UI, финальный commit+push, ветка
   feature/bossman-command-center-v2 на итог.

## Точные команды
```bash
cd /home/user/AiMaxBossman/command-center
timeout 200 python -u -m pytest -q          # 110 passed
BCC_DATA_DIR=/tmp/bcc BCC_PORT=8800 python -m bcc.app   # токен в консоли/файле
```

## Как устроено расширение (для продолжения)
- Новая фича backend: `bcc/features/<имя>.py` c `FEATURE = Feature(...)` —
  авто-подключается. Хуки: `svc.engine.add_hook(...)` в `setup(svc)`.
- Новая UI-страница: `ui/pages/<имя>.js` (объект {id,title,icon,nav,render,onEvent})
  + строка в `ui/pages/index.js` FEATURE_PAGES.
- Тесты: `tests/test_feat_<имя>.py`, фикстура `env` (conftest), `FakeAdapter`.
- Таблицы V2 — только в `bcc/db.py` (лид), НЕ в feature-модулях.

## Не делать без владельца
production deploy, финансы, удаление данных, отправка клиентам, force push,
публикация в интернет, выдача wallet/banking доступа агентам.
