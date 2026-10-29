"""Tests for the unified Bossman AI Gateway (`bcc.security.ai_gateway` & `GovernedAdapter`).

Verifies all 5 owner requirements:
1. Request sensitivity classification:
   - PUBLIC (`public`): allowed on any permitted provider (`local`, `trusted`, `experimental`).
   - INTERNAL (`internal`): allowed ONLY on `local` or `trusted` providers after local scrubbing & context minification; blocked on `experimental` and `unknown`.
   - CONFIDENTIAL (`confidential`): personal memory, personal documents, business/finance secrets, API keys/passwords/seed phrases, private repo code -> local model ONLY; external calls forbidden (rerouted to local fallback or blocked fail-closed).
2. Local text scrubbing & reversible pseudonymization (`[КЛИЕНТ_1]`, `[СЧЕТ_1]`, `[ПРОЕКТ_1]`, `[ВНУТР_АДРЕС_1]`, etc.):
   - Strips API keys, passwords, seed phrases, PII, bank accounts, internal IPs/paths, and project names.
   - Rehydrates non-secret placeholders locally in the model's response while NEVER sending raw values or secrets to cloud providers.
3. Minimal context packaging:
   - Strips `[MEMORY CONTEXT...]`, `_local_only` messages, raw tool outputs, and trims long chat histories before cloud dispatch.
4. Provider trust policy & no direct bypass:
   - `local` vs `trusted` vs `experimental` vs `unknown` (blocked until explicitly allowed by owner).
   - `GovernedAdapter` enforces the AI Gateway on all provider calls.
5. Metadata-only local audit logging:
   - Logs provider, model, timestamp, token volume, data categories, and triggered rules without logging raw prompt or response text by default.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bcc.features.ai_gateway import router as ai_gateway_router
from bcc.provider_governance import GovernedAdapter
from bcc.providers import ChatResult
from bcc.security.ai_gateway import (
    AIGateway,
    ContextMinifier,
    DataSensitivity,
    GatewayAuditLogger,
    GatewayBlockedError,
    LocalScrubber,
    ProviderPolicyRegistry,
    ProviderTrustTier,
    RedactionSession,
    reset_ai_gateway,
)


def _fake_openai_key() -> str:
    # Constructed with low-entropy repetition so static entropy scanners do not trip on test fixtures
    return "sk-" + "proj-" + ("a1b2" * 6)


def _fake_google_key() -> str:
    return "AIza" + ("b2c3" * 6)


def _fake_github_pat() -> str:
    return "ghp_" + ("d4e5" * 6)


def _fake_seed_phrase() -> str:
    words = [
        "abandon", "ability", "able", "about", "above", "absent",
        "absorb", "abstract", "absurd", "abuse", "access", "accident",
    ]
    return " ".join(words)


class RecordingAdapter:
    """Test adapter that records the exact model, messages, and kwargs sent to it."""

    def __init__(self, reply_fn=None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._reply_fn = reply_fn

    async def chat(self, model_arg: str, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        self.calls.append({"model": model_arg, "messages": messages, "kwargs": kwargs})
        if self._reply_fn is not None:
            return self._reply_fn(model_arg, messages, kwargs)
        last_content = messages[-1].get("content", "") if messages else ""
        return ChatResult(
            text=f"Ответ по запросу: {last_content}",
            tokens_in=42,
            tokens_out=18,
        )


# ---------------------------------------------------------------------------
# 1. Sensitivity Classification & Local Scrubbing Tests
# ---------------------------------------------------------------------------


def test_scrubber_detects_and_removes_secrets_keys_and_seed_phrases():
    scrubber = LocalScrubber()
    session = RedactionSession()
    openai_key = _fake_openai_key()
    google_key = _fake_google_key()
    gh_pat = _fake_github_pat()
    seed = _fake_seed_phrase()

    raw_text = (
        f"Проверь конфиг: OPENAI_API_KEY={openai_key}, "
        f"google={google_key}, github={gh_pat}, "
        f"password: MySuperSecretPass123! "
        f"и моя мнемоническая фраза: {seed}"
    )

    scrubbed = scrubber.scrub_text(raw_text, session)

    assert openai_key not in scrubbed
    assert google_key not in scrubbed
    assert gh_pat not in scrubbed
    assert "MySuperSecretPass123!" not in scrubbed
    assert seed not in scrubbed
    assert session.unscrubbable_secrets_found is True
    assert "secrets" in session.data_categories
    assert "seed_phrase" in session.data_categories
    # Secrets must NOT be stored in reversible placeholder_to_original map
    for original_val in session.placeholder_to_original.values():
        assert openai_key not in original_val
        assert seed not in original_val


def test_scrubber_pseudonymizes_clients_accounts_ips_paths_projects_and_rehydrates():
    scrubber = LocalScrubber(project_names=("ProjectKronos", "SwapMeVIP"))
    session = RedactionSession()
    raw_text = (
        "Клиент ООО «АльфаТех» (контакт Иванов Сергей Петрович, email: director@alfatech.ru, "
        "тел +7 (999) 123-45-67) просит сверить счёт 40702810900000012345 "
        "на внутреннем сервере 192.168.10.45 (путь C:\\Users\\asd\\Projects\\secret_spec.docx) "
        "по проекту ProjectKronos."
    )

    scrubbed = scrubber.scrub_text(raw_text, session)

    # None of the raw private identifiers may remain in the scrubbed text
    assert "АльфаТех" not in scrubbed
    assert "Иванов Сергей Петрович" not in scrubbed
    assert "director@alfatech.ru" not in scrubbed
    assert "123-45-67" not in scrubbed
    assert "40702810900000012345" not in scrubbed
    assert "192.168.10.45" not in scrubbed
    assert "C:\\Users\\asd\\Projects\\secret_spec.docx" not in scrubbed
    assert "ProjectKronos" not in scrubbed

    # Placeholders must be present
    assert "[КЛИЕНТ_1]" in scrubbed
    assert "[КЛИЕНТ_2]" in scrubbed
    assert "[EMAIL_1]" in scrubbed
    assert "[ТЕЛЕФОН_1]" in scrubbed
    assert "[СЧЕТ_1]" in scrubbed
    assert "[ВНУТР_АДРЕС_1]" in scrubbed
    assert "[ПУТЬ_1]" in scrubbed
    assert "[ПРОЕКТ_1]" in scrubbed

    # Simulated model response using placeholders is rehydrated locally
    simulated_cloud_reply = (
        "Для [КЛИЕНТ_1] ([КЛИЕНТ_2]) по проекту [ПРОЕКТ_1] проверен счёт [СЧЕТ_1] и узел [ВНУТР_АДРЕС_1]."
    )
    rehydrated = session.rehydrate(simulated_cloud_reply)
    assert "ООО «АльфаТех»" in rehydrated
    assert "Иванов Сергей Петрович" in rehydrated
    assert "ProjectKronos" in rehydrated
    assert "40702810900000012345" in rehydrated
    assert "192.168.10.45" in rehydrated


def test_gateway_sensitivity_classification_levels(tmp_path: Path):
    gw = AIGateway(audit=GatewayAuditLogger(log_path=tmp_path / "gw.jsonl"))
    trusted_provider = {
        "id": 1,
        "kind": "openai_compat",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
    }
    cloud_model = {
        "name": "qwen/qwen3-coder:free",
        "kind": "cloud",
        "pricing_known": True,
        "price_in": 0.0,
        "price_out": 0.0,
    }

    # 1. Public neutral query -> PUBLIC
    pub_insp = gw.classify_and_prepare(
        trusted_provider,
        cloud_model,
        [{"role": "user", "content": "Объясни алгоритм быстрой сортировки на Python"}],
    )
    assert pub_insp.sensitivity == DataSensitivity.PUBLIC
    assert pub_insp.allowed is True
    assert pub_insp.decision == "allow_cloud_sanitized"

    # 2. Internal query with client/project/IP -> INTERNAL (allowed on trusted after scrubbing)
    int_insp = gw.classify_and_prepare(
        trusted_provider,
        cloud_model,
        [
            {
                "role": "user",
                "content": "Сделай шаблон письма для ООО «Вектор» по проекту ProjectKronos с IP 10.20.30.40",
            }
        ],
    )
    assert int_insp.sensitivity == DataSensitivity.INTERNAL
    assert int_insp.allowed is True
    assert "Вектор" not in int_insp.sanitized_messages[-1]["content"]
    assert "ProjectKronos" not in int_insp.sanitized_messages[-1]["content"]
    assert "10.20.30.40" not in int_insp.sanitized_messages[-1]["content"]
    assert "[КЛИЕНТ_1]" in int_insp.sanitized_messages[-1]["content"]
    assert "[ПРОЕКТ_1]" in int_insp.sanitized_messages[-1]["content"]
    assert "[ВНУТР_АДРЕС_1]" in int_insp.sanitized_messages[-1]["content"]

    # 3. Confidential queries (keys, seed phrases, personal memory, finance domain, private repo files) -> CONFIDENTIAL
    conf_cases = [
        ([{"role": "user", "content": f"Мой ключ {_fake_openai_key()}, проверь его"}], {}),
        ([{"role": "user", "content": f"Seed фраза кошелька: {_fake_seed_phrase()}"}], {}),
        ([{"role": "user", "content": "Прочитай наш файл .env.production и id_ed25519"}], {}),
        ([{"role": "user", "content": "Сверь данные из личной памяти"}], {"domain": "personal_memory"}),
        ([{"role": "user", "content": "Финансовый отчёт и ключи бизнеса"}], {"domain": "finance"}),
        ([{"role": "user", "content": "Код из закрытого репозитория"}], {"domain": "private_repo"}),
    ]
    for msgs, meta in conf_cases:
        insp = gw.classify_and_prepare(
            trusted_provider,
            cloud_model,
            msgs,
            context_meta=meta,
        )
        assert insp.sensitivity == DataSensitivity.CONFIDENTIAL
        assert insp.allowed is False
        assert insp.decision == "blocked"


# ---------------------------------------------------------------------------
# 2. Minimal Context Packaging Tests
# ---------------------------------------------------------------------------


def test_context_minifier_strips_memory_local_only_tool_dumps_and_trims_history():
    minifier = ContextMinifier(max_history_messages=8, max_tool_output_chars=200)
    session = RedactionSession()
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "[MEMORY CONTEXT — verified Bossman knowledge]\nСекретная память владельца: сделка на 5 млн.",
        },
        {"role": "system", "content": "Ты краткий ассистент."},
        {"role": "system", "_local_only": True, "content": "Внутренняя заметка только для локальной модели"},
        {"role": "tool", "content": "Огромный сырой дамп инструмента " * 200},
    ]
    # Add 14 chat turns (exceeding max_history_messages=8)
    for i in range(14):
        messages.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"Сообщение истории #{i}"})

    minified = minifier.pack_minimal_context(
        messages,
        session,
        include_tool_results=False,
        max_history_messages=8,
    )

    all_text = "\n".join(str(m.get("content", "")) for m in minified)
    assert "Секретная память владельца" not in all_text
    assert "Внутренняя заметка только для локальной модели" not in all_text
    assert "Огромный сырой дамп инструмента" not in all_text
    assert "context:stripped_memory_block" in session.triggered_rules
    assert "context:stripped_local_only_message" in session.triggered_rules
    assert "context:stripped_tool_output" in session.triggered_rules
    assert "context:trimmed_chat_history" in session.triggered_rules
    # 1 system message + at most 8 history messages
    non_system = [m for m in minified if m.get("role") != "system"]
    assert len(non_system) == 8
    assert non_system[-1]["content"] == "Сообщение истории #13"


# ---------------------------------------------------------------------------
# 3. Provider Trust Policy & Routing Matrix Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_blocks_unknown_provider_until_owner_explicitly_allows(tmp_path: Path):
    gw = AIGateway(audit=GatewayAuditLogger(log_path=tmp_path / "gw.jsonl"))
    unknown_provider = {
        "kind": "openai_compat",
        "name": "ShadyLLM",
        "base_url": "https://api.shady-untrusted-llm.example.com/v1",
    }
    model_dict = {"name": "shady-v1", "kind": "cloud", "pricing_known": True, "price_in": 0.0, "price_out": 0.0}
    cloud_adapter = RecordingAdapter()

    # Unknown provider must be blocked fail-closed even on a public prompt
    with pytest.raises(GatewayBlockedError) as exc_info:
        await gw.execute_chat(
            cloud_adapter.chat,
            unknown_provider,
            model_dict,
            "shady-v1",
            [{"role": "user", "content": "Привет, сколько будет 2+2?"}],
        )
    assert exc_info.value.provider_tier == ProviderTrustTier.UNKNOWN.value
    assert exc_info.value.reason_code == "unknown_provider_blocked"
    assert len(cloud_adapter.calls) == 0

    # Owner explicitly allows the provider as 'experimental'
    gw.policy.allow_provider("api.shady-untrusted-llm.example.com", tier=ProviderTrustTier.EXPERIMENTAL)

    # Public prompt now succeeds on experimental provider
    res = await gw.execute_chat(
        cloud_adapter.chat,
        unknown_provider,
        model_dict,
        "shady-v1",
        [{"role": "user", "content": "Привет, сколько будет 2+2?"}],
    )
    assert "Ответ по запросу" in res.text
    assert len(cloud_adapter.calls) == 1

    # Internal prompt with client/IP is STILL blocked on experimental provider!
    with pytest.raises(GatewayBlockedError) as exc_internal:
        await gw.execute_chat(
            cloud_adapter.chat,
            unknown_provider,
            model_dict,
            "shady-v1",
            [{"role": "user", "content": "Проверь сервер 192.168.1.50 для клиента ООО «Ромашка»"}],
        )
    assert exc_internal.value.sensitivity == DataSensitivity.INTERNAL.value
    assert exc_internal.value.provider_tier == ProviderTrustTier.EXPERIMENTAL.value
    assert exc_internal.value.reason_code == "experimental_provider_requires_public_synthetic"
    assert len(cloud_adapter.calls) == 1  # no second call made

    # Owner revokes provider -> blocked again even for public prompts
    gw.policy.revoke_provider("api.shady-untrusted-llm.example.com")
    with pytest.raises(GatewayBlockedError):
        await gw.execute_chat(
            cloud_adapter.chat,
            unknown_provider,
            model_dict,
            "shady-v1",
            [{"role": "user", "content": "Привет, сколько будет 2+2?"}],
        )


@pytest.mark.asyncio
async def test_confidential_request_reroutes_to_local_fallback_and_never_touches_cloud(tmp_path: Path):
    gw = AIGateway(audit=GatewayAuditLogger(log_path=tmp_path / "gw.jsonl"))
    trusted_cloud = {
        "id": 1,
        "kind": "openai_compat",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
    }
    cloud_model = {"name": "qwen/qwen3-coder:free", "kind": "cloud", "pricing_known": True, "price_in": 0.0, "price_out": 0.0}
    cloud_adapter = RecordingAdapter()
    local_adapter = RecordingAdapter(
        reply_fn=lambda m_arg, msgs, kw: ChatResult(
            text=f"Локальный ответ ({m_arg}): обработано {len(msgs)} сообщ.",
            tokens_in=15,
            tokens_out=10,
        )
    )

    secret_key = _fake_openai_key()
    res = await gw.execute_chat(
        cloud_adapter.chat,
        trusted_cloud,
        cloud_model,
        "qwen/qwen3-coder:free",
        [{"role": "user", "content": f"Помоги ротировать наш ключ {secret_key} в личном сейфе"}],
        local_fallback_adapter=local_adapter,
        local_fallback_model="gemma4:31b",
    )

    # Cloud adapter must receive ZERO calls
    assert len(cloud_adapter.calls) == 0
    # Local fallback adapter handled the confidential request locally
    assert len(local_adapter.calls) == 1
    assert "Локальный ответ (gemma4:31b)" in res.text
    latest_audit = gw.audit.recent(limit=1)[0]
    assert latest_audit["decision"] == "rerouted_to_local"
    assert latest_audit["sensitivity"] == "confidential"


@pytest.mark.asyncio
async def test_governed_adapter_end_to_end_scrubbing_and_rehydration(tmp_path: Path):
    gw = reset_ai_gateway(AIGateway(audit=GatewayAuditLogger(log_path=tmp_path / "gw.jsonl")))
    trusted_cloud = {
        "id": 1,
        "kind": "openai_compat",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
    }
    cloud_model = {
        "name": "qwen/qwen3-coder:free",
        "kind": "cloud",
        "pricing_known": True,
        "price_in": 0.0,
        "price_out": 0.0,
    }
    raw_cloud = RecordingAdapter(
        reply_fn=lambda m_arg, msgs, kw: ChatResult(
            text=f"Подготовлен акт сверки для {msgs[-1]['content']}",
            tokens_in=30,
            tokens_out=20,
        )
    )
    governed = GovernedAdapter(raw_cloud, trusted_cloud, cloud_model)

    user_prompt = "Сформируй акт для ООО «ТехноЛидер» по счёту 40702810400000098765"
    result = await governed.chat(
        "qwen/qwen3-coder:free",
        [{"role": "user", "content": user_prompt}],
    )

    # Verify what the cloud actually received: ONLY placeholders, zero raw client/account data!
    assert len(raw_cloud.calls) == 1
    sent_to_cloud = raw_cloud.calls[0]["messages"][-1]["content"]
    assert "ТехноЛидер" not in sent_to_cloud
    assert "40702810400000098765" not in sent_to_cloud
    assert "[КЛИЕНТ_1]" in sent_to_cloud
    assert "[СЧЕТ_1]" in sent_to_cloud

    # Verify that the caller locally receives the rehydrated text with original names restored
    assert "ООО «ТехноЛидер»" in result.text
    assert "40702810400000098765" in result.text
    assert len(gw.audit.recent(limit=5)) == 1


# ---------------------------------------------------------------------------
# 4. Metadata-Only Audit Logging Tests (No Raw Text in Logs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_records_metadata_only_and_never_logs_prompt_or_response_text(tmp_path: Path):
    log_file = tmp_path / "ai_gateway_audit.jsonl"
    gw = AIGateway(audit=GatewayAuditLogger(log_path=log_file))
    trusted_cloud = {
        "id": 1,
        "kind": "openai_compat",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
    }
    cloud_model = {"name": "qwen/qwen3-coder:free", "kind": "cloud", "pricing_known": True, "price_in": 0.0, "price_out": 0.0}
    cloud_adapter = RecordingAdapter()

    sensitive_client = "ООО «СекретныйКлиент777»"
    sensitive_account = "40702810111122223333"
    await gw.execute_chat(
        cloud_adapter.chat,
        trusted_cloud,
        cloud_model,
        "qwen/qwen3-coder:free",
        [{"role": "user", "content": f"Сверка для {sensitive_client} счет {sensitive_account}"}],
    )

    assert log_file.is_file()
    raw_log_text = log_file.read_text(encoding="utf-8")

    # Raw prompt text, client name, account number, and response text must NEVER appear in the log file
    assert sensitive_client not in raw_log_text
    assert sensitive_account not in raw_log_text
    assert "Ответ по запросу" not in raw_log_text
    assert "Сверка для" not in raw_log_text

    entries = gw.audit.recent(limit=10)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["provider"] == "OpenRouter"
    assert entry["provider_trust_tier"] == "trusted"
    assert entry["model"] == "qwen/qwen3-coder:free"
    assert entry["tokens_in"] == 42
    assert entry["tokens_out"] == 18
    assert entry["sensitivity"] == "internal"
    assert "pii_client" in entry["data_categories"]
    assert "finance_accounts" in entry["data_categories"]
    assert "redact:client_entity" in entry["triggered_rules"]
    assert "redact:bank_account" in entry["triggered_rules"]
    assert entry["decision"] == "allow_cloud_sanitized"
    assert entry["raw_text_logged"] is False
    assert len(entry["prompt_sha256"]) == 64


# ---------------------------------------------------------------------------
# 5. FastAPI Feature Endpoints (`/ai-gateway/*`) Tests
# ---------------------------------------------------------------------------


def test_ai_gateway_fastapi_endpoints(tmp_path: Path):
    reset_ai_gateway(AIGateway(audit=GatewayAuditLogger(log_path=tmp_path / "gw.jsonl")))
    app = FastAPI()
    app.include_router(ai_gateway_router)
    client = TestClient(app)

    # 1. GET /ai-gateway/status
    r_status = client.get("/ai-gateway/status")
    assert r_status.status_code == 200
    status_data = r_status.json()
    assert status_data["status"] == "active"
    assert status_data["log_raw_text_default"] is False
    assert status_data["defaults"]["unknown_provider_policy"] == "blocked_until_owner_approves"

    # 2. POST /ai-gateway/inspect (dry-run classification & scrubbing preview)
    r_inspect = client.post(
        "/ai-gateway/inspect",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Клиент ООО «Орион» просит проверить сервер 10.0.5.12 по проекту ProjectKronos",
                }
            ],
            "provider": {
                "id": 1,
                "kind": "openai_compat",
                "name": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
            },
            "model": {"name": "qwen/qwen3-coder:free", "kind": "cloud"},
        },
    )
    assert r_inspect.status_code == 200
    insp = r_inspect.json()
    assert insp["sensitivity"] == "internal"
    assert insp["allowed"] is True
    preview_text = insp["sanitized_messages"][-1]["content"]
    assert "Орион" not in preview_text
    assert "10.0.5.12" not in preview_text
    assert "ProjectKronos" not in preview_text
    assert "[КЛИЕНТ_1]" in preview_text
    assert "[ВНУТР_АДРЕС_1]" in preview_text
    assert "[ПРОЕКТ_1]" in preview_text

    # 3. Allow & revoke provider via API
    r_allow = client.post(
        "/ai-gateway/policy/allow",
        json={"host": "api.custom-partner.example.com", "tier": "trusted"},
    )
    assert r_allow.status_code == 200
    assert "api.custom-partner.example.com" in r_allow.json()["policy"]["trusted_hosts"]

    r_revoke = client.post(
        "/ai-gateway/policy/revoke",
        json={"host": "api.custom-partner.example.com"},
    )
    assert r_revoke.status_code == 200
    assert "api.custom-partner.example.com" in r_revoke.json()["policy"]["revoked_hosts"]
