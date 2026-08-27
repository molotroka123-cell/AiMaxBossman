"""Агент = папка: загрузка, права инструментов, политика облака."""
import shutil
from pathlib import Path

import pytest

from bossman.agents import load_agent, load_all, set_cloud_policy
from bossman.llm import CloudDenied, NeedsCloudApproval, chat, is_cloud

AGENTS_DIR = Path(__file__).parent.parent / "agents"


def test_all_bundled_agents_load():
    agents = load_all(AGENTS_DIR)
    assert set(agents) == {"analyst", "coder", "fresh-vibes"}
    assert agents["analyst"].cloud_policy == "never"
    assert agents["coder"].cloud_policy == "ask"


def test_tool_grants_with_confirm():
    fv = load_agent(AGENTS_DIR / "fresh-vibes")
    assert fv.grant("gmail.draft").confirm is None       # как объявлено в инструменте
    assert fv.grant("gmail.send").confirm is True        # переопределено: с подтверждением
    assert fv.grant("fs.write") is None                  # не выдан вовсе


def test_prompts_carry_context_block():
    for spec in load_all(AGENTS_DIR).values():
        assert "## Работа с контекстом" in spec.prompt, spec.name
        assert "Ключевое ограничение задачи" in spec.prompt, spec.name


def test_set_cloud_policy_writes_yaml(tmp_path):
    shutil.copytree(AGENTS_DIR / "analyst", tmp_path / "analyst")
    spec = set_cloud_policy("analyst", "ask", agents_dir=tmp_path)
    assert spec.cloud_policy == "ask"
    assert load_agent(tmp_path / "analyst").cloud_policy == "ask"
    with pytest.raises(ValueError):
        set_cloud_policy("analyst", "whatever", agents_dir=tmp_path)


def test_is_cloud_by_alias():
    assert is_cloud("claude-heavy") and is_cloud("cloud-fallback")
    assert not is_cloud("bossman-fast")


async def test_never_policy_blocks_cloud_before_wire():
    analyst = load_agent(AGENTS_DIR / "analyst")
    with pytest.raises(CloudDenied):
        await chat(analyst, [{"role": "user", "content": "секрет"}], alias="claude-heavy")


async def test_ask_policy_demands_approval_with_preview():
    coder = load_agent(AGENTS_DIR / "coder")
    with pytest.raises(NeedsCloudApproval) as exc:
        await chat(coder, [{"role": "user", "content": "ревью diff"}], alias="claude-heavy")
    assert "ревью diff" in exc.value.preview             # видно, что именно уйдёт
