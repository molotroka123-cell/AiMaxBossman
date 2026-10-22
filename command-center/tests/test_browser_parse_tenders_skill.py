"""browser-parse-tenders: Bossman picks the skill by itself from a plain owner request and gets browser + War Terminal tools only."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bcc.features import agent_opencode as ao
from bcc.v2 import skill_catalog as sc
from bcc.v2 import skill_library as sl

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
SID = "browser-parse-tenders"


def test_manifest_is_clean_and_read_only():
    sk = sl.parse_skill(SKILLS_ROOT / SID / "SKILL.md", SKILLS_ROOT)
    val = sl.validate_manifest(sk)
    assert val == {"errors": [], "warnings": []}
    assert sc.scan_policy(sk.body) == []
    con = sl.skill_contract(sk)
    assert set(con.permissions) == {"read"}
    assert "browser.open" in con.required_tools and "browser.screenshot" in con.required_tools
    assert not {"browser.download", "browser.submit", "browser.login", "terminal.run"} & set(con.required_tools)


def test_catalog_provenance_verified_and_selection():
    cat = sc.SkillCatalog(REPO_ROOT / ".agents")
    entry = {e.name: e for e in cat.entries()}[SID]
    assert entry.status == sc.VERIFIED, entry.status_reason
    assert entry.summary()["grants"] == {"tools": [], "permissions": []}
    assert any(i["title"] == SID for i in cat.select("Спарси тендеры браузером и проверь тендер в браузере"))
    assert all(i["title"] != SID for i in cat.select("Как приготовить яблочный пирог с корицей в духовке"))


def _svc(monkeypatch):
    lib = sl.SkillLibrary(sl.default_skill_roots(REPO_ROOT), SKILLS_ROOT)
    monkeypatch.setattr("bcc.features.skills._lib", lambda svc: lib)
    monkeypatch.setattr(ao, "_orch", lambda svc: SimpleNamespace(skill_index_cache={}))
    return SimpleNamespace()


def test_agent_mode_matches_skill_from_plain_owner_request(monkeypatch):
    svc = _svc(monkeypatch)
    m = ao.match_workspace_skill_for_task(svc, "Спарси 2 открытых тендера из War Terminal браузером и пришли скрины")
    assert m is not None and m["id"] == SID
    assert "browser.open" in m["required_tools"] and "war_terminal.list_tenders" in m["required_tools"]


def test_agent_mode_does_not_pick_it_for_unrelated_request(monkeypatch):
    svc = _svc(monkeypatch)
    m = ao.match_workspace_skill_for_task(svc, "Сделай видео с котом на пляже")
    assert m is None or m["id"] != SID


def test_matched_skill_narrows_run_tools_to_its_contract(monkeypatch):
    """Live 22.10 task 170: without the allowlist gemma looped on terminal.run instead of browser.open."""
    from bcc.tools import REGISTRY, allowed_tools_for
    m = {"id": SID, "fast_path": True, "filtered_tools": ["browser.open", "browser.screenshot", "no.such_tool"]}
    real_get = REGISTRY.get
    monkeypatch.setattr(REGISTRY, "get", lambda n: object() if n.startswith("browser.") else real_get(n))
    meta = ao._task_meta_for_skill("ag-x", m)
    assert meta["allowed_tools"] == ["browser.open", "browser.screenshot"] and meta["skill_id"] == SID
    assert allowed_tools_for({"meta": meta}, {"tools": ["terminal.run"]}) == ["browser.open", "browser.screenshot"]
    # weak match or unknown tools: unchanged meta, agent keeps its own tools (never widened)
    assert ao._task_meta_for_skill("ag-x", {**m, "fast_path": False}) == {"client_request_id": "ag-x"}
    assert ao._task_meta_for_skill("ag-x", {**m, "filtered_tools": ["no.such_tool"]}) == {"client_request_id": "ag-x"}
    assert ao._task_meta_for_skill("ag-x", None) == {"client_request_id": "ag-x"}


def test_war_terminal_app_name_is_not_a_shell_terminal_action():
    from bcc.features import action_contract as ac
    t = ("Спарси 2 открытых тендера из War Terminal браузером: открой их официальные страницы в браузере, прочитай "
         "название, заказчика и срок, сравни с данными терминала и сделай скриншоты.")
    assert "TERMINAL_FILE_ACTION" not in [c.name for c in ac.classify_all(t)]
    # a real shell request still classifies
    assert "TERMINAL_FILE_ACTION" in [c.name for c in ac.classify_all("Запусти в терминале команду dir и сделай отчёт")]
    assert "TERMINAL_FILE_ACTION" in [c.name for c in ac.classify_all("Создай файл result.txt")]
