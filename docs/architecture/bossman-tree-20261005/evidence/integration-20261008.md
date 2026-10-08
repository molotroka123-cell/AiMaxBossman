# Интеграция одной линии 08.10.2026 — уровни доказательства по листьям

Линия: `integrate/bossman-2.1-one-20261006` → `main` (PR #98). База: `green/tree-leaves-20261006` 4a14c26c + `main` f52ee111.
Уровни считаются РАЗДЕЛЬНО и по порядку; верхний не выводится из нижнего:

- **CODE** — код есть в этой линии;
- **TEST** — тесты прошли в облаке (локальный прогон, команда и счёт ниже);
- **CI** — обязательные задания GitHub прошли на точном SHA (заполняется по факту, см. PR #98);
- **OWNER_HW** — проверено на ПК владельца. Из облака всегда `NOT_RUN`.

| Лист / модуль | Что изменено 08.10 | CODE | TEST (облако) | CI | OWNER_HW |
|---|---|---|---|---|---|
| Direct Generation (`bcc/direct_gen/service.py`) | STOP посреди отправки без `asyncio.shield` | да | `test_direct_gen*.py` 53 passed (фейковый ComfyUI) | см. PR | NOT_RUN (живой ComfyUI/GPU) |
| Direct Generation UI «Создать» | обход страниц проверяет её как встроенную отправку | да | `test_ux2_pages_sweep.py` 3 passed (Chromium) | см. PR | NOT_RUN |
| Командная строка (`features/command_bar.py`) | весь каталог по умолчанию (`tasks.create`) | да | `test_command_bar*.py` 30 passed | см. PR | NOT_RUN |
| Локальный кодер (`apprentice/local_sidecar.py`) | обрезанный JSON вызова не исполняется | да | apprentice 167 passed; новые 4 падают на старом коде | см. PR | NOT_RUN |
| Цикл самоисправления (`tools/tree_self_repair_cycle.py`) | выход за зону ≠ PASS, рецепт не сохраняется | да | 18 passed; новый тест падает на старом коде | см. PR | NOT_RUN |
| Приватность провайдеров (`providers.is_local_url`, `bossman_shared.privacy`) | IPv4-mapped адрес метаданных не «локальный» на любом Python | да | 110 + 120 passed на CPython 3.12.3 | см. PR | NOT_RUN |
| Бюджет (`bossman_shared/fable_budget.py`) | цена Haiku 5.5 | да | pricing test passed | см. PR | — |
| Экзамен K1m6a (`trading_learning/k1m6a_exam.py`) | новый: протокол доказательства обучения | да | 18 passed (синтетика) | см. PR | NOT_RUN (ролики) |
| YouTube ingest (`tools/youtube_trader_ingest.py`) | сохранённые субтитры не теряются | да | 9 passed | см. PR | NOT_RUN |
| Motion Studio library | LF на Windows | да | 24 passed | см. PR | NOT_RUN |
| Telegram-звонки (`account/stopflag.py`) | «битая» история не держит воркер «в звонке» | да | 79 passed (+4 новых, падают на старом коде) | см. PR | NOT_RUN (живой звонок) |
| Poker Vision UI (`ui/pages/poker_vision.js`) | опросы не накладываются | да | статический инвариант 2 passed | см. PR | NOT_RUN |
| Приложения: ai-3d-maker (`app.manifest.yaml`) | карточка опрашивает `/metrics` | да | 99 + 44 passed | см. PR | NOT_RUN |
| Локальный manifest (`tools/local_completeness_manifest.py`) | новый | да | 5 passed | см. PR | NOT_RUN (`LOCAL_COMPLETENESS=UNVERIFIED`) |
| CI-контракт веток (`tools/owner_facing_branches.json` + 8 workflows) | `main` и 2.1 покрыты | да | 231 passed | см. PR | — |
| OSS: faster-whisper + PyAV | `av<19` | да | измерено av 15–18 OK, 19 падает | см. PR | — |
| Сценарий OS-60 | без гонки с тиком очереди | да | 3/3 локально | см. PR | — |

Полные прогоны в облаке на этой линии (до правок 08.10, коммит 4e08a314): root 2989 passed / 6 failed (все шесть разобраны:
5 исправлены, 1 — нет pip в облачном venv); Command Center 8498 passed / 10 failed (исправлены 4, остальные — облачная
среда: Chromium 1194 вместо 1243, нет pip, CPython 3.12.3 для link-local — последнее тоже исправлено в коде).
Владельческие сценарии локально: 103/110, 0 FAIL, 2 без PostgreSQL, 5 без ключа ИИ.

Пакет: source SHA и SHA-256 архива Windows — в последнем комментарии PR #98 (этот файл не может содержать SHA коммита,
в котором лежит).
