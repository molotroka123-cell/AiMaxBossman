"""HW-10 MVČR prepare pipeline — contract/integration evidence on SYNTHETIC fixtures only.

A fake 'official' site is served by a local http.server; the logical URLs stay
https://www.mvcr.cz/… so the same domain policy runs as in the live mode. None of
this is OWNER_HARDWARE evidence: the real MVČR site, the real form and the real
owner facts are verified only on the owner's machine.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pypdf")
pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "mvcr_prepare.py"
FIXTURES = REPO / "tests" / "fixtures" / "mvcr"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mv = _load("mvcr_prepare", TOOL)
fx = _load("mvcr_make_fixtures", FIXTURES / "make_fixtures.py")

from pypdf import PdfReader  # noqa: E402

FAKE_VALUES = ("SYNTETICKÁ-TESTOVÁ", "Fiktivní Osoba", "FAKE-000-TEST", "Fiktivní 1, 000 00 Testov",
               "Testland (fiktivní stát)")
HEADINGS = ("Что нашёл на официальном сайте", "Какие периоды проживания использовал и откуда",
            "Что скачал", "Что заполнил", "Чего не хватает", "Какая оплата/пошлина указана официально",
            "Что готово к отправке", "Куда именно будет отправлено", "Что требует моего подтверждения",
            "Что было реально проверено, а что осталось предположением")


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def world(tmp_path):
    """Build a fixture variant + synthetic owner folder + facts; return an argv factory."""
    def make(variant: str = "happy", mutate=None, out: Path | None = None, extra=()):
        fixtures = fx.build(tmp_path / f"fx_{variant}", variant)
        owner = fx.build_owner_folder(tmp_path / "owner")
        facts = json.loads((FIXTURES / "facts_synthetic.json").read_text(encoding="utf-8"))
        if mutate:
            mutate(facts)
        facts_path = tmp_path / "facts.json"
        facts_path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
        out = out or tmp_path / "project_mvcr"
        argv = ["--owner-folder", str(owner), "--facts", str(facts_path), "--out", str(out),
                "--offline-fixtures", str(fixtures), *extra]
        return argv, {"fixtures": fixtures, "owner": owner, "out": out, "facts": facts_path}
    return make


def run(argv) -> dict:
    return mv.run_pipeline(mv.build_parser().parse_args(argv))


def state(out: Path) -> dict:
    return json.loads((out / "state.json").read_text(encoding="utf-8"))


def approval_row(out: Path, approval_id: int) -> dict:
    async def go(approvals):
        return await mv._find(approvals, approval_id)
    return asyncio.run(mv._with_approvals(out / "approval" / "approvals.sqlite", go))


# --------------------------------------------------------------------------- happy path

def test_happy_path_reaches_wait_approval_with_verified_filled_form(world):
    argv, w = world()
    res = run(argv)
    assert res["status"] == "WAIT_APPROVAL" and res["exit_code"] == 0 and res["questions"] == []
    assert res["forbidden_methods_seen_by_fixture_site"] == []
    assert all(r["method"] == "GET" for r in res["network_requests"])

    pkg = w["out"] / "package"
    fields = {k: v.get("/V") for k, v in PdfReader(str(pkg / "zadost_VYPLNENO_NEPODEPSANO.pdf")).get_fields().items()}
    assert fields["prijmeni"] == "SYNTETICKÁ-TESTOVÁ"          # Czech characters survive the round trip
    assert fields["cislo_pasu"] == "FAKE-000-TEST"
    assert fields["datum_narozeni"] == "01.01.1990"
    assert fields["pobyt_na_uzemi_od"] == "01.03.2019"         # computed from confirmed periods
    assert fields["podpis"] in (None, "") and fields["datum_podpisu"] in (None, "")

    # the untouched original is byte-identical to what the official site served
    served = w["fixtures"] / "site" / "www.mvcr.cz" / "soubor" / "zadost-trvaly-pobyt-2026.pdf"
    assert sha(pkg / "zadost_ORIGINAL.pdf") == sha(served)
    st = state(w["out"])
    chosen = st["steps"]["download_and_verify"]["result"]["chosen_form"]
    assert chosen["sha256"] == sha(served) and chosen["sniffed_type"] == "application/pdf"
    prov = json.loads((w["out"] / "downloads" / "provenance" / f"{chosen['sha256'][:12]}.json").read_text())
    assert prov["final_url"] == "https://www.mvcr.cz/soubor/zadost-trvaly-pobyt-2026.pdf"
    assert prov["retrieved_at"] and prov["version_date"] == "2026-01-01"

    review = (pkg / "review.md").read_text(encoding="utf-8")
    positions = [review.index(f"## {i}. {h}") for i, h in enumerate(HEADINGS, start=1)]
    assert positions == sorted(positions)
    assert "Ничего не подано" in review and "2763 дн." in review
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["submitted"] is False and manifest["paid"] is False and manifest["signed"] is False
    assert [f["path"] for f in manifest["files"] if f["would_leave_computer"]] == [
        "package/zadost_VYPLNENO_NEPODEPSANO.pdf"]
    row = approval_row(w["out"], res["approval_id"])
    assert row["status"] == "pending" and row["kind"] == mv.APPROVAL_KIND


def test_cli_json_and_exit_code(world, capsys):
    argv, _ = world()
    assert mv.main([*argv, "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "WAIT_APPROVAL" and printed["submitted"] is False


# --------------------------------------------------------------------------- official sources

@pytest.mark.parametrize("url", [
    "https://www.mvcr.cz/clanek/trvaly-pobyt.aspx", "https://mvcr.cz/", "https://mv.gov.cz/x",
    "https://ipc.gov.cz/pobyt/", "https://portal.gov.cz/sluzby", "https://frs.gov.cz/informace"])
def test_official_domains_accepted(url):
    assert mv.official_url(url)[0] is True


@pytest.mark.parametrize("url,reason", [
    ("https://www.mvcr-cz.info/trvaly-pobyt.html", "NOT_OFFICIAL_DOMAIN"),
    ("https://www.mvcr.cz.trvaly-pobyt.info/", "NOT_OFFICIAL_DOMAIN"),
    ("https://mvcr.cz.evil.com/", "NOT_OFFICIAL_DOMAIN"),
    ("https://notmvcr.cz/", "NOT_OFFICIAL_DOMAIN"),
    ("http://www.mvcr.cz/", "NOT_HTTPS"),
    ("https://www.mvcr.cz@evil.com/", "USERINFO_IN_URL"),
    ("https://xn--mvr-8na.cz/", "IDN_LOOKALIKE"),
    ("https://www.mvcr.cz:8443/", "NON_STANDARD_PORT"),
    ("https://www.mvcr.cz/login", "ACTION_URL_REFUSED"),
    ("https://frs.gov.cz/rezervace/novy", "ACTION_URL_REFUSED"),
    ("https://portal.gov.cz/prihlaseni?bankid=1", "ACTION_URL_REFUSED"),
])
def test_lookalike_and_action_urls_rejected(url, reason):
    assert mv.official_url(url) == (False, reason)


def test_lookalike_site_never_fetched_nor_authoritative(world):
    argv, w = world()
    res = run(argv)
    hosts = {r["url"].split("/")[2] for r in res["network_requests"]}
    assert hosts == {"www.mvcr.cz"}
    rules = state(w["out"])["steps"]["official_rules"]["result"]
    rejected = {r["url"]: r["reason"] for r in rules["rejected"]}
    assert rejected["https://www.mvcr-cz.info/trvaly-pobyt.html"] == "NOT_OFFICIAL_DOMAIN"
    assert rejected["https://www.mvcr.cz.trvaly-pobyt.info/"] == "NOT_OFFICIAL_DOMAIN"
    # the lookalike's 990 Kč fee and "2 years" rule never enter the evidence
    assert {f["amount_czk"] for f in rules["fees"]} == {2500}
    assert {r["years"] for r in rules["rules"]} == {5}


def test_only_lookalike_source_blocks(world):
    argv, w = world()
    (w["fixtures"] / "sources.json").write_text(json.dumps(
        {"entry_points": ["https://www.mvcr-cz.info/trvaly-pobyt.html"]}), encoding="utf-8")
    res = run(argv)
    assert res["status"] == "BLOCKED" and res["reason"] == "NO_OFFICIAL_SOURCE" and res["exit_code"] == 20
    assert res["network_requests"] == []


def test_fetcher_refuses_non_official_and_action_urls():
    f = mv.Fetcher("http://127.0.0.1:9")                      # nothing listens; refusal must come first
    for url in ("https://www.mvcr-cz.info/x.pdf", "https://www.mvcr.cz/platba/zaplatit"):
        with pytest.raises(mv.FetchError, match="REFUSED"):
            f.get(url)
    assert f.requests == []


# --------------------------------------------------------------------------- stale form / fee

def test_archived_form_detected_and_not_chosen(world):
    argv, w = world("happy")
    assert run(argv)["status"] == "WAIT_APPROVAL"
    dl = state(w["out"])["steps"]["download_and_verify"]["result"]
    assert dl["chosen_form"]["source_url"].endswith("-2026.pdf")
    assert [s["url"].rsplit("/", 1)[-1] for s in dl["stale"]] == ["zadost-trvaly-pobyt-2019.pdf"]


def test_two_unmarked_versions_newest_chosen_older_flagged_stale(world):
    argv, w = world("two_versions")
    assert run(argv)["status"] == "WAIT_APPROVAL"
    dl = state(w["out"])["steps"]["download_and_verify"]["result"]
    assert dl["chosen_form"]["version_date"] == "2026-01-01"
    assert dl["stale"][0]["stale_reason"] == "OLDER_VERSION:2019-03-01<2026-01-01"


def test_only_stale_form_blocks_instead_of_silently_using_it(world):
    argv, _ = world("stale_only")
    res = run(argv)
    assert res["status"] == "BLOCKED" and res["reason"] == "ONLY_STALE_FORM"


def test_conflicting_fee_is_a_question_not_a_choice(world):
    argv, w = world("conflicting_fee")
    res = run(argv)
    assert res["status"] == "PARTIAL_MISSING_DATA" and res["exit_code"] == 10
    assert {"id": "F-CONFLICT", "topic": "fee"} in res["questions"]
    review = (w["out"] / "package" / "review.md").read_text(encoding="utf-8")
    assert "КОНФЛИКТ сумм" in review and "2500 Kč" in review and "5000 Kč" in review
    assert res["approval_id"] is None                          # incomplete package is not offered for approval


def test_consistent_fee_raises_no_question(world):
    argv, _ = world("happy")
    assert not [q for q in run(argv)["questions"] if q["topic"] == "fee"]


# --------------------------------------------------------------------------- downloads

def test_sniff_pdf_accepts_real_pdf_and_rejects_masquerades():
    assert mv.sniff_pdf(fx.form_pdf()) == (True, "application/pdf")
    assert mv.sniff_pdf(fx.HTML_ERROR_PAGE) == (False, "HTML_OR_TEXT_AS_PDF")
    assert mv.sniff_pdf(b"\n  <html><body>login</body></html>") == (False, "HTML_OR_TEXT_AS_PDF")
    assert mv.sniff_pdf(fx.form_pdf()[:-200]) == (False, "TRUNCATED_PDF")
    assert mv.sniff_pdf(b"GIF89a" + b"0" * 500) == (False, "NO_PDF_MAGIC")


def test_html_error_page_renamed_pdf_is_rejected(world):
    argv, w = world("html_as_pdf")
    res = run(argv)
    assert res["status"] == "BLOCKED" and res["reason"] == "NO_VALID_FORM_DOWNLOAD"
    assert "HTML_OR_TEXT_AS_PDF" in res["detail"]
    blocked = state(w["out"])["steps"]["download_and_verify"]
    assert blocked["status"] == "blocked"
    assert not list((w["out"] / "downloads" / "original").glob("*2026*"))


def test_valid_pdf_that_is_not_the_intended_form_is_rejected(world):
    argv, w = world("happy")
    target = w["fixtures"] / "site" / "www.mvcr.cz" / "soubor" / "zadost-trvaly-pobyt-2026.pdf"
    target.write_bytes(fx.text_pdf(["Vyrocni zprava ministerstva 2025"]))
    res = run(argv)
    assert res["status"] == "BLOCKED" and "NOT_INTENDED_DOCUMENT" in res["detail"]


# --------------------------------------------------------------------------- missing data

def test_missing_personal_field_becomes_question_not_fabrication(world):
    argv, w = world(mutate=lambda f: f["person"].pop("passport_number"))
    res = run(argv)
    assert res["status"] == "PARTIAL_MISSING_DATA"
    assert {"id": "Q-cislo_pasu", "topic": "form_field"} in res["questions"]
    filled = PdfReader(str(w["out"] / "package" / "zadost_VYPLNENO_NEPODEPSANO.pdf")).get_fields()
    assert filled["cislo_pasu"].get("/V") in (None, "")       # no placeholder that could pass for data
    assert filled["prijmeni"].get("/V") == "SYNTETICKÁ-TESTOVÁ"  # verified fields still filled
    review = (w["out"] / "package" / "review.md").read_text(encoding="utf-8")
    assert "| cislo_pasu | MISSING_DATA | — (пусто) |" in review


@pytest.mark.parametrize("fact,usable,reason", [
    ({"value": "AB123", "confirmed": True, "source": "owner:chat"}, True, "OWNER_STATEMENT"),
    ({"value": "XXX", "confirmed": True, "source": "owner:chat"}, False, "PLACEHOLDER_VALUE"),
    ({"value": "???", "confirmed": True, "source": "owner:chat"}, False, "PLACEHOLDER_VALUE"),
    ({"value": "AB123", "confirmed": False, "source": "owner:chat"}, False, "UNCONFIRMED"),
    ({"value": "AB123", "confirmed": True, "source": ""}, False, "NO_SOURCE"),
    ({"value": "AB123", "confirmed": True, "source": "web:google"}, False, "UNKNOWN_SOURCE_KIND"),
    ({"value": "AB123", "confirmed": True, "source": "owner:chat",
      "conflicts_with": [{"value": "AB124", "source": "doc:x"}]}, False, "CONFLICT"),
    (None, False, "MISSING"),
])
def test_fact_strictness(fact, usable, reason):
    ok, why, _ = mv.check_fact(fact, None)
    assert (ok, why) == (usable, reason)


def test_document_source_must_live_inside_owner_folder(tmp_path):
    owner = fx.build_owner_folder(tmp_path / "owner")
    (tmp_path / "outside.txt").write_text("x")
    good = {"value": "A", "confirmed": True, "source": "doc:pas_SYNTETICKY.txt"}
    ok, why, proof = mv.check_fact(good, owner)
    assert ok and why == "OWNER_DOCUMENT" and len(proof["sha256"]) == 64
    for src, code in (("doc:../outside.txt", "DOC_OUTSIDE_OWNER_FOLDER"), ("doc:nope.txt", "DOC_NOT_FOUND")):
        ok, why, _ = mv.check_fact({**good, "source": src}, owner)
        assert not ok and why.startswith(code)


def test_field_classification():
    assert mv.classify_field("podpis", "Podpis zadatele", {}) == ("OWNER_ONLY", [])
    assert mv.classify_field("datum_podpisu", "", {}) == ("OWNER_ONLY", [])
    assert mv.classify_field("prijmeni", "Surname", {}) == ("MAPPED", ["surname"])
    assert mv.classify_field("pole_17", "", {}) == ("UNMAPPED", [])
    assert mv.classify_field("x", "Adresa / passport", {})[0] == "AMBIGUOUS"
    assert mv.classify_field("pole_17", "", {"pole_17": "passport_number"}) == ("MAPPED", ["passport_number"])
    assert mv.classify_field("prijmeni", "", {"prijmeni": "OWNER_ONLY"}) == ("OWNER_ONLY", [])


# --------------------------------------------------------------------------- residence timeline

def _facts(periods, **extra):
    return {"schema": mv.FACTS_SCHEMA, "residence_periods": periods, **extra}


def _p(pid, a, b, **kw):
    return {"id": pid, "from": a, "to": b, "confirmed": True, "source": "owner:test", **kw}


def test_timeline_contiguous_chain_and_calendar_arithmetic():
    import datetime as dt
    t = mv.residence_timeline(_facts([_p("A", "2019-03-01", "2021-02-28"), _p("B", "2021-03-01", "ongoing")]),
                              None, dt.date(2024, 2, 29))
    assert t["complete"] and t["chain"]["from"] == "2019-03-01"
    assert t["chain"]["days"] == (dt.date(2024, 2, 29) - dt.date(2019, 3, 1)).days + 1
    assert (t["chain"]["years"], t["chain"]["months"], t["chain"]["days_rest"]) == (5, 0, 0)
    assert any("A: 2019-03-01 → 2021-02-28 = 731 дн." == line for line in t["calculation"])


def test_timeline_gap_breaks_continuity():
    import datetime as dt
    t = mv.residence_timeline(_facts([_p("A", "2015-01-01", "2018-12-31"), _p("B", "2020-01-01", "2024-12-31")]),
                              None, dt.date(2025, 1, 1))
    assert t["chain"]["from"] == "2020-01-01" and t["gaps"][0]["gap_days"] == 365


def test_timeline_overlap_is_conflict_question_and_no_calculation():
    import datetime as dt
    t = mv.residence_timeline(_facts([_p("A", "2019-01-01", "2021-01-01"), _p("B", "2020-06-01", "2022-01-01")]),
                              None, dt.date(2025, 1, 1))
    assert t["chain"] is None and not t["complete"]
    assert any(q["id"] == "T-OVERLAP-A-B" for q in t["questions"])


@pytest.mark.parametrize("period,code", [
    (_p("A", "2019-01-01", "2021-01-01", confirmed=False), "не подтверждено"),
    (_p("A", "01.01.2019", "2021-01-01"), "формате"),
    (_p("A", "2019-01-01", "2030-01-01"), "позже даты расчёта"),
    (_p("A", "2021-01-01", "2019-01-01"), "начало позже конца"),
])
def test_timeline_bad_period_is_question_not_guess(period, code):
    import datetime as dt
    t = mv.residence_timeline(_facts([period]), None, dt.date(2025, 1, 1))
    assert t["periods"] == [] and code in t["questions"][0]["text"]


def test_incomplete_timeline_leaves_continuous_since_empty(world):
    argv, w = world(mutate=lambda f: f["residence_periods"][0].update(confirmed=False))
    res = run(argv)
    assert res["status"] == "PARTIAL_MISSING_DATA"
    ids = {q["id"] for q in res["questions"]}
    assert "T-P1" in ids and "Q-pobyt_na_uzemi_od" in ids
    filled = PdfReader(str(w["out"] / "package" / "zadost_VYPLNENO_NEPODEPSANO.pdf")).get_fields()
    assert filled["pobyt_na_uzemi_od"].get("/V") in (None, "")


# --------------------------------------------------------------------------- approval gate

def test_denied_approval_sends_nothing_and_stays_denied(world):
    argv, w = world()
    first = run(argv)
    assert first["status"] == "WAIT_APPROVAL"
    denied = run([*argv, "--decide", "deny"])
    assert denied["status"] == "DENIED" and denied["exit_code"] == 0
    assert denied["network_requests"] == [] and denied["forbidden_methods_seen_by_fixture_site"] == []
    decision = json.loads((w["out"] / "approval" / "decision.json").read_text(encoding="utf-8"))
    assert decision["sent"] is False and decision["status"] == "DENIED"
    assert approval_row(w["out"], first["approval_id"])["status"] == "rejected"
    again = run(argv)
    assert again["status"] == "DENIED" and again["network_requests"] == []
    assert (w["out"] / "package" / "zadost_VYPLNENO_NEPODEPSANO.pdf").is_file()   # draft preserved


def test_approval_consumed_once_and_replay_has_no_effect(world):
    argv, w = world()
    first = run(argv)
    approved = run([*argv, "--decide", "approve"])
    assert approved["status"] == "APPROVED_FOR_MANUAL_DELIVERY" and approved["network_requests"] == []
    assert approval_row(w["out"], first["approval_id"])["status"] == "consumed"
    replay = run([*argv, "--decide", "approve"])
    assert replay["status"] == "REFUSED" and replay["reason"] == "ALREADY_DECIDED:consumed"
    assert replay["exit_code"] == 21 and replay["network_requests"] == []
    assert state(w["out"])["final_status"] == "APPROVED_FOR_MANUAL_DELIVERY"


def test_decision_refused_when_package_changed_after_review(world):
    argv, w = world()
    first = run(argv)
    draft = w["out"] / "package" / "zadost_VYPLNENO_NEPODEPSANO.pdf"
    draft.write_bytes(draft.read_bytes() + b"\n% tampered\n")
    res = run([*argv, "--decide", "approve"])
    assert res["status"] == "REFUSED" and res["reason"] == "PACKAGE_CHANGED_SINCE_REVIEW"
    assert approval_row(w["out"], first["approval_id"])["status"] == "pending"


def test_decide_without_pending_approval_is_refused(world):
    argv, _ = world("conflicting_fee")
    assert run(argv)["status"] == "PARTIAL_MISSING_DATA"
    res = run([*argv, "--decide", "approve"])
    assert res["status"] == "REFUSED" and res["reason"] == "NO_PENDING_APPROVAL"


# --------------------------------------------------------------------------- restart / resume

def test_restart_before_approval_preserves_draft_without_refetch_or_submit(world):
    argv, w = world()
    first = run(argv)
    draft = w["out"] / "package" / "zadost_VYPLNENO_NEPODEPSANO.pdf"
    before = sha(draft)
    second = run(argv)
    assert second["status"] == "WAIT_APPROVAL" and second["network_requests"] == []
    assert second["approval_id"] == first["approval_id"] and sha(draft) == before
    assert approval_row(w["out"], first["approval_id"])["status"] == "pending"


def test_crash_mid_pipeline_resumes_from_checkpoint(world, monkeypatch):
    argv, w = world()
    def boom(self):
        raise KeyboardInterrupt("simulated process kill")
    monkeypatch.setattr(mv.Pipeline, "step_package", boom)
    with pytest.raises(KeyboardInterrupt):
        run(argv)
    assert state(w["out"])["steps"]["reopen_verify"]["status"] == "done"
    monkeypatch.undo()
    res = run(argv)
    assert res["status"] == "WAIT_APPROVAL" and res["network_requests"] == []


def test_tampered_work_file_is_regenerated_as_new_version(world):
    argv, w = world()
    run(argv)
    (w["out"] / "work" / "filled_v1.pdf").write_bytes(b"garbage")
    res = run(argv)
    assert res["status"] == "WAIT_APPROVAL" and res["network_requests"] == []
    assert state(w["out"])["steps"]["fill"]["result"]["work_file"] == "work/filled_v2.pdf"


def test_changed_facts_rerun_fill_but_not_downloads_and_need_new_approval(world):
    argv, w = world()
    first = run(argv)
    facts = json.loads(w["facts"].read_text(encoding="utf-8"))
    facts["person"]["address_cz"]["value"] = "Fiktivní 2, 000 00 Testov"
    w["facts"].write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    second = run(argv)
    assert second["status"] == "WAIT_APPROVAL" and second["network_requests"] == []
    assert second["approval_id"] != first["approval_id"]


# --------------------------------------------------------------------------- privacy / scope

def test_no_fact_leak_into_logs_stdout_or_unrelated_project(world, capsys):
    argv, w = world()
    unrelated = w["out"].parent / "other_project"
    unrelated.mkdir()
    (unrelated / "notes.txt").write_text("unrelated project", encoding="utf-8")
    before_unrelated = snapshot(unrelated)
    before_owner = snapshot(w["owner"])
    assert mv.main([*argv, "--json"]) == 0
    stdout = capsys.readouterr().out
    log = (w["out"] / "run.log").read_text(encoding="utf-8")
    row = approval_row(w["out"], state(w["out"])["approval"]["id"])
    for value in FAKE_VALUES + ("1990-01-01", "01.01.1990"):
        assert value not in stdout and value not in log and value not in row["preview"]
    assert snapshot(unrelated) == before_unrelated
    assert snapshot(w["owner"]) == before_owner                # owner folder is read-only
    siblings = {p.name for p in w["out"].parent.iterdir()}
    assert "other_project" in siblings and not (unrelated / "package").exists()


def test_out_inside_owner_folder_is_blocked_and_writes_nothing(world, tmp_path):
    argv, w = world(out=tmp_path / "owner" / "bossman_out")
    before = snapshot(w["owner"])
    res = run(argv)
    assert res["status"] == "BLOCKED" and res["reason"] == "OUT_OVERLAPS_OWNER_FOLDER"
    assert snapshot(w["owner"]) == before and not (w["owner"] / "bossman_out").exists()


def test_owner_folder_inside_secrets_dir_is_refused(world, tmp_path):
    argv, w = world()
    secret = tmp_path / ".ssh"
    secret.mkdir()
    argv[argv.index("--owner-folder") + 1] = str(secret)
    res = run(argv)
    assert res["status"] == "BLOCKED" and res["reason"] == "OWNER_FOLDER_NOT_ALLOWED"


# --------------------------------------------------------------------------- forbidden actions

@pytest.mark.parametrize("action", sorted(mv.FORBIDDEN_ACTIONS))
def test_every_forbidden_action_is_refused(action):
    with pytest.raises(mv.ForbiddenAction):
        mv.request_external_action(action)


def test_no_code_path_can_submit_sign_pay_book_or_login():
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    banned_imports = {"smtplib", "ftplib", "webbrowser", "playwright", "selenium", "requests", "httpx",
                      "http.client", "paramiko"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name for a in node.names} & banned_imports
        if isinstance(node, ast.ImportFrom):
            assert node.module not in banned_imports
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "request_external_action":
            lowered = node.name.lower()
            assert not any(w in lowered for w in ("submit", "sign", "pay", "book", "login", "upload", "send")), node.name
        if isinstance(node, ast.Call):
            func = ast.unparse(node.func)
            if func.endswith("Request"):
                kw = {k.arg: ast.unparse(k.value) for k in node.keywords}
                assert kw.get("method") == "'GET'" and "data" not in kw
            if func.endswith((".open", "urlopen")):
                assert "data" not in {k.arg for k in node.keywords} and len(node.args) <= 1
    # the only mention of the external-action entry point is its (always-raising) definition
    assert TOOL.read_text(encoding="utf-8").count("request_external_action(") == 1
