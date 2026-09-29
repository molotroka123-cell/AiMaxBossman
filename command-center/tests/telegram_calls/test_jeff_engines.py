"""Jeff-based call engines (bcc.telegram_calls.speech.jeff_engines): wiring, error mapping, cancellation, learning-after-speech.

The heavy parts (Whisper decode, Piper synthesis) are injected callables here; what is proven is the glue and its failure modes,
NOT recognition/synthesis quality or latency (those need the owner's real models: see docs/telegram-calls/ACCEPTANCE.md).
"""
from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import wave

import pytest

from bcc.pit.models import ConsentState
from bcc.telegram_calls.settings import CallSettings
from bcc.telegram_calls.speech import jeff_engines as je
from bcc.telegram_calls.types import CallError, CancelToken, Turn

from .test_jeff_call_surface import LOCAL, PEER, LocalModel, enable_memory, make  # noqa: F401  (fixture re-export)
from ..test_pit_runtime import make_settings


def wav_info(data: bytes) -> tuple[int, int, int]:
    with wave.open(io.BytesIO(data)) as w:
        return w.getframerate(), w.getnchannels(), w.getnframes()


class Decoder:
    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def __call__(self, wav, *, language, stopped, beam_size):
        self.calls.append({"wav": wav, "language": language, "beam": beam_size})
        item = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(item, Exception):
            raise item
        return item


def ok(text="привет", conf=0.9, dur=1.0):
    return {"text": text, "duration_seconds": dur, "confidence": conf}


PCM_1S = b"\x01\x00" * 16000


# ------------------------------------------------------------------ STT

async def test_stt_decodes_one_utterance_as_16k_mono_wav_with_the_cheap_beam():
    dec = Decoder(ok("привет босман"))
    stt = je.JeffSTT(transcribe=dec, stopped=lambda: False)
    s = stt.new_stream()
    s.feed(PCM_1S)
    res = await s.finalize()
    assert res.text == "привет босман" and res.confidence == 0.9 and res.decode_ms >= 0
    call = dec.calls[0]
    assert call["language"] == "ru" and call["beam"] == je.STT_BEAM == 1
    assert wav_info(call["wav"]) == (16000, 1, 16000)


async def test_stt_no_speech_is_an_empty_result_not_an_error():
    stt = je.JeffSTT(transcribe=Decoder(RuntimeError("VOICE_NO_SPEECH")), stopped=lambda: False)
    s = stt.new_stream()
    s.feed(PCM_1S)
    assert (await s.finalize()).text == ""


async def test_stt_busy_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(je, "BUSY_SLEEP_S", 0.001)
    dec = Decoder(RuntimeError("VOICE_BUSY"), RuntimeError("VOICE_BUSY"), ok("готово"))
    s = je.JeffSTT(transcribe=dec, stopped=lambda: False).new_stream()
    s.feed(PCM_1S)
    assert (await s.finalize()).text == "готово" and len(dec.calls) == 3


async def test_stt_permanent_busy_gives_up_with_a_stable_code(monkeypatch):
    monkeypatch.setattr(je, "BUSY_SLEEP_S", 0.0)
    monkeypatch.setattr(je, "BUSY_RETRIES", 3)
    dec = Decoder(RuntimeError("VOICE_BUSY"))
    s = je.JeffSTT(transcribe=dec, stopped=lambda: False).new_stream()
    s.feed(PCM_1S)
    with pytest.raises(CallError) as ei:
        await s.finalize()
    assert ei.value.code == "STT_UNAVAILABLE" and len(dec.calls) == 3


async def test_stt_stop_cancels_and_other_errors_never_leak_their_text():
    s = je.JeffSTT(transcribe=Decoder(RuntimeError("VOICE_STOPPED")), stopped=lambda: True).new_stream()
    s.feed(PCM_1S)
    with pytest.raises(asyncio.CancelledError):
        await s.finalize()
    s2 = je.JeffSTT(transcribe=Decoder(ValueError("C:\\Users\\owner\\secret\\model.bin missing")), stopped=lambda: False).new_stream()
    s2.feed(PCM_1S)
    with pytest.raises(CallError) as ei:
        await s2.finalize()
    assert ei.value.code == "STT_UNAVAILABLE" and "secret" not in str(ei.value) and "secret" not in repr(ei.value.detail)


async def test_speculative_decode_is_reused_when_only_silence_followed_and_dropped_when_speech_continued():
    dec = Decoder(ok("первая"), ok("вторая"))
    stt = je.JeffSTT(transcribe=dec, stopped=lambda: False)
    s = stt.new_stream()
    s.feed(PCM_1S)
    assert await s.partial() == ""                                       # honest: never invents partial text
    s.feed(b"\x00\x00" * 1600)                                            # 0.1 s of silence only
    assert (await s.finalize()).text == "первая" and len(dec.calls) == 1  # no second decode: the speculative one is reused

    dec2 = Decoder(ok("старая"), ok("полная фраза"))
    s2 = je.JeffSTT(transcribe=dec2, stopped=lambda: False).new_stream()
    s2.feed(PCM_1S)
    await s2.partial()
    await asyncio.sleep(0.05)                                              # the speculative decode really ran (and heard 1 s)
    s2.feed(PCM_1S * 2)                                                    # the speaker kept talking: the speculative text is stale
    assert (await s2.finalize()).text == "полная фраза"


async def test_cancelled_stream_ignores_audio_and_stops_the_speculative_decode():
    dec = Decoder(ok("x"))
    s = je.JeffSTT(transcribe=dec, stopped=lambda: False).new_stream()
    s.feed(PCM_1S)
    await s.partial()
    s.cancel()
    s.feed(PCM_1S)
    assert bytes(s._buf) == b""


async def test_utterance_buffer_is_bounded():
    s = je.JeffSTT(transcribe=Decoder(ok()), stopped=lambda: False).new_stream()
    for _ in range(je.MAX_UTTERANCE_S + 20):
        s.feed(PCM_1S)
    assert len(s._buf) <= (je.MAX_UTTERANCE_S + 1) * 32000


# ------------------------------------------------------------------ TTS

def synth_ok(pcm=b"\x02\x00" * 22050, rate=22050, seen=None):
    def fn(text, *, piper_executable, model_path, stopped):
        if seen is not None:
            seen.append((text, piper_executable, model_path))
        return pcm, rate
    return fn


async def collect(tts, text, cancel=None):
    return [c async for c in tts.synthesize(text, cancel or CancelToken())]


async def test_tts_streams_100ms_chunks_and_passes_the_voice_files():
    seen = []
    tts = je.JeffTTS(synthesize_pcm=synth_ok(seen=seen), exe="piper.exe", model="voice/ru.onnx", egress_guard=lambda t: True,
                     stopped=lambda: False)
    chunks = await collect(tts, " Привет. ")
    assert seen == [("Привет.", "piper.exe", "voice/ru.onnx")] and tts.voice == "ru"
    assert len(chunks) == 10 and all(len(c) == 4410 for c in chunks)      # 22.05 kHz * 0.1 s * 2 bytes; one second in total


async def test_tts_sample_rate_follows_what_piper_really_produced():
    tts = je.JeffTTS(synthesize_pcm=synth_ok(b"\x00\x00" * 16000, 16000), exe="e", model="m", egress_guard=lambda t: True,
                     stopped=lambda: False, sample_rate=22050)
    await collect(tts, "Да.")
    assert tts.sample_rate == 16000


async def test_tts_egress_guard_blocks_before_any_synthesis():
    seen = []
    tts = je.JeffTTS(synthesize_pcm=synth_ok(seen=seen), exe="e", model="m", egress_guard=lambda t: False, stopped=lambda: False)
    with pytest.raises(CallError) as ei:
        await collect(tts, "секрет")
    assert ei.value.code == "TTS_UNAVAILABLE" and seen == []


async def test_tts_failure_is_a_stable_code_and_a_cancelled_call_is_silent():
    def boom(text, **kw):
        raise OSError("C:\\voice\\piper.exe: access denied")
    tts = je.JeffTTS(synthesize_pcm=boom, exe="e", model="m", egress_guard=lambda t: True, stopped=lambda: False)
    with pytest.raises(CallError) as ei:
        await collect(tts, "Привет")
    assert ei.value.code == "TTS_UNAVAILABLE" and "piper.exe" not in repr(ei.value.detail)
    cancel = CancelToken()
    cancel.cancel("barge_in")
    assert await collect(tts, "Привет", cancel) == []                     # cancelled: no error, no audio


async def test_tts_barge_in_between_chunks_stops_the_stream():
    tts = je.JeffTTS(synthesize_pcm=synth_ok(), exe="e", model="m", egress_guard=lambda t: True, stopped=lambda: False)
    cancel = CancelToken()
    got = []
    async for chunk in tts.synthesize("Привет", cancel):
        got.append(chunk)
        if len(got) == 3:
            cancel.cancel("barge_in")
    assert len(got) == 3


async def test_tts_empty_text_synthesizes_nothing():
    seen = []
    tts = je.JeffTTS(synthesize_pcm=synth_ok(seen=seen), exe="e", model="m", egress_guard=lambda t: True, stopped=lambda: False)
    assert await collect(tts, "   ") == [] and seen == []


# ------------------------------------------------------------------ Brain

async def drain(brain, history, text, cancel=None):
    return [d async for d in brain.reply(history, text, cancel or CancelToken())]


async def test_brain_answers_through_jeff_and_learns_only_after_speech(make):
    runtime, local, remote = make("Меня зовут Джефф. Рад слышать тебя.")
    key = enable_memory(runtime)
    brain = je.JeffBrain(runtime, PEER, model=LOCAL)
    out = await drain(brain, [Turn("user", "привет"), Turn("assistant", "Здравствуй")], "Меня зовут Тимур, ты меня помнишь?")
    assert out == ["Меня зовут Джефф. Рад слышать тебя."] and remote.calls == []
    sent = local.calls[0][1]
    assert any("Здравствуй" in m["content"] for m in sent)                # the in-call history reached the model
    assert not list(runtime.vault.iter_candidate_records(key))
    brain.turn_finished(False)                                            # nothing was spoken (barge-in): nothing is learned
    assert not runtime._pending_call_turns and not list(runtime.vault.iter_candidate_records(key))
    await drain(brain, [], "Меня зовут Тимур")
    brain.turn_finished(True)                                             # spoken: the explicit self-statement may become a fact
    assert not runtime._pending_call_turns and not runtime._pending_chat_records
    learned = [f["value"] for f in runtime.vault.iter_candidate_records(key)]
    assert any("Тимур" in v for v in learned)


async def test_brain_cancel_during_thinking_yields_nothing_and_leaves_no_pending_state(make):
    runtime, *_ = make(delay=5.0)
    brain = je.JeffBrain(runtime, PEER, model=LOCAL)
    cancel = CancelToken()
    task = asyncio.create_task(drain(brain, [], "Расскажи что-нибудь", cancel))
    await asyncio.sleep(0.05)
    cancel.cancel("barge_in")
    assert await asyncio.wait_for(task, 2) == []
    assert not runtime._pending_call_turns and not runtime._pending_chat_records


async def test_brain_maps_jeff_failures_to_stable_codes_without_provider_text(make):
    runtime, local, _ = make()

    async def down(*a, **kw):
        raise RuntimeError("upstream 500: api-key sk-SECRET")
    local.chat = down
    brain = je.JeffBrain(runtime, PEER, model=LOCAL)
    with pytest.raises(CallError) as ei:
        await drain(brain, [], "Привет")
    assert ei.value.code == "BRAIN_UNAVAILABLE" and "SECRET" not in repr(ei.value.detail) and "SECRET" not in str(ei.value)


async def test_brain_refuses_a_blocked_peer(make):
    runtime, *_ = make()
    runtime._blocked = lambda *a: True
    with pytest.raises(CallError) as ei:
        await drain(je.JeffBrain(runtime, PEER, model=LOCAL), [], "Привет")
    assert ei.value.code == "PEER_NOT_ALLOWED"


async def test_brain_a_turn_that_never_reported_back_is_discarded_not_leaked(make):
    runtime, *_ = make()
    brain = je.JeffBrain(runtime, PEER, model=LOCAL)
    await drain(brain, [], "Первый вопрос")
    assert len(runtime._pending_call_turns) == 1
    await drain(brain, [], "Второй вопрос")                               # the session never called turn_finished for the first
    assert len(runtime._pending_call_turns) == 1
    brain.turn_finished(False)
    assert not runtime._pending_call_turns


async def test_brain_stop_before_reply_speaks_nothing_and_asks_no_model(make):
    runtime, local, _ = make()
    brain = je.JeffBrain(runtime, PEER, model=LOCAL, stopped=lambda: True)
    assert await drain(brain, [], "Привет") == [] and local.calls == []


# ------------------------------------------------------------------ summary

def summary_json(summary="Поговорили о планах.", tasks=("Позвонить завтра",)):
    return json.dumps({"summary": summary, "agreed_tasks": list(tasks)}, ensure_ascii=False)


async def test_summary_is_one_local_call_stored_only_with_the_peers_consent(make):
    runtime, local, remote = make(summary_json())
    brain = je.JeffBrain(runtime, PEER, model=LOCAL)
    brain.bind_call("c-1")
    turns = [Turn("user", "давай созвонимся завтра"), Turn("assistant", "Хорошо, созвонимся.")]
    res = await brain.summarize(turns)                                    # zero-start peer: no consent -> nothing stored
    assert res.text == "Поговорили о планах." and res.agreed_tasks == ["Позвонить завтра"] and res.generated_by == "jeff-local"
    assert remote.calls == [] and len(local.calls) == 1
    key = enable_memory(runtime)
    res2 = await brain.summarize(turns)
    assert res2.generated_by == "jeff-local+memory"
    stored = list(runtime.vault.iter_candidate_records(key))
    assert len(stored) == 1 and stored[0]["source_message_id"] == "call:c-1" and "Поговорили" in stored[0]["value"]


async def test_summary_tolerates_fences_redacts_secrets_and_bounds_the_lists(make):
    body = summary_json("Он назвал ключ sk-abcdefghijklmnopqrstuvwxyz0123456789 и город Париж.", [f"дело {i}" for i in range(9)])
    runtime, *_ = make("```json\n" + body + "\n```")
    res = await je.JeffBrain(runtime, PEER, model=LOCAL).summarize([Turn("user", "привет")])
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in res.text and "Париж" in res.text and len(res.agreed_tasks) == 5


async def test_summary_that_is_not_json_or_empty_raises_a_stable_code_so_the_session_falls_back(make):
    runtime, *_ = make("Это был приятный разговор.")
    with pytest.raises(CallError) as ei:
        await je.JeffBrain(runtime, PEER, model=LOCAL).summarize([Turn("user", "привет")])
    assert ei.value.code == "BRAIN_UNAVAILABLE"
    runtime2, *_ = make(summary_json(""))
    with pytest.raises(CallError):
        await je.JeffBrain(runtime2, PEER, model=LOCAL).summarize([Turn("user", "привет")])


async def test_summary_after_stop_asks_no_model(make):
    runtime, local, _ = make(summary_json())
    with pytest.raises(CallError):
        await je.JeffBrain(runtime, PEER, model=LOCAL, stopped=lambda: True).summarize([Turn("user", "привет")])
    assert local.calls == []


# ------------------------------------------------------------------ assembly

@pytest.fixture
def voice_env(tmp_path):
    exe, model = tmp_path / "piper.exe", tmp_path / "ru_RU-test.onnx"
    exe.write_bytes(b"MZ")
    model.write_bytes(b"onnx")
    (tmp_path / "ru_RU-test.onnx.json").write_text(json.dumps({"audio": {"sample_rate": 16000}}), encoding="utf-8")
    return {"BOSSMAN_PIT_TTS_EXECUTABLE": str(exe), "BOSSMAN_PIT_TTS_MODEL_PATH": str(model)}


@pytest.fixture
def pit_settings(tmp_path, monkeypatch):
    from bcc.pit import resources
    monkeypatch.setattr(resources, "_read_free_vram_mb", lambda: 8192)
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "bcc"))
    return dataclasses.replace(make_settings(tmp_path / "pit"), local_url="http://127.0.0.1:11434/v1", local_models=(LOCAL,),
                               local_chat_only=True, chat_models=(), web_only=True, bot_token="")


def call_settings(**kw):
    return CallSettings(enabled=True, peer_user_id=kw.pop("peer", PEER), **kw)


async def build(settings, pit, env, **kw):
    return await je.build_jeff_engines(settings, pit_settings=pit, transcribe=Decoder(ok()), synthesize_pcm=synth_ok(), env=env, **kw)


async def test_build_assembles_jeff_engines_and_reports_what_is_used(pit_settings, voice_env):
    eng = await build(call_settings(), pit_settings, voice_env)
    try:
        assert eng.stt.name == "jeff-whisper" and eng.tts.name == "jeff-piper" and eng.brain.route == "jeff"
        assert eng.tts.sample_rate == 16000 and eng.tts.voice == "ru_RU-test"
        assert eng.notes["surface"] == "call" and eng.notes["llm"] == LOCAL
        assert eng.brain.runtime.settings.local_chat_only is True and eng.brain.runtime.surface == "call"
    finally:
        await eng.brain.runtime.close()


async def test_build_needs_a_selected_peer(pit_settings, voice_env):
    with pytest.raises(CallError) as ei:
        await je.build_jeff_engines(CallSettings(enabled=True), pit_settings=pit_settings, transcribe=Decoder(ok()),
                                    synthesize_pcm=synth_ok(), env=voice_env)
    assert ei.value.code == "PEER_NOT_SELECTED"


async def test_build_without_a_local_model_never_falls_back_to_a_cloud_route(pit_settings, voice_env):
    cloud_only = dataclasses.replace(pit_settings, local_models=(), local_url="", local_chat_only=False, chat_models=("free/model:free",),
                                     web_only=False, bot_token="t")
    with pytest.raises(CallError) as ei:
        await build(call_settings(), cloud_only, voice_env)
    assert ei.value.code == "BRAIN_NOT_CONFIGURED"


@pytest.mark.parametrize("missing", ["exe", "model", "json"])
async def test_build_reports_a_missing_voice_piece_instead_of_going_silent(pit_settings, voice_env, tmp_path, missing):
    env = dict(voice_env)
    if missing == "exe":
        env["BOSSMAN_PIT_TTS_EXECUTABLE"] = str(tmp_path / "nope.exe")
    elif missing == "model":
        env["BOSSMAN_PIT_TTS_MODEL_PATH"] = str(tmp_path / "nope.onnx")
    else:
        (tmp_path / "ru_RU-test.onnx.json").unlink()
    with pytest.raises(CallError) as ei:
        await build(call_settings(), pit_settings, env)
    assert ei.value.code == "TTS_UNAVAILABLE"


async def test_build_without_the_voice_environment_at_all_is_a_clear_error(pit_settings):
    with pytest.raises(CallError) as ei:
        await build(call_settings(), pit_settings, {})
    assert ei.value.code == "TTS_UNAVAILABLE"


async def test_build_reports_missing_asr_model(pit_settings, voice_env, monkeypatch):
    from bcc.pit import speech
    monkeypatch.setattr(speech, "asr_status", lambda: {"available": False, "reason_code": "VOICE_STT_UNAVAILABLE"})
    with pytest.raises(CallError) as ei:
        await je.build_jeff_engines(call_settings(), pit_settings=pit_settings, synthesize_pcm=synth_ok(), env=voice_env)
    assert ei.value.code == "STT_UNAVAILABLE"


async def test_build_refuses_a_peer_the_owner_blocked(pit_settings, voice_env, monkeypatch):
    from bcc.pit import call_surface
    monkeypatch.setattr(call_surface.CallParticipantRuntime, "_blocked", lambda self, *a: True)
    with pytest.raises(CallError) as ei:
        await build(call_settings(), pit_settings, voice_env)
    assert ei.value.code == "PEER_NOT_ALLOWED"


async def test_build_without_jeff_configuration_says_so(tmp_path, voice_env, monkeypatch):
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "empty"))
    with pytest.raises(CallError) as ei:
        await je.build_jeff_engines(call_settings(), transcribe=Decoder(ok()), synthesize_pcm=synth_ok(), env=voice_env)
    assert ei.value.code == "BRAIN_NOT_CONFIGURED"


async def test_build_uses_jeffs_own_voice_defaults_from_the_data_dir(pit_settings, tmp_path, monkeypatch):
    """No environment variables: the SAME ``<data>/voice`` layout Jeff's window uses is picked up (no third resolver)."""
    from bcc.telegram_calls.settings import data_dir
    voice = data_dir() / "voice"
    (voice / "piper").mkdir(parents=True)
    (voice / "piper" / "piper.exe").write_bytes(b"MZ")
    (voice / "ru_RU-denis-medium.onnx").write_bytes(b"o")
    (voice / "ru_RU-denis-medium.onnx.json").write_text("{}", encoding="utf-8")
    env: dict = {}
    eng = await build(call_settings(), pit_settings, env)
    try:
        assert eng.tts.voice == "ru_RU-denis-medium" and eng.tts.sample_rate == 22050       # rate missing in json -> Piper default
        assert env["BOSSMAN_PIT_TTS_EXECUTABLE"].endswith("piper.exe")
    finally:
        await eng.brain.runtime.close()
