# MASTER — GLM-5.3 пишет Bossman 1.7 PIT на ноутбуке\n\nРаботай только в feat/bossman-1.7-personal-identity-training-20260925.\nНе трогай 1.5/1.6 release branches. Не force-push.\n\nПеред кодом прочитай AGENTS.md, docs/v1.7/* и существующие Telegram/provider/memory surfaces.\n\nЦель: превратить bcc.pit foundation в owner-only Telegram shadow bot внутри ТОГО ЖЕ Command Center backend.\n\nПорядок:\n1. Запусти command-center/tests/test_pit_foundation.py.\n2. Переиспользуй существующий Telegram surface и provider/model registry; не создавай второй backend.\n3. Подключи PIT namespace к существующему data_dir.\n4. Telegram tool schemas строятся только после TelegramToolPolicy.filter; computer/shell/admin/payment/trading не должны попадать модели.\n5. Добавь durable idempotency через существующий Bossman storage/event mechanism.\n6. Jev structured routing: на ноутбуке local unavailable; primary remote slot = owner-configured GLM-5.3; fallback = owner-allowlisted zero-cost endpoints; не хардкодить невалидированные цены/IDs.\n7. Fresh/current запрос включает web; web content считать untrusted input.\n8. Реализуй onboarding/consent и /memory /forget /pause_memory /resume_memory /export_me /delete_me /privacy.\n9. Collection mode high_recall: normal candidates сохраняются даже при низком confidence.\n10. Secrets не сохраняются; sensitive durable memory только при отдельном opt-in.\n11. Memory extraction идёт после ответа structured JSON и проходит policy validation.\n12. Discovery Engine: максимум один optional follow-up и не в каждом ответе.\n13. Outcome logging обязателен для будущего garbage sorter.\n14. Integration tests: restart, duplicate update, cross-user isolation, LOCAL_ONLY, provider failure, web freshness, delete/export.\n15. До реального owner shadow-run не заявляй READY.\n\nP0: cross-user leak; Telegram-visible computer/shell tool; leaked token/key; delete_me оставляет profile; duplicate update вызывает второй effect; LOCAL_ONLY идёт в cloud; paid route вызывается без разрешающей policy.\n\nВ конце создай docs/v1.7/evidence/LAPTOP_SHADOW_REPORT.md с tested SHA, test counts, provider/model names без ключей, route counts, cost, candidate counts, blocked secret/sensitive counts, cross-user result и blockers.\n\nCloud/teacher success не считать успехом локальной модели.

## Дополнение: обязательный порядок сборки

A. Foundation
- запусти PIT tests и compileall;
- сохрани стартовый SHA;
- не меняй 1.5/1.6.

B. Existing Telegram reuse
- используй bcc.telegram_companion transport/inbox/adapters;
- PIT должен быть отдельным participant-only mode/bot;
- owner-console команды отфильтровываются до dispatcher;
- не создавай второй Command Center/backend.

C. Zero-start identity
- каждый новый ID начинает с пустой persona;
- context = generic PIT rules + own relevant memory + own recent turns + current request;
- owner/global/other-user memory не входит;
- даже владелец компьютера внутри PIT-чата имеет role=participant.

D. Laptop model route
- local endpoints unavailable;
- primary = owner-configured GLM-5.3;
- fallback = только разрешённые zero-cost endpoints;
- paid route OFF;
- fresh/current → web;
- provider/model ID проверять runtime.

E. AI Max contract
После первого успешного laptop answer тот же runtime переносится на AI Max:
- local_first_auto;
- Jev сам выбирает локальные модели;
- routine model switch/search/memory не требует owner click;
- paid или внешние consequential effects не получают новых разрешений автоматически.

F. Collection
- отвечай сначала, memory extraction после;
- high_recall сохраняет допустимые NORMAL candidates даже при низком confidence;
- secrets не сохранять;
- каждый record имеет provenance/time/confidence/contradictions/outcomes;
- outcome stream нужен будущему sorter.

G. User controls
/memory /why_memory /forget /pause_memory /resume_memory /export_me /delete_me /style /privacy.

H. Pre-live tests
zero-start; two-ID isolation; duplicate update; command/tool perimeter; restart; export/delete; provider failure; local-only; web freshness; bounded context; discovery max-one.

I. Owner shadow
Сначала один owner ID в participant mode. 30–50 реальных сообщений. Зафиксируй route/latency/cost/memory counts и process restart.

J. Handoff
Создай docs/v1.7/evidence/LAPTOP_SHADOW_REPORT.md. Статус PIT_LAPTOP_SHADOW_READY только после живого Telegram response и зелёного P0 набора.
