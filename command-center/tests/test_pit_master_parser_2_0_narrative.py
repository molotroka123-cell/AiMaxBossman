"""Master Parser 2.0 narrative, speed report and checkpoint hand-over (synthetic data, fake models)."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.pit.master_parser.engine import Options, run_master_parse
from bcc.pit.models import ConsentState
from bcc.telegram_companion.store import Store
from tests.test_pit_master_parser import ALICE, BOB, FakeModel, build_root, settings_for
from tests.test_pit_master_parser_2_0 import DegradedModel, build_many, run


@pytest.fixture(autouse=True)
def _no_private_blocklist(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_JEFF_BLOCKLIST", str(tmp_path / "no-blocklist.txt"))


class Narrator(FakeModel):
    """Fake local model: passport facts, chunk notes and the final two paragraphs."""

    def __init__(self, context="Обсуждает гитару и походы, пишет коротко и неформально.",
                 personality="Судя по формулировкам, любознательный и прямой."):
        super().__init__()
        self.notes: list[str] = []
        self.finals: list[str] = []
        self.context, self.personality = context, personality

    async def chat(self, model, messages, **kw):
        system, user = messages[0]["content"], messages[-1]["content"]
        if system.startswith("Ты аналитик переписки"):
            self.notes.append(user)
            return SimpleNamespace(text="Заметка: темы и тон фрагмента.", finish="stop")
        if system.startswith("Ты сводишь"):
            return SimpleNamespace(text="Сводная заметка.", finish="stop")
        if system.startswith("По заметкам"):
            self.finals.append(user)
            return SimpleNamespace(text=json.dumps({"context": self.context,
                                                    "personality": self.personality},
                                                   ensure_ascii=False), finish="stop")
        return await super().chat(model, messages, **kw)


def narrative_files(root: Path) -> dict[str, dict]:
    folder = root / "pit-v1.7" / "passport-checkpoints" / "narratives"
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")}


def test_narrative_two_paragraphs_isolated_per_participant(tmp_path):
    data = build_root(tmp_path)
    build_many(tmp_path, BOB, 4, start=100)
    model = Narrator()
    report = run(settings_for(tmp_path), model, narrative=True, concurrency=1)
    files = narrative_files(tmp_path)
    assert set(files) == {data["keys"][ALICE], data["keys"][BOB]}, "Carol gave no memory consent"
    alice = files[data["keys"][ALICE]]
    assert set(alice["paragraphs"]) == {"context", "personality"}
    assert all(0 < len(text) <= 450 for text in alice["paragraphs"].values())
    assert alice["provenance"]["participant_messages"] >= 2
    assert alice["provenance"]["from"] and alice["provenance"]["to"]
    assert model.notes and all("джаз" not in n for n in model.notes), "Carol never consented"
    for note in model.notes:
        assert not ("космос" in note and "походы" in note), "two participants in one prompt"
        assert not ("Сообщение номер" in note and "походы" in note), "two participants in one prompt"
    for person in report["participants"]:
        assert model.context not in json.dumps(person, ensure_ascii=False), "no narrative text in report"
    assert any(p["narrative"]["status"] == "OK" for p in report["participants"]
               if p.get("narrative"))


def test_narrative_is_map_reduce_over_the_whole_corpus_in_time_order(tmp_path):
    build_many(tmp_path, ALICE, 55)
    model = Narrator()
    run(settings_for(tmp_path), model, narrative=True, concurrency=1, narrative_chunk_chars=1500)
    assert len(model.notes) >= 2, "chunks by char budget"
    joined = "\n".join(model.notes)
    assert joined.index("номер 1 ") < joined.index("номер 30 ") < joined.index("номер 55 ")
    assert "Фрагмент 1 из" in model.notes[0]
    assert all(f"номер {n} " in joined for n in range(1, 56)), "every message reaches a chunk"
    assert len(model.finals) == 1


def test_overlong_paragraphs_are_trimmed_and_inference_is_marked(tmp_path):
    build_many(tmp_path, ALICE, 5)
    model = Narrator(context="Много слов. " * 100, personality="Любит порядок и ясность. " * 50)
    run(settings_for(tmp_path), model, narrative=True)
    record = next(iter(narrative_files(tmp_path).values()))
    assert len(record["paragraphs"]["context"]) <= 450
    assert len(record["paragraphs"]["personality"]) <= 450
    assert record["paragraphs"]["personality"].lower().startswith(("предположение", "судя по"))


@pytest.mark.parametrize("bad", ["Похоже на депрессию.", "У него диагноз.",
                                 "Скорее всего верующий христианин."])
def test_clinical_or_sensitive_guesses_are_rejected_not_saved(tmp_path, bad):
    build_many(tmp_path, ALICE, 5)
    model = Narrator(personality=bad)
    report = run(settings_for(tmp_path), model, narrative=True)
    person = next(p for p in report["participants"] if p.get("narrative"))
    assert person["narrative"]["status"] == "REJECTED"
    assert not (tmp_path / "pit-v1.7" / "passport-checkpoints" / "narratives").exists()
    assert len(model.finals) == 2, "one bounded rewrite, then honest rejection"


def test_secrets_are_redacted_before_the_model_sees_them(tmp_path):
    build_many(tmp_path, ALICE, 4)
    store = Store(tmp_path / "pit-v1.7")
    store.db.execute("INSERT INTO inbox(id,who,body,lane,phase,created) VALUES(?,?,?,?,?,?)",
                     (99, f"{ALICE}:{ALICE}", store.seal({
                         "_user_id": ALICE, "_message_id": 99,
                         "text": "пароль: hunter2xyz и токен sk-abcdefghijklmnopqrstuvwxyz123456"}),
                      "chat", "done", time.time()))
    store.close()
    model = Narrator()
    run(settings_for(tmp_path), model, narrative=True)
    everything = "\n".join(model.notes + model.finals)
    assert "hunter2xyz" not in everything and "sk-abcdefghij" not in everything
    assert "[REDACTED_SECRET]" in everything


def test_narrative_never_uses_the_cloud_even_when_allowed(tmp_path):
    data = build_root(tmp_path)
    data["vault"].set_consent(data["keys"][ALICE], ConsentState(
        memory_enabled=True, remote_processing_enabled=True))

    class Cloud:
        calls = 0

        async def chat(self, *a, **k):
            Cloud.calls += 1
            raise AssertionError("cloud must never be called for a narrative")

    class LocalNarratorDown(Narrator):
        async def chat(self, model, messages, **kw):
            if messages[0]["content"].startswith(("Ты аналитик", "Ты сводишь", "По заметкам")):
                raise RuntimeError("local down")
            return await super().chat(model, messages, **kw)

    settings = settings_for(tmp_path, provider_key="k", chat_models=("m:free",))
    options = Options(checkpoint=False, cloud=True, narrative=True, concurrency=1)
    report = asyncio.run(run_master_parse(settings, options, adapter=LocalNarratorDown(),
                                          cloud_adapter=Cloud()))
    assert Cloud.calls == 0
    assert any((p.get("narrative") or {}).get("status") == "FAILED" for p in report["participants"])


def test_narrative_recovers_from_an_empty_runner_and_reports_it(tmp_path):
    build_many(tmp_path, ALICE, 6)

    class Flaky(Narrator, DegradedModel):
        def __init__(self):
            Narrator.__init__(self)
            DegradedModel.__init__(self)

        async def chat(self, model, messages, **kw):
            if self.degraded:
                return await DegradedModel.chat(self, model, messages, **kw)
            return await Narrator.chat(self, model, messages, **kw)

    model = Flaky()
    report = run(settings_for(tmp_path), model, narrative=True, concurrency=1)
    person = next(p for p in report["participants"] if p.get("narrative"))
    assert person["narrative"]["status"] == "OK"
    assert len(model.unloads) == 1
    assert person["speed"]["empty_answer_recoveries"] >= 1
    assert report["speed"]["overall"]["runner_unloads"] == 1


def test_speed_report_and_sanitized_public_copy(tmp_path):
    data = build_root(tmp_path)
    out = tmp_path / "out" / "speed.json"
    run(settings_for(tmp_path), Narrator(), narrative=True, concurrency=1,
        speed_report=str(out), checkpoint=True)
    full = json.loads(out.read_text(encoding="utf-8"))
    overall = full["overall"]
    for name in ("messages", "chars", "llm_calls", "empty_answer_recoveries", "phase_seconds",
                 "messages_per_second", "chars_per_second", "latency_p50_seconds",
                 "latency_p95_seconds", "time_to_first_paragraph_seconds", "delivery_seconds"):
        assert name in overall, name
    assert set(overall["phase_seconds"]) == {"collect", "map", "reduce", "write"}
    assert overall["llm_calls"] > 0 and overall["messages"] > 0
    assert any(p["time_to_first_paragraph_seconds"] is not None for p in full["participants"])
    public_text = (out.parent / "speed_report_public.json").read_text(encoding="utf-8")
    public = json.loads(public_text)
    assert [p["participant"] for p in public["participants"]] == ["P1", "P2"]
    for key in data["keys"].values():
        assert key not in public_text and key[:8] not in public_text
    for forbidden in ("label", "telegram", "Алис", "Боб", "гитар", "космос", "person_key"):
        assert forbidden not in public_text
    from bcc.pit.master_parser.speed import table_ru
    table = table_ru(full)
    assert "P1" in table and "Итого" in table


def test_checkpoint_gets_the_narrative_and_uses_the_same_route(tmp_path):
    build_root(tmp_path)
    report = asyncio.run(run_master_parse(
        settings_for(tmp_path), Options(cloud=False, narrative=True, checkpoint=True,
                                        concurrency=1), adapter=Narrator()))
    assert report["checkpoint"]["status"] == "saved"
    saved = json.loads((tmp_path / "pit-v1.7" / "passport-checkpoints" / "latest.json")
                       .read_text(encoding="utf-8"))
    with_story = [row for row in saved["participants"] if row.get("narrative")]
    assert with_story and all(len(r["narrative"]["context"]) <= 450 for r in with_story)


def test_dry_run_and_no_llm_skip_the_narrative_and_save_nothing(tmp_path):
    build_root(tmp_path)
    model = Narrator()
    run(settings_for(tmp_path), model, narrative=True, dry_run=True)
    run(settings_for(tmp_path), model, narrative=True, use_llm=False)
    assert not (tmp_path / "pit-v1.7" / "passport-checkpoints").exists()
    assert model.notes == []


def test_status_shows_the_narrative_phase(tmp_path):
    build_many(tmp_path, ALICE, 5)
    phases = []
    asyncio.run(run_master_parse(
        settings_for(tmp_path), Options(cloud=False, checkpoint=False, narrative=True),
        adapter=Narrator(), progress=lambda status: phases.append(status["phase"])))
    assert "narrative" in phases and phases.index("analyze") < phases.index("narrative")
    from bcc.pit.master_parser.engine import read_status
    assert read_status(tmp_path)["phase"] == "done"


def test_cli_uncensored_profile_and_flags():
    from bcc.pit.master_parser import cli
    ns = cli.build_parser().parse_args(["--profile", "uncensored", "--speed-report", "x.json"])
    assert ns.profile == "uncensored" and ns.narrative is True and ns.speed_report == "x.json"
    assert cli.build_parser().parse_args(["--no-narrative"]).narrative is False
    assert cli.build_parser().parse_args(["--narrative"]).narrative is True


def test_cli_profile_forces_local_serial_run(tmp_path, monkeypatch):
    from bcc.pit.master_parser import cli
    seen = {}

    async def fake_run(settings, options, **kw):
        seen["options"] = options
        return {"dry_run": False, "use_llm": True, "participants": [], "totals": {}}

    monkeypatch.setattr(cli, "resolve_settings", lambda config: settings_for(tmp_path))
    monkeypatch.setattr(cli, "run_master_parse", fake_run)
    code = cli.cli_main(tmp_path / "config.json", ["--profile", "uncensored", "--concurrency", "4"])
    options = seen["options"]
    assert code == 0
    assert options.model == "bossman-community-qwen-uncensored:latest"
    assert options.cloud is False and options.concurrency == 1 and options.narrative is True


def test_exit_codes_are_honest():
    from bcc.pit.master_parser.cli import exit_code

    def person(status="OK", pending=3, story="OK"):
        return {"status": status, "messages_pending": pending, "narrative": {"status": story}}

    ok = {"participants": [person(), person(status="NO_MEMORY_CONSENT", pending=0, story="")]}
    assert exit_code(ok) == 0
    assert exit_code({"participants": [person(), person(status="PARTIAL")]}) == 4
    assert exit_code({"participants": [person(story="FAILED"), person()]}) == 4
    assert exit_code({"participants": [person(status="LLM_FAILED", story="FAILED")]}) == 5
    assert exit_code({"participants": [person(status="ERROR")]}) == 5
    assert exit_code({"dry_run": True, "participants": [person(status="ERROR")]}) == 0
    assert exit_code({"participants": [person(pending=0, story="INSUFFICIENT_DATA")]}) == 0
