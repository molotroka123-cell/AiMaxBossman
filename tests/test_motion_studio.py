"""Motion Studio: the spec contract a local model writes, and the generate/repair loop."""
import copy
import json
import sys
from pathlib import Path

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


def test_library_has_44_valid_scenarios_and_all_are_in_the_dataset():
    assert len(LIBRARY) == 44
    rows = {json.loads(x)["id"]: json.loads(x) for x in
            (ROOT / "dataset" / "brief_to_spec.jsonl").read_text(encoding="utf-8").splitlines() if x}
    for path in LIBRARY:
        spec_obj = json.loads(path.read_text(encoding="utf-8"))
        assert spec.validate(spec_obj) == [], path.name
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
