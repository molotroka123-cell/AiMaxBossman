# CURRENT STATE

- Ветка: `claude/bossman-control-v03-43igbk` | HEAD: `dc5b4f9` | всё запушено.
- Полный набор bossman-core: **507 passed, 2 skipped**.

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

## Подсистемы в реестре жизненного цикла
`resource_brain, remote_client, search_everything, video_factory, sandbox` — все
регистрируются лениво в `bossman/api.py`, все `critical=False`.

## Ключевые env
- `BOSSMAN_SANDBOX_ENABLED` — дефолт OFF (OFF значит OFF).
- `BOSSMAN_GATEWAY_URL`, `BOSSMAN_GATEWAY_CORE_KEY` — Этап 3.
- `TELEGRAM_WEBHOOK_SECRET` — обязателен, иначе вебхук approvals отдаёт 403.
- `BOSSMAN_TEST_CHROMIUM` — путь к Chromium для 2 браузерных тестов.
- `BOSSMAN_CORE_API_KEY` — обязателен для консеквентных маршрутов ядра
  (решение подтверждения, cloud_policy агента, approve проекта, гейт
  обучающего набора). Не задан → эти маршруты отдают 401.
- `BOSSMAN_UNSAFE_LOCAL_EXEC` — только разработка: разрешает
  `SANDBOX_MODE=local` (исполнение команды агента на хосте без изоляции).
  Без него `local` отказывает, неизвестный режим — тоже.
