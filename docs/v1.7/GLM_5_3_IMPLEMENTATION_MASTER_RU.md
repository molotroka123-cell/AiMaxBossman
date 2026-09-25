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


## Первые команды завтра

```bash
git fetch --all --prune
git switch feat/bossman-1.7-personal-identity-training-20260925
git pull --ff-only
git rev-parse HEAD
PYTHONPATH=command-center python -m pytest command-center/tests/test_pit_foundation.py -q
python -m compileall -q command-center/bcc/pit
```

После каждого исправления повторяй PIT foundation и затронутые существующие Telegram-contract tests. Не делай merge в 1.5/1.6 во время laptop shadow.


## K. Jeff public behavior — обязательно

Прочитай `JEFF_PUBLIC_BEHAVIOR_RU.md`.

Порядок обработки participant message:
1. auth/idempotency;
2. `public_guard(message)`;
3. если guard вернул ответ — отправь его напрямую, без Jev/model route;
4. иначе обычный PIT pipeline;
5. перед Telegram send используй PIT presentation renderer, который НЕ добавляет model/provider/route metadata.

`/model` отсутствует у PIT participant.

Глубокие вопросы о публичном Bossman можно отвечать через публичный GitHub/web. Не используй внутреннюю память/ветки PIT как источник ответа участнику.

Никакой owner/global memory не подмешивать даже если participant — сам владелец компьютера.


## L. Free-only + Bossman-only invariant

PIT handler не имеет собственного прямого cloud client как отдельный мозг. Route идёт через Bossman/Jev/provider registry.

Remote candidate eligible только если текущий provider catalog подтвердил zero-cost. Unknown price = no route. Paid fallback в 1.7 выключен.

## M. Local storage and direct-access invariant

PIT authoritative root:
`<BOSSMAN_DATA_DIR>/pit-v1.7/personalities/<person_key>/`.

Не выдавай модели `persona.*`, filesystem или arbitrary file tools.
Модель получает только transient bounded context от Bossman broker.
`security/risk.json` никогда не входит в prompt/export.

## N. Risk wiring

Если `public_guard` вернул `risk_delta > 0`:
1. `RiskLedger.add(current_person_key, delta, kind)`;
2. guard reply отправить без LLM;
3. score не показывать модели;
4. при следующем разрешённом discovery Bossman может передать только число в deterministic selector;
5. risk не разрешает sensitive question, второй follow-up или новые tools.

## O. Capability parity

До `PIT_LAPTOP_SHADOW_READY` реализовать безопасный laptop chat surface:
text/chat, web+sources, safe non-image uploaded-file understanding, code reasoning, calculator, summaries, translation, memory.

Фото/vision/edit НЕ являются laptop blocker: локальных media-моделей там нет.

Laptop photo analysis:
`PhotoPipeline(..., ai_max_ready=False)`
→ «Фото получил. Разбирать и редактировать изображения локально я начну после переезда на AI Max 😊»

Laptop image generation/edit:
`image_generation_reply(ai_max_image_generation_ready=False)`
→ «Скоро научусь, малышка 😊».

После AI Max:
- qwen_vision.py = fast local VLM;
- photo_pipeline.py = foreground answer + background memory;
- studio_image_edit.py = existing Studio reference/job/run broker;
- photo_edit.py = current participant latest-photo editing.

Прочитай PHOTO_PIPELINE_AI_MAX_RU.md и COMPAT_1_6_MEDIA_20260925.md.
Не создавай второй media backend и не запускай shell из participant path.

## P. CMD acceptance

Реализовать в существующем `bossman` CLI, не отдельном пользовательском скрипте:
- `bossman pit start`
- `bossman pit status`
- `bossman pit doctor`
- `bossman pit stop`

`start` запускает именно PIT mode того же Command Center/Telegram infrastructure.
`status` показывает transport/backend/provider availability без model ID в participant UI.
`doctor` проверяет config/storage/free-route/web/vision/token availability без вывода secrets.
`stop` останавливает PIT transport без остановки всего Bossman.

Добавь CLI regression и installed-path smoke test.


## Q. Three local behavior signals

Wire all three through Bossman, never into LLM context:

1. `RiskLedger` — monotonic privacy-probe counter.
2. `BehaviorLedger.engagement` — reversible 0..100.
3. `BehaviorLedger.profile_stability` — reversible 0..100.

Rules:
- engagement up: answered discovery, voluntary preference/goal, useful correction, continued context;
- engagement down: skipped/ignored discovery;
- low engagement suppresses personal questions;
- stability up: confirmed memory, consistent observation, useful retrieval;
- stability down: correction, contradiction, changed preference, expiry;
- low stability raises memory-confidence floor;
- scores are local security telemetry, excluded from export/model context.

Use `BehaviorController` rather than duplicating math in Telegram handler.

## R. Moderately personal contextual discovery

Use `moderate_discovery.py` only when the current task benefits from it.

Allowed examples:
- approximate budget range;
- city/region, never exact address;
- rough age range where relevant;
- work schedule;
- household/travel-companion context;
- experience level;
- device ecosystem;
- availability window;
- communication preference.

Question remains optional and skippable. Never ask for passwords, IDs, bank details, 2FA, seed phrases, exact address, hidden location or other secrets.

## S. Politics / religion

Use `topic_policy.py`.

Do not introduce politics/religion for profile enrichment.
When participant brings topic up, discussion is allowed.
Personal political/religious durable memory requires:
- topic active from participant message;
- explicit self-statement;
- memory enabled;
- sensitive-memory opt-in.

No political persuasion or owner-view inheritance.

## T. Role-play / parody

Wire `/roleplay` and `/parody` to `roleplay_commands.py`.

Flow:
1. participant requests roleplay/parody;
2. show short preview and get consent;
3. persist roleplay state under this person_key;
4. add `roleplay_prompt()` only for this participant;
5. allow playful benign/moderate discovery;
6. same secret/sensitive/tool/privacy boundaries remain;
7. /roleplay off clears active state.

Prove restart persistence and cross-user isolation.

## U. Public Bossman through v1.6 only

Jeff may answer public Bossman questions using the curated public overview and public GitHub/docs through v1.6.

Do not expose PIT/1.7 internals.
If user asks specifically about v1.7/PIT/internal branches, use public_guard.

## V. Latest 1.5 alignment

Before tomorrow implementation compare against:
`release/bossman-1.5-rc2-20260925 @ 21a8092b75763aefb741b4b150344a0673e01344`.

Recorded state: current 1.7 was a direct descendant, behind_by=0.

Reuse 1.5:
- same backend;
- free-first provider economy;
- same Bossman terminal/CMD;
- same STOP/budgets/privacy/evidence;
- same Telegram primitives;
- exact-SHA honesty.

Do not create a new launcher when 1.5 already has unified CMD infrastructure.


## W. AI Max photo speed contract

При фото с вопросом foreground vision имеет приоритет над visual-memory enrichment.

Обязательный порядок:
1. verify Telegram identity;
2. reject >10 MiB before model;
3. verify JPEG/PNG/WebP magic bytes;
4. persist only under current person_key media folder;
5. run short local Qwen vision answer;
6. send Jeff answer;
7. only then schedule deeper structured visual analysis;
8. if chat/model resources remain busy, background analysis waits or is skipped;
9. never hold up a new chat answer for memory enrichment.

Visual memory stores only neutral short-lived context initially. No person identification or inferred protected/sensitive traits from pixels.

Photo edit:
participant own verified image → Bossman Studio reference → local Qwen edit model → verified run bytes → Telegram.

Do not change shared 1.6 Studio API unless actual provider integration requires one minimal adapter.
