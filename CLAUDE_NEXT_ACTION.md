# Multi-agent convergence note — 2026-09-22

Before the existing actions below, read `docs/agents/MULTI_AGENT_FULL_SURFACE_CONVERGENCE.md`.

Parallel lanes now exist:
- `audit/codex-full-surface-20260922` — bounded engineering/audit;
- `audit/aster6-full-surface-20260922` — economical independent audit.

Claude remains the final integrator. Do not share a worktree with them. Consume only committed deltas/checkpoints, preserve contradictory evidence, and integrate by meaning onto the current working tip. Canonical destination remains `release/bossman-owner` through PR #72 after re-verification.

The objective is full-surface convergence: keep closing the originally planned Bossman functionality after the 1.0 baseline, not only the minimum release scenarios.

---

# BOSSMAN — ПОСЛЕДНИЙ ПРОХОД ПЕРЕД ТЕСТОМ ВЛАДЕЛЬЦА

Репозиторий: `molotroka123-cell/AiMaxBossman`.
Единственная финальная ветка: `release/bossman-owner`, PR #67.

Ты — последний интегратор. Цель: закончить текущую сборку, запушить, проверить и передать один Windows-архив. Не новый аудит вместо работы и не новые функции.

1. Fetch актуального remote HEAD, прочитай README, INSTALL, OWNER_ACCEPTANCE, KNOWN_LIMITATIONS, CONVERGENCE_DECISIONS и salvage ledger. Сохрани чужие новые коммиты. Не создавай финальную ветку, не меняй default branch, не делай force-push и не сливай #67 в старую базу ради уборки.
2. Закрой подтверждённые программные P0/P1 и текущие красные обязательные проверки: воспроизведение → падающая регрессия → исправление → повторный тест. Не ослабляй проверки. Проверь реальные связи UI/API, creative brief → AI-сборка сайта, контекст/рестарт, approvals, Telegram, image/video и упаковку. Не переписывай уже работающие подсистемы.
3. Прогони установленный продукт в чистом Windows и настоящий браузер: создать → изменить → сохранить → открыть снова; двойной клик, отмена, reload, рестарт и отказ провайдера. Собери console/pageerror/requestfailed и неожиданные 4xx/5xx. Для медиа проверь файл, пиксели/ожидаемую правку, ffprobe и полное декодирование. В отчёте укажи фактическое покрытие, не заявляй «все кнопки» без полного реестра.
4. Закончи release-critical salvage по каждой способности. PR #69 и другие новые идеи не расширяют объём этого прохода: переноси только необходимое для текущего обещанного продукта. Остальное явно в EVO/backlog, не выдавай за перенесённое. Не удаляй ветки и не закрывай PR с полезной неперенесённой работой.
5. Сохрани never/ask/allowed, LOCAL_ONLY, бюджеты и защиту от повторных эффектов. Трейдинг только READ-ONLY/PAPER. MVČR только подготовка до WAIT_APPROVAL. Реальная модель через разрешённый провайдер — отдельно от mocks; нет ключа/железа — честный статус, не fake PASS. EVO пока только рекомендации.
6. Подготовь HW-01…HW-13 и рабочие команды внутри архива. Исправь устаревший диапазон из 12 кейсов в owner-acceptance.ps1; проверь, что wrapper использует нужный runtime и не принимает старый JSON после неудачного запуска. Не заставляй владельца клонировать репозиторий или вручную чинить зависимости.
7. Собери исправления в один согласованный push, объяви кандидат штатным tools/release_candidate.json и прекрати обычные push во время финального прогона. Выполни все обязательные workflows из текущего tools/exact_sha_certify.py: учти push/PR/dispatch, проверь checkout/build SHA и выполненные jobs. Старый SHA, пропуск, отмена, очередь, action_required и ноль jobs не PASS. Не подделывай fail-closed измерение интеллекта и не убирай обязательный gate.
8. Передай: FINAL_BRANCH, TESTED_SHA, Windows URL, SHA-256 точного ZIP, результаты CI/owner/UX, открытые P0/P1, ограничения и три шага запуска. Только при выполнении критериев — READY FOR OWNER TEST. Иначе NOT READY с точными блокерами. После freeze новых функций нет. Финальный отчёт прикрепи к тому же SHA, не создавай commit только ради галочки сертификата.

Выполняй доступные исправления до результата; при реальном внешнем блокере сохрани работу и назови его без выдуманной готовности. Финальная ветка уже выбрана: `release/bossman-owner`.
