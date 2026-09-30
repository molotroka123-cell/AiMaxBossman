"""Motion Studio: the spec contract a local model writes, and the generate/repair loop."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "tools" / "motion_studio"
sys.path.insert(0, str(ROOT))

import generate_spec  # noqa: E402
import spec  # noqa: E402

EXAMPLES = sorted((ROOT / "examples").glob("*.json"))


def _load(name):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


def test_every_shipped_example_is_valid():
    assert EXAMPLES
    for path in EXAMPLES:
        assert spec.validate(json.loads(path.read_text(encoding="utf-8"))) == [], path.name


def test_dataset_rows_hold_valid_specs_with_brief_and_facts():
    rows = [json.loads(x) for x in (ROOT / "dataset" / "brief_to_spec.jsonl").read_text(encoding="utf-8").splitlines() if x]
    assert rows
    for row in rows:
        assert row["brief"] and isinstance(row["facts"], dict)
        assert spec.validate(row["spec"]) == [], row["id"]


def test_validator_rejects_broken_specs_with_actionable_messages():
    good = _load("bossman_32_days.json")
    cases = []
    gap = copy.deepcopy(good); gap["scenes"][1]["start"] = 3.2
    cases.append((gap, "must start where the previous scene ends"))
    no_disc = copy.deepcopy(good); del no_disc["scenes"][6]["disclaimer"]
    cases.append((no_disc, "'disclaimer' is required"))
    overlap = copy.deepcopy(good); overlap["scenes"][0]["vo"][1]["t"] = 0.6
    cases.append((overlap, "voice-over overlaps"))
    long_title = copy.deepcopy(good); long_title["scenes"][0]["title"] = "A" * 30
    cases.append((long_title, "will not fit on screen"))
    bad_type = copy.deepcopy(good); bad_type["scenes"][2]["type"] = "explosion"
    cases.append((bad_type, "unknown type"))
    fake_numbers = copy.deepcopy(good); fake_numbers["scenes"][1]["values"] = ["lots", 3, 4]
    cases.append((fake_numbers, "real data only"))
    digits_vo = copy.deepcopy(good); digits_vo["scenes"][4]["vo"][0]["text"] = "Version 1.7 — 100%"
    cases.append((digits_vo, "plain English"))
    for broken, needle in cases:
        errors = spec.validate(broken)
        assert any(needle in e for e in errors), (needle, errors)


def test_hits_are_scene_cuts_and_item_pops():
    h = spec.hits(_load("jeff_voice_12s.json"))
    assert h == [2.5, 6.0, 6.1, 7.0, 7.9, 9.5]


def test_extract_json_survives_thinking_and_fences():
    assert generate_spec.extract_json('<think>hmm {"x":1}</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert generate_spec.extract_json("no json here") is None


def test_generate_repairs_an_invalid_draft_using_validator_feedback():
    good = _load("jeff_voice_12s.json")
    bad = copy.deepcopy(good); bad["scenes"][1]["start"] = 3.0
    replies = iter([json.dumps(bad), "```json\n" + json.dumps(good) + "\n```"])
    seen = []

    def chat(messages):
        seen.append(messages[-1]["content"])
        return next(replies)

    result, errors, transcript = generate_spec.generate("12 s clip about Jeff voice", {"voice_in": "live"}, chat, tries=3)
    assert errors == [] and result == good
    assert "must start where the previous scene ends" in seen[1]      # the model was told what to fix
    assert transcript[-1]["role"] == "assistant"


def test_generate_reports_failure_honestly_when_the_model_never_fixes_it():
    replies = iter(["not json", "still not json"])
    result, errors, _ = generate_spec.generate("x", None, lambda m: next(replies), tries=2)
    assert result is None and errors == ["the reply was not a single JSON object"]


# ---------------------------------------------------------------- library, Lottie catalog
import lottie_assets  # noqa: E402

LIBRARY = sorted((ROOT / "library").glob("*.json"))


def test_library_has_100_valid_scenarios_and_every_approved_one_is_in_the_dataset():
    assert len(LIBRARY) == 100
    rows = {json.loads(x)["id"]: json.loads(x) for x in
            (ROOT / "dataset" / "brief_to_spec.jsonl").read_text(encoding="utf-8").splitlines() if x}
    for path in LIBRARY:
        spec_obj = json.loads(path.read_text(encoding="utf-8"))
        assert spec.validate(spec_obj) == [], path.name
        if _is_candidate(spec_obj):
            continue                                                     # candidates live in their own file
        assert rows[f"lib_{path.stem}"]["spec"] == spec_obj              # dataset matches the file


def test_lottie_catalog_pins_100_licensed_files():
    cat = lottie_assets.catalog()
    assert cat["license"] == "CC-BY-4.0" and "Noto" in cat["attribution"]
    items = cat["items"]
    assert len(items) == 100 and len({i["id"] for i in items}) == 100
    for i in items:
        assert i["url"].startswith("https://fonts.gstatic.com/s/e/notoemoji/")
        assert len(i["sha256"]) == 64 and i["frames"] > 0


def test_lottie_icons_and_stickers_accept_catalog_ids_and_reject_unknown_ones():
    good = _load("jeff_voice_12s.json")
    ok = copy.deepcopy(good)
    ok["scenes"][2]["items"][0]["icon"] = "lottie:noto_rocket"
    assert spec.validate(ok) == []
    assert lottie_assets.used_ids(ok) == {"noto_rocket"}
    bad = copy.deepcopy(good)
    bad["scenes"][2]["items"][0]["icon"] = "lottie:definitely_not_in_catalog"
    assert any("lottie:<catalog id>" in e for e in spec.validate(bad))
    sticker = copy.deepcopy(good)
    sticker["scenes"][3] = {"type": "sticker", "start": 9.5, "end": 12.0, "lottie": "noto_fire", "text": "HOT"}
    assert spec.validate(sticker) == []
    sticker["scenes"][3]["lottie"] = "made_up"
    assert any("'lottie' must be an id" in e for e in spec.validate(sticker))


def test_fetch_rejects_a_file_whose_hash_changed(tmp_path, monkeypatch):
    item = lottie_assets.catalog()["items"][0]

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"tampered": true}'

    monkeypatch.setattr(lottie_assets.urllib.request, "urlopen", lambda *a, **k: Resp())
    status = lottie_assets.fetch(tmp_path, {item["id"]})
    assert "sha256 mismatch" in status[item["id"]]
    assert not (tmp_path / f"{item['id']}.json").exists()


def test_generate_survives_a_timed_out_call_and_reports_it():
    good = _load("jeff_voice_12s.json")
    calls = iter([TimeoutError("timed out"), json.dumps(good)])

    def chat(messages):
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    result, errors, _ = generate_spec.generate("x", None, chat, tries=2)
    assert result == good and errors == []
    only_timeouts = iter([TimeoutError("timed out")] * 2)
    result, errors, _ = generate_spec.generate("x", None, lambda m: (_ for _ in ()).throw(next(only_timeouts)), tries=2)
    assert result is None and errors[0].startswith("model call failed: TimeoutError")


def test_ollama_native_call_disables_thinking(monkeypatch):
    sent = {}

    def fake_post(url, payload, timeout):
        sent.update(url=url, payload=payload)
        return {"message": {"content": "{}"}}

    monkeypatch.setattr(generate_spec, "_post", fake_post)
    assert generate_spec._chat_ollama("http://127.0.0.1:11434/v1", "m", [], 5, 100) == "{}"
    assert sent["url"] == "http://127.0.0.1:11434/api/chat"
    assert sent["payload"]["think"] is False and sent["payload"]["format"] == "json"
    assert sent["payload"]["options"]["num_predict"] == 100


def test_ollama_call_keeps_the_loaded_context_size_and_records_tokens(monkeypatch):
    # rc19 owner-PC audit: the shared model runs with num_ctx 32768; a fixed 16384 forced Ollama to
    # reload ~27 GB (and evict the instance Jeff uses) on every generation run.
    sent = {}

    def fake_post(url, payload, timeout):
        sent.update(payload=payload)
        return {"message": {"content": "{}"}, "prompt_eval_count": 2100, "eval_count": 640,
                "load_duration": 150_000_000, "total_duration": 9_000_000_000, "done_reason": "stop"}

    monkeypatch.setattr(generate_spec, "_post", fake_post)
    stats = []
    generate_spec._chat_ollama("http://127.0.0.1:11434", "m", [], 5, 100, stats=stats)
    assert "num_ctx" not in sent["payload"]["options"]
    assert stats == [{"prompt_tokens": 2100, "output_tokens": 640, "load_s": 0.15, "total_s": 9.0,
                      "done_reason": "stop"}]
    generate_spec._chat_ollama("http://127.0.0.1:11434", "m", [], 5, 100, num_ctx=32768)
    assert sent["payload"]["options"]["num_ctx"] == 32768                # only when explicitly asked


def test_a_reply_cut_at_max_tokens_is_reported_as_truncation_and_retried(monkeypatch):
    good = _load("jeff_voice_12s.json")
    replies = iter([{"message": {"content": '{"meta": {"title": "Jeff'}, "eval_count": 100, "done_reason": "length"},
                    {"message": {"content": json.dumps(good)}, "eval_count": 400, "done_reason": "stop"}])
    monkeypatch.setattr(generate_spec, "_post", lambda url, payload, timeout: next(replies))
    stats = []
    seen = []

    def chat(messages):
        seen.append(messages[-1]["content"])
        return generate_spec._chat_ollama("http://127.0.0.1:11434", "m", messages, 5, 100, stats=stats)

    result, errors, _ = generate_spec.generate("x", None, chat, tries=2)
    assert result == good and errors == []
    assert "cut off" in seen[1] and [s["done_reason"] for s in stats] == ["length", "stop"]
    replies = iter([{"message": {"content": "{"}, "eval_count": 100, "done_reason": "length"}] * 2)
    result, errors, _ = generate_spec.generate("x", None, chat, tries=2)
    assert result is None and errors[0].startswith("reply cut off at max_tokens=100")


def test_length_errors_carry_a_fitting_candidate_and_vo_overlaps_a_concrete_time():
    # rc19 owner-PC run: Qwen stayed at 11-12 chars for a 10-char field for three retries and moved
    # voice lines by 0.1 s per retry; the feedback now names a text that fits and the earliest valid t.
    good = _load("jeff_voice_12s.json")
    bad = copy.deepcopy(good)
    bad["scenes"][0]["title"] = "COMMITS · 7D WEEK"
    bad["scenes"][1]["vo"][1]["t"] = 3.0          # first line "Send a voice note on Telegram." starts at 2.6
    errors = spec.validate(bad)
    assert any('e.g. "COMMITS · 7D"' in e for e in errors), errors
    assert spec.shorten("COMMITS · 7D", 10) == "COMMITS"
    assert all(len(spec.shorten(x, 12)) <= 12 for x in ("A" * 40, "BOSSMAN · MOTION STUDIO", "x y"))
    overlap = [e for e in errors if "voice-over overlaps" in e]
    assert overlap and "set its t to 4.00 or later" in overlap[0]       # 2.6 + 0.3 + 30/24 - 0.15
    fixed = copy.deepcopy(bad); fixed["scenes"][0]["title"] = "VOICE"; fixed["scenes"][1]["vo"][1]["t"] = 4.0
    fixed["scenes"][1]["vo"][1]["text"] = "Local."
    assert spec.validate(fixed) == []


def test_axis_labels_must_be_short_text_not_numbers():
    spec_obj = json.loads((ROOT / "library" / "bossman_velocity.json").read_text(encoding="utf-8"))
    bars = next(i for i, s in enumerate(spec_obj["scenes"]) if s["type"] == "bars")
    spec_obj["scenes"][bars]["x_from"] = 0          # what the local model wrote in the rc19 run
    assert any("'x_from' must be a non-empty string" in e for e in spec.validate(spec_obj))


def test_generate_tells_the_model_when_it_repeats_the_same_draft():
    good = _load("jeff_voice_12s.json")
    bad = copy.deepcopy(good); bad["scenes"][0]["title"] = "A" * 30
    replies = iter([json.dumps(bad), json.dumps(bad), json.dumps(good)])
    seen = []

    def chat(messages):
        seen.append(messages[-1]["content"])
        return next(replies)

    result, errors, _ = generate_spec.generate("x", None, chat, tries=3)
    assert result == good and errors == []
    assert seen[1].startswith("Fix these problems") and "repeated the previous JSON" in seen[2]


def test_numbers_not_in_facts_are_flagged_for_the_owner():
    good = _load("jeff_voice_12s.json")
    flags = generate_spec.unsupported_numbers(good, {"voice_in": "live"}, "12 s clip about Jeff voice")
    assert any("items[2].sub: the number 1.8" in f for f in flags)          # "next · v1.8"
    assert any("tagline: the number 2026" in f for f in flags)              # "SEP 2026"
    assert not any(".t:" in f or "start" in f for f in flags)               # timings are not content
    ok = generate_spec.unsupported_numbers(good, {"voice_out": "next in v1.8", "month": "SEP 2026"}, "12 s clip")
    assert ok == []


def test_errors_name_the_offending_value_and_say_when_a_scene_has_no_room_left():
    # rc19 owner-PC run: with only "'line' is 23 chars" the model shortened the other line of the item,
    # and it kept "lottie:noto_mic" (not in the catalog) for three retries.
    good = _load("jeff_voice_12s.json")
    bad = copy.deepcopy(good)
    bad["scenes"][2]["items"][2]["icon"] = "lottie:noto_mic"
    bad["scenes"][2]["items"][0]["title"] = "HEARS YOU WELL"  # 14 chars: fits
    bad["scenes"][2]["items"][1]["sub"] = "audio stays on your own PC"
    bad["scenes"][3]["vo"][0]["text"] = "This is Jeff, and this line is far too long for its own scene."
    bad["scenes"][3]["vo"].append({"t": 11.9, "text": "Bye."})
    errors = spec.validate(bad)
    assert any("'icon' 'lottie:noto_mic' is not allowed" in e for e in errors), errors
    assert any("'sub' \"audio stays on your own PC\" is 26 chars" in e for e in errors), errors
    assert any("scenes[3].vo[1]" in e and "no room left before this scene ends at 12.0" in e for e in errors), errors
    early = copy.deepcopy(good); early["scenes"][1]["vo"][0]["t"] = 1.0
    assert any("must be inside the scene [2.5, 6.0)" in e for e in spec.validate(early))


def test_voice_over_digits_are_rejected_because_they_break_the_timing_estimate():
    # rc19 owner-PC run: a VALID spec with "960 commits in the last 7 days." (estimated 1.6 s, spoken 2.6 s)
    # stopped make_video on a real voice-over overlap after the model and validator had accepted it.
    good = _load("jeff_voice_12s.json")
    digits = copy.deepcopy(good); digits["scenes"][0]["vo"][0]["text"] = "960 commits in the last 7 days."
    assert any("numbers spelled out in words" in e for e in spec.validate(digits))
    words = copy.deepcopy(good); words["scenes"][0]["vo"][0]["text"] = "Nine hundred sixty commits."
    assert spec.validate(words) == []


def _vo(t, seconds, end, text="x"):
    return {"t": t, "seconds": seconds, "scene_end": end, "text": text}


def test_measured_voice_overlaps_are_cleared_by_a_short_delay_or_reported():
    # rc19 owner-PC renders: two VALID generated specs stopped on 0.17 s and 0.34 s overlaps that the
    # character estimate missed (Kokoro ran up to 0.27 s longer). make_video now places lines by their
    # measured length with a bounded delay; a real clash still stops with the old message.
    placed = spec.schedule_voice([_vo(3.0, 1.0, 6.0, "b"), _vo(0.3, 3.02, 3.0, "a")], 10.0)
    assert [p["text"] for p in placed] == ["a", "b"]
    assert placed[1]["t"] == 3.17 and placed[1]["t_spec"] == 3.0 and placed[1]["shift"] == 0.17
    assert placed[0]["shift"] == 0
    with pytest.raises(ValueError, match="voice-over overlap: 'a' ends at 3.90 s"):
        spec.schedule_voice([_vo(0.3, 3.6, 3.0, "a"), _vo(3.0, 1.0, 6.0, "b")], 10.0)     # 0.6 s > max shift
    with pytest.raises(ValueError, match="voice-over overlap"):
        spec.schedule_voice([_vo(0.3, 3.0, 3.0, "a"), _vo(3.0, 1.0, 3.1, "b")], 10.0)     # would leave its scene
    with pytest.raises(ValueError, match="runs past the end"):
        spec.schedule_voice([_vo(9.0, 1.6, 10.0, "late")], 10.0)


def test_logo_build_never_gets_a_negative_length():
    # rc19 owner-PC render: VALID 5 s spec title(0-2.5) -> logo(2.5-5) crashed score.py
    # ("negative dimensions are not allowed") because the build into the logo had 0 s.
    two = {"meta": {"duration": 5.0}, "scenes": [{"type": "title", "start": 0.0, "end": 2.5},
                                                 {"type": "logo", "start": 2.5, "end": 5.0}]}
    assert spec.logo_build_seconds(two) == 0.0
    first = {"meta": {"duration": 5.0}, "scenes": [{"type": "logo", "start": 0.0, "end": 2.5},
                                                   {"type": "end_card", "start": 2.5, "end": 5.0}]}
    assert spec.logo_build_seconds(first) == 0.0
    assert spec.logo_build_seconds(json.loads((ROOT / "examples" / "bossman_32_days.json").read_text(encoding="utf-8"))) == 2.5
    assert spec.logo_build_seconds(_load("jeff_voice_12s.json")) == 2.5
    assert spec.logo_build_seconds({"meta": {"duration": 5.0}, "scenes": [{"type": "title", "start": 0, "end": 5}]}) == 0.0


def test_bars_headline_label_is_placed_after_the_counter_it_follows():
    # rc19 owner-PC render (7 daily values): the counter is drawn zero-padded ("05") but the label
    # offset measured the unpadded length ("7"), so "COMMITS" overlapped the second digit. Library
    # specs have 10+ values, where both strings have two digits, which hid it. Source-level guard:
    # the offset must measure the same padded string the counter draws.
    html = (ROOT / "engine.html").read_text(encoding="utf-8")
    body = html[html.index("function sBars"):html.index("function drawIcon")]
    assert "text(String(n).padStart(2, '0'), 0, 0, '800 150px Onest'" in body
    assert "ctx.measureText(String(V.length).padStart(2, '0')).width + 24" in body


# ---------------------------------------------------------------- the 100-scenario library, two provenance classes
# The library holds 44 owner-facing scenarios plus 56 Claude-written candidates. The candidates must stay
# valid and render-checkable, stay out of the file the trainer reads, and stay distinguishable from the 44:
# neither renamed copies of them nor clones of each other.
import collections  # noqa: E402
import re  # noqa: E402

sys.path.insert(0, str(ROOT / "library"))
import build_library  # noqa: E402

APPROVED_IDS = frozenset({"app_changelog", "app_rating", "birthday", "bossman_capabilities",
    "bossman_first_commit", "bossman_privacy", "bossman_roadmap", "bossman_tests", "bossman_velocity",
    "channel_intro", "charity_drive", "coming_soon", "community_call", "countdown_launch", "course_launch",
    "event_teaser", "feature_compare", "fitness_progress", "follower_milestone", "game_trailer", "hiring",
    "jeff_voice_notes", "motion_studio_intro", "new_year", "newsletter_teaser", "partner_announcement",
    "podcast_teaser", "pricing_tiers", "product_launch", "reel_hook", "restaurant_special", "sale_promo",
    "security_update", "sprint_review", "study_progress", "talk_intro", "team_intro", "thank_you", "travel_recap",
    "tutorial_steps", "uptime_report", "webinar_invite", "weekly_report", "year_in_review"})
CANDIDATE_FILE = ROOT / "dataset" / "candidates" / "motion_animation_56.jsonl"


def _is_candidate(spec_obj):
    """A scenario counts as a candidate only when its own meta says so, both fields present."""
    meta = spec_obj.get("meta", {})
    return meta.get("status") == "candidate" and bool(meta.get("provenance"))


def _library():
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in LIBRARY}


def _candidate_rows():
    return [json.loads(x) for x in CANDIDATE_FILE.read_text(encoding="utf-8").splitlines() if x]


def _signature(spec_obj):
    """Composition + rhythm: what a renamed copy of another scenario would still share."""
    return (tuple(sc["type"] for sc in spec_obj["scenes"]),
            tuple(round(sc["end"] - sc["start"], 2) for sc in spec_obj["scenes"]))


def _twins(specs):
    """{name: [other names with the same composition and rhythm]} - empty values mean all distinct."""
    sigs = {name: _signature(s) for name, s in specs.items()}
    return {name: sorted(o for o, s in sigs.items() if s == sig and o != name) for name, sig in sigs.items()}


def test_library_splits_into_44_approved_and_56_candidate_scenarios():
    lib = _library()
    assert len(lib) == 100
    approved = {k for k, v in lib.items() if not _is_candidate(v)}
    candidates = {k for k, v in lib.items() if _is_candidate(v)}
    assert approved == APPROVED_IDS                      # no old scenario renamed, dropped or reclassified
    assert len(candidates) == 56
    assert approved.isdisjoint(candidates)


def test_a_candidate_is_only_a_candidate_when_its_own_meta_says_so():
    lib = _library()
    good = lib["intro_freelancer"]
    assert _is_candidate(good)
    for drop in ("status", "provenance"):                # negative control: a half-marked spec is not one
        half = copy.deepcopy(good)
        del half["meta"][drop]
        assert not _is_candidate(half), drop
    wrong = copy.deepcopy(good)
    wrong["meta"]["status"] = "approved"
    assert not _is_candidate(wrong)
    assert not _is_candidate(lib["bossman_velocity"])    # a real approved scenario claims nothing


def test_candidates_stay_out_of_the_file_the_trainer_reads():
    rows = [json.loads(x) for x in
            (ROOT / "dataset" / "brief_to_spec.jsonl").read_text(encoding="utf-8").splitlines() if x]
    train_ids = {r["id"] for r in rows}
    assert train_ids == {f"lib_{i}" for i in APPROVED_IDS} | {"bossman_32_days_v1", "jeff_voice_12s_v1"}
    for cid in (k for k, v in _library().items() if _is_candidate(v)):
        assert f"lib_{cid}" not in train_ids and f"cand_{cid}" not in train_ids   # no leak under either id form
    for row in rows:
        assert not _is_candidate(row["spec"]), row["id"]
    trainer = (ROOT / "finetune_lora.py").read_text(encoding="utf-8")
    assert 'HERE / "dataset" / "brief_to_spec.jsonl"' in trainer      # the trainer's only default input
    assert "candidates" not in trainer                                # and it never reaches the candidate file


def test_every_candidate_row_carries_explicit_unapproved_provenance():
    rows = _candidate_rows()
    lib = _library()
    assert len(rows) == 56
    assert {r["id"] for r in rows} == {f"cand_{k}" for k, v in lib.items() if _is_candidate(v)}
    for r in rows:
        stem = r["id"][len("cand_"):]
        assert r["status"] == "candidate"
        assert r["owner_approved"] is False
        assert r["training_use"] == "excluded"
        assert "not owner-approved" in r["provenance"]
        assert "candidate" in r["source"]
        assert r["spec"] == lib[stem]                    # dataset row matches the file on disk
        assert r["brief"] and isinstance(r["facts"], dict)
        assert spec.validate(r["spec"]) == [], r["id"]
        assert r["facts"]["sample"] is True and r["facts"]["placeholder"] is True
        assert r["facts"]["owner_approved"] is False


def test_no_scenario_is_a_renamed_or_cloned_copy_of_another():
    lib = _library()
    bodies = [json.dumps(v, sort_keys=True) for v in lib.values()]
    assert len(set(bodies)) == 100                       # no byte-level duplicate under a new name
    twins = _twins(lib)
    for name, v in lib.items():                          # every candidate is structurally its own film
        if _is_candidate(v):
            assert twins[name] == [], (name, twins[name])
    assert len(set(r["brief"] for r in _candidate_rows())) == 56
    cloned = dict(lib)                                   # negative control: the check does catch a clone
    cloned["intro_freelancer_copy"] = copy.deepcopy(lib["intro_freelancer"])
    assert _twins(cloned)["intro_freelancer"] == ["intro_freelancer_copy"]


def test_candidates_cover_varied_compositions_rhythms_and_every_scene_type():
    cand = [v for v in _library().values() if _is_candidate(v)]
    assert len({_signature(v)[0] for v in cand}) >= 30       # measured 35 distinct scene-type sequences
    assert len({v["meta"]["bpm"] for v in cand}) >= 20       # measured 27 distinct tempos
    assert {v["meta"]["key"] for v in cand} == set("CDEFGAB")
    assert len({v["meta"]["duration"] for v in cand}) >= 12
    assert {sc["type"] for v in cand for sc in v["scenes"]} == set(spec.SCENE_TYPES)   # all nine types
    assert max(len(v["scenes"]) for v in cand) >= 5


def test_every_candidate_brief_states_the_length_its_spec_actually_runs():
    # The rows are brief -> spec training shape, so a brief that says "12 s" over a 13 s spec teaches
    # the wrong mapping. Retiming a scenario without retyping its brief is the way this drifts.
    for r in _candidate_rows():
        stated = re.match(r"([\d.]+) s ", r["brief"])
        assert stated, r["brief"]
        assert float(stated.group(1)) == r["spec"]["meta"]["duration"], (r["id"], r["brief"])
    drifted = copy.deepcopy(_candidate_rows()[0])                    # negative control
    drifted["spec"]["meta"]["duration"] += 1
    stated = float(re.match(r"([\d.]+) s ", drifted["brief"]).group(1))
    assert stated != drifted["spec"]["meta"]["duration"]


def test_candidates_only_show_numbers_that_come_from_their_own_facts():
    rows = _candidate_rows()
    for r in rows:
        assert generate_spec.unsupported_numbers(r["spec"], r["facts"], r["brief"]) == [], r["id"]
    faked = copy.deepcopy(next(r for r in rows if r["id"] == "cand_intro_developer"))
    faked["spec"]["scenes"][2]["items"][0]["sub"] = "97 percent faster"    # negative control
    assert generate_spec.unsupported_numbers(faked["spec"], faked["facts"], faked["brief"])


def test_candidates_use_only_scene_fields_the_engine_actually_draws():
    engine = (ROOT / "engine.html").read_text(encoding="utf-8")
    structural = {"type", "start", "end", "vo"}
    for name, v in _library().items():
        if not _is_candidate(v):
            continue
        for sc in v["scenes"]:
            for key in set(sc) - structural:
                assert re.search(r"sc\." + key + r"\b", engine), (name, key)
    # spec.py accepts bars.headline, but engine.html never reads it and the validator never measures its
    # length, so a scenario using it would silently drop that text. Nothing in the library may rely on it.
    assert not re.search(r"sc\.headline\b", engine)
    assert all("headline" not in sc for v in _library().values() for sc in v["scenes"])


def test_candidates_claim_no_format_or_voice_the_pipeline_cannot_deliver():
    engine = (ROOT / "engine.html").read_text(encoding="utf-8")
    assert 'width="1920" height="1080"' in engine and "const W = 1920, H = 1080" in engine
    cyrillic = re.compile(r"[Ѐ-ӿ]")
    for name, v in _library().items():
        if not _is_candidate(v):
            continue
        assert v["meta"].get("voice") == "am_fenrir", name
        # No Cyrillic anywhere: Motion Studio has no Russian TTS, and a voice scene's `lines` and `status`
        # strings are the one place the validator checks no character set at all.
        assert not cyrillic.search(json.dumps(v, ensure_ascii=False)), name
        for claim in ("vertical", "9:16", "1080x1920"):
            assert claim not in json.dumps(v).lower(), (name, claim)


def test_candidate_lottie_use_mixes_with_the_pinned_catalog():
    catalog_ids = lottie_assets.ids()
    lib = _library()
    cand = {k: v for k, v in lib.items() if _is_candidate(v)}
    used = set().union(*(lottie_assets.used_ids(v) for v in cand.values()))
    assert used <= catalog_ids and len(used) >= 30            # measured 39 distinct catalog animations
    already = set().union(*(lottie_assets.used_ids(v) for k, v in lib.items() if k not in cand))
    assert len(used - already) >= 20                          # measured 31 animations the 44 never used
    # Representative compatibility matrix: a spread of catalog ids dropped into a spread of candidate
    # compositions stays valid, in both the sticker slot and the card-icon slot.
    probe_ids = sorted(catalog_ids)[::10]
    assert len(probe_ids) == 10
    stickers = sorted(k for k, v in cand.items() if any(sc["type"] == "sticker" for sc in v["scenes"]))[:6]
    carded = sorted(k for k, v in cand.items() if any(sc["type"] == "cards" for sc in v["scenes"]))[:6]
    assert len(stickers) == 6 and len(carded) == 6
    for lid in probe_ids:
        for name in stickers:
            swapped = copy.deepcopy(cand[name])
            for sc in swapped["scenes"]:
                if sc["type"] == "sticker":
                    sc["lottie"] = lid
            assert spec.validate(swapped) == [], (name, lid)
        for name in carded:
            swapped = copy.deepcopy(cand[name])
            for sc in swapped["scenes"]:
                if sc["type"] == "cards":
                    sc["items"][0]["icon"] = f"lottie:{lid}"
            assert spec.validate(swapped) == [], (name, lid)
    broken = copy.deepcopy(cand[stickers[0]])                 # negative control
    for sc in broken["scenes"]:
        if sc["type"] == "sticker":
            sc["lottie"] = "noto_not_in_the_catalog"
    assert any("'lottie' must be an id" in e for e in spec.validate(broken))


def test_the_generator_reproduces_all_100_files_byte_for_byte_with_lf_endings():
    assert len(build_library.LIB) == 100
    assert collections.Counter(row[4] for row in build_library.LIB) == {"approved": 44, "candidate": 56}
    for sid, _brief, _facts, film, _kind in build_library.LIB:
        expected = (json.dumps(film.spec(), ensure_ascii=False, indent=1) + "\n").encode("utf-8")
        actual = (ROOT / "library" / f"{sid}.json").read_bytes()
        assert actual == expected, sid
        # The failure this pins down: write_text on Windows turns every "\n" into CRLF, so a rebuild
        # rewrote all 44 committed files with identical text and different bytes.
        assert b"\r\n" not in actual, sid
        assert expected.replace(b"\n", b"\r\n") != actual
    for path in (ROOT / "dataset" / "brief_to_spec.jsonl", CANDIDATE_FILE):
        assert b"\r\n" not in path.read_bytes(), path.name
