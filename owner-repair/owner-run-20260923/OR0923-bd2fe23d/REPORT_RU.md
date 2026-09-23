# Отчёт OWNER RUN 23.09.2026 — Bossman: реальный UX + Terminal 1.2 + обучение

RUN_ID `OR0923-bd2fe23d` · RUNTIME=OLLAMA_PROXY · PRIVILEGE=ADMIN_RUN (+ короткий STANDARD_USER smoke) · Smart App Control = On (не отключался).
Terminal Run 1.2 = другой пульт того же Bossman (общие задачи/память/approvals); цель — измеряемое самоулучшение и бизнес-пилот в окне 4–6 дней, заработок не гарантирован.

## Код и сборки
| | SHA | ZIP SHA-256 | Статус |
|---|---|---|---|
| PRE-FIX baseline | `bd2fe23d` (integrate/owner-final, содержит #74) | `95476d4a…3528c` | локальная сборка, bundle acceptance PASS |
| SHA2 | `d53f3b12` (fix/owner-run-20260923-p1) | `2edb4276…2007` | + 3 фикса P1/P2 |
| **SHA3 (текущий кандидат)** | `cdb4b09d` | `484590c7…8bc8` | SHA2 + NO_PROGRESS-детектор; bundle acceptance PASS |
| SHA4 (CLI «как у Claude Code») | ветка feat/cli-claude-parity-20260923 | см. CHECKPOINT | собирается/проверяется отдельно |

Все ZIP собраны локально штатным скриптом: в GitHub Actions для этих SHA Windows-сборки не было (**PACKAGING_BLOCKER**). Это не CI-сертификат. В release/main ничего не вливалось.

## Ответы на 8 вопросов

**1. Готова ли 1.0 по фактически выполненным owner-кейсам?** Нет. OWNER_HARDWARE_CERTIFIED не заявляю. Открыты P1:
- ложный успех «Скачай PDF-файл https://…» без скачивания;
- action-contract подменяет инструменты агента (Computer Use через агента невозможен, HW-02 FAIL);
- coding path не работает с полным репозиторием Bossman (80 МБ > 32 МБ).

Нет CI exact-SHA сертификата. HW-06 Telegram и HW-12 soak не выполнены. P0 не найдено.

**2. Работает ли новый CLI в настоящем Windows-терминале?** Да, с P2. ConHost и Windows Terminal:
- кириллица без искажений;
- Tab-дополнение;
- Ctrl+C = отмена, подтверждённая backend;
- многострочная вставка правым кликом не исполняется построчно;
- resume сессии;
- закрытие окна не останавливает задачи;
- JSON без ANSI и без токена;
- replay по курсору, идемпотентный `request_id`, нейтрализация escape-последовательностей.

P2:
- Ctrl+V и Shift+Insert в ConHost (Shift+Insert вставляет `[2;2~`);
- «Terminate batch job?» после Ctrl+C;
- prompt выглядит как prompt cmd;
- resume не восстанавливает агента;
- шум «проверка: NOT_APPLICABLE».

Главный P1 чата (прошлое «Запомни…» ломало каждый следующий ход) **исправлен в SHA2** и проверен вживую после рестарта. На рабочем столе есть ярлык «Bossman CMD» (Windows Terminal, без прав администратора).

**3. Доказаны ли общие данные UI ↔ CLI ↔ Telegram?**
- UI ↔ CLI — да для моделей, агентов, задач, approvals, STOP, coding-задач и памяти (см. UX_CLI_PARITY.md).
- Файлы проекта — нет: CLI не пишет в проект (coding path работает в клоне без apply), у UI-агентов нет инструментов.
- Telegram-путь продукта — OWNER_ACTION_REQUIRED: компаньон привязан к рабочей установке, на одном токене может работать только один поллер. Бот использовался только как канал «владелец ↔ Claude».

**4. Что локальный Bossman сделал сам, а где помог Claude?**
- Qwen MAIN (Ollama) сам решил D2 (unseen) без подсказок.
- D1 (реальный дефект CR CR LF в .cmd архива) Qwen решил на подсказке L4 (подход): патч и регрессию написал сам, скрытый verifier подтвердил, неверный патч отклонён.
- На L0–L3 модель находила строку причины поиском, но не переходила к правке.
- Coaching-пакет 5+5 — 10/10 без помощи: пакет насыщен (CEILING_SATURATED).
- Claude: подсказки L1–L4, три продуктовых фикса (чат-контекст, CRLF в coding path, пометка обрезки вывода sidecar), детектор повторов, harness и verifier.
- Patch учителя в продукт ученика не применялся; подготовленный L5 не понадобился.

**5. Пережил ли урок restart и помог ли на unseen-задаче?** Пережил: VERIFIED `coach-lesson:29a7424bd33b115b` после полного рестарта (новый PID и started_at), в задаче D2 был извлечён и применён. Но пользы не измерено — **NO_MEASURED_GAIN**:
- RAW: 7 шагов / 122 с — PASS;
- с уроком: 8 шагов / 150 с — PASS.

North Star: **SELF_REPAIR_SINGLE_CYCLE_PASS (coached, L4)**. TRANSFER_MEASURED_GAIN — нет.

**6. Какие обязательные проверки не пройдены?**
- CI exact-SHA;
- HW-02 (Computer Use через агента) — FAIL;
- HW-06 Telegram — OWNER_ACTION_REQUIRED;
- HW-12 soak — NOT_RUN;
- TR-06 общие файлы — PARTIAL;
- TR-12 STOP из Telegram — NOT_RUN;
- TR-13 одновременный approve из двух каналов — NOT_RUN;
- TR-15 junction — NOT_RUN;
- Image Studio через ComfyUI — ENVIRONMENT_BLOCKER (SAC). Картинка через sd.cpp — PASS, но в UI «Студии» sd.cpp-модели не видны;
- Music Studio — NOT_RUN;
- независимый red-team роем агентов — в этой сессии не запускался;
- self-repair на 3 циклах и transfer gain — не достигнуты.

**7. Какие процессы и задачи оставлены работать?**
- Тестовый Bossman SHA3: порт 8810, backend без повышения, окно Edge с CDP 9333.
- Ollama :11435 (KEEP_ALIVE=-1, модели выгружены после медиа и подгружаются по запросу).
- Прокси без размышлений :11500.
- Заглушка «облака» :11600 (безвредна; модель удалена из реестра).
- Поллер Telegram-inbox (канал коррекций; остановить — создать файл STOP рядом с `tg_inbox.py`).
- Пользовательский Ollama :11434 (был запущен до прогона).
- Активных задач Bossman нет; STOP управления компьютером включён.

**8. Точные команды следующего запуска** — CONTINUE.md.

## Таблица причин ошибок
| Причина | Примеры |
|---|---|
| PRODUCT | ложный успех загрузки (P1); action-contract прячет computer.* (P1); лимит снимка 32 МБ (P1); цикл повторного скачивания после verify FAIL; false FAIL после повторной попытки; при autocrlf=true evidence-diff не применяется; UI Студии без sd.cpp; «зависший» backend после закрытия окна; двухфазный submit; UX-дефекты терминала — см. BUGS.md |
| MODEL | D1 L0–L3: не переходит к правке, игнорирует предупреждения о повторах; FAST ответил «Турецкий» вместо «бирюзовый»; `valid_at` 2024 выдуман |
| HARNESS (мои) | встроенный Python не видит cwd; CRLF в diff D2; GUI-замер ловил старую карточку; heredoc портил `\r` и `\` — всё исправлено, записи помечены |
| RUNTIME | llama.cpp → Ollama (SAC); результаты не сравнимы напрямую со вчерашними llama.cpp |
| ENVIRONMENT | Smart App Control (llama.cpp, ComfyUI/scipy); сессия с повышенными правами; `NoDefaultCurrentDirectoryInExePath` в сессии Claude |
| OWNER_ACTION_REQUIRED | Telegram-путь продукта на тестовом экземпляре; решение по фазе 2 Jev; слияние fix-веток в release/CI |

## Прочие результаты
- MVČR (синтетика + живые официальные источники): PARTIAL_MISSING_DATA, ничего не подано.
- Web Designer: создание → правка → сохранение → повторное открытие → версии → откат — PASS.
- Медиа sd.cpp:
  - картинка 768×768: PASS (decode, sha256, provenance, Vulkan);
  - видео Wan2.2 1 с: PASS (h264 640×352, 17 кадров, 16 fps, полное декодирование);
  - cancel и падение backend: сирот нет, `interrupted_unknown` без слепого повтора.
- Jev через OpenRouter System One: CONTRACT_VERIFIED + SHADOW_PASS (6/6, 4/4, согласие 18/18, ≈$0.0008); фаза 2 — INSUFFICIENT_EVIDENCE.
- GUI vs CLI:
  - SHA3: память/задача и браузер — MEASURED_CLI_OVERHEAD_REDUCTION (в 2 раза быстрее до проверенного результата, 1 действие вместо 6–9);
  - coding — по времени без выигрыша, но в 17 раз меньше действий учителя.
- Облако: лимит $3/день включён; потрачено ≈$0.001; тихого платного fallback нет.
