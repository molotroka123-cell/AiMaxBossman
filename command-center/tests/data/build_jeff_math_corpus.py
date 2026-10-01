"""Builds ``jeff_math_corpus.json``: the fixed, versioned Russian math corpus for Jeff (owner order 2026-10-01).

Every answer key is computed here with Python (``Fraction`` / ``datetime``), never typed by hand. 58 math questions
in 10 categories plus 10 non-math chat questions that are a negative control (the patch must not change them).

Run:  python tests/data/build_jeff_math_corpus.py   (rewrites jeff_math_corpus.json next to this file)
Do NOT edit the corpus after a baseline was taken: a new corpus is a new version number.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from decimal import Decimal, localcontext
from fractions import Fraction as F
from pathlib import Path

VERSION = "jeff-math-corpus/1"
OUT = Path(__file__).with_name("jeff_math_corpus.json")

ITEMS: list[dict] = []


def num(cat: str, question: str, answer, *, decimals: int | None = None) -> None:
    value = F(answer)
    if decimals is not None:
        scaled = value * 10 ** decimals
        q, r = divmod(abs(scaled.numerator), scaled.denominator)
        if 2 * r >= scaled.denominator:
            q += 1
        value = F(q if scaled >= 0 else -q, 10 ** decimals)
    ITEMS.append({"category": cat, "question": question, "answer_type": "number",
                  "answer": f"{value.numerator}/{value.denominator}" if value.denominator != 1 else str(value.numerator),
                  "answer_decimal": _dec(value), "decimals": decimals, "control": False})


def _dec(value: F) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    with localcontext() as ctx:
        ctx.prec = 60
        text = format(Decimal(value.numerator) / Decimal(value.denominator), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def other(cat: str, question: str, kind: str, answer: str) -> None:
    ITEMS.append({"category": cat, "question": question, "answer_type": kind, "answer": answer,
                  "answer_decimal": answer, "decimals": None, "control": False})


def control(question: str) -> None:
    ITEMS.append({"category": "chat_control", "question": question, "answer_type": "none", "answer": "",
                  "answer_decimal": "", "decimals": None, "control": True})


# 1. arithmetic with big numbers
num("arith_big", "Сколько будет 48 271 × 9 356?", 48271 * 9356)
num("arith_big", "Посчитай 123456789 * 987654321", 123456789 * 987654321)
num("arith_big", "Вычисли 2^40", 2 ** 40)
num("arith_big", "Сколько будет 987654321 - 123456789 + 55555555?", 987654321 - 123456789 + 55555555)
num("arith_big", "Чему равно 7919 * 7907 - 12345?", 7919 * 7907 - 12345)
num("arith_big", "Сколько будет 1 000 000 000 / 8 / 25?", F(1_000_000_000, 8 * 25))
num("arith_big", "Найди значение выражения (345 + 678) * (912 - 456) / 12", F((345 + 678) * (912 - 456), 12))
num("arith_big", "Сколько будет 17 в степени 6?", 17 ** 6)

# 2. decimals and fractions
num("decimals_fractions", "Сколько будет 12,75 * 8,4?", F("12.75") * F("8.4"))
num("decimals_fractions", "Посчитай 0.1 + 0.2 + 0.3 * 3", F("0.1") + F("0.2") + F("0.3") * 3)
num("decimals_fractions", "Чему равно 3/7 + 2/5? Ответь десятичной дробью, округлив до 4 знаков.",
    F(3, 7) + F(2, 5), decimals=4)
num("decimals_fractions", "Раздели 22 на 7 и округли до 3 знаков после запятой.", F(22, 7), decimals=3)
num("decimals_fractions", "Сколько будет 5,5 * 4,2 - 3,15?", F("5.5") * F("4.2") - F("3.15"))
num("decimals_fractions", "Чему равно (1/2 + 1/3 + 1/6) * 15,5?", (F(1, 2) + F(1, 3) + F(1, 6)) * F("15.5"))
num("decimals_fractions", "Сколько будет 0,75 * 0,8 * 120?", F("0.75") * F("0.8") * 120)

# 3. percent / discount / VAT
num("percent_vat", "Сколько будет 15% от 2 400?", F(15, 100) * 2400)
num("percent_vat", "Товар стоит 3 500 рублей, скидка 18%. Сколько надо заплатить?", 3500 * (1 - F(18, 100)))
num("percent_vat", "Цена без НДС 12 500 рублей, НДС 20%. Сколько будет с НДС?", 12500 * F(120, 100))
num("percent_vat", "Сумма с НДС 20% равна 7 200 рублей. Сколько из них НДС?", F(7200 * 20, 120))
num("percent_vat", "Цена выросла на 25% и теперь составляет 1 500 рублей. Какой она была раньше?", F(1500 * 100, 125))
num("percent_vat", "Зарплата 80 000 рублей, налог 13%. Сколько останется после уплаты налога?", 80000 * F(87, 100))
num("percent_vat", "Сколько процентов составляет 45 от 360?", F(45, 360) * 100)
num("percent_vat", "Население города выросло с 250 000 до 290 000 человек. На сколько процентов оно выросло?",
    F(290000 - 250000, 250000) * 100)
num("percent_vat", "Сколько будет 7,5% от 1 280?", F("7.5") / 100 * 1280)

# 4. compound / simple interest
num("compound_interest", "Вклад 100 000 рублей под 8% годовых на 3 года со сложным процентом, капитализация раз в год. "
    "Сколько будет на счёте через 3 года? Округли до копеек.", 100000 * F(108, 100) ** 3, decimals=2)
num("compound_interest", "Положили 50 000 рублей под 12% годовых с ежемесячной капитализацией на 2 года. "
    "Какая сумма будет через 2 года? Округли до копеек.", 50000 * (1 + F(12, 100) / 12) ** 24, decimals=2)
num("compound_interest", "Простые проценты: 200 000 рублей под 6% годовых на 4 года. Сколько процентов начислят всего?",
    200000 * F(6, 100) * 4)
num("compound_interest", "Кредит 300 000 рублей под 10% годовых с ежеквартальной капитализацией на 2 года. "
    "Сколько долга накопится за 2 года? Округли до копеек.", 300000 * (1 + F(10, 100) / 4) ** 8, decimals=2)

# 5. unit conversion
num("unit_conversion", "Переведи 5 миль в километры.", F(5) * F(1609344, 1000000))
num("unit_conversion", "Сколько секунд в 3,5 часах?", F("3.5") * 3600)
num("unit_conversion", "Сколько градусов по Фаренгейту — это 37 градусов по Цельсию?", F(37) * F(9, 5) + 32)
num("unit_conversion", "Сколько килограммов в 12 фунтах? Округли до 2 знаков.", F(12) * F(45359237, 100000000), decimals=2)
num("unit_conversion", "Скорость 90 км/ч. Сколько это в метрах в секунду?", F(90 * 1000, 3600))
num("unit_conversion", "Сколько квадратных метров в 3,2 гектарах?", F("3.2") * 10000)
num("unit_conversion", "В бассейне 2,5 кубометра воды. Сколько это литров?", F("2.5") * 1000)

# 6. averages
num("average", "Найди среднее арифметическое чисел 12, 15, 21, 8 и 19.", F(12 + 15 + 21 + 8 + 19, 5))
num("average", "Оценки по математике: 5, 4, 4, 3, 5, 5, 4. Какой средний балл? Округли до 2 знаков.",
    F(5 + 4 + 4 + 3 + 5 + 5 + 4, 7), decimals=2)
num("average", "Среднее арифметическое трёх чисел равно 14, два из них 10 и 20. Чему равно третье число?", 14 * 3 - 30)

# 7. simple equations
num("equation", "Реши уравнение 7x - 15 = 3x + 21", F(21 + 15, 7 - 3))
num("equation", "Найди x: 2(x + 5) = 3x - 4", F(10 + 4, 3 - 2) * 1)   # 2x + 10 = 3x - 4 -> x = 14
num("equation", "Реши квадратное уравнение x² - 7x + 12 = 0. В ответе укажи только больший корень.",
    F(7 + math.isqrt(7 * 7 - 4 * 12), 2))
num("equation", "Реши уравнение: 0,5x + 3 = 11", F(8) / F("0.5"))

# 8. small combinatorics
num("combinatorics", "Сколько существует способов выбрать 4 человек из 12 для комитета?", math.comb(12, 4))
num("combinatorics", "Сколькими способами можно расставить 7 различных книг на полке?", math.factorial(7))
num("combinatorics", "Чему равно 10!", math.factorial(10))
num("combinatorics", "В классе 20 учеников. Сколькими способами можно выбрать старосту, его заместителя и казначея "
    "(три разных человека на три разные должности)?", 20 * 19 * 18)

# 9. time / date arithmetic
d = dt.date(2024, 3, 15) + dt.timedelta(days=100)
other("time_date", "Какая дата будет через 100 дней после 15.03.2024? Ответь в формате ДД.ММ.ГГГГ.", "date",
      f"{d.day:02d}.{d.month:02d}.{d.year}")
num("time_date", "Сколько дней между 12.02.2024 и 05.09.2024?", (dt.date(2024, 9, 5) - dt.date(2024, 2, 12)).days)
weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
other("time_date", "Какой день недели был 1 января 2000 года?", "weekday", weekdays[dt.date(2000, 1, 1).weekday()])
t = (21 * 60 + 40 + 5 * 60 + 35) % 1440
other("time_date", "Сейчас 21:40. Который час будет через 5 часов 35 минут? Ответь в формате ЧЧ:ММ.", "time",
      f"{t // 60:02d}:{t % 60:02d}")
num("time_date", "Поезд отправился в 08:25 и прибыл в 17:10. Сколько минут он был в пути?", (17 * 60 + 10) - (8 * 60 + 25))

# 10. word problems
num("word_problem", "Поезд едет со скоростью 84 км/ч. Сколько километров он проедет за 3 часа 15 минут?", 84 * F(13, 4))
num("word_problem", "В магазине купили 3 кг яблок по 129 рублей за кг и 2,5 кг груш по 188 рублей за кг. "
    "Сколько всего заплатили?", 3 * 129 + F("2.5") * 188)
num("word_problem", "Из бочки в 240 литров сначала вылили 35%, потом ещё 48 литров. Сколько литров осталось?",
    240 - 240 * F(35, 100) - 48)
num("word_problem", "Двое рабочих делают заказ: первый за 6 часов, второй за 3 часа. За сколько часов они сделают "
    "заказ вместе?", 1 / (F(1, 6) + F(1, 3)))
num("word_problem", "Велосипедист проехал 45 км за 2,5 часа. С какой средней скоростью он ехал (в км/ч)?",
    F(45) / F("2.5"))
num("word_problem", "У Пети в 3 раза больше марок, чем у Васи, а вместе у них 156 марок. Сколько марок у Пети?",
    F(156 * 3, 4))
num("word_problem", "Книга стоит 450 рублей. Её купили со скидкой 20%, а на сдачу с 1 000 рублей взяли ещё "
    "2 тетради по 35 рублей. Сколько денег осталось?", 1000 - 450 * F(80, 100) - 2 * 35)

# negative control: not math, must not be altered by the patch
control("Привет! Как у тебя дела?")
control("Посоветуй, что почитать на выходные, мне нравится фантастика.")
control("Мне 25 лет, и я думаю сменить профессию. С чего начать?")
control("Объясни простыми словами, чем TCP отличается от UDP.")
control("Через 2-3 дня у меня собеседование, как лучше успокоиться?")
control("Напиши короткое стихотворение про осень в 4 строки.")
control("Мой номер 8-900-123-45-67, безопасно ли оставлять его на сайте объявлений?")
control("Встреча в 15:30 в понедельник, как лучше к ней подготовиться?")
control("Фильм на 10 из 10!!! Рекомендую. А ты что любишь смотреть?")
control("Я купил наушники за 5 000 рублей, но они мне не нравятся. Вернуть или оставить?")


def build() -> dict:
    cats: dict[str, int] = {}
    for index, item in enumerate(ITEMS, 1):
        cats[item["category"]] = cats.get(item["category"], 0) + 1
        item["id"] = f"{item['category']}-{cats[item['category']]:02d}"
    return {"version": VERSION, "language": "ru", "count": len(ITEMS),
            "math_count": sum(not i["control"] for i in ITEMS), "control_count": sum(i["control"] for i in ITEMS),
            "categories": cats,
            "scoring": "answer_type number: final number of the reply == answer (rounded to `decimals` when set); "
                       "date: last DD.MM.YYYY; time: last HH:MM; weekday: last weekday word. See jeff_math_eval.py.",
            "items": ITEMS}


if __name__ == "__main__":
    corpus = build()
    OUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(corpus["count"], "items", corpus["categories"])
