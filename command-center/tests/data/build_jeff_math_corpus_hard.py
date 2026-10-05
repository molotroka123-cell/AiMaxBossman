"""Builds ``jeff_math_corpus_hard.json``: a SECOND, harder Russian math corpus (stress set, 25 questions).

Why it exists: the first corpus (build_jeff_math_corpus.py) turned out to be too easy for the cloud model (ceiling
effect: 57/58 before the patch). This set uses longer numbers, long compounding, big combinatorics and far dates, i.e.
the cases where a model's "mental" arithmetic is expected to fail. It was written AFTER the first measurement and after
the helper's shapes were fixed; the helper was not tuned on it (it is a held-out check of the same shapes).
Answers are computed with Python (Fraction / datetime), never typed by hand.

Run:  python tests/data/build_jeff_math_corpus_hard.py
"""
from __future__ import annotations

import datetime as dt
import json
import math
from fractions import Fraction as F
from pathlib import Path

import build_jeff_math_corpus as base          # run as a script from tests/data or with tests/data on sys.path

VERSION = "jeff-math-corpus-hard/1"
OUT = Path(__file__).with_name("jeff_math_corpus_hard.json")
base.ITEMS.clear()
num, other = base.num, base.other

num("arith_hard", "Сколько будет 8 765 432 × 6 543 219?", 8765432 * 6543219)
num("arith_hard", "Вычисли 3^45", 3 ** 45)
num("arith_hard", "Посчитай 98765432123 / 12345 и округли до 3 знаков после запятой.", F(98765432123, 12345), decimals=3)
num("arith_hard", "Сколько будет 123456789012 + 987654321098 - 55555555555?", 123456789012 + 987654321098 - 55555555555)
num("arith_hard", "Чему равно (17 + 29) * (113 - 58) * 7?", (17 + 29) * (113 - 58) * 7)
num("arith_hard", "Вычисли 2^100", 2 ** 100)
num("arith_hard", "Сколько будет 999999 × 999999?", 999999 * 999999)
num("arith_hard", "Посчитай 12345,678 * 8,765", F("12345.678") * F("8.765"))
num("arith_hard", "Чему равно 25!", math.factorial(25))
num("arith_hard", "Сколько будет 1/7 + 1/13 + 1/17? Округли до 6 знаков после запятой.", F(1, 7) + F(1, 13) + F(1, 17), decimals=6)

num("compound_hard", "Вклад 250 000 рублей под 7,5% годовых с ежемесячной капитализацией на 5 лет. "
    "Какая сумма будет через 5 лет? Округли до копеек.", 250000 * (1 + F("7.5") / 100 / 12) ** 60, decimals=2)
num("compound_hard", "Положили 80 000 рублей под 11% годовых со сложным процентом (капитализация раз в год) на 12 лет. "
    "Сколько будет на счёте? Округли до копеек.", 80000 * (1 + F(11, 100)) ** 12, decimals=2)
num("compound_hard", "Инвестиция 1 500 000 рублей под 9% годовых с ежеквартальной капитализацией на 7 лет. "
    "Какая сумма накопится? Округли до копеек.", 1500000 * (1 + F(9, 100) / 4) ** 28, decimals=2)

num("combinatorics_hard", "Сколькими способами можно выбрать 17 человек из 40?", math.comb(40, 17))
num("combinatorics_hard", "Сколько существует перестановок из 15 элементов?", math.factorial(15))
num("combinatorics_hard", "Сколькими способами можно расставить 12 различных книг на полке?", math.factorial(12))

num("units_hard", "Переведи 123,456 мили в километры. Округли до 3 знаков после запятой.",
    F("123.456") * F(1609344, 1000000), decimals=3)
num("units_hard", "Сколько секунд в 9,75 суток?", F("9.75") * 86400)
num("units_hard", "Переведи 451 градус по Фаренгейту в градусы Цельсия. Округли до 2 знаков после запятой.",
    (F(451) - 32) * F(5, 9), decimals=2)

d = dt.date(2024, 2, 29) + dt.timedelta(days=1000)
other("date_hard", "Какая дата будет через 1000 дней после 29.02.2024? Ответь в формате ДД.ММ.ГГГГ.", "date",
      f"{d.day:02d}.{d.month:02d}.{d.year}")
num("date_hard", "Сколько дней между 03.11.1987 и 01.10.2026?", (dt.date(2026, 10, 1) - dt.date(1987, 11, 3)).days)
weekdays = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
other("date_hard", "Какой день недели будет 31 декабря 2099 года?", "weekday", weekdays[dt.date(2099, 12, 31).weekday()])

num("percent_hard", "Сколько будет 17,35% от 48 920?", F("17.35") / 100 * 48920)
num("percent_hard", "Цена 7 890 рублей, скидка 23%. Сколько заплатим?", 7890 * (1 - F(23, 100)))
num("percent_hard", "Сумма с НДС 20% равна 98 760 рублей. Сколько из них НДС? Округли до копеек.",
    F(98760 * 20, 120), decimals=2)

if __name__ == "__main__":
    corpus = base.build()
    corpus["version"] = VERSION
    corpus["note"] = "stress set written after the baseline of jeff-math-corpus/1; not used to tune the helper"
    OUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(corpus["count"], "items", corpus["categories"])
