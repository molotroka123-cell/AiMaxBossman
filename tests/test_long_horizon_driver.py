"""tools/long_horizon_run.py - the gaps of run g (01.11): report completeness, memory usefulness, rounds/resume.

Pure logic plus one driver round against an in-process fake backend (no :8801, no PowerShell, no network).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tools import long_horizon_run as lh

TRUTH = {"worst_margin_product": "P-07", "worst_margin_value": -207530.4, "top_customer": "C-0042",
         "top_customer_share_pct": 15.9, "declining_region": "Север", "region_drop_pct": 32.1}
FULL = ("# Отчёт\n"
        "1. Убыточный продукт P-07: маржа -207 530.4\n"
        "2. Топ-клиент C-0042: доля 15.9%\n"
        "3. Регион Север: падение выручки 32.1%\n")


# ------------------------------------------------------------------ report completeness
def test_complete_report_passes():
    res = lh.check_report(FULL, TRUTH)
    assert res["complete"] and res["missing"] == []
    assert all(i["entity_found"] and i["number_found"] for i in res["items"].values())


def test_run_g_report_is_incomplete_without_the_region_finding():
    text = (Path(__file__).resolve().parents[1] / "docs/audits/2026-10-30-long-horizon-run-g/agent_REPORT.md").read_text(encoding="utf-8")
    res = lh.check_report(text, TRUTH)
    assert not res["complete"]
    assert [m.split(":")[0] for m in res["missing"]] == ["region_drop"]


@pytest.mark.parametrize("drop", ["worst_margin", "top_customer", "region_drop"])
def test_one_missing_finding_is_named(drop):
    keep = {"worst_margin": 1, "top_customer": 2, "region_drop": 3}
    lines = [ln for i, ln in enumerate(FULL.splitlines()) if i != keep[drop]]
    res = lh.check_report("\n".join(lines), TRUTH)
    assert not res["complete"]
    assert len(res["missing"]) == 1 and res["missing"][0].startswith(drop) and "not mentioned" in res["missing"][0]


@pytest.mark.parametrize("bad,which", [
    (FULL.replace("32.1%", "23.1%"), "region_drop"),
    (FULL.replace("15.9%", "19.5%"), "top_customer"),
    (FULL.replace("-207 530.4", "-170 530.4"), "worst_margin"),
    (FULL.replace("Убыточный продукт P-07: маржа -207 530.4", "Продукт P-07: маржа 207 530.4"), "worst_margin"),  # sign
])
def test_wrong_number_fails_with_the_numbers_seen(bad, which):
    res = lh.check_report(bad, TRUTH)
    assert not res["complete"]
    assert len(res["missing"]) == 1 and res["missing"][0].startswith(which)
    assert "without the verified number" in res["missing"][0]


@pytest.mark.parametrize("margin", ["−207 530,4", "-207530.4", "–207,530.4", "-207\u00a0530,4", "-207\u202f530.4",
                                    "убыток 207 530,4", "отрицательная маржа 207530.4", "−207,5 тыс.", "-207530"])
def test_margin_number_formats(margin):
    text = FULL.replace("маржа -207 530.4", f"маржа {margin}")
    assert lh.check_report(text, TRUTH)["complete"], (margin, lh.check_report(text, TRUTH))


@pytest.mark.parametrize("share,drop", [("15,9 %", "−32,1 %"), ("15.9", "на 32,1%"), ("— 15,9%", "-32.1 %"),
                                        ("15.94%", "32.08%")])
def test_percent_formats(share, drop):
    text = FULL.replace("доля 15.9%", f"доля {share}").replace("выручки 32.1%", f"выручки {drop}")
    assert lh.check_report(text, TRUTH)["complete"], (share, drop)


def test_number_may_sit_on_the_next_line_but_not_a_neighbours_number():
    ok = "### Регион Север\nпадение: 32.1 %\n" + FULL.splitlines()[1] + "\n" + FULL.splitlines()[2]
    assert lh.check_report(ok, TRUTH)["complete"]
    # the next line names another finding: its 15.9 must not be credited to C-0042 written without a number
    stolen = "Регион Север: падение 32.1%\nКлиент C-0042 крупнейший\nP-07 маржа -207530.4 и 15.9"
    res = lh.check_report(stolen, TRUTH)
    assert not res["complete"] and res["missing"][0].startswith("top_customer")


def test_entity_spelling_variants_and_case():
    text = FULL.replace("P-07", "p\u201107").replace("Север", "СЕВЕРЕ")
    assert lh.check_report(text, TRUTH)["complete"]


def test_parse_numbers_ignores_ids_and_dates_and_keeps_ambiguity():
    assert lh.parse_numbers("C-0042 P-07 2025-01-05") == []
    assert lh.parse_numbers("−1 157") == [-1157.0]
    assert sorted(lh.parse_numbers("32,100")) == [32.1, 32100.0]
    assert lh.parse_numbers("15,9%") == [15.9]
    assert lh.parse_numbers("−207,5 тыс.") == [-207500.0]


# ------------------------------------------------------------------ memory usefulness
CW = "AMBER-OTTER-417"


def _recall(sources, preview=""):
    return [{"sources": sources, "preview": preview}]


def test_memory_present_by_record_id_and_code_word_in_report():
    res = lh.check_memory(_recall(["fact:77", "notes/x.md"]), f"Аудитор: {CW}", CW, ["fact:77", ""], False)
    assert res["ok"] and res["record_in_recall"] and res["code_word_in_report"]


def test_memory_note_ref_matches_absolute_note_path():
    res = lh.check_memory(_recall(["memory/bossman/lh-r1.md"]), f"аудитор: {CW.lower()}", CW,
                          ["", r"D:\vault\memory\bossman\lh-r1.md"], False)
    assert res["ok"] and res["record_in_recall"]


def test_memory_code_word_in_recall_text_counts_as_this_record():
    res = lh.check_memory(_recall(["fact:5"], f"... аудитор: {CW}"), f"Аудитор: {CW}", CW, ["fact:77"], False)
    assert res["ok"] and not res["record_in_recall"] and res["code_word_in_recall"]


def test_memory_fails_when_report_lacks_the_code_word():
    res = lh.check_memory(_recall(["fact:77"], f"аудитор: {CW}"), "Аудитор: не найден в памяти", CW, ["fact:77"], False)
    assert not res["ok"] and "code word" in res["reason"]


def test_memory_fails_without_recall_event_or_with_another_rounds_record():
    assert not lh.check_memory([], f"Аудитор: {CW}", CW, ["fact:77"], False)["ok"]
    other = lh.check_memory(_recall(["fact:76", "fact:770"], "аудитор: COBALT-LYNX-100"), "Аудитор: COBALT-LYNX-100",
                            CW, ["fact:77"], False)
    assert not other["ok"] and other["reason"] == "recall did not surface this round's memory record"


def test_control_passes_only_when_the_code_word_is_absent():
    assert lh.check_memory([], "Аудитор: не найден в памяти", CW, [], True)["ok"]
    assert lh.check_memory(_recall(["fact:76"], "аудитор: COBALT-LYNX-100"), "Аудитор: COBALT-LYNX-100", CW, [], True)["ok"]
    leaked = lh.check_memory([], f"Аудитор: {CW}", CW, [], True)
    assert not leaked["ok"] and "CONTROL BROKEN" in leaked["reason"]


def test_code_word_is_not_secret_shaped_and_varies():
    import random
    words = {lh.make_code_word(random.Random(i)) for i in range(50)}
    assert len(words) > 40
    assert all(lh.FORBIDDEN.search(w) is None for w in words)


# ------------------------------------------------------------------ fresh datasets per round
def test_round_seeds_are_fresh_and_planted_entities_are_the_truth(tmp_path):
    seeds = [lh.round_seed("lh-x", k) for k in range(1, 5)]
    assert len(set(seeds)) == 4 and lh.LEGACY_SEED not in seeds
    for k, seed in enumerate(seeds[:2], 1):
        t = lh.make_dataset(tmp_path / f"w{k}", tmp_path / f"t{k}", 5000, seed)
        pl = t["planted"]
        assert (t["worst_margin_product"], t["top_customer"], t["declining_region"]) == (
            pl["negative_margin_product"], pl["top_customer"], pl["declining_region"])
        assert t["worst_margin_value"] < 0 and t["region_drop_pct"] > 10
        assert json.loads((tmp_path / f"t{k}" / "truth.json").read_text(encoding="utf-8"))["planted"]["seed"] == seed


def test_prompts_carry_no_answers():
    text = " ".join(s["prompt"] for s in lh.steps(Path("."), "lh-x-r2"))
    for leaked in ("P-07", "C-0042", "Север", "207530", "15.9", "32.1"):
        assert leaked not in text
    assert "LongHorizon lh-x-r2" in text


# ------------------------------------------------------------------ rounds bookkeeping / resume
@pytest.mark.parametrize("rec,status,stop,expected", [
    ({"task_id": 1, "verified": True}, "completed", False, "skip"),
    ({}, None, False, "new"),
    ({"task_id": 1}, "running", False, "attach"),
    ({"task_id": 1}, "completed", False, "attach"),                  # finished while the driver was down: verify it
    ({"task_id": 1}, "failed", False, "new"),
    ({"task_id": 1}, "stopped", False, "new"),
    ({"task_id": 1}, "stopped", True, "attach"),                     # our own STOP: same task gets the retry
    ({"task_id": 1, "problems": ["x"], "verified": False}, "completed", False, "new"),
])
def test_step_action(rec, status, stop, expected):
    assert lh.step_action(rec, status, stop) == expected


def _state(rounds_planned=2, rounds=None, soak=None, started_ts=0.0, deadline=None):
    return {"run_id": "lh-x", "rounds_planned": None if soak else rounds_planned, "soak_hours": soak,
            "started_ts": started_ts, "deadline_ts": deadline, "current_round": max([int(k) for k in rounds or {}] or [1]),
            "rounds": rounds or {}}


def _passed_round(k, sha="abc", start=0.0, end=1800.0):
    return {"round": k, "round_id": f"lh-x-r{k}", "seed": k, "sha": sha, "next": 5, "started_ts": start,
            "finished_ts": end, "finished": "t", "interrupt_done": True, "stop_new_calls": 0,
            "steps": {n: {"task_id": i + 10 * k, "verified": True, "attempts": 1} for i, n in enumerate(lh.STEP_NAMES)},
            "report_check": {"complete": True, "missing": []},
            "memory_check": {"mode": "memory", "ok": True, "reason": "ok"}}


def test_resume_point():
    assert lh.resume_point(_state()) == (1, 0)
    r = _passed_round(1)
    r["next"] = 3
    r["steps"]["verify"]["verified"] = False
    assert lh.resume_point(_state(rounds={"1": r})) == (1, 3)
    assert lh.resume_point(_state(rounds={"1": _passed_round(1)}), 10.0) == (2, 0)
    assert lh.resume_point(_state(rounds_planned=1, rounds={"1": _passed_round(1)}), 10.0) == (1, 5)
    soak = _state(rounds={"1": _passed_round(1)}, soak=24, started_ts=0.0)
    assert lh.resume_point(soak, 3600.0) == (2, 0) and lh.resume_point(soak, 25 * 3600.0) == (1, 5)


def test_duplicate_calls_are_counted_across_rounds_and_attempts():
    ok_rows = {1: [(1, "a", "t", "h", "e", "executed"), (2, "b", "t", "h", "e", "executed")],
               2: [(3, "a", "t", "h", "e", "executed")]}                 # same call_id in another task is not a replay
    assert lh.count_duplicated_completed_calls(ok_rows) == 0
    replay = {**ok_rows, 7: [(9, "z", "t", "h", "e", "executed"), (10, "z", "t", "h", "e", "ok"),
                             (11, "y", "t", "h", "e", "executed"), (12, "y", "t", "h", "e", "pending")]}
    assert lh.count_duplicated_completed_calls(replay) == 1


def test_time_for_round_uses_mean_round_duration():
    st = _state(rounds={"1": _passed_round(1, start=0, end=2400)}, deadline=5000.0)
    assert lh.time_for_round(st, 2500.0) and not lh.time_for_round(st, 2700.0)
    assert lh.time_for_round(_state(deadline=None), 1e12)


def test_summarize_pass_fail_partial():
    two = _state(rounds={"1": _passed_round(1), "2": _passed_round(2, start=1800, end=3600)})
    res = lh.summarize(two, {1: 0, 2: 0}, 3600.0)
    assert res["verdict"] == "PASS" and res["rounds_passed"] == 2 and res["soak"]["status"] == "NOT_CLAIMED"
    assert lh.summarize(two, {1: 0, 2: 1}, 3600.0)["verdict"] == "FAIL"
    bad = _passed_round(2)
    bad["stop_new_calls"] = 2
    res = lh.summarize(_state(rounds={"1": _passed_round(1), "2": bad}), {}, 3600.0)
    assert res["verdict"] == "FAIL" and any("after STOP" in x for x in res["reasons"])
    nomem = _passed_round(2)
    nomem["memory_check"] = {"ok": False, "reason": "REPORT.md does not reproduce the memory-only auditor code word"}
    assert lh.summarize(_state(rounds={"1": _passed_round(1), "2": nomem}), {}, 3600.0)["verdict"] == "FAIL"
    capped = _passed_round(2)
    capped.update(next=2, finished_ts=None, finished=None, stopped_by_deadline=True)
    res = lh.summarize(_state(rounds={"1": _passed_round(1), "2": capped}), {}, 3600.0, "max-hours cap reached mid-round")
    assert res["verdict"] == "PARTIAL" and any("max-hours" in x for x in res["reasons"])
    one = lh.summarize(_state(rounds={"1": _passed_round(1)}), {}, 3600.0, "cap would cut round 2")
    assert one["verdict"] == "PARTIAL" and "1 of 2 planned rounds passed" in one["reasons"]
    blocked = _passed_round(2)
    blocked.update(next=4, blocked="report", finished=None, finished_ts=None)
    blocked["steps"]["report"] = {"task_id": 5, "verified": False, "problems": ["REPORT.md misses: region_drop"]}
    assert lh.summarize(_state(rounds={"1": _passed_round(1), "2": blocked}), {}, 3600.0)["verdict"] == "FAIL"
    mixed = _state(rounds={"1": _passed_round(1, sha="a"), "2": _passed_round(2, sha="b")})
    assert lh.summarize(mixed, {}, 3600.0)["verdict"] == "FAIL"


def test_soak_is_only_claimed_by_a_soak_run_that_lasted():
    rounds = {str(k): _passed_round(k) for k in range(1, 4)}
    short = lh.summarize(_state(rounds_planned=3, rounds=rounds), {}, 4 * 3600.0)
    assert short["verdict"] == "PASS" and short["soak"]["status"] == "NOT_CLAIMED"
    soak = _state(rounds=rounds, soak=24, started_ts=0.0)
    assert lh.summarize(soak, {}, 4 * 3600.0)["soak"]["status"] == "NOT_MET"
    assert lh.summarize(soak, {}, 4 * 3600.0)["verdict"] == "PARTIAL"
    done = lh.summarize(soak, {}, 24.2 * 3600.0)
    assert done["verdict"] == "PASS" and done["soak"]["status"] == "SOAK_PASS"


def test_readme_table_has_a_row_per_round():
    st = _state(rounds={"1": _passed_round(1), "2": _passed_round(2)})
    st["agent"] = 52
    text = lh.render_readme(lh.summarize(st, {}, 3600.0), st)
    assert text.count("| PASS |") == 2 and "NOT_CLAIMED" in text and "--no-memory-control" in text


def test_cli_rejects_a_cap_shorter_than_the_soak():
    with pytest.raises(SystemExit):
        lh.main(["start", "--soak-hours", "24", "--max-hours", "4"])


# ------------------------------------------------------------------ one driver against a fake backend
class Crash(Exception):
    pass


class FakeBackend:
    """Tasks complete as soon as they are polled; a fake 'agent' writes correct evidence from truth.json and, in
    memory mode, the code word it could only have learned from the (fake) recall."""

    def __init__(self, truth_root: Path, run_id: str, control=False):
        self.truth_root, self.run_id, self.control = truth_root, run_id, control
        self.tasks: dict[int, dict] = {}
        self.created: list[str] = []
        self.crash_on_first_poll: str | None = None
        self.memory_writes = 0

    def api(self, method, path, body=None, timeout=60, **_):
        if method == "POST" and path == "/api/tasks":
            tid = 100 + len(self.tasks)
            name = body["title"].split()[-1]
            self.tasks[tid] = {"name": name, "ws": Path(body["workspace_path"]), "polls": 0, "status": "running"}
            self.created.append(name)
            return {"id": tid}
        if method == "GET" and path.startswith("/api/tasks/"):
            t = self.tasks[int(path.rsplit("/", 1)[1])]
            t["polls"] += 1
            if self.crash_on_first_poll == t["name"]:
                self.crash_on_first_poll = None
                raise Crash("driver died")
            if t["status"] == "running":
                self._agent(t)
                t["status"] = "completed"
            return {"task": {"status": t["status"]}}
        if path.startswith("/api/memory/"):
            self.memory_writes += 1
            return {"id": 77} if path.endswith("facts") else {"path": "memory/bossman/lh.md"}
        if path.startswith("/api/approvals/auto-full"):
            return {"current": {"id": 5, "folder": "x"}} if method == "POST" and path.endswith("auto-full") else {}
        if path.startswith("/api/approvals"):
            return []
        return {}

    def _truth(self, ws: Path) -> tuple[dict, Path]:
        k = ws.name.rsplit("-r", 1)[1]
        td = self.truth_root / self.run_id / f"r{k}"
        return json.loads((td / "truth.json").read_text(encoding="utf-8")), td

    def _agent(self, t):
        ws, name = t["ws"], t["name"]
        truth, td = self._truth(ws)
        (ws / "out").mkdir(exist_ok=True)
        (ws / "notes").mkdir(exist_ok=True)
        if name == "profile":
            (ws / "out/profile.json").write_text(json.dumps({k: truth[k] for k in ("duplicate_rows", "unique_orders")}))
            (ws / "notes/01_profile.md").write_text("ok")
        elif name == "metrics":
            with (ws / "out/product_margin.csv").open("w", newline="") as f:
                csv.writer(f).writerows([["product", "revenue", "margin"], [truth["worst_margin_product"], 1, -5], ["P-99", 1, 9]])
            (ws / "out/monthly_region.csv").write_text("region,month,revenue\nx,1,1\n")
            (ws / "out/top_customers.csv").write_text("customer_id,revenue,share_pct\nx,1,1\n")
        elif name == "findings":
            (ws / "notes/03_findings.md").write_text(
                f"{truth['worst_margin_product']} {truth['top_customer']} {truth['declining_region']}", encoding="utf-8")
        elif name == "verify":
            (ws / "out/verify.py").write_text("pass")
            (ws / "out/verify.json").write_text(json.dumps({
                "worst_margin_product": {"product": truth["worst_margin_product"], "ok": True},
                "top_customer": {"customer_id": truth["top_customer"], "ok": True},
                "region_drop": {"region": truth["declining_region"], "ok": True}}, ensure_ascii=False), encoding="utf-8")
        elif name == "report":
            cw = "не найден в памяти" if self.control else json.loads(
                (td / "memory_code_word.json").read_text(encoding="utf-8"))["code_word"]
            (ws / "REPORT.md").write_text(
                f"1. {truth['worst_margin_product']} маржа {truth['worst_margin_value']}\n"
                f"2. {truth['top_customer']} доля {truth['top_customer_share_pct']} %\n"
                f"3. {truth['declining_region']} падение {truth['region_drop_pct']} %\nАудитор: {cw}\n", encoding="utf-8")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(lh, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(lh, "TRUTH_ROOT", tmp_path / "truth")
    monkeypatch.setattr(lh, "ROOT", tmp_path / "repo")

    def build(run_id="lh-t", rounds=2, control=False, max_hours=None):
        fake = FakeBackend(tmp_path / "truth", run_id, control)
        monkeypatch.setattr(lh, "_api", lambda: fake)
        monkeypatch.setattr(lh.Driver, "identity", lambda self: {"runtime": "sha1", "head": "sha1"})
        monkeypatch.setattr(lh.Driver, "tool_rows",
                            lambda self, tid: [(tid, f"c{tid}", "terminal.run", "h", "e", "executed")])

        def interrupt(self, task_id, calls):
            self.rsave(interrupt_done=True, interrupt_task=task_id)

        def after_stop(self, task_id):
            self.rsave(stop_new_calls=0, stop_pending=False)

        def recall(self, task_id):
            if control:
                return []
            cw = self.code_word()
            return [{"sources": ["fact:77", "memory/bossman/lh.md"], "preview": f"аудитор: {cw}"}]

        monkeypatch.setattr(lh.Driver, "_do_interrupt", interrupt)
        monkeypatch.setattr(lh.Driver, "_after_stop", after_stop)
        monkeypatch.setattr(lh.Driver, "_recall_rows", recall)
        state = lh.new_state(run_id, 52, rows=3000, rounds=rounds, max_hours=max_hours, soak_hours=None, control=control)
        return fake, lh.Driver(run_id, 52, new=state)
    return build


def test_two_rounds_pass_with_fresh_datasets_and_receipts(rig, tmp_path):
    fake, d = rig(rounds=2)
    assert d.run() == 0
    res = d.state["result"]
    assert res["verdict"] == "PASS" and res["rounds_passed"] == 2
    assert fake.created == list(lh.STEP_NAMES) * 2                    # one task per step per round, no repeats
    r1, r2 = d.state["rounds"]["1"], d.state["rounds"]["2"]
    assert r1["seed"] != r2["seed"] and r1["round_id"] != r2["round_id"]
    assert all(r["interrupt_done"] and r["stop_new_calls"] == 0 and r["memory_check"]["ok"] for r in (r1, r2))
    out = tmp_path / "repo" / "docs" / "audits" / f"{d.state['started'][:10]}-long-horizon-lh-t"
    assert {p.name for p in out.iterdir()} >= {"README.md", "state.json", "driver.log", "r1", "r2"}
    t1 = json.loads((out / "r1" / "truth.json").read_text(encoding="utf-8"))
    assert t1["memory_code_word"] and t1["planted"]["seed"] == r1["seed"]
    # the code word never lands in an agent-visible folder or in the state (only its hash)
    agent_files = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in (tmp_path / "runs").rglob("*")
                          if p.is_file() and p.name != "REPORT.md")
    assert t1["memory_code_word"] not in agent_files
    assert t1["memory_code_word"] not in (tmp_path / "truth" / "lh-t" / "state.json").read_text(encoding="utf-8")


def test_resume_after_driver_crash_attaches_to_the_same_task(rig):
    fake, d = rig(rounds=1)
    fake.crash_on_first_poll = "findings"
    with pytest.raises(Crash):
        d.run()
    assert fake.created == ["profile", "metrics", "findings"]
    st = json.loads(d.state_path.read_text(encoding="utf-8"))
    assert lh.resume_point(st) == (1, 2)
    again = lh.Driver("lh-t", 52)
    assert again.run() == 0
    assert fake.created == list(lh.STEP_NAMES)                        # findings was NOT started a second time
    rec = again.state["rounds"]["1"]["steps"]["findings"]
    assert rec["attempts"] == 1 and rec["task_ids"] == [rec["task_id"]]


def test_control_run_writes_no_memory_and_passes_only_without_the_code_word(rig):
    fake, d = rig(rounds=1, control=True)
    assert d.run() == 0
    assert fake.memory_writes == 0
    mc = d.state["rounds"]["1"]["memory_check"]
    assert mc["mode"] == "control" and mc["ok"] and not mc["code_word_in_report"]
    assert d.state["result"]["mode"] == "no-memory-control"


def test_max_hours_cap_ends_partial(rig, monkeypatch):
    fake, d = rig(rounds=3, max_hours=1.0)
    real = lh.Driver.run_round

    def one_then_expire(self, k, ident):
        out = real(self, k, ident)
        self.state["deadline_ts"] = 0.0                               # the wall clock passes the cap after round 1
        return out
    monkeypatch.setattr(lh.Driver, "run_round", one_then_expire)
    assert d.run() == 1
    res = d.state["result"]
    assert res["verdict"] == "PARTIAL" and res["rounds_passed"] == 1
    assert any("max-hours" in x for x in res["reasons"])
