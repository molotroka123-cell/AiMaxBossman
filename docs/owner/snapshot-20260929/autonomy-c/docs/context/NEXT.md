# NEXT — исполняемые шаги

> **BOSSMAN V1 FROZEN** (Release Freeze: 2026-08-30)
> V1 is complete and frozen. Future development begins with **LLM ARCHITECTURE V2**.

---

# LLM ARCHITECTURE V2

## Initial Planned Pillars

1. **Working Memory**: Task-scoped active state, step trackers, invariants, and versioned checkpointing with optimistic concurrency.
2. **Context Compiler**: Priority-budgeted multi-channel context assembly (P0 Security Invariants → P1 Objectives → P2 Working State → P3 Decisions → P4 Failures → P5 Observations → P6 Background Evidence) with token clipping and deduplication.
3. **Executable Task DAG**: Dependency graph execution with parallel branches, barrier sync, and rollback semantics.
4. **Adaptive Reasoning Levels**: Dynamic depth selection based on task complexity, confidence score, and capability classification.
5. **Task-Specific Model Routing**: Routing planner, coder, critic, extractor, and fast classifier queries to specialized local vs cloud models.
6. **Planner / Coder / Critic / Verifier Separation**: Strict modular roles with discrete input/output validation contracts.
7. **Confidence-Based Escalation**: Automated escalation ladder (Local Fast → Local Smart/Coder → Cloud/Human Approval) triggered on low verification confidence or policy constraints.
8. **Multi-Candidate Generation + Judge**: Parallel generation of alternative solutions with deterministic and LLM judging against rubric criteria.
9. **Execution-Grounded Verification**: Runtime proofs and test-backed validation loops before transition to DONE state.
10. **Self-Repair Policy**: Bounded retry and repair loops with negative memory / failure pattern recall before re-attempting failed steps.
11. **Model Capability Registry**: Dynamic registration and validation of local/cloud model features (tools, vision, reasoning, context windows, cost parameters).
12. **Real-World Model Scorecards**: Empirical benchmarks tracking real-world speed, cost, error rates, and task completion accuracy.
13. **Speculative Local-First Execution**: Opportunistic local execution with fallback on threshold breach.
14. **Conditional Multi-Model Debate**: Multi-agent consensus for high-stakes architectural or sensitive actions.
15. **Tool-Aware Reasoning**: Fine-grained schema compression, tool capability filtering, and structured output adherence.
16. **Structured Inter-Agent Contracts**: Typed envelope schemas with immutable provenance, correlation tokens, and anti-replay integrity.

---

## 1. PRE-DISPATCH АУДИТ ВЛАДЕЛЬЦА (post-freeze)
Stage 13 Dispatch НЕ начат намеренно. Нужен отдельный аудит/одобрение владельца
по FINAL_HARDENING_STATUS.md. Проверить особенно: branch protection (required
checks) и политику Tailscale (наружу только /remote).

## 2. runsc / MicroVM на живом хосте  (БЛОКЕР: железо)
Раннер без runsc/KVM: сильные рантаймы Stage 8 протестированы только по пути
ОТКАЗА (fail closed). На Ai Max (Linux+KVM): установить gVisor / обеспечить
/dev/kvm, прогнать `tests/test_sandbox_strong_runtimes.py`, затем реальную
задачу в DEVELOPER/HOSTILE (должны ИСПОЛНЯТЬСЯ), проверить egress-барьер.

## 3. LOCAL-LIVE dev-factory
LLMPlanner+GatewayEditor подключены, но живьём (реальный Gateway+модель, реальная
правка+тест в песочнице) не гонялись. Прогнать одну задачу end-to-end на хосте с
живым Gateway; убедиться, что патч собирается и НЕ публикуется автоматически.

## 4. Периодический red-team (постоянная практика)
После каждого крупного изменения повторять атаки, а не доверять зелёным тестам
(см. FAIL-001 в FAILURES.md). Новые цели: обход scope-гейта, WS-подписка без
events, containment AI Lab (traversal/symlink), argv-only (нет shell-инъекции в
gitops/media/shell), editor (побег из рабочей копии).

## Команды проверки
```
cd bossman-core && python -m pytest -q --timeout=180 --timeout-method=thread   # 589 passed, 2 skipped
cd command-center && python -m pytest -q --timeout=180 --timeout-method=thread  # 430 passed, 2 skipped
python tools/ci_secret_scan.py                                                  # PASS
```

## 5. Два command-center теста зависают на GitHub-раннере (открытый баг)
`tests/test_discovery.py::test_open_port_that_stays_silent_is_not_called_absent`
и `tests/test_v21_failure_injection.py::test_provider_failure_retries_are_bounded_and_status_is_honest`
зависают >180с ТОЛЬКО на GitHub-раннере (signal-таймаут их называет), локально
идут за ~2.5с и проходят. Оба на FakeAdapter — сеть ни при чём; зависает
teardown asyncio/движка BCC под окружением раннера. Пока помечены
`BCC_CI_SKIP_RUNNER_HANGS=1` в CI (локально/на железе гоняются). Воспроизвести
на self-hosted раннере, добавить bounded-timeout в движок/фикстуру `env`,
снять флаг. НЕ трогать продовый discovery.py без воспроизведения — 429 тестов
сейчас зелёные.
