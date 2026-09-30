# 02 — Architecture and security / Архитектура и безопасность

Status: design is fixed by the user constitution (A14 in 07); the autonomy control plane is **IN_PROGRESS** (G1).
Everything marked "planned" is not yet running.

## 1. Control flow (EN)

```text
User goals (constitution, pinned SHA-256)
   |
Jev (TypeScript/Python client, OpenRouter reasoning + web search)      <- existing (bcc/jev)
   |  proposes one bounded Goal: problem, acceptance tests, budget, risk tier, target + protected metrics
   v
Bossman control plane  (planned: bcc/autonomy/)
   |- policy.check()      -> allowed / refused / needs_user
   |- lease               -> exactly ONE writer at a time, orphan process groups killed
   |- hands (HandBroker)  -> the only privileged path: terminal, files, browser/desktop, git worktrees, services
   |- journal             -> hash-chained, append-only, secret-redacted
   v
Writer: Claude CLI  OR  Codex CLI (official CLIs, subscription login, child env without secrets)  <- existing (bcc/rave)
   |  commits in an isolated worktree, exits
   v
Deterministic tests + security suites
   v
Review by the OTHER CLI, then by the first: both APPROVE the same immutable SHA + diff SHA-256
   |  any change -> both approvals invalidated
   v
Staging (separate port, temp data dir, never owner data) + metrics gate (target up, no protected metric down)
   v
User release gate: Apply / Reject / Revise  (release tiers below)
   v
Monitor -> automatic rollback on regression -> journal -> next goal
```

Claude/Codex never get direct hands: they emit a structured `HandRequest`
(`goal_id, requested_by, action, target, arguments, expected_evidence, risk_class`), Bossman validates it against the
policy, executes, and returns a `HandResult` with timestamps and artifact hashes (`docs/autonomy/AUTONOMY_CONTRACT.md`).

## 2. Конституция (RU, кратко)

Файл `docs/constitution/BOSSMAN_CONSTITUTION.md` меняет только пользователь; его SHA-256 закрепляется командой
`bossman autonomy constitution pin` вне репозитория. Несовпадение → цикл в `BLOCKED`.

- Всегда решение пользователя: деньги, подписки, **заявки на финансирование**, сообщения третьим лицам, ключи,
  изменение конституции/гейта, удаление важных данных, отключение защит, рискованные деплои, новый способ заработка.
- Ни одно изменение не применяется без одобрения Claude **и** Codex для одного и того же неизменяемого SHA.
- Уровни автономии L0–L4; система стартует не выше L2; повышение — после 25 успешных циклов и явного «да» пользователя.
- Правило качества: целевая метрика выросла, ни одна защищённая (успех, задержка, стоимость, стабильность роли,
  память, отказы/галлюцинации, red-team, CPU/RAM/диск, откаты) не упала сверх порога.

| Release tier | Gate |
|---|---|
| Docs, tests | auto after tests + both approvals (from L3) |
| Prompts, model settings | both approvals + user |
| Memory, keys, Telegram, system services | always user |
| Critical runtime | staging + both approvals + manual user release |

## 3. Local-first

- Default execution is on the owner's machine (Ryzen AI Max+ 395, 128 GB unified memory; 07 A7). Owner data stays in
  `%LOCALAPPDATA%\Bossman\CommandCenter`; staging never uses it.
- Local models (Ollama; e.g. `bossman-fast-qwen36-35b-a3b-q5`, GPT-OSS-120B) do private, repetitive, classification,
  summarisation and recovery work. Measured limit: they do not make release decisions and lessons did not change their
  outcomes (07 A8, A9).
- Jeff 2.0 media/vision is local only; memory is per participant and consent-gated (`docs/pit/JEFF_2_0_*.md`).
- Cloud GPU from credits (planned) runs as an **isolated benchmark node** with no production credentials, reached through
  an OpenAI-compatible endpoint, shut down after each window (03, 04).

## 4. Free-model policy / Политика моделей

| Order | Model class | Role | Spend |
|---|---|---|---|
| 1 | Local (Ollama) | private prep, triage, recovery | $0 |
| 2 | Free cloud (OpenRouter `:free`, e.g. Nemotron) | default planner **while it passes role/quality tests** | $0; "free" verified against the live price, unknown price is not treated as free |
| 3 | Low-cost paid (e.g. GLM Flash, DeepSeek API) | hard tasks, tie-break opinion only | hard daily $ cap, fail-closed budget gate |
| — | Claude CLI, Codex CLI | engineering workers + mandatory independent reviewers | owner's subscriptions only; no API keys, no browser-session extraction |

A DeepSeek (or any third model) opinion can never replace either Claude or Codex approval.
The 24/7 learning loop must run without Claude (07 A11: 0 Claude calls).

## 5. Security controls (existing vs planned)

| Control | State | Evidence |
|---|---|---|
| Approval not trusted from model-supplied fields; anti-replay | existing, VERIFIED | 07 A5 |
| Fail-closed budget/cost gates; unknown price ≠ free | existing, IMPLEMENTED | scorecard Treasury axis |
| One backend per data dir (`backend_lock`) | existing | 07 A1 (PR #84) |
| Jeff safety module: injection, prompt-extraction, rate limits, leak check | existing, tests | `docs/pit/JEFF_2_0_SAFETY.md` |
| Web/search results are untrusted data, never instructions or memory | existing (Jeff research) + planned (autonomy) | `docs/pit/JEFF_2_0_RESEARCH.md` |
| Engineering lease, HandBroker, hash-chained journal, staging runner | **planned** | G1 |
| Benchmark node isolation, auto-shutdown, credit-expiry kill switch | **planned** | 04 §4 |

## 6. English summary for applications

Bossman is a local-first autonomous software-engineering control plane. A planner (Jev) proposes bounded goals; two
independent commercial coding agents (Claude Code CLI and OpenAI Codex CLI) alternate as writer and reviewer in isolated
git worktrees; nothing is applied without both approving the same immutable commit hash, passing deterministic tests,
staging and a metrics gate; money, external communication, credentials and production release are always gated by the
human owner through a pinned, user-owned constitution. Large open-weight models (DeepSeek-V3.2 class, ~690 GB) are the
target for a private in-house reviewer/planner, which is why 700+ GB of coherent memory is requested.
