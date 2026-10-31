"""tools/coding_swarm.py: refusals before any call, receipt shape, reasoning kept out of the receipt (no network)."""
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("cs", Path(__file__).resolve().parents[1] / "tools" / "coding_swarm.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "STATE_DIR", tmp_path / "state")      # never the real D:\Bossman limits ledger


def _fake(name, prompt, **_):
    return {"model": name, "id": "fake", "ok": True, "seconds": 0.1, "tokens_in": 10, "tokens_out": 5,
            "cost_usd": 0.0, "text": "answer", "reasoning": "chain of thought" if name == "kimi" else ""}


def test_secret_in_prompt_is_refused_before_any_call(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_call", lambda *a, **k: pytest.fail("network call made"))
    with pytest.raises(SystemExit):
        cs.run("key sk-" + "a" * 24, ["kimi"], tmp_path, "t")


def test_unknown_model_and_oversized_prompt_are_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_call", lambda *a, **k: pytest.fail("network call made"))
    with pytest.raises(SystemExit):
        cs.run("hi", ["nope"], tmp_path, "t")
    with pytest.raises(SystemExit):
        cs.run("x" * (cs.MAX_PROMPT_CHARS + 1), ["kimi"], tmp_path, "t")


def test_receipt_has_costs_but_no_reasoning_and_dataset_gets_it(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_call", _fake)
    monkeypatch.setattr(cs, "DATASET", tmp_path / "ds")
    doc = cs.run("task", ["kimi", "qwen"], tmp_path / "out", "t")
    receipt = json.loads((tmp_path / "out" / "t-receipt.json").read_text(encoding="utf-8"))
    assert receipt == doc and all("reasoning" not in c for c in receipt["calls"])
    rows = [json.loads(l) for f in (tmp_path / "ds").glob("*.jsonl") for l in f.read_text(encoding="utf-8").splitlines()]
    assert [r["model_id"] for r in rows] == ["fake"] and rows[0]["training_use"] == "ALLOWED_BY_TERMS" and rows[0]["terms_ref"].startswith("owner-decision-2026-10-29")
    assert set(cs.M4_FIELDS) <= set(rows[0]) and rows[0]["provider"] == "7api.st" and len(rows[0]["prompt_sha256"]) == 64
    assert list((tmp_path / "ds").glob("*.jsonl"))[0].stem.count("-") == 2          # <YYYY-MM-DD>.jsonl


# ------------------------------------------------------------------ T5: ContextSanitizer

def _san(tmp_path, tracked):
    for rel in tracked:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("print('ok')\n", encoding="utf-8")
    return cs.ContextSanitizer(root=tmp_path, tracked=set(tracked))


@pytest.mark.parametrize("path", [".env", "config/.env.local", "keys/provider-keys.env", "data/bcc.db", "ops/tailnet.json",
                                  "vault/master.bin", "certs/server.pem", "untracked.py", "../outside.py"])
def test_files_off_the_allowlist_never_reach_the_prompt(tmp_path, path):
    san = _san(tmp_path, [".env", "config/.env.local", "keys/provider-keys.env", "data/bcc.db", "ops/tailnet.json",
                          "vault/master.bin", "certs/server.pem", "tools/ok.py"])
    if path == "untracked.py":
        (tmp_path / path).write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(cs.SanitizerRefusal):
        san.read_file(path)


def test_a_tracked_source_file_is_allowed(tmp_path):
    rel, text = _san(tmp_path, ["tools/ok.py"]).read_file("tools/ok.py")
    assert rel == "tools/ok.py" and "ok" in text


@pytest.mark.parametrize("text", ["token sk-or-v1-" + "a" * 30, "nvapi-" + "b" * 30, 'api_key = "' + "c" * 24 + '"',
                                  "-----BEGIN RSA " + "PRIVATE KEY-----", r"photos in D:\datasets\gf\x.jpg", "Гречка archive",
                                  "telegram-companion/companion.env"])
def test_secrets_and_personal_locations_are_refused_before_any_call(monkeypatch, tmp_path, text):
    monkeypatch.setattr(cs, "_call", lambda *a, **k: pytest.fail("network call made"))
    with pytest.raises(cs.SanitizerRefusal):
        cs.run("review this: " + text, ["kimi"], tmp_path, "t")


def test_incidental_personal_strings_are_redacted_not_sent(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(cs, "_call", lambda name, prompt, **k: seen.append(prompt) or _fake(name, prompt))
    monkeypatch.setattr(cs, "DATASET", tmp_path / "ds")
    doc = cs.run(r"path C:\Users\asd\Bossman\x.py mail a.b@example.com ip 100.101.5.7 lan 192.168.1.9 %LOCALAPPDATA%",
                 ["qwen"], tmp_path / "out", "t")
    assert "asd" not in seen[0] and "example.com" not in seen[0] and "100.101.5.7" not in seen[0] and "192.168" not in seen[0]
    assert {"<HOME>", "<EMAIL>", "<LAN_IP>", "<APPDATA>"} <= set(doc["sanitizer"]["redacted"])


# ------------------------------------------------------------------ T5: M4 dataset rows and the training export guard

def _row(**kw):
    row = {f: "" for f in cs.M4_FIELDS}
    row.update(id="r1", prompt="def f(): pass", reasoning="think", answer="ok", cost_usd=0.0, training_use="PENDING_TERMS_CHECK")
    row.update(kw)
    return row


@pytest.mark.parametrize("bad", [{"training_use": None}, {"training_use": "YES"}, {"answer": "call me at a.b@example.com"},
                                 {"reasoning": "the key is sk-" + "z" * 30}, {"training_use": "ALLOWED_BY_TERMS", "terms_ref": ""}])
def test_rows_without_training_use_or_with_personal_data_are_refused(bad):
    with pytest.raises(ValueError):
        cs.validate_row(_row(**bad))
    row = _row()
    row.pop("training_use")
    with pytest.raises(ValueError):
        cs.validate_row(row)


def test_training_export_is_impossible_while_terms_are_pending():
    with pytest.raises(PermissionError):
        cs.export_training_rows([_row(id="a", training_use="ALLOWED_BY_TERMS", terms_ref="https://terms"), _row(id="b")])
    out = cs.export_training_rows([_row(id="a", training_use="ALLOWED_BY_TERMS", terms_ref="https://terms")])   # control
    assert out == [{"prompt": "def f(): pass", "reasoning": "think", "answer": "ok", "source_id": "a"}]


# ------------------------------------------------------------------ T5: provider limits

def test_free_model_over_its_daily_limit_falls_back_to_local_and_paid_over_cap_is_skipped():
    day = cs._today()
    ledger = {"days": {day: {"nemotron": {"calls": cs.FREE_DAILY_CALLS["nemotron"], "usd": 0.0},
                             "qwen": {"calls": 9, "usd": cs.PAID_DAILY_USD["qwen"]}}},
              # owner 31.10: Kimi has its own $5 cap (was the shared 7API cap)
              "totals": {"7api": cs.PAID_TOTAL_USD["7api"], "kimi": cs.MODEL_TOTAL_USD["kimi"]}}
    planned, notes = cs.plan_models(["nemotron", "qwen", "kimi", "gemini"], ledger)
    assert planned == ["local", "gemini"]
    assert {n["model"]: n["action"] for n in notes} == {"nemotron": "fallback_local", "qwen": "skipped", "kimi": "skipped"}
    assert cs.plan_models(["nemotron", "qwen"], {"days": {}, "totals": {}})[0] == ["nemotron", "qwen"]   # control


def test_usage_is_counted_per_provider_and_day(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_call", _fake)
    monkeypatch.setattr(cs, "DATASET", tmp_path / "ds")
    cs.run("task", ["kimi", "nemotron"], tmp_path / "out", "t")
    led = json.loads((cs.STATE_DIR / "limits.json").read_text(encoding="utf-8"))
    assert led["days"][cs._today()]["nemotron"]["calls"] == 1 and led["days"][cs._today()]["kimi"]["calls"] == 1


def test_all_models_over_limit_means_no_call(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_call", lambda *a, **k: pytest.fail("network call made"))
    cs.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (cs.STATE_DIR / "limits.json").write_text(json.dumps({"days": {cs._today(): {"qwen": {"calls": 1, "usd": 99}}}, "totals": {}}))
    with pytest.raises(SystemExit, match="over its limit"):
        cs.run("task", ["qwen"], tmp_path, "t")


# ------------------------------------------------------------------ T5 swarm-flow proof: Kimi patch -> Qwen/Nemotron review -> Claude

def test_limits_mode_reports_the_ledger_without_network_or_run_args(monkeypatch, capsys):
    monkeypatch.setattr(cs, "_call", lambda *a, **k: pytest.fail("network call made"))
    monkeypatch.setattr(cs.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network call made"))
    today = {"kimi": {"calls": 2, "usd": 0.25}, "qwen": {"calls": 3, "usd": 0.5}, "gemini": {"calls": 249, "usd": 0.0}}
    cs.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (cs.STATE_DIR / "limits.json").write_text(json.dumps({"days": {cs._today(): today}, "totals": {"7api": 1.25}}), encoding="utf-8")
    assert cs.main(["limits"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["today"] == today and rep["7api_total_usd"] == 1.25
    assert rep["remaining"]["gemini"] == {"calls": cs.FREE_DAILY_CALLS["gemini"] - 249}
    assert rep["remaining"]["nemotron"] == {"calls": cs.FREE_DAILY_CALLS["nemotron"]}
    assert rep["remaining"]["qwen"] == {"usd": cs.PAID_DAILY_USD["qwen"] - 0.5}
    assert rep["remaining"]["7api"] == {"usd": cs.PAID_TOTAL_USD["7api"] - 1.25}


@pytest.mark.parametrize("content", ["{broken", "[]", '{"days": null, "totals": null}', '{"days": {"%s": null}}'])
def test_limits_mode_survives_a_corrupt_ledger(capsys, content):
    cs.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (cs.STATE_DIR / "limits.json").write_text(content.replace("%s", cs._today()), encoding="utf-8")
    assert cs.main(["limits"]) == 0
    assert json.loads(capsys.readouterr().out)["remaining"]["nemotron"] == {"calls": cs.FREE_DAILY_CALLS["nemotron"]}


def test_review_and_task_still_require_ask_and_out():
    with pytest.raises(SystemExit):
        cs.main(["task", "--out", "x"])
    with pytest.raises(SystemExit):
        cs.main(["review", "--ask", "x"])



# ------------------------------------------------------------------ owner terms decision 29.10

def test_apply_terms_upgrades_legacy_rows_and_keeps_personal_rows_pending(tmp_path):
    ds = tmp_path / "ds"
    ds.mkdir()
    legacy = [{"at": "2026-10-25T10:00:00", "label": "t", "model": "kimi", "model_id": "kimi-k3.1-pro", "prompt": "def f(): pass",
               "reasoning": "think", "answer": "ok", "training_use": "PENDING_TERMS_CHECK"},
              {"at": "2026-10-25T10:01:00", "label": "t", "model": "qwen", "model_id": "q", "prompt": "mail me a.b@example.com",
               "reasoning": "r", "answer": "a", "training_use": "PENDING_TERMS_CHECK"}]
    (ds / "20261025.jsonl").write_text("".join(json.dumps(r) + "\n" for r in legacy), encoding="utf-8")
    stats = cs.apply_terms_decision(ds)
    assert stats == {"cleared": 1, "kept_pending": 1, "files": 1}
    rows = [json.loads(l) for l in (ds / "2026-10-25.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not (ds / "20261025.jsonl").exists() and set(cs.M4_FIELDS) <= set(rows[0])
    assert [r["training_use"] for r in rows] == ["ALLOWED_BY_TERMS", "PENDING_TERMS_CHECK"]
    exported = cs.export_training_rows([r for r in rows if r["training_use"] == "ALLOWED_BY_TERMS"])
    assert len(exported) == 1
    with pytest.raises(PermissionError):
        cs.export_training_rows(rows)                      # the personal-data row never reaches training


@pytest.mark.parametrize("text", ["key sk-" + "ant-api03-" + "a" * 24, "sk-" + "proj-" + "b" * 24, "AKIA" + "ABCDEFGHIJKLMNOP",
                                  "Authorization: Bearer " + "c" * 30, "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
                                  'password = "hunter2"', "password=Sup3rS3cret!", '{"api_key": "' + "d" * 10 + '"}',
                                  "github_pat_" + "e" * 24, "glpat-" + "f" * 22])
def test_security_audit_secret_formats_are_refused(monkeypatch, tmp_path, text):
    """Security audit 30.10: these key formats reached sanitize() unchanged before."""
    import coding_swarm as cs
    assert cs._SECRET_EXTRA.search(text)


@pytest.mark.parametrize("text", ["def get_token_count(text):", "password_field = form.field", "tokens = 3"])
def test_ordinary_code_is_not_flagged_as_a_secret(text):
    import coding_swarm as cs
    assert not cs._SECRET_EXTRA.search(text)


@pytest.mark.parametrize("text", ['token = request.headers.get("X-Token")', "password = form.cleaned_data['pw']",
                                  'api_key = os.environ["KEY"]'])
def test_code_that_reads_a_secret_is_not_itself_a_secret(text):
    import coding_swarm as cs
    assert not cs._SECRET_EXTRA.search(text)


def test_owner_caps_kimi_5_usd_glm_uncapped():
    """Owner 31.10: Kimi stops at its own $5; GLM keeps running past the shared 7API $5; Nemotron stays free."""
    import coding_swarm as cs
    over = {"days": {}, "totals": {"kimi": 5.0, "7api": 9.0, "glm": 4.0}}
    planned, notes = cs.plan_models(["kimi", "glm", "nemotron"], over)
    assert planned == ["glm", "nemotron"] and notes[0]["model"] == "kimi" and "kimi budget" in notes[0]["reason"]
    under = {"days": {}, "totals": {"kimi": 4.99, "7api": 9.0}}
    assert cs.plan_models(["kimi"], under)[0] == ["kimi"]
