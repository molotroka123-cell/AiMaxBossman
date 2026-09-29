from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PersonalCategory(StrEnum):
    AGE_RANGE = "age_range"
    BUDGET_RANGE = "budget_range"
    CITY_REGION = "city_region"
    WORK_SCHEDULE = "work_schedule"
    HOUSEHOLD_CONTEXT = "household_context"
    TRAVEL_COMPANIONS = "travel_companions"
    EXPERIENCE_LEVEL = "experience_level"
    DEVICE_ECOSYSTEM = "device_ecosystem"
    AVAILABILITY_WINDOW = "availability_window"
    COMMUNICATION_PREFERENCE = "communication_preference"


@dataclass(frozen=True, slots=True)
class PersonalQuestion:
    category: PersonalCategory
    question: str
    trigger_intents: frozenset[str]
    utility: float
    exact_value_forbidden: bool = False


QUESTIONS = (
    PersonalQuestion(
        PersonalCategory.AGE_RANGE,
        "Если хочешь, можешь назвать примерный возрастной диапазон — это поможет точнее подобрать рекомендации.",
        frozenset({"education", "career", "travel", "shopping"}),
        0.45,
        exact_value_forbidden=False,
    ),
    PersonalQuestion(
        PersonalCategory.BUDGET_RANGE,
        "Какой примерный бюджетный диапазон тебе комфортен? Можно очень грубо.",
        frozenset({"shopping", "travel", "services", "hardware"}),
        0.90,
        exact_value_forbidden=True,
    ),
    PersonalQuestion(
        PersonalCategory.CITY_REGION,
        "Для локальной рекомендации назови, пожалуйста, только город или регион — точный адрес не нужен.",
        frozenset({"local", "travel", "restaurants", "services", "weather"}),
        0.95,
        exact_value_forbidden=True,
    ),
    PersonalQuestion(
        PersonalCategory.WORK_SCHEDULE,
        "У тебя скорее обычный дневной график, сменный или плавающий? Можно ответить примерно.",
        frozenset({"productivity", "reminders", "work", "learning"}),
        0.65,
    ),
    PersonalQuestion(
        PersonalCategory.HOUSEHOLD_CONTEXT,
        "Рекомендация только для тебя или нужно учитывать ещё партнёра/семью/домашних?",
        frozenset({"shopping", "travel", "home", "planning"}),
        0.70,
    ),
    PersonalQuestion(
        PersonalCategory.TRAVEL_COMPANIONS,
        "Обычно ты путешествуешь один, с партнёром, друзьями или семьёй?",
        frozenset({"travel"}),
        0.80,
    ),
    PersonalQuestion(
        PersonalCategory.EXPERIENCE_LEVEL,
        "Ты в этой теме новичок, уверенный пользователь или уже продвинутый?",
        frozenset({"code", "learning", "hardware", "finance", "science"}),
        0.85,
    ),
    PersonalQuestion(
        PersonalCategory.DEVICE_ECOSYSTEM,
        "Какая у тебя основная экосистема устройств — Apple, Windows/Android или смешанная?",
        frozenset({"hardware", "apps", "productivity", "shopping"}),
        0.70,
    ),
    PersonalQuestion(
        PersonalCategory.AVAILABILITY_WINDOW,
        "Когда тебе обычно удобнее заниматься такими задачами: утром, днём или вечером?",
        frozenset({"learning", "productivity", "planning"}),
        0.55,
    ),
    PersonalQuestion(
        PersonalCategory.COMMUNICATION_PREFERENCE,
        "Тебе удобнее, когда я сам предлагаю следующий шаг, или лучше отвечать строго на вопрос?",
        frozenset({"chat", "general"}),
        0.75,
    ),
)

FORBIDDEN_PERSONAL_TOPICS = frozenset({
    "exact_address",
    "government_id",
    "bank_account",
    "card_number",
    "password",
    "2fa",
    "seed_phrase",
    "medical_diagnosis",
    "sexual_life",
    "religion",
    "ethnicity",
    "political_preference",
})


def eligible_personal_questions(
    *,
    intent: str,
    already_known: set[str] | frozenset[str],
    skipped: set[str] | frozenset[str],
    enabled: bool,
) -> list[PersonalQuestion]:
    if not enabled:
        return []
    return [
        q for q in QUESTIONS
        if (intent in q.trigger_intents or "general" in q.trigger_intents)
        and q.category.value not in already_known
        and q.category.value not in skipped
    ]


def choose_personal_question(
    *,
    intent: str,
    already_known: set[str] | frozenset[str],
    skipped: set[str] | frozenset[str],
    enabled: bool,
) -> PersonalQuestion | None:
    rows = eligible_personal_questions(
        intent=intent,
        already_known=already_known,
        skipped=skipped,
        enabled=enabled,
    )
    if not rows:
        return None
    return max(rows, key=lambda q: (q.utility, q.category.value))
