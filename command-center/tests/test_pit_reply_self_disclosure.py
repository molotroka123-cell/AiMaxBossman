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
