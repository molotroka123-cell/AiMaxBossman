"""V2.6 модуль N — Personal Context Router: отбор критики из memory.md.

Всё детерминировано, без LLM/сети/БД. Ключевой инвариант — RAW fallback:
default OFF = memory.md в system целиком (как раньше); context_engine выключен =
тоже RAW (без retrieved-канала память не урезаем). KeepRisk: критические
ограничения переживают отбор ВСЕГДА.
"""
from __future__ import annotations

from pathlib import Path

from bossman import personal_context, runner
from bossman.agents import AgentSpec
from bossman.config import settings

MEMORY = """# Правила безопасности
НИКОГДА не отправляй пароли
Всегда проверяй тесты перед push
! перед деплоем — approval

# Заметки о проекте
Проект X использует React 18
Любимый редактор владельца — vim
"""


# ---------------- _memory_for_system: флаг и fallback ----------------

def test_flag_off_by_default_returns_raw():
    """Default OFF: вход возвращается дословно — прежнее поведение ядра."""
    assert settings.personal_context_select is False
    assert runner._memory_for_system(MEMORY) == MEMORY


def test_flag_on_engine_on_keeps_critical_drops_trivia(monkeypatch):
    monkeypatch.setattr(settings, "personal_context_select", True, raising=False)
    monkeypatch.setattr(settings, "context_engine_enabled", True, raising=False)
    out = runner._memory_for_system(MEMORY)
    # критические ограничения переживают отбор (KeepRisk)
    assert "НИКОГДА не отправляй пароли" in out
    assert "Всегда проверяй тесты перед push" in out
    assert "! перед деплоем — approval" in out
    # контекст заголовка сохранён, note про retrieved присутствует
    assert "# Правила безопасности" in out
    assert personal_context.RETRIEVED_NOTE in out
    # проектная мелочь ушла в retrieved-канал, не в system
    assert "React 18" not in out
    assert "vim" not in out


def test_flag_on_engine_off_falls_back_to_raw(monkeypatch):
    """RAW fallback доказан: без retrieved-канала память не урезаем НИКОГДА."""
    monkeypatch.setattr(settings, "personal_context_select", True, raising=False)
    monkeypatch.setattr(settings, "context_engine_enabled", False, raising=False)
    assert runner._memory_for_system(MEMORY) == MEMORY


def test_selector_exception_degrades_to_raw(monkeypatch):
    monkeypatch.setattr(settings, "personal_context_select", True, raising=False)
    monkeypatch.setattr(settings, "context_engine_enabled", True, raising=False)
    def boom(_mem):
        raise RuntimeError("selector down")
    monkeypatch.setattr(personal_context, "select_memory", boom)
    assert runner._memory_for_system(MEMORY) == MEMORY


# ---------------- select_memory / render_selected ----------------

def test_select_memory_stats_and_determinism():
    block1, stats1 = personal_context.select_memory(MEMORY)
    block2, stats2 = personal_context.select_memory(MEMORY)
    assert (block1, stats1) == (block2, stats2)  # детерминированно
    assert stats1["kept_lines"] < stats1["total_lines"]
    assert stats1["kept_lines"] == 3
    # порядок исходный, заголовок один раз
    assert block1.count("# Правила безопасности") == 1
    assert block1.index("НИКОГДА") < block1.index("Всегда")


def test_ru_en_and_symbol_markers():
    mem = ("do not commit secrets\nmust run linters\n"
           "деплой только через CI\n⚠ прод трогать после бэкапа\n"
           "ВАЖНО: бэкапы каждый день\nобычная строка про погоду")
    block, stats = personal_context.select_memory(mem)
    assert stats["kept_lines"] == 5
    assert "погоду" not in block


def test_empty_memory_renders_note_only():
    block, stats = personal_context.select_memory("")
    assert block == "" and stats == {"total_lines": 0, "kept_lines": 0}
    assert personal_context.render_selected(block) == personal_context.RETRIEVED_NOTE


def test_render_selected_appends_note():
    out = personal_context.render_selected("НИКОГДА не удаляй бэкапы")
    assert out.startswith("НИКОГДА не удаляй бэкапы")
    assert out.endswith(personal_context.RETRIEVED_NOTE)


# ---------------- _system_prompt end-to-end ----------------

def _fake_agent(tmp_path: Path) -> AgentSpec:
    """Реальный AgentSpec без инструментов: prompt/memory читаются из папки."""
    (tmp_path / "prompt.md").write_text("Ты — тестовый агент.", encoding="utf-8")
    (tmp_path / "memory.md").write_text(MEMORY, encoding="utf-8")
    return AgentSpec(name="t", title="T", model="local-small", tools=[], path=tmp_path)


def test_system_prompt_off_keeps_full_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "personal_context_select", False, raising=False)
    prompt = runner._system_prompt(_fake_agent(tmp_path))
    assert "## Твоя память (memory.md)" in prompt
    assert "React 18" in prompt  # RAW: память целиком, как раньше
    assert "НИКОГДА не отправляй пароли" in prompt


def test_system_prompt_on_keeps_critical_drops_trivia(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "personal_context_select", True, raising=False)
    monkeypatch.setattr(settings, "context_engine_enabled", True, raising=False)
    prompt = runner._system_prompt(_fake_agent(tmp_path))
    assert "## Твоя память (memory.md)" in prompt
    assert "НИКОГДА не отправляй пароли" in prompt
    assert personal_context.RETRIEVED_NOTE in prompt
    assert "React 18" not in prompt
