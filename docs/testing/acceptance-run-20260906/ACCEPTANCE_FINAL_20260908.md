# ACCEPTANCE FINAL — super-test 2026-09-08 (cloud-only, candidate 1efb5471)

RUN_ID=acceptance-20260906-01 / phase: super-test. RUNNING_SHA=1efb5471 (PR49 head, freeze-кандидат — новейшая линия). Политика: ТОЛЬКО облако через OpenRouter Bossman (glm-5.3-flash cheap, claude-sonnet-4.5 strong), локальные модели выключены, Docker/Core/UIA не поднимались (машина разгружена по требованию владельца).

## Результаты

| Блок | Вердикт | Факты |
|---|---|---|
| APP_START | PASS | CC на 8801 из verify-worktree 1efb5471; identity=bossman-command-center 0.1.0 |
| UI_TABS_TESTED | **34/34** | 33 loaded + 1 честный empty (Расписания); 0 console errors, 0 network 4xx/5xx за весь обход |
| CONTROLS_TESTED | 24 | PASS 14, NO_OP 3, EXPECTED_DISABLED 5 (корректные), NOT_RUN 2, **BROKEN 0** |
| Навигация | PASS | back/forward (41 запись истории), 8/8 hash-URL, Ctrl+K палитра |
| TASK_1 (simple) | **PARTIAL** | Файл создан реально (47B, настоящий таймштамп), но review дважды ложный FAIL → deadlock |
| TASK_2 (medium) | **PASS артефакт / FAIL процесс** | t2-web.json валиден, 3 факта реально со страницы example.com; browser-инструмент не выдан (chromium probe=false), движок честно упал на curl; фантомное обязательство file:example.com → deadlock |
| TASK_3 (hard) | **PARTIAL** | Ветка `acceptance-t3-20260908-002609`, коммит `f077cdf` (INSTALL.md: удалена ссылка на несуществующий backend_patch/INTEGRATE.md, путь iOS исправлен); CC реально гоняет git. Минусы: review-ретрай внёс trailing-whitespace, мусор gen_report.py/report.txt; **$4.04 и 1.28M input-токенов на правку доки** |
| TASK_4 (agentic) | NOT_COMPLETED | Остановлен по капу 12 мин (шаг 13, $0.011); chromium отсутствует |
| COMPUTER_USE | **4/10** | Топ-3 провала: (1) review-гейт ложных негативов блокирует завершение; (2) browser-инструмент без chromium → падение на curl; (3) approval-шторм 30–60/задачу + полный пересыл контекста каждый шаг |
| OPENROUTER_ROUTING | **PASS** | T1/T2/T4=glm-5.3-flash, T3=claude-sonnet-4.5 (run.model_alias + route=direct provider=cloud); fallback не срабатывал; usage ключа совпал с суммой cost-ов ($4.13→$8.30) |
| GITHUB | см. traces-20260908/github-audit.md | main ddea2111; PR49 MERGEABLE/UNSTABLE (py3.12 + intelligence); живой инстанс отстаёт: на freeze-кандидате PR48 на 10 коммитов впереди, в main 6 коммитов вне живого дерева |

## Критические находки (NEW P0/P1)

1. **P0 Deadlock review-escalation** (воспроизведён 4 раза): `review_escalated` → `waiting_approval`, очередь approvals ПУСТА; даже одобренный escalation-approval не возобновляет ран. Единственный выход — /stop. Ретрай запрещён (TASK_STATE_CONFLICT, hint вводит в заблуждение).
2. **P1 Ложные негативы review**: дословное сравнение файла с шаблоном цели ВМЕСТЕ с плейсхолдером `<current timestamp>` (obligations.py:38-43,371 — такой файл невозможен); фантомные обязательства из URL planner'а; вердикт «не соответствует» при expect={exists:true}.
3. **P1 Approval-шторм**: ~121 подтверждений на 3 задачи; каждый шаг пересылает весь контекст (1.28M input-токенов на правку одной доки, $4).
4. **P2 command-bar/parse — устойчивый 409** (подозрение на залипший лок).
5. **P2 Sandbox дублирует записи**: файл пишется в реальный путь и в зеркало bcc-acc-data.
6. **P2 Модельный статус не детектирует истёкший ключ** (status=unknown; 401 виден только в ране).
7. **P3 UI**: «Открыть проект» в Web Designer создаёт новый; sticky-toast на #/router; форма router не рендерится за 3.5с по навигации.

## Машинные данные для файн-тюна

- `tasks-trace.jsonl` — 202 события (цели, шаги, инструменты, вердикты review, модели, cost, interventions).
- `ui-walkthrough.json` — полный обход 34 вкладок, 24 контрола, console/network логи.
- `tasks/diff.txt`, `t3-report.md` — дифф и отчёт T3; `ui/*.png` — 11 скриншотов состояний.
- Interventions тестировщика: замена истёкшего ключа (dcba→986a), ~121 approval, 4×/stop.

## Итог

FINAL=**READY_WITH_EXTERNAL_EVIDENCE_PENDING**: UI стабилен (0 ошибок), маршрутизация облака честная и проверяемая, реальный артефакт-путь работает (2 из 4 задач дали проверяемый эффект), но автономное завершение задач блокируется review-гейтом (P0 deadlock) — до фикса агента нельзя отпускать без надзора.
