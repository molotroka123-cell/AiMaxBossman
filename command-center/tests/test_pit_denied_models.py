"""liquid/lfm-* is out of the Jeff stack for good: it cannot be configured, and an old config that lists it still starts without it."""
from __future__ import annotations

import dataclasses
import json

import pytest

from bcc.pit import config as pcfg

from .test_pit_runtime import make_settings


@pytest.mark.parametrize("model", ["liquid/lfm-2.5-2.6b:free", "Liquid/LFM-3", " liquid/lfm-2.5-1.2b-instruct:free "])
def test_a_denied_model_is_recognised(model):
    assert pcfg.is_denied_model(model)


@pytest.mark.parametrize("model", ["nvidia/nemotron-3-ultra-550b-a55b:free", "bossman-community-qwen-uncensored:latest",
                                   "google/gemma-4-26b-a4b-it:free", "", None, 5])
def test_other_models_are_not_denied(model):
    assert not pcfg.is_denied_model(model)


def test_settings_refuse_a_denied_chat_or_local_model(tmp_path):
    base = make_settings(tmp_path)
    with pytest.raises(ValueError, match="denied"):
        dataclasses.replace(base, chat_models=("liquid/lfm-2.5-2.6b:free", "free/model:free"))
    with pytest.raises(ValueError, match="denied"):
        dataclasses.replace(base, chat_models=(), local_url="http://127.0.0.1:11434/v1",
                            local_models=("liquid/lfm-2.5-2.6b",), local_chat_only=True)
    assert dataclasses.replace(base, chat_models=("nvidia/nemotron-3-ultra-550b-a55b:free",)).chat_models


def test_an_old_config_that_lists_lfm_loads_without_it(tmp_path, monkeypatch):
    for name in ("BOSSMAN_PIT_BOT_TOKEN", "BOSSMAN_PIT_PROVIDER_KEY", "BOSSMAN_PIT_CORE_TOKEN", "BOSSMAN_PIT_VISION_TOKEN"):
        monkeypatch.setenv(name, "x" * 12)
    monkeypatch.setattr(pcfg, "_read_credentials", lambda _home: {"identity_salt": "ab" * 32})
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "data_dir": str(tmp_path), "people": [{"user_id": 1, "chat_id": 1, "role": "owner"}],
        "chat_models": ["liquid/lfm-2.5-2.6b:free", "nvidia/nemotron-3-ultra-550b-a55b:free"],
        "provider_base_url": "https://openrouter.ai/api/v1"}), encoding="utf-8")
    settings = pcfg.load(cfg)
    assert settings.chat_models == ("nvidia/nemotron-3-ultra-550b-a55b:free",)
    only_lfm = tmp_path / "only.json"
    only_lfm.write_text(cfg.read_text(encoding="utf-8").replace('"nvidia/nemotron-3-ultra-550b-a55b:free"', '"liquid/lfm-3"'),
                        encoding="utf-8")
    # Newer owner policy (model_policy, 2026-10-01): nothing left -> the default free routes, LFM recorded as rejected.
    fallback = pcfg.load(only_lfm)
    assert fallback.chat_models == tuple(pcfg.DEFAULT_FREE_CHAT_MODELS)
    assert not any(pcfg.is_denied_model(m) for m in fallback.chat_models)
