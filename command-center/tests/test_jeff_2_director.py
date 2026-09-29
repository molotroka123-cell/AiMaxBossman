"""Jeff 2.0 director: intent classification on a labelled Russian set, one clarifying question, topic state, plans."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from bcc.pit.j2 import J2Pipeline, TurnContext
from bcc.pit.j2 import director as dr
from bcc.pit.j2.director import DirectorModule, Intent, TopicState, classify

I = Intent

# ---- labelled Russian set ------------------------------------------------------------------------------
LABELLED: list[tuple[str, Intent]] = [
    ("Привет!", I.GREETING), ("Здравствуйте", I.GREETING), ("Добрый вечер, Джефф", I.GREETING), ("хай", I.GREETING),
    ("Доброе утро!", I.GREETING), ("приветствую", I.GREETING),
    ("Пока!", I.FAREWELL), ("До завтра", I.FAREWELL), ("Спокойной ночи", I.FAREWELL), ("Ладно, мне пора", I.FAREWELL),
    ("Спасибо!", I.THANKS), ("Большое спасибо, очень выручил", I.THANKS), ("благодарю", I.THANKS), ("спс", I.THANKS),
    ("Как дела?", I.SMALLTALK), ("Что нового?", I.SMALLTALK), ("Чем занимаешься?", I.SMALLTALK),
    ("Как настроение?", I.SMALLTALK),
    ("Почему небо голубое?", I.QUESTION), ("Кто написал Войну и мир?", I.QUESTION),
    ("Сколько лет Земле?", I.QUESTION), ("Что такое инфляция?", I.QUESTION), ("В чём разница между вирусом и бактерией?", I.QUESTION),
    ("Где находится озеро Байкал?", I.QUESTION), ("Когда началась вторая мировая война?", I.QUESTION),
    ("Объясни, как работает блокчейн", I.QUESTION), ("Чем отличается кошка от кота, кроме пола?", I.QUESTION),
    ("Расскажи про Древний Рим", I.QUESTION),
    ("Как приготовить борщ?", I.HOWTO), ("Как настроить VPN на телефоне?", I.HOWTO),
    ("Как научиться быстро печатать?", I.HOWTO), ("Как избавиться от привычки грызть ногти", I.HOWTO),
    ("Как включить тёмную тему в телеграме?", I.HOWTO), ("Что делать, если сломался ноутбук?", I.HOWTO),
    ("Напиши письмо начальнику об отпуске", I.TASK), ("Составь план тренировок на месяц", I.TASK),
    ("Придумай название для кофейни", I.TASK), ("Сократи этот текст до трёх предложений", I.TASK),
    ("Сочини стих про осень", I.TASK), ("Расскажи анекдот", I.TASK), ("Подготовь резюме для работы менеджером", I.TASK),
    ("Дай мне список идей для подарка", I.TASK),
    ("Напиши функцию на python, которая переворачивает строку", I.CODE), ("Исправь мой код, там ошибка в цикле for", I.CODE),
    ("Напиши sql запрос для выборки пользователей", I.CODE), ("Что значит traceback в питоне", I.QUESTION),
    ("Сделай скрипт на bash для бэкапа", I.CODE), ("Помоги отладить программу, падает с exception", I.CODE),
    ("Сделай регулярку для проверки email", I.CODE),
    ("Посоветуй хороший фильм на вечер", I.ADVICE), ("Что лучше выбрать: айфон или андроид?", I.ADVICE),
    ("Стоит ли учить английский в 30 лет?", I.ADVICE), ("Как думаешь, мне менять работу?", I.ADVICE),
    ("Куда поехать в отпуск летом?", I.ADVICE), ("Порекомендуй книгу про историю", I.ADVICE),
    ("Мне грустно", I.EMOTIONAL), ("Я очень устала от всего этого", I.EMOTIONAL), ("Меня бесит мой начальник", I.EMOTIONAL),
    ("Не знаю, что делать, всё плохо", I.EMOTIONAL), ("Мне страшно перед завтрашним собеседованием", I.EMOTIONAL),
    ("Нет сил вставать по утрам", I.EMOTIONAL),
    ("Ты не понял, я спрашивал совсем про другое", I.FEEDBACK_NEG), ("Это неправда", I.FEEDBACK_NEG),
    ("Опять не то", I.FEEDBACK_NEG), ("Что за бред ты несёшь", I.FEEDBACK_NEG), ("Ты ошибся в расчётах", I.FEEDBACK_NEG),
    ("Отлично, то что надо", I.FEEDBACK_POS), ("Супер, молодец", I.FEEDBACK_POS), ("Круто получилось", I.FEEDBACK_POS),
    ("Нет, я имел в виду вторник", I.CORRECTION), ("Не совсем, мне нужен другой вариант", I.CORRECTION),
    ("Я не об этом спрашивал", I.CORRECTION), ("Точнее, не в среду, а в четверг", I.CORRECTION),
    ("Продолжай", I.CONTINUE), ("Дальше", I.CONTINUE), ("Расскажи подробнее", I.CONTINUE), ("Ещё", I.CONTINUE),
    ("А дальше?", I.CONTINUE), ("Поподробнее", I.CONTINUE),
    ("Да", I.CONFIRM), ("Ок", I.CONFIRM), ("Давай", I.CONFIRM), ("Согласен", I.CONFIRM), ("Конечно!", I.CONFIRM),
    ("Хорошо, договорились", I.CONFIRM),
    ("Нет", I.DENY), ("Не надо", I.DENY), ("Хватит", I.DENY), ("не хочу", I.DENY),
    ("Кто ты?", I.META), ("Что ты умеешь?", I.META), ("Ты бот или человек?", I.META), ("Как тебя зовут?", I.META),
    ("Ты помнишь меня?", I.META),
    ("Какая сегодня погода в Москве?", I.CURRENT), ("Какой курс доллара?", I.CURRENT), ("Что нового в новостях?", I.CURRENT),
    ("Который час?", I.CURRENT),
    ("Переведи на английский: доброе утро", I.TRANSLATE), ("Как по-немецки будет «спасибо»?", I.TRANSLATE),
    ("Переведи это на русский", I.TRANSLATE), ("Translate this to French please", I.TRANSLATE),
    ("Сколько будет 15 умножить на 12? 15*12", I.MATH), ("Посчитай 2+2*2", I.MATH), ("Реши уравнение x+5=12", I.MATH),
    ("Сколько будет 7 + 8", I.MATH), ("Найди процент от 340", I.MATH),
    ("Давай сыграем в ролевую игру", I.ROLEPLAY), ("Представь, что ты капитан пиратов", I.ROLEPLAY),
    ("Притворись моим тренером по бегу", I.ROLEPLAY),
    ("...", I.UNCLEAR), ("ммм", I.UNCLEAR), ("?", I.UNCLEAR), ("эээ", I.UNCLEAR), ("хм", I.UNCLEAR),
    ("Добрый день", I.GREETING), ("Всего доброго!", I.FAREWELL), ("Спасибо тебе большое", I.THANKS),
    ("Как жизнь?", I.SMALLTALK), ("Зачем нужны налоги?", I.QUESTION), ("Как выучить английский за год?", I.HOWTO),
    ("Составь список покупок на неделю", I.TASK), ("Что такое рекурсия в python?", I.QUESTION),
    ("Какой ноутбук лучше взять студенту?", I.ADVICE), ("Я боюсь, что не сдам экзамен", I.EMOTIONAL),
    ("Что ты можешь?", I.META), ("Сколько сейчас времени?", I.CURRENT), ("Реши пример 12*12", I.MATH),
]

ACCURACY_FLOOR = 0.88
PER_INTENT_RECALL_FLOOR = 0.6


def run(coro):
    return asyncio.run(coro)


def ctx(text: str, person: str = "p1", mid: str = "1", memory: bool = False) -> TurnContext:
    return TurnContext(person_key=person, who="tg:" + person, text=text, message_id=mid, memory_enabled=memory)


def turn(module: DirectorModule, text: str, person: str = "p1", mid: str | None = None, memory: bool = False):
    module._n = getattr(module, "_n", 0) + 1
    c = ctx(text, person, mid or str(module._n), memory)
    return run(module.plan_turn(c)), c


# ---- classification ------------------------------------------------------------------------------------
def test_labelled_set_is_large_and_covers_every_intent():
    assert len(LABELLED) >= 120
    assert {label for _, label in LABELLED} == set(Intent)


def test_rules_accuracy_on_the_labelled_russian_set_meets_the_floor():
    hits = [(text, label, classify(text).intent) for text, label in LABELLED]
    wrong = [(t, want.value, got.value) for t, want, got in hits if want != got]
    accuracy = 1 - len(wrong) / len(hits)
    assert accuracy >= ACCURACY_FLOOR, (accuracy, wrong)


def test_every_intent_has_a_recall_floor():
    for intent in Intent:
        cases = [text for text, label in LABELLED if label == intent]
        ok = sum(1 for text in cases if classify(text).intent == intent)
        assert ok / len(cases) >= PER_INTENT_RECALL_FLOOR, (intent, ok, len(cases))


@pytest.mark.parametrize("text,want", [
    ("Привет", I.GREETING), ("пока", I.FAREWELL), ("Да", I.CONFIRM), ("Нет", I.DENY), ("Продолжай", I.CONTINUE),
    ("Сколько будет 2+3?", I.MATH), ("Мне грустно", I.EMOTIONAL), ("Переведи слово apple", I.TRANSLATE),
])
def test_anchor_cases_are_high_confidence(text, want):
    result = classify(text)
    assert result.intent is want and result.confidence >= 0.6


def test_classification_is_deterministic_and_pure():
    for text, _ in LABELLED[:30]:
        assert classify(text) == classify(text)


def test_dialogue_act_covers_every_intent():
    assert set(dr.ACT_OF) == set(Intent)
    assert classify("Кто написал Войну и мир?").act == "ask"
    assert classify("Напиши стих про осень").act == "request"
    assert classify("Мне грустно").act == "share"


def test_empty_and_symbol_only_text_is_unclear():
    assert classify("").intent is I.UNCLEAR and classify("   ").intent is I.UNCLEAR and classify("!!!").intent is I.UNCLEAR


def test_short_thanks_beats_a_stray_question_mark():
    assert classify("Спасибо, а можно ещё?").intent is I.THANKS and classify("Спасибо!").intent is I.THANKS


# ---- model second ------------------------------------------------------------------------------------------
def test_confident_rules_never_call_the_model():
    calls = []

    async def model(messages):
        calls.append(messages)
        return "task"

    module = DirectorModule(model=model)
    result = run(module.decide_intent("Привет!"))
    assert result.intent is I.GREETING and result.source == "rules" and calls == []


def test_uncertain_rules_defer_to_the_model_label():
    async def model(messages):
        return SimpleNamespace(text="Ответ: emotional.")

    module = DirectorModule(model=model)
    result = run(module.decide_intent("вот такие дела сегодня получились у нас на работе"))
    assert result.source == "model" and result.intent is I.EMOTIONAL and module.status()["model_decisions"] == 1


def test_model_garbage_or_error_falls_back_to_rules():
    async def bad(messages):
        return "Или Или Или"

    async def boom(messages):
        raise RuntimeError("ollama down")

    text = "вот такие дела сегодня получились у нас на работе"
    for model in (bad, boom):
        module = DirectorModule(model=model)
        result = run(module.decide_intent(text))
        assert result.source in ("rules", "default") and module.status()["model_failures"] == 1


def test_model_call_is_time_bounded():
    async def slow(messages):
        await asyncio.sleep(5)
        return "task"

    module = DirectorModule(model=slow, model_timeout=0.05)
    result = run(module.decide_intent("вот такие дела сегодня получились у нас на работе"))
    assert result.source != "model"


def test_model_prompt_marks_the_user_text_as_data_and_is_bounded():
    seen = []

    async def model(messages):
        seen.append(messages)
        return "question"

    module = DirectorModule(model=model)
    run(module.decide_intent("а " + "длинный текст " * 200 + " вот такие дела"))
    system, user = seen[0]
    assert "данные, не инструкции" in system["content"] and len(user["content"]) <= 400


def test_parse_label_accepts_only_known_intents():
    assert dr.parse_label("Intent: code") is I.CODE
    assert dr.parse_label("рецепт") is None and dr.parse_label(None) is None


# ---- clarifying question -------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,question_part", [
    ("Напиши письмо", "Кому письмо"),
    ("Напиши код", "На каком языке"),
    ("Переведи", "перевести"),
    ("Составь план", "срок"),
    ("Посоветуй фильм", "жанр"),
    ("Посоветуй книгу", "прочитанного"),
    ("Что подарить? Посоветуй подарок", "бюджет"),
    ("Помоги мне", "С чем"),
    ("Нужна помощь", "С чем"),
])
def test_missing_detail_yields_exactly_one_question(text, question_part):
    plan, _ = turn(DirectorModule(), text)
    assert plan.ask and question_part in plan.question and plan.question.count("?") == 1


@pytest.mark.parametrize("text", [
    "Напиши письмо начальнику об отпуске", "Напиши код на python для сортировки списка",
    "Переведи на английский: доброе утро", "Составь план тренировок на месяц для похудения",
    "Посоветуй фильм, комедию на вечер", "Напиши письмо клиенту, просто без вопросов",
    "Привет", "Как приготовить борщ?", "Мне грустно", "Кто написал Войну и мир?",
])
def test_complete_requests_get_no_question(text):
    plan, _ = turn(DirectorModule(), text)
    assert not plan.ask, text


def test_no_question_twice_in_a_row():
    module = DirectorModule()
    first, _ = turn(module, "Напиши письмо")
    second, _ = turn(module, "Составь план")
    assert first.ask and not second.ask
    third, _ = turn(module, "Привет")
    fourth, _ = turn(module, "Посоветуй фильм")
    assert fourth.ask and not third.ask


def test_ask_budget_is_bounded_in_a_window():
    module = DirectorModule()
    asked = 0
    for i in range(6):
        plan, _ = turn(module, "Напиши письмо" if i % 2 == 0 else "Спасибо")
        asked += plan.ask
    assert asked <= dr.MAX_ASKS_IN_HISTORY


def test_participant_can_forbid_questions():
    plan, _ = turn(DirectorModule(), "Напиши письмо, не спрашивай ничего, просто сделай")
    assert not plan.ask


def test_emotional_turn_is_never_interrogated():
    plan, _ = turn(DirectorModule(), "Мне очень плохо, помоги")
    assert not plan.ask and plan.shape == "empathy_first"


def test_note_carries_the_single_question_and_forbids_others():
    module = DirectorModule()
    plan, _ = turn(module, "Напиши письмо")
    note = module.note_for(plan)
    assert "ровно один уточняющий вопрос" in note and plan.question in note and "никаких вопросов" in note


def test_trim_questions_keeps_one_trailing_question():
    reply = "Могу написать письмо. Кому оно адресовано? О чём оно? Нужен ли официальный тон?"
    out = dr.trim_questions(reply, 1)
    assert out.count("?") == 1 and out.startswith("Могу написать письмо.")


def test_trim_questions_never_touches_questions_in_the_body_or_a_single_question():
    body = "Вопрос «почему?» решается так: ставим флажок. Потом проверяем."
    assert dr.trim_questions(body, 1) == body
    single = "Держи ответ. Подойдёт?"
    assert dr.trim_questions(single, 1) == single


def test_boilerplate_closing_question_is_stripped():
    reply = "Вот план на неделю: пн, ср, пт. Чем ещё могу помочь?"
    assert dr.strip_boilerplate(reply) == "Вот план на неделю: пн, ср, пт."
    kept = "Чем ещё могу помочь?"
    assert dr.strip_boilerplate(kept) == kept


def test_post_reply_trims_extra_questions_when_a_question_was_planned():
    module = DirectorModule()
    plan, c = turn(module, "Напиши письмо", mid="m1")
    reply = "Помогу. Кому письмо? О чём оно? В каком тоне?"
    out = run(module.post_reply(c, reply))
    assert out and out.count("?") == 1


def test_post_reply_strips_boilerplate_when_no_question_was_planned():
    module = DirectorModule()
    plan, c = turn(module, "Как приготовить борщ?", mid="m2")
    out = run(module.post_reply(c, "Свари бульон, добавь свёклу. Есть ли ещё вопросы?"))
    assert out == "Свари бульон, добавь свёклу."


def test_post_reply_leaves_normal_text_and_unknown_turns_alone():
    module = DirectorModule()
    plan, c = turn(module, "Как приготовить борщ?", mid="m3")
    assert run(module.post_reply(c, "Свари бульон и добавь свёклу.")) is None
    assert run(module.post_reply(ctx("x", mid="never-planned"), "Что? Как? Почему?")) is None


# ---- topic state ---------------------------------------------------------------------------------------------
def test_follow_up_inherits_the_topic():
    module = DirectorModule()
    turn(module, "Расскажи про озеро Байкал и его глубину")
    plan, c = turn(module, "А почему оно такое глубокое?")
    assert plan.follow_up
    state = module._states["p1"]
    assert any(word.startswith("байкал") for word in state.top(5))
    note = module.note_for(plan, state)
    assert "продолжает тему" in note and "байкал" in note


def test_topic_shift_is_detected_and_resets_context():
    module = DirectorModule()
    turn(module, "Расскажи про озеро Байкал и его глубину")
    plan, _ = turn(module, "Помоги выбрать ноутбук для программирования и монтажа видео")
    assert plan.topic_shift and not plan.follow_up
    assert not any(w.startswith("байкал") for w in module._states["p1"].top(6))


def test_short_acknowledgement_keeps_the_topic_and_does_not_shift():
    module = DirectorModule()
    turn(module, "Расскажи про озеро Байкал и его глубину")
    plan, _ = turn(module, "Да")
    assert not plan.topic_shift
    assert any(w.startswith("байкал") for w in module._states["p1"].top(6))


def test_topic_scores_decay_over_turns_and_stay_bounded():
    state = TopicState()
    dr.advance_topic(state, "Байкал глубокое озеро Сибирь омуль", I.QUESTION)
    first = state.topic[dr.stem_of("байкал")]["s"]
    for text in ("Погода сегодня хорошая, солнце светит", "Кофе горячий, чашка большая, сахар"):
        dr.advance_topic(state, text, I.SMALLTALK)
    assert state.topic.get(dr.stem_of("байкал"), {"s": 0})["s"] < first
    for i in range(30):
        dr.advance_topic(state, f"слово{i}aaa слово{i}bbb ещё{i}ccc другое{i}ddd", I.QUESTION)
    assert len(state.topic) <= dr.TOPIC_SLOTS


def test_state_is_per_participant_and_lru_bounded():
    module = DirectorModule(max_tracked=3)
    turn(module, "Расскажи про озеро Байкал и его глубину", person="alice")
    turn(module, "Помоги выбрать ноутбук для программирования", person="bob")
    assert not any(w.startswith("байкал") for w in module._states["bob"].top(6))
    for i in range(10):
        turn(module, "привет", person=f"p{i}")
    assert module.status()["tracked_participants"] == 3


def test_topic_persists_under_the_participant_namespace_only_with_memory_consent(tmp_path):
    class Vault:
        def person_dir(self, key):
            if key != "a" * 64:
                raise ValueError("invalid PIT person_key")
            return tmp_path / key

    key = "a" * 64
    module = DirectorModule(vault=Vault())
    for i in range(4):
        turn(module, "Расскажи про озеро Байкал и его глубину", person=key, memory=False)
    assert not (tmp_path / key).exists()
    for i in range(4):
        turn(module, "Расскажи про озеро Байкал и его глубину", person=key, memory=True)
    saved = json.loads((tmp_path / key / "j2" / "director.json").read_text(encoding="utf-8"))
    assert saved["schema"] == dr.SCHEMA and "Расскажи" not in json.dumps(saved, ensure_ascii=False)
    assert all(set(v) == {"w", "s"} for v in saved["topic"].values())
    fresh = DirectorModule(vault=Vault())
    turn(fresh, "А почему оно такое глубокое?", person=key, memory=True)
    assert any(w.startswith("байкал") for w in fresh._states[key].top(6))
    turn(fresh, "привет", person=key, memory=False)
    assert not (tmp_path / key / "j2" / "director.json").exists()


def test_invalid_person_key_never_touches_the_filesystem(tmp_path):
    class Vault:
        def person_dir(self, key):
            raise ValueError("invalid PIT person_key")

    module = DirectorModule(vault=Vault())
    for _ in range(4):
        turn(module, "Расскажи про озеро Байкал и его глубину", person="../../etc", memory=True)
    assert list(tmp_path.iterdir()) == []


def test_corrupt_or_foreign_state_file_is_ignored():
    assert TopicState.from_json({"schema": "other"}) is None
    assert TopicState.from_json("nope") is None
    ok = TopicState.from_json({"schema": dr.SCHEMA, "turn": 3, "topic": {"байкал": {"w": "байкал", "s": 1.0}, "x": "bad"}})
    assert ok and list(ok.topic) == ["байкал"]


# ---- plan ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,length,shape", [
    ("Привет!", "one_line", "conversational"),
    ("Почему небо голубое?", "short", "direct"),
    ("Как приготовить борщ?", "medium", "steps"),
    ("Напиши функцию на python для сортировки", "medium", "code"),
    ("Мне грустно", "short", "empathy_first"),
    ("Что лучше выбрать: айфон или андроид?", "short", "options"),
    ("Какой курс доллара?", "one_line", "direct"),
])
def test_default_plan_by_intent(text, length, shape):
    plan, _ = turn(DirectorModule(), text)
    assert (plan.length, plan.shape) == (length, shape)


@pytest.mark.parametrize("text,length", [
    ("Расскажи про Байкал кратко", "short"),
    ("Что такое инфляция, в двух словах", "short"),
    ("Что такое инфляция, одним предложением", "one_line"),
    ("Расскажи про Древний Рим подробно", "long"),
    ("Объясни блокчейн максимально подробно", "long"),
])
def test_explicit_length_wish_wins(text, length):
    plan, _ = turn(DirectorModule(), text)
    assert plan.length == length


def test_step_wish_switches_the_shape():
    plan, _ = turn(DirectorModule(), "Объясни пошагово, как работает блокчейн")
    assert plan.shape == "steps"


def test_note_is_short_russian_data_and_grants_nothing():
    module = DirectorModule()
    plan, _ = turn(module, "Как приготовить борщ?")
    note = module.note_for(plan)
    assert len(note) < 420 and note.startswith("Директор диалога")
    for banned in ("разрешаю", "доступ", "команд", "пароль", "владелец"):
        assert banned not in note.lower()


def test_current_intent_note_warns_about_missing_live_data():
    module = DirectorModule()
    plan, _ = turn(module, "Какая сегодня погода в Москве?")
    assert "не выдумывай" in module.note_for(plan)


# ---- hooks, status, factory -----------------------------------------------------------------------------------
def test_augment_returns_a_note_and_a_tag_and_never_a_reply():
    module = DirectorModule()
    advice = run(module.augment(ctx("Напиши письмо")))
    assert advice.reply is None and advice.notes and advice.tags == ("director:task",)
    assert run(module.pre_route(ctx("Напиши письмо"))) is None


def test_empty_text_gets_no_note():
    assert run(DirectorModule().augment(ctx("   "))) is None


def test_module_can_be_switched_off(monkeypatch):
    monkeypatch.setenv(dr.MODULE_ENV, "off")
    module = DirectorModule()
    assert run(module.augment(ctx("Напиши письмо"))) is None and run(module.post_reply(ctx("x"), "Что? Как?")) is None


def test_status_counts_intents_and_clarifications():
    module = DirectorModule()
    turn(module, "Привет")
    turn(module, "Напиши письмо")
    st = module.status()
    assert st["turns"] == 2 and st["by_intent"] == {"greeting": 1, "task": 1} and st["clarifications"] == 1
    assert st["model_assist"] is False


def test_create_reads_the_runtime_and_model_assist_is_opt_in(monkeypatch):
    calls = []

    class Adapter:
        async def chat(self, model, messages, **kw):
            calls.append(model)
            return SimpleNamespace(text="emotional")

    runtime = SimpleNamespace(local_adapter=Adapter(), settings=SimpleNamespace(local_models=("qwen3:8b",)), vault=None)
    assert dr.create(runtime).status()["model_assist"] is False
    monkeypatch.setenv(dr.MODEL_ENV, "on")
    module = dr.create(runtime)
    assert module.status()["model_assist"] is True
    run(module.decide_intent("вот такие дела сегодня получились у нас на работе"))
    assert calls == ["qwen3:8b"]
    injected = SimpleNamespace(j2_local_chat=lambda m: None)
    assert dr.create(injected).status()["model_assist"] is True


def test_pipeline_puts_the_director_note_before_the_user_message():
    pipeline = J2Pipeline([DirectorModule()])
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "Напиши письмо"}]
    out = run(pipeline.augment(ctx("Напиши письмо"), messages))
    assert "Директор диалога" in out[-2]["content"] and out[-1]["content"] == "Напиши письмо"


def test_pipeline_post_reply_trims_through_the_pipeline():
    module = DirectorModule()
    pipeline = J2Pipeline([module])
    c = ctx("Напиши письмо", mid="pp1")
    run(pipeline.augment(c, [{"role": "user", "content": c.text}]))
    out = run(pipeline.post_reply(c, "Хорошо. Кому оно? О чём оно?"))
    assert out.count("?") == 1
