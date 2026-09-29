"""Jeff 2.0 model_guard: garbage detection, canary, state machine, rate-limited unload-only recovery (all fakes)."""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from bcc.pit.j2 import J2Pipeline, TurnContext
from bcc.pit.j2 import model_guard as mg
from bcc.pit.j2.model_guard import Assessment, ModelGuardModule, State, assess


class Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def run(coro):
    return asyncio.run(coro)


def ctx(text: str = "Расскажи про Байкал", **extra) -> TurnContext:
    return TurnContext(person_key="p1", who="tg:1", text=text, extra=dict(extra))


class FakeModel:
    """Scripted local model: pops answers; an Exception instance is raised, a float sleeps that long first."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def __call__(self, messages):
        self.calls.append(messages)
        item = self.answers.pop(0) if self.answers else "Париж"
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):                  # (delay, text)
            await asyncio.sleep(item[0])
            item = item[1]
        return SimpleNamespace(text=item)


class Recorder:
    def __init__(self):
        self.calls = []

    async def unload(self):
        self.calls.append("unload")

    async def restart(self):
        self.calls.append("restart")


def guard(*answers, clock=None, rec=None, **kw) -> tuple[ModelGuardModule, FakeModel, Recorder, Clock]:
    clock = clock or Clock()
    rec = rec or Recorder()
    model = FakeModel(*answers)
    module = ModelGuardModule(chat=model, model="qwen-test", unload=rec.unload, clock=clock, wall_clock=clock, **kw)
    return module, model, rec, clock


# ---- assess: garbage detection ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,code", [
    ("", "empty"),
    ("   \n ", "empty"),
    ("Или Или Или", "repetition"),
    ("или или или или или или или или", "repetition"),
    ("Да да да да да да да", "repetition"),
    ("Хорошо, понятно. Хорошо, понятно. Хорошо, понятно. Хорошо, понятно. Хорошо, понятно.", "repetition"),
    ("这是一个测试，我们需要处理这个问题，谢谢你的帮助。", "foreign_script"),
    ("Привет! 你好你好你好你好你好 как дела", "foreign_script"),
    ("안녕하세요 여러분 반갑습니다", "foreign_script"),
    ("���� текст", "replacement_char"),
    ("<|im_start|>assistant <|im_end|> <|im_start|>", "template_leak"),
    ("!!!???###@@@$$$%%%^^^&&&***(((", "symbol_noise"),
    ("а" * 40, "char_run"),
])
def test_garbage_is_detected(text, code):
    verdict = assess(text, prompt="Расскажи про Байкал")
    assert verdict.garbage and code in verdict.problems, (text, verdict)


@pytest.mark.parametrize("text", [
    "Байкал — самое глубокое озеро на планете, его глубина превышает 1600 метров.",
    "Да.",
    "5",
    "Париж",
    "Конечно! Вот три шага: 1) собрать данные, 2) проверить, 3) отправить отчёт.",
    "Держи код:\n```python\nfor i in range(3):\n    print(i)\n```",
    "Ха-ха-ха, ну ты даёшь! Ладно, ладно, шучу.",
    "Нет, нет, подожди: сначала проверим договор, потом решим.",
    "Bonjour! Ça va? Voici la réponse.",
    "OK.",
    "Ответ: 42. Ответ: 42 подтверждён двумя способами, но это не цикл.",
    "https://example.com/very/long/path/with/many/parts/and-numbers-12345-67890",
    "Список: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15",
])
def test_normal_answers_are_not_flagged(text):
    assert assess(text, prompt="Расскажи про Байкал").ok, text


def test_foreign_script_the_user_wrote_is_not_noise():
    assert assess("这个词的意思是“你好”，读作 nǐ hǎo.", prompt="Как по-китайски 你好?").ok


def test_a_single_foreign_character_is_only_suspect():
    verdict = assess("Байкал глубокий, самый глубокий на планете 好, это факт.", prompt="Про Байкал")
    assert verdict.severity == "suspect" and "foreign_script_trace" in verdict.problems


def test_mixed_script_word_salad_is_garbage_for_russian_prompts():
    verdict = assess("Прivет мoй дpуг кaк дeла сeгодня тeбя вижy", prompt="Привет, как дела?")
    assert verdict.garbage and "mixed_script_words" in verdict.problems


def test_long_english_answer_to_a_russian_prompt_is_suspect_not_garbage():
    text = "The deepest lake in the world is Baikal, located in Siberia, with a maximum depth of 1642 metres."
    verdict = assess(text, prompt="Расскажи про самое глубокое озеро мира")
    assert verdict.severity == "suspect" and "language_switch" in verdict.problems


# ---- canary -------------------------------------------------------------------------------------------------
def test_canary_passes_on_a_sane_answer_and_records_the_result():
    module, model, _, _ = guard("Париж")
    outcome = run(module.probe())
    assert outcome.ok and outcome.reason == "ok" and outcome.id == "capital"
    assert module.status()["last_canary"]["ok"] is True and module.state is State.HEALTHY
    assert model.calls[0][0]["role"] == "user" and "Франции" in model.calls[0][0]["content"]


def test_canaries_rotate_through_the_suite():
    module, _, _, _ = guard("Париж", "5", "голубое")
    ids = [run(module.probe()).id for _ in range(3)]
    assert ids == ["capital", "sum", "sky"]


def test_canary_garbage_answer_degrades_at_once_and_triggers_unload():
    module, _, rec, _ = guard("Или Или Или")
    outcome = run(module.probe())
    assert not outcome.ok and outcome.reason.startswith("garbage:")
    assert module.state is State.RECOVERING and rec.calls == ["unload"]


def test_canary_wrong_but_clean_answer_is_only_suspect():
    module, _, rec, _ = guard("Лондон")
    outcome = run(module.probe())
    assert outcome.reason == "mismatch" and module.state is State.SUSPECT and rec.calls == []


def test_two_suspect_canaries_within_the_window_degrade():
    module, _, rec, clock = guard("Лондон", "7")
    run(module.probe())
    clock.advance(60)
    run(module.probe())
    assert module.state is State.RECOVERING and rec.calls == ["unload"]


def test_suspects_outside_the_window_do_not_accumulate():
    module, _, rec, clock = guard("Лондон", "7", "Париж")
    run(module.probe())
    clock.advance(mg.SUSPECT_WINDOW_S + 5)
    run(module.probe())
    assert module.state is State.SUSPECT and rec.calls == []


def test_canary_timeout_is_suspect_and_never_hangs():
    module, _, _, _ = guard((5.0, "Париж"), canary_timeout=0.05)
    outcome = run(module.probe())
    assert outcome.reason == "timeout" and module.state is State.SUSPECT


def test_canary_transport_error_is_a_signal_not_a_crash():
    module, _, _, _ = guard(ConnectionError("down"))
    outcome = run(module.probe())
    assert outcome.reason == "error:ConnectionError" and not outcome.ok


def test_probe_without_a_local_model_is_a_noop():
    module = ModelGuardModule()
    assert run(module.probe()) is None and module.status()["available"] is False


# ---- state machine and recovery -----------------------------------------------------------------------------
def test_healing_needs_two_good_observations_after_recovery():
    module, _, rec, _ = guard("Или Или Или", "5", "голубое")
    run(module.probe())
    assert module.state is State.RECOVERING
    run(module.probe())
    assert module.state is State.RECOVERING
    run(module.probe())
    assert module.state is State.HEALTHY and rec.calls == ["unload"]


def test_a_bad_canary_during_recovery_degrades_again_but_recovery_is_rate_limited():
    module, _, rec, clock = guard("Или Или Или", "Или Или Или", "Или Или Или")
    run(module.probe())
    run(module.probe())                      # immediately again: too soon for a second action
    assert rec.calls == ["unload"] and module.status()["recoveries"]["suppressed"] >= 1
    clock.advance(mg.RECOVERY_MIN_INTERVAL_S + 1)
    run(module.probe())
    assert rec.calls == ["unload", "unload"]


def test_recovery_never_loops_and_flags_the_owner_after_the_hourly_cap():
    notes = []
    module, _, rec, clock = guard(*(["Или Или Или"] * 12), notify=notes.append)
    for _ in range(8):
        run(module.probe())
        clock.advance(mg.RECOVERY_MIN_INTERVAL_S + 1)
    assert len(rec.calls) == mg.RECOVERY_MAX_PER_HOUR
    status = module.status()
    assert status["needs_owner"] is True and status["recoveries"]["suppressed"] >= 1
    assert [n["kind"] for n in notes] == ["model_guard.stuck"]


def test_hourly_budget_resets_after_an_hour():
    module, _, rec, clock = guard(*(["Или Или Или"] * 12))
    for _ in range(mg.RECOVERY_MAX_PER_HOUR):
        run(module.probe())
        clock.advance(mg.RECOVERY_MIN_INTERVAL_S + 1)
    clock.advance(3600)
    run(module.probe())
    assert len(rec.calls) == mg.RECOVERY_MAX_PER_HOUR + 1


def test_restart_is_never_used_unless_injected_and_allowed():
    rec = Recorder()
    clock = Clock()
    off = ModelGuardModule(chat=FakeModel(*(["Или Или Или"] * 4)), unload=rec.unload, restart=rec.restart,
                           clock=clock, wall_clock=clock)
    for _ in range(3):
        run(off.probe())
        clock.advance(mg.RECOVERY_MIN_INTERVAL_S + 1)
    assert "restart" not in rec.calls

    rec2, clock2 = Recorder(), Clock()
    on = ModelGuardModule(chat=FakeModel(*(["Или Или Или"] * 4)), unload=rec2.unload, restart=rec2.restart,
                          allow_restart=True, clock=clock2, wall_clock=clock2)
    for _ in range(3):
        run(on.probe())
        clock2.advance(mg.RECOVERY_MIN_INTERVAL_S + 1)
    assert rec2.calls[0] == "unload" and rec2.calls[1:] == ["restart", "restart"]


def test_a_failing_recovery_action_is_recorded_not_raised():
    async def broken():
        raise RuntimeError("ollama api refused")

    clock = Clock()
    module = ModelGuardModule(chat=FakeModel("Или Или Или"), unload=broken, clock=clock, wall_clock=clock)
    run(module.probe())
    assert module.status()["recoveries"]["last_result"] == "error:RuntimeError"


def test_a_hanging_recovery_action_is_bounded():
    async def hang():
        await asyncio.sleep(10)

    clock = Clock()
    module = ModelGuardModule(chat=FakeModel("Или Или Или"), unload=hang, clock=clock, wall_clock=clock,
                              action_timeout=0.05)
    run(module.probe())
    assert module.status()["recoveries"]["last_result"] == "error:TimeoutError"


def test_module_source_never_starts_or_kills_processes():
    source = inspect.getsource(mg)
    for forbidden in ("subprocess", "os.kill", "os.system", "taskkill", "psutil", "signal.", "Popen", "shell"):
        assert forbidden not in source, forbidden


# ---- hooks --------------------------------------------------------------------------------------------------
def test_garbage_reply_is_replaced_in_the_same_turn_and_model_unloaded():
    async def scenario():
        module, _, rec, _ = guard()
        out = await module.post_reply(ctx(), "Или Или Или")
        await module.wait_idle()
        return module, out, rec

    module, out, rec = run(scenario())
    assert out == mg.APOLOGY and "Или" not in out
    assert module.state is State.RECOVERING and rec.calls == ["unload"]
    assert module.status()["garbage_replies"] == 1


def test_cjk_noise_reply_is_replaced():
    module, *_ = guard()
    out = run(module.post_reply(ctx(), "这是一个测试，我们需要处理这个问题，谢谢你的帮助。"))
    assert out == mg.APOLOGY


def test_normal_reply_is_untouched_and_state_stays_healthy():
    module, *_ = guard()
    assert run(module.post_reply(ctx(), "Байкал — самое глубокое озеро.")) is None
    assert module.state is State.HEALTHY


def test_cloud_served_reply_is_not_judged_against_the_local_model():
    module, *_ = guard()
    assert run(module.post_reply(ctx(served_by="cloud"), "Или Или Или")) is None
    assert module.state is State.HEALTHY


def test_augment_adds_a_routing_note_only_while_unhealthy():
    module, *_ = guard()
    assert run(module.augment(ctx())) is None
    module.observe(Assessment(("repetition",), "garbage"))
    advice = run(module.augment(ctx()))
    assert advice and advice.notes == (mg.HINT_NOTE,) and advice.tags == ("model_guard:degraded",)


def test_routing_hint_prefers_another_model_when_degraded():
    module, *_ = guard()
    assert module.routing_hint()["avoid_local"] is False
    module.observe(Assessment(("empty",), "garbage"))
    hint = module.routing_hint()
    assert hint["avoid_local"] is True and hint["state"] == "degraded"


def test_module_never_blocks_a_turn():
    module, *_ = guard()
    module.observe(Assessment(("empty",), "garbage"))
    assert run(module.pre_route(ctx())) is None


def test_switched_off_module_is_silent(monkeypatch):
    monkeypatch.setenv(mg.MODULE_ENV, "off")
    module, model, *_ = guard()
    assert run(module.post_reply(ctx(), "")) is None and run(module.probe()) is None and model.calls == []


def test_status_shape_and_last_canary():
    module, *_ = guard("Лондон")
    before = module.status()
    assert before["last_canary"] is None and before["model"] == "qwen-test" and before["state"] == "healthy"
    run(module.probe())
    after = module.status()
    assert after["last_canary"]["reason"] == "mismatch" and after["canaries_run"] == 1
    assert after["canary_failures"] == 1 and set(after["recoveries"]) >= {"attempted", "suppressed", "last_action"}


def test_status_never_contains_model_text():
    module, *_ = guard("Секретный ответ ЖЖЖ ЖЖЖ ЖЖЖ ЖЖЖ")
    run(module.probe())
    assert "ЖЖЖ" not in repr(module.status())


# ---- background loop ----------------------------------------------------------------------------------------
def test_background_loop_probes_and_stops_cleanly():
    async def scenario():
        ticks = []

        async def fake_sleep(seconds):
            ticks.append(seconds)
            await asyncio.sleep(0)

        module, model, *_ = guard(sleep=fake_sleep)
        await module.start()
        for _ in range(20):
            await asyncio.sleep(0)
        await module.stop()
        return module, model, ticks

    module, model, ticks = run(scenario())
    assert len(model.calls) >= 2 and ticks[0] == mg.CANARY_INTERVAL_S


def test_degraded_state_probes_faster():
    async def scenario():
        ticks = []

        async def fake_sleep(seconds):
            ticks.append(seconds)
            await asyncio.sleep(0)

        module, *_ = guard("Или Или Или", "Париж", sleep=fake_sleep)
        await module.start()
        for _ in range(30):
            await asyncio.sleep(0)
        await module.stop()
        return ticks

    ticks = run(scenario())
    assert mg.CANARY_DEGRADED_INTERVAL_S in ticks


# ---- factory and pipeline -----------------------------------------------------------------------------------
def test_create_wires_the_runtime_local_adapter():
    class Adapter:
        def __init__(self):
            self.calls = []

        async def chat(self, model, messages, **kw):
            self.calls.append(("chat", model, kw))
            return SimpleNamespace(text="Париж")

        async def unload(self, model):
            self.calls.append(("unload", model))

    adapter = Adapter()
    runtime = SimpleNamespace(local_adapter=adapter, settings=SimpleNamespace(local_models=("qwen3:8b",)))
    module = mg.create(runtime)
    assert run(module.probe()).ok
    run(module.observe(Assessment(("empty",), "garbage")) and module.recover(reason="t"))
    assert adapter.calls[0][:2] == ("chat", "qwen3:8b") and adapter.calls[0][2]["temperature"] == 0.0
    assert ("unload", "qwen3:8b") in adapter.calls


def test_create_without_a_local_adapter_is_a_passive_checker():
    module = mg.create(SimpleNamespace())
    assert module.available is False
    assert run(module.post_reply(ctx(), "Или Или Или")) == mg.APOLOGY


def test_pipeline_applies_the_guard_after_the_model_reply():
    module, *_ = guard()
    pipeline = J2Pipeline([module])
    out = run(pipeline.post_reply(ctx(), "Или Или Или"))
    assert out == mg.APOLOGY


def test_pipeline_note_appears_in_messages_when_degraded():
    module, *_ = guard()
    module.observe(Assessment(("empty",), "garbage"))
    pipeline = J2Pipeline([module])
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "привет"}]
    out = run(pipeline.augment(ctx(), messages))
    assert mg.HINT_NOTE in out[-2]["content"] and out[-1]["content"] == "привет"
