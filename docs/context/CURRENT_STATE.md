# CURRENT STATE

- Ветка: `claude/bossman-control-v03-43igbk` | HEAD: `dc5b4f9` | всё запушено.
- Полный набор bossman-core: **589 passed, 2 skipped**.

## Стадии
| Стадия | Состояние |
|---|---|
| 1 ComputerUse browser | интегрирована; approvals ужесточены (3 обхода закрыты) |
| 2.222 Context/Memory | интегрирована в runner (apply_context_engine, CompactSkill) |
| 3 AI Gateway | интегрирован; облачная политика держится Gateway'ем; failover не на 4xx |
| 4 Resource Brain | готова: аренды памяти (OOM-race закрыт), единый пул, PressureLevel |
| 5 Search Everything | готова: поверх context_engine, без второго RAG, secret-exclusion |
| 6 Remote Client | готова: устройства в Postgres, scope на каждом роуте, lock fail-closed |
| 7 Video Factory | готова: возобновляемая, admission-gated, ffmpeg argv, browser-guard |
| **8 Sandbox** | **ЯДРО готово** (control plane). Реальных рантаймов нет — только FakeRuntime |
| 10 Dev Factory | planner+editor подключены через Gateway (fake/gateway/auto); тесты в песочнице; без auto-push |
| 11 AI Lab | admin scope + containment по sandbox_id + release аренды; training OFF по умолчанию |

## Подсистемы в реестре жизненного цикла
`resource_brain, remote_client, search_everything, video_factory, sandbox` — все
регистрируются лениво в `bossman/api.py`, все `critical=False`.

## Ключевые env
- `BOSSMAN_SANDBOX_ENABLED` — дефолт OFF (OFF значит OFF).
- `BOSSMAN_GATEWAY_URL`, `BOSSMAN_GATEWAY_CORE_KEY` — Этап 3.
- `TELEGRAM_WEBHOOK_SECRET` — обязателен, иначе вебхук approvals отдаёт 403.
- `BOSSMAN_TEST_CHROMIUM` — путь к Chromium для 2 браузерных тестов.
- Доступ к маршрутам ядра — устройством Stage 6 (скоупы chat/events/approve/
  admin), НЕ отдельным ключом. Без токена — 401/403. См. CORE_AUTH_MATRIX.md.
- `BOSSMAN_UNSAFE_LOCAL_EXEC` — только разработка: разрешает
  `SANDBOX_MODE=local` (исполнение команды агента на хосте без изоляции).
  Без него `local` отказывает, неизвестный режим — тоже.
