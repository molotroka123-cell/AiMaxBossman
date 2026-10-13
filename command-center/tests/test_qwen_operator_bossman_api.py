"""Package E (stage 2.2): the operator calls Bossman's OWN Motion Studio routes through an allow-list.

Offline: the HTTP client is replaced by a fake; the allow-list is a pure function. What must not change: no
route outside Motion Studio, no delete, no approval / key / delivery route, the token never lands in the record.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bcc.features import qwen_operator as qo
from bcc.telegram_companion.operator_bridge import receipt_text

from .test_qwen_operator import PLAN, Fakes, repo, stack  # noqa: F401  (fixtures)


@pytest.mark.parametrize("method,path,ok", [
    ("GET", "/api/motion-workflow/projects", True),
    ("GET", "/api/motion-workflow/projects/ab12/plan", True),
    ("GET", "/api/motion-studio/status", True),
    ("POST", "/api/motion-workflow/projects", True),
    ("POST", "/api/motion-workflow/projects/ab12/analyze", True),
    ("POST", "/api/motion-workflow/projects/ab12/ops", True),
    ("POST", "/api/motion-workflow/projects/ab12/jobs", True),
    ("POST", "/api/motion-workflow/projects/ab12/jobs/j1/render", True),
    ("POST", "/api/motion-studio/jobs", True),
    ("DELETE", "/api/motion-workflow/projects/ab12/references/x", False),
    ("POST", "/api/approvals/1/approve", False),
    ("GET", "/api/providers", False),
    ("GET", "/api/motion-studio/../providers", False),
    ("GET", "/api/motion-workflow/projects?x=1", False),
    ("POST", "/api/motion-workflow/projects/ab12/source", False),
    ("PUT", "/api/motion-workflow/projects", False),
])
def test_allow_list(method, path, ok):
    assert qo.bossman_api_allowed(method, path) is ok


async def test_plan_with_bossman_calls_runs_allowed_and_refuses_the_rest(stack, repo, monkeypatch):  # noqa: F811
    svc, _c, _a = stack
    seen = []

    async def fake_call(_svc, method, path, body=None):
        seen.append((method, path, body))
        return {"method": method, "path": path, "status": 200, "ok": True, "response": '{"id": "p1"}'}
    monkeypatch.setattr(qo, "call_bossman_api", fake_call)
    plan = {**PLAN, "files": [], "read_only": False, "bossman_calls": [
        {"method": "POST", "path": "/api/motion-workflow/projects", "body": {"title": "t"}, "why": "создать"},
        {"method": "GET", "path": "/api/motion-workflow/projects", "why": "список"},
        {"method": "POST", "path": "/api/approvals/1/approve", "why": "попытка обойти кнопку"},
    ]}
    f = Fakes(repo, plan)
    rec = await qo.start_task(svc, "создай проект Motion Studio", source_repo=str(repo), decision_wait_s=0,
                              local_chat=f.local_chat, free_cloud=f.free_cloud, coder=f.coder)
    await qo._JOBS[rec["id"]]
    saved = json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rec['id']}.json").read_text("utf-8"))
    assert saved["status"] == "completed", saved.get("error")
    assert [(c["method"], c["path"], c["ok"]) for c in saved["bossman_calls"]] == [
        ("POST", "/api/motion-workflow/projects", True), ("GET", "/api/motion-workflow/projects", True),
        ("POST", "/api/approvals/1/approve", False)]
    assert seen == [("POST", "/api/motion-workflow/projects", {"title": "t"}),
                    ("GET", "/api/motion-workflow/projects", None)]          # the third never reached the client
    assert "REFUSED" in saved["bossman_calls"][2]["response"]
    assert "Вызовы Bossman: 2/3" in receipt_text(saved)


async def test_token_never_lands_in_the_record(stack, repo, monkeypatch):  # noqa: F811
    svc, _c, _a = stack
    (Path(svc.settings.data_dir) / "token").write_text("SECRET-TOKEN-VALUE", encoding="utf-8")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, json=None):
            assert headers["X-BCC-Token"] == "SECRET-TOKEN-VALUE"        # used for the call ...
            return type("R", (), {"status_code": 200, "text": '{"projects": []}'})()
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    res = await qo.call_bossman_api(svc, "GET", "/api/motion-workflow/projects")
    assert res["ok"] and "SECRET-TOKEN-VALUE" not in json.dumps(res)      # ... and never returned


async def test_read_only_task_with_bossman_calls_answers_from_the_responses(stack, repo, monkeypatch):  # noqa: F811
    """Seen live 13.10: a read-only wording plus calls went down the read-only path that ignored the calls."""
    svc, _c, _a = stack
    seen = []

    async def fake_call(_svc, method, path, body=None):
        seen.append((method, path))
        return {"method": method, "path": path, "status": 200, "ok": True, "response": '{"status": "ok"}'}
    monkeypatch.setattr(qo, "call_bossman_api", fake_call)
    plan = {**PLAN, "files": [], "read_files": [], "read_only": True, "bossman_calls": [
        {"method": "GET", "path": "/api/motion-studio/status", "why": "состояние"}]}
    f = Fakes(repo, plan)
    rec = await qo.start_task(svc, "прочитай статус Motion Studio, ничего не меняй", source_repo=str(repo),
                              decision_wait_s=0, local_chat=f.local_chat, free_cloud=f.free_cloud, coder=f.coder)
    await qo._JOBS[rec["id"]]
    saved = json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rec['id']}.json").read_text("utf-8"))
    assert saved["status"] == "completed", saved.get("error")
    assert seen == [("GET", "/api/motion-studio/status")] and saved["analysis"]
    assert not saved.get("commit_sha")                                        # nothing was changed


async def test_call_uses_the_really_bound_port(stack, monkeypatch):  # noqa: F811
    """Seen live 13.10 (d029679aea7d): settings.port said 8800 while the backend was started with --port 8801."""
    svc, _c, _a = stack
    urls = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, headers=None, json=None):
            urls.append(url)
            return type("R", (), {"status_code": 200, "text": "{}"})()
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("BCC_ACTUAL_PORT", "8801")
    await qo.call_bossman_api(svc, "GET", "/api/motion-studio/status")
    assert urls == ["http://127.0.0.1:8801/api/motion-studio/status"]


async def test_read_only_task_with_no_readable_file_still_answers(stack, repo):  # noqa: F811
    """Seen live 13.10 (15c792545a28): the plan named files that do not exist -> 'анализ не получен'."""
    svc, _c, _a = stack
    plan = {**PLAN, "files": [], "read_files": ["docs/owner/DOES_NOT_EXIST.md"], "read_only": True}
    f = Fakes(repo, plan)
    rec = await qo.start_task(svc, "объясни, как работает pkg", source_repo=str(repo), decision_wait_s=0,
                              local_chat=f.local_chat, free_cloud=f.free_cloud, coder=f.coder)
    await qo._JOBS[rec["id"]]
    saved = json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rec['id']}.json").read_text("utf-8"))
    assert saved["status"] == "completed", saved.get("error")
    assert saved["analysis"] and not saved.get("commit_sha")
