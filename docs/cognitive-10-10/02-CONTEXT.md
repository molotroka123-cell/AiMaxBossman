# 02 — Контекст 7/10 → 10/10

Код: `bossman-core/bossman/cognitive/context.py`
Тесты: `bossman-core/tests/test_cognitive_context.py`

## Context Compiler — единственный сборщик запроса

Фиксированный порядок секций (`COMPILER_ORDER`):

```text
System invariants → User goal → Current working state → Critical constraints
→ Verified evidence → Relevant memory → Required code/interfaces
→ Recent tool results → Unresolved questions → Current action
```

Никаких "последних N сообщений до заполнения окна".

## Приоритеты

- P0: безопасность, цель, approval, текущее действие.
- P1: ограничения, working state, verified evidence.
- P2: план/память/интерфейсы/последние результаты.
- P3: вспомогательная история, открытые вопросы.
- P4: предположения, необязательная документация.

`compile(budget_tokens)`: P0/P1 неприкосновенны (переполнение фиксируется
в `overflow_protected`, а не молчаливым дропом), урезание идёт P4→P2.
`must_preserve` вне P0/P1 тоже не дропается.

## Critical-fact ledger

- `CriticalFactLedger.record()` — до compression.
- `verify_roundtrip(facts_before, summary)` — после: дословное вхождение
  или ≥70% ключевых токенов; потеря `must_preserve` → `ok=False`.
- `HierarchicalCompressor.compress()` дописывает `PRESERVED FACT [id]: ...`
  дословно; при `ok=False` вызывающий код обязан взять raw fallback
  (summary отменён, а не "починен" молча).
- Уровни: `step | episode | module | project`, каждый со `source_refs+fact_ids`.

## Raw-context fallback (`should_use_raw`)

Хотя бы одно из: низкая уверенность retrieval (< 0.35), конфликт источников,
несогласие verifier, необратимое действие, потеря факта в summary, нужна точная
цитата/SHA/команда, найден injection → `use_raw=True` + причины.
`CognitiveRuntime.compile()` при `use_raw` возвращает сырой текст, не сжатый.

## Injection firewall

- Источники `website/ui/readme/git_issue/log/video/ocr/email/tool_result/...`
  → `TrustTag.UNTRUSTED_DATA` (автоматически в `ContextItem.__post_init__`).
- `InjectionFirewall.scan()`: вырезает директивы смены инструкций/разрешений
  (`[removed-by-injection-firewall]`), `hit=True` → триггер fallback.
- Untrusted никогда не меняет системные инструкции — проверено тестом.

## Метрики приёмки (см. `verify.CONTEXT_GATES`)

`CriticalFactRecall = 1.00, LostConstraintRate = 0, CrossProjectConfusion = 0,
InjectionExecutionRate = 0, StaleContextUsage = 0, ContextWasteRate ≤ 0.10`,
плюс `VerifiedSuccess ≥ raw baseline` **одновременно** с `TokenReduction ≥ 30%`.
