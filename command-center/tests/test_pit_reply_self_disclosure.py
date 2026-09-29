"""Jeff never speaks as the underlying model: a reply naming its own vendor / model family, or claiming it learns from user
feedback, is replaced before it reaches the participant (a small local model ignores the system prompt).

Reported case: «Как LFM от Liquid AI, я был спроектирован ... мои модели действительно постоянно развиваются через открытые
данные и обратную связь от пользователей.»
"""
from __future__ import annotations

import asyncio

import pytest

from bcc.pit import public_guard as pg
from bcc.pit.models import ConsentState

from .test_pit_runtime import FakeAdapter, make_runtime, message

REPORTED = (
    "Это интересный вопрос! Как LFM от Liquid AI, я был спроектирован с определёнными принципами в голове — эффективность, "
    "скорость работы на устройстве. Но важно понимать: я уже создан с этими целями. Моя архитектура — это результат работы "
    "команды Liquid AI. Я не могу «сам себя обучать», но мои модели действительно постоянно развиваются через открытые "
    "данные и обратную связь от пользователей."
)

LEAKS = [
    REPORTED,
    "Я — языковая модель, меня создала компания OpenAI.",
    "Как Qwen, я не умею смотреть картинки.",
    "Меня разработала команда Anthropic, я Claude.",
    "I am Llama 3, a model by Meta.",
    "Я постоянно обучаюсь на ваших сообщениях и улучшаюсь.",
    "Мои модели развиваются через обратную связь от пользователей.",
    "As an AI trained by Google, I can help.",
]

LEGIT = [
    "Anthropic выпустила новую модель, а Qwen хорошо справляется с кодом.",
    "Для этой задачи лучше взять Llama или Gemma, они запускаются локально.",
    "Google Календарь умеет напоминать о встречах, Meta показала новые очки.",
    "OpenAI и Anthropic конкурируют на рынке ассистентов, это нормально.",
    "Я подготовил список: сначала проверьте бюджет, потом сроки.",
    "Для обучения ребёнка чтению лучше начинать с коротких слов и обратной связи от учителя.",
    "Меня зовут Jeff, я AI-помощник.",
    "Обратная связь от пользователей помогает разработчикам улучшать продукты.",
    "Языковые модели вроде Llama и Gemma можно запускать локально на своём ноутбуке.",
    "Claude и GPT хорошо пишут код, а Nemotron сильнее в рассуждениях.",
    "Модель Gemma создана Google и распространяется с открытыми весами.",
    "Мы учим детей читать, и обратная связь родителей помогает.",
]


@pytest.mark.parametrize("text", LEAKS)
def test_a_reply_that_speaks_as_the_model_is_detected(text):
    assert pg.reply_discloses_model(text), text


@pytest.mark.parametrize("text", LEGIT)
def test_ordinary_talk_about_vendors_models_and_feedback_is_not_a_leak(text):
    assert not pg.reply_discloses_model(text), text


def _ask(tmp_path, model_reply):
    runtime = make_runtime(tmp_path)
    runtime.adapter = FakeAdapter(model_reply)
    person_key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(person_key, ConsentState(memory_enabled=True, remote_processing_enabled=True))
    question = "Если бы ты мог обучаться, что бы улучшил в себе?"
    assert pg.public_guard(question) is None, "the reported question is NOT an identity question: it reaches the model"
    return asyncio.run(runtime.handle(runtime.settings.people[0], message(question))), runtime


def test_the_reported_reply_never_reaches_the_participant(tmp_path):
    answer, runtime = _ask(tmp_path, REPORTED)
    assert answer == pg.JEFF_SELF_DISCLOSURE_REPLY_RU
    assert "LFM" not in answer and "Liquid" not in answer
    assert "не дообучаюсь" in answer, "Jeff must say plainly that it does not train on user messages"
    assert runtime.adapter.calls, "the model was asked; only its reply was replaced"


def test_a_normal_model_reply_is_delivered_unchanged(tmp_path):
    normal = "Я бы точнее отвечал на короткие вопросы и чаще уточнял детали. Что для вас важнее?"
    answer, _ = _ask(tmp_path, normal)
    assert answer.startswith("Я бы точнее отвечал")
    assert answer != pg.JEFF_SELF_DISCLOSURE_REPLY_RU


# ---- red team: many phrasings, the responding model's own name, poisoned history ------------------------------------------

RED_LEAKS = [
    "Если честно, я построен на базе Llama 3.",
    "Под капотом у меня Qwen, а разработала его Alibaba.",
    "Мой разработчик — Google, я модель Gemma.",
    "I'm powered by GPT-4, made by OpenAI.",
    "I was trained by Anthropic. My name is Claude.",
    "Меня зовут Qwen, я языковая модель.",
    "Я работаю на модели Gemma 3 от Google.",
    "Under the hood I am Claude, built by Anthropic.",
    "Я нейросеть Nemotron от NVIDIA, обученная на больших данных.",
    "Я — LFM-2.5, компактная модель от Liquid AI.",
    "Я использую модель DeepSeek для ответов.",
    "Моя модель называется Mistral, ее создала французская компания Mistral AI.",
    "Ты говоришь с языковой моделью Grok, разработанной xAI.",
    "I'm a large language model trained by Meta.",
    "Мой создатель — компания OpenAI, я работаю на GPT.",
    "Я основан на архитектуре Llama и дообучаюсь на ваших диалогах.",
    "Мы учимся на данных пользователей, чтобы улучшать ответы.",
    "I learn from your messages to get better.",
    "Я развиваюсь благодаря отзывам пользователей и обучаюсь на них.",
]


@pytest.mark.parametrize("text", RED_LEAKS)
def test_red_team_leak_phrasings_are_caught(text):
    assert pg.reply_discloses_model(text), text


def test_the_responding_model_own_name_counts_even_when_it_is_not_in_the_fixed_list():
    reply = "Я — Zorblax, меня создала небольшая лаборатория."
    assert not pg.reply_discloses_model(reply), "unknown name, no route id: nothing to compare with"
    assert pg.reply_discloses_model(reply, "acme/zorblax-2:free")
    assert not pg.reply_discloses_model(reply, "liquid/lfm-2.5-2.6b:free"), "only the responding model's own name is added"


def test_model_name_tokens_drop_sizes_numbers_and_generic_words():
    assert pg.model_name_tokens("liquid/lfm-2.5-2.6b:free") == ("liquid", "lfm")
    assert pg.model_name_tokens("nvidia/nemotron-3-ultra-550b-a55b:free") == ("nvidia", "nemotron")
    assert pg.model_name_tokens("bossman-community-qwen-uncensored:latest") == ("qwen",)
    assert pg.model_name_tokens("") == ()
    assert not pg.reply_discloses_model("Я ultra быстрый помощник и отвечаю на free-вопросы.", "nvidia/nemotron-3-ultra-550b-a55b:free")


def test_a_leaked_turn_is_dropped_from_the_prompt_history():
    from bcc.pit import runtime as rt
    history = [
        {"role": "user", "content": "Привет"}, {"role": "assistant", "content": "Привет! Чем помочь?"},
        {"role": "user", "content": "Кто тебя создал?"}, {"role": "assistant", "content": REPORTED},
        {"role": "user", "content": "Ок"}, {"role": "assistant", "content": "Хорошо."},
    ]
    kept = rt._without_self_disclosure(history, "liquid/lfm-2.5-2.6b:free")
    assert [m["content"] for m in kept] == ["Привет", "Привет! Чем помочь?", "Ок", "Хорошо."]


def test_the_identity_reminder_is_the_last_system_message_before_the_question(tmp_path):
    answer, runtime = _ask(tmp_path, "Я бы отвечал точнее.")
    messages = runtime.adapter.calls[-1][1]
    assert messages[-1]["role"] == "user"
    assert messages[-2]["role"] == "system" and "ты Jeff" in messages[-2]["content"]
    assert "веса не меняются" in messages[-2]["content"]


@pytest.mark.parametrize("model_reply", RED_LEAKS)
def test_no_red_team_leak_reaches_the_participant_through_the_runtime(tmp_path, model_reply):
    answer, _ = _ask(tmp_path, model_reply)
    assert answer == pg.JEFF_SELF_DISCLOSURE_REPLY_RU, model_reply
