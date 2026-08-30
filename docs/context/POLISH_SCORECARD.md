# POLISH SCORECARD — BEFORE / AFTER / EVIDENCE / REMAINING GAP

Правило оценок: **≥8** = работает + есть регрессионные доказательства (тесты);
**~9** = production + operational/E2E-доказательство на реальном хосте. Оценка
никогда не ставится «из желания закрыть». Всё, что требует живого хоста/креда,
оценено консервативно и отправлено в `EVENING_LIVE_ACCEPTANCE.md`.

| Измерение | Before | After | Evidence | Remaining gap (real-host) |
|---|--:|--:|---|---|
| Core (канонический цикл authority) | 8 | 8 | bossman-core полный набор зелёный; perimeter на Stage 6 scopes; argv-only | — |
| AI Gateway (Stage 3) | 8 | 8 | облачная политика держится Gateway; failover не на 4xx; correlation | live cloud-провайдер вечером |
| Coding workflow (session/worktree/diff/review) | 5 | 8 | `coding_session.py` + `diff_aware_review`; 15 тестов (изоляция/diff/merge/conflict/discard/orphan/reviewer-negative) | живой полный цикл task→merge на реальной модели |
| Code intelligence (LSP) | 6 | 8 | capability negotiation + normalization + bounded/graceful; fake-LSP тесты (13+ ) | реальный pyright/gopls smoke (`SKIP_HOST` иначе) |
| Context retention | 7 | 8 | компакция/чекпоинт/durable facts; deterministic long-session тест в наборе | измеримый resume на реальной длинной сессии |
| Multi-agent / lineage | 8 | 8 | forks.py (checkpoint lineage) сохранён как авторитет; coding sessions расширяют, не дублируют | — |
| Local AI (Ollama, cloud_policy=never) | 7 | 7→8* | путь через Gateway готов; контракт `ollama.chat` | *8 после live Notepad-сценария с `CLOUD_CALLS=0` |
| Provider routing / Cost | 8 | 8 | Cost Governor authority; provider-путь единый | live cost-счёт вечером |
| Security (red team) | 8 | 8 | SSRF(resolve+redirect+pinned IP)/path/symlink/redaction/SQL-write-deny/unknown-cap-deny; тесты | live red-team на хосте |
| Memory | 8 | 8 | единый пул, аренды, secret-exclusion в поиске | — |
| Browser / Computer Operator | 7 | 7→8* | Stage 13 production path; approvals ужесточены; `browser.open` контракт | *8 после live навигации/Notepad |
| Plugins | 5 | 8 | 24 capability на существующей authority; **sql.read и obsidian.write — реальное исполнение**; http/monitor реальны; 54 теста | live github/gmail/telegram/mcp/n8n/drive с кредами |
| Reliability / recovery | 7 | 8 | durable JSON сессий; anti-replay; restart-recovery тесты | live restart-в-середине вечером |
| Observability | 7 | 8 | correlation id task/run/agent/model/provider/tools/approvals/cost; no-secret-logging | полный live-трейс вечером |
| UI/UX (Command Center) | 6 | 7 | health `/api/plugins` без внешних вызовов; degraded-состояния описаны | live operator path (Wave 5) вечером |
| Cost control | 8 | 8 | Cost Governor + admission | live лимиты вечером |
| Benchmark evidence (vs OpenCode) | 3 | 4 | `eval_scorecard.py` (summarize/compare) готов агрегировать | 10-задачный A/B на одной машине; без него — `NOT_TESTED` |

\* «7→8» = код и unit-доказательства на 8, но финальная production-оценка ждёт
живого хоста (вечер). До live не ставлю 8 «из желания закрыть».

## Сводно
- Все измерения, доказуемые unit/регрессией, подняты до **8** с evidence.
- Ни одно измерение не получило 9: production/E2E-доказательство — только вечером
  на реальном хосте (`EVENING_LIVE_ACCEPTANCE.md`).
- Benchmark честно остаётся низким до реального A/B — агрегатор готов, данных нет.
