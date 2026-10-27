"""Единый пакет ПК-оператора Argon (`bcc.os_agent`) — P0–P3/P5.

Объединяет фасад управления компьютером владельца, политику T0–T3, уровни автономности L0–L3,
STOP-контроллер (3 уровня), append-only журнал `audit.jsonl` (с цепочкой SHA-256 и якорем `.head`),
выбор локальной модели (Gemma 4 31B -> Qwen 3.8 27B -> Strata) и таблицу прав оператора (`PERMISSION_KEY_TABLE`).

Честная граница планировщика (D-003):
- Детерминированное ядро (`compile_plan_for_prompt`, `classify_pc_action`, `AuditJournal`, `PCAgentRuntime`):
  * классификация риска каждого шага (T0/T1/T2/T3) и проверка жёстких инвариантов безопасности (Т3 не переопределяется промптом);
  * распознавание типовых команд владельца (чтение/запись файлов, список окон, скриншот, запуск/фокус окна, локальные тесты, проверка здоровья) в воспроизводимый список шагов с критериями пост-проверки;
  * проверка результата фактом (`verified`: exit_code, существование и SHA-256 файла, заголовок окна, непустой PNG-скриншот);
  * ведение цепочки `audit.jsonl` + `.head` и мгновенное исполнение сигнала STOP.
- Локальная LLM (`gemma4-31b-uncensored-ctx32k:latest` / `gemma4-31b-uncensored:latest` через `call_local_qwen`):
  * построение и адаптация плана для нестандартных/свободных формулировок владельца, не покрытых детерминированными шаблонами;
  * диагностика ошибок шага (до 3 попыток самоисправления) и формулирование итогового отчёта владельцу на русском языке;
  * модель НИКОГДА не принимает финальное решение о разрешении T2/T3: каждый предложенный моделью шаг проходит через `classify_pc_action` и гейты подтверждения владельца.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..features.pc_agent import (
    AGENT_NAME,
    AGENT_ROLE,
    ARGON_DEFAULT_TOOLS,
    AUTONOMY_LEVEL_SPECS,
    DEFAULT_CMD_TIMEOUT_S,
    ETERNAL_PROHIBITIONS,
    LOCAL_MODELS,
    MANAGER_SYSTEM_PROMPT,
    MAX_CMD_TIMEOUT_S,
    MAX_OUTPUT_BYTES,
    MAX_READ_CHARS,
    MAX_REPAIR_ATTEMPTS,
    MAX_STEPS_PER_TASK,
    MAX_TRUST_MINUTES,
    PERMISSION_KEY_TABLE,
    PRIMARY_GEMMA_MODELS,
    PROFILE_NAME,
    RATE_LIMIT_PER_MINUTE,
    STOP_FLAG_PATH,
    STRATA_FALLBACK_MODELS,
    VISION_MODEL,
    AuditJournal,
    PCAgentRuntime,
    PolicyVerdict,
    PromptIntentReadOnly,
    call_local_qwen,
    check_hard_content_invariants,
    classify_bossman_api,
    classify_pc_action,
    compile_plan_for_prompt,
    default_safe_roots,
    ensure_argon_agent,
    execute_pc_step,
    get_runtime,
    is_bossman_api_token_allowed,
    is_immutable_core_target,
    is_protected_process,
    is_protected_service,
    is_t3_secret_or_protected_path,
    is_within_safe_roots,
    local_model_priority_key,
    normalize_bossman_api_path,
    normalize_operator_permissions,
    plan_pc_task_with_llm,
    scrub_secrets,
    select_preferred_local_model,
    start_pc_task,
)

AUTONOMY_LEVELS = AUTONOMY_LEVEL_SPECS
run_pc_agent_task = start_pc_task


@dataclass(frozen=True)
class OSAgentDescriptor:
    profile: str = PROFILE_NAME
    agent_name: str = AGENT_NAME
    agent_role: str = AGENT_ROLE
    primary_model: str = PRIMARY_GEMMA_MODELS[0]
    default_autonomy_level: str = "L1"
    runtime_autonomy_level: str = "L2"


PLANNER_ARCHITECTURE: dict[str, Any] = {
    "profile": PROFILE_NAME,
    "primary_local_model": PRIMARY_GEMMA_MODELS[0],
    "fallback_local_models": list(LOCAL_MODELS[1:]),
    "deterministic_responsibilities": [
        "T0/T1/T2/T3 policy classification (classify_pc_action) and T3 hard blocks",
        "Immutable core protection (is_immutable_core_target) and secret scrubbing (scrub_secrets)",
        "Fast-path step compilation for canonical file/window/screen/terminal/browser operations (compile_plan_for_prompt)",
        "Fact-based post-condition verification (file SHA-256, window title/handle, screenshot header, exit_code)",
        "Append-only SHA-256 hash chain + head anchor verification (AuditJournal)",
        "Three-level STOP enforcement (current step, task process tree, full agent pause)",
    ],
    "llm_responsibilities": [
        "Decomposing open-ended natural-language goals into candidate tool calls via local Gemma 4 31B",
        "Diagnosing step failures and proposing bounded repair steps (max 3 attempts)",
        "Synthesizing clear Russian-language status and completion summaries for the owner pult",
    ],
    "llm_cannot_bypass": [
        "Cannot downgrade T2/T3 verdicts or auto-approve owner-button actions",
        "Cannot disable STOP, watchdog, or AuditJournal",
        "Cannot access or print secrets/DPAPI/Vault keys",
        "Cannot call paid cloud models when local_only=True",
    ],
}


def describe_os_agent_architecture() -> dict[str, Any]:
    """Возвращает честное описание контура `bcc.os_agent` (детерминированное ядро vs локальная LLM Gemma 4 31B)."""
    return {
        **PLANNER_ARCHITECTURE,
        "autonomy_levels": list(AUTONOMY_LEVEL_SPECS.keys()),
        "permission_key_table": {k: list(v) for k, v in PERMISSION_KEY_TABLE.items()},
        "default_tools": list(ARGON_DEFAULT_TOOLS),
        "stop_flag_path": str(STOP_FLAG_PATH),
    }


__all__ = [
    "AGENT_NAME",
    "AGENT_ROLE",
    "ARGON_DEFAULT_TOOLS",
    "AUTONOMY_LEVELS",
    "AUTONOMY_LEVEL_SPECS",
    "AuditJournal",
    "DEFAULT_CMD_TIMEOUT_S",
    "ETERNAL_PROHIBITIONS",
    "LOCAL_MODELS",
    "MANAGER_SYSTEM_PROMPT",
    "MAX_CMD_TIMEOUT_S",
    "MAX_OUTPUT_BYTES",
    "MAX_READ_CHARS",
    "MAX_REPAIR_ATTEMPTS",
    "MAX_STEPS_PER_TASK",
    "MAX_TRUST_MINUTES",
    "OSAgentDescriptor",
    "PCAgentRuntime",
    "PERMISSION_KEY_TABLE",
    "PLANNER_ARCHITECTURE",
    "PRIMARY_GEMMA_MODELS",
    "PROFILE_NAME",
    "PolicyVerdict",
    "PromptIntentReadOnly",
    "RATE_LIMIT_PER_MINUTE",
    "STOP_FLAG_PATH",
    "STRATA_FALLBACK_MODELS",
    "VISION_MODEL",
    "call_local_qwen",
    "check_hard_content_invariants",
    "classify_bossman_api",
    "classify_pc_action",
    "compile_plan_for_prompt",
    "default_safe_roots",
    "describe_os_agent_architecture",
    "ensure_argon_agent",
    "execute_pc_step",
    "get_runtime",
    "is_bossman_api_token_allowed",
    "is_immutable_core_target",
    "is_protected_process",
    "is_protected_service",
    "is_t3_secret_or_protected_path",
    "is_within_safe_roots",
    "local_model_priority_key",
    "normalize_bossman_api_path",
    "normalize_operator_permissions",
    "plan_pc_task_with_llm",
    "run_pc_agent_task",
    "scrub_secrets",
    "select_preferred_local_model",
    "start_pc_task",
]
