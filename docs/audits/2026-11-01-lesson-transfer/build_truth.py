"""Builds the lesson-transfer task set (prompts -> tasks.json) and the GROUND TRUTH (-> truth.json), both in THIS dir.

Lives OUTSIDE the repo and the temp Bossman data dir while the runs happen; published with the receipts afterwards.
Truth = Decimal arithmetic; scorer.py never imports this file. A Fraction cross-check is asserted here.
"""
import hashlib
import json
import math
import sys
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal as D
from fractions import Fraction
from pathlib import Path

C = D("0.01")
FMT = "\n\nВ последней строке ответа напиши строго: ОТВЕТ: <число> CZK"
FMT_PLAIN = "\n\nВ последней строке ответа напиши строго: ОТВЕТ: <ответ>"


def desk(side, amount, mid):
    raw = D(amount) * D(mid)
    return (raw * D("1.03")).quantize(C, ROUND_UP) if side == "BUY" else (raw * D("0.97")).quantize(C, ROUND_DOWN)


def frac_desk(side, amount, mid):
    raw = Fraction(amount) * Fraction(mid) * 100
    cents = math.ceil(raw * Fraction(103, 100)) if side == "BUY" else math.floor(raw * Fraction(97, 100))
    return D(cents) / 100


def halfup(side, amount, mid):
    raw = D(amount) * D(mid)
    k = D("1.03") if side == "BUY" else D("0.97")
    return (raw * k).quantize(C, ROUND_HALF_UP)


BUY_T = [
    "Клиент обменного стола SwapMe хочет купить {a} {c} за кроны. Средний рыночный курс (mid): 1 {c} = {m} CZK. "
    "Сколько CZK клиент должен заплатить по правилам стола SwapMe?",
    "SwapMe: клиенту нужно получить ровно {a} {c}, платит он в CZK. Mid-курс 1 {c} = {m} CZK. "
    "Какую сумму в CZK назовёт стол SwapMe по своим правилам котировки?",
]
SELL_T = [
    "Клиент обменного стола SwapMe продаёт {a} {c} и получает кроны. Mid: 1 {c} = {m} CZK. "
    "Сколько CZK стол SwapMe выплатит клиенту по своим правилам?",
    "SwapMe: клиент приносит {a} {c} и хочет CZK. Средний курс 1 {c} = {m} CZK. "
    "Сколько крон он получит по правилам котировки стола SwapMe?",
]

HELD = [  # side, amount, ccy, mid
    ("BUY", "1200", "USDT", "23.947"), ("SELL", "850", "EUR", "24.318"), ("BUY", "640", "EUR", "24.385"),
    ("SELL", "2300", "USDT", "23.873"), ("BUY", "3150", "USD", "23.612"), ("SELL", "475", "USD", "23.509"),
    ("BUY", "980", "EUR", "24.457"), ("SELL", "1640", "EUR", "24.296"), ("BUY", "2750", "USDT", "23.981"),
    ("SELL", "720", "USDT", "24.033"), ("BUY", "515", "USD", "23.477"), ("SELL", "3900", "USD", "23.555"),
    ("BUY", "1875", "EUR", "24.409"), ("SELL", "1125", "EUR", "24.367"), ("BUY", "430", "USDT", "23.903"),
    ("SELL", "5200", "USDT", "23.919"), ("BUY", "2025", "USD", "23.533"), ("SELL", "690", "EUR", "24.471"),
    ("BUY", "1500", "EUR", "24.400"), ("SELL", "2000", "USD", "23.500"),
]


def build():
    tasks, truth = [], {}
    for i, (side, a, c, m) in enumerate(HELD, 1):
        tid = f"H{i:02d}"
        tmpl = (BUY_T if side == "BUY" else SELL_T)[i % 2]
        tasks.append({"id": tid, "set": "heldout", "kind": "money",
                      "prompt": tmpl.format(a=a, c=c, m=m) + FMT})
        v = desk(side, a, m)
        assert v == frac_desk(side, a, m), tid
        truth[tid] = {"value": str(v), "kind": "money", "params": {"side": side, "amount": a, "ccy": c, "mid": m},
                      "diag": {"half_up_would_be": str(halfup(side, a, m)),
                               "no_margin_would_be": str((D(a) * D(m)).quantize(C, ROUND_HALF_UP))}}

    # INDEPENDENT: same desk rule, different framing / composite; no "SwapMe", no "стол".
    ind = [
        ("I01", "Менеджер нашего обменника (вкладка «Обмен» в Bossman) готовит котировку: клиент приносит "
                "640 USD наличными и хочет кроны. Mid USD/CZK = 23.187. Сколько крон выдать клиенту по "
                "стандартной политике нашего обменника?", desk("SELL", "640", "23.187")),
        ("I02", "Покупатель хочет получить ровно 2500 EUR; средний курс 24.351 CZK за 1 EUR. Сколько крон "
                "взять с него по стандартной политике нашего обменного сервиса?", desk("BUY", "2500", "24.351")),
        ("I03", "Два клиента обменного сервиса в один день: A покупает 300 USDT, B продаёт 300 USDT, mid в обоих "
                "случаях 23.951 CZK за 1 USDT. На сколько CZK сумма, которую заплатит A, больше суммы, которую "
                "получит B? Считай каждую котировку по стандартной политике сервиса.",
         desk("BUY", "300", "23.951") - desk("SELL", "300", "23.951")),
        ("I04", "Для отчёта: клиент купил у нашего обменника 1500 EUR при mid 24.402 CZK/EUR. На сколько CZK "
                "котировка по стандартной политике обменника больше, чем 1500 × 24.402?",
         desk("BUY", "1500", "24.402") - D("1500") * D("24.402")),
        ("I05", "Клиентка продаёт обменнику 1 USDT по mid 23.987 CZK. Сколько крон она получит по стандартной "
                "политике нашего обменного сервиса?", desk("SELL", "1", "23.987")),
        ("I06", "Вкладка «Обмен»: клиент хочет купить 12.5 EUR, mid 24.433 CZK/EUR. Сумма к оплате в кронах по "
                "стандартной политике?", desk("BUY", "12.5", "24.433")),
    ]
    for tid, prompt, v in ind:
        tasks.append({"id": tid, "set": "independent", "kind": "money", "prompt": prompt + FMT})
        truth[tid] = {"value": str(v), "kind": "money"}

    # CONTROL: near-miss money with an EXPLICIT different rule (lesson must not override), then unrelated.
    ctrl = [
        ("C01", "Банк конвертирует 800 EUR клиента в CZK по курсу 24.37 с комиссией банка 1.5 % (комиссия уменьшает "
                "сумму). Округление ROUND_HALF_UP до 0.01. Сколько CZK получит клиент?",
         (D(800) * D("24.37") * D("0.985")).quantize(C, ROUND_HALF_UP), "money"),
        ("C02", "Счёт поставщика: 1250 USD по курсу 23.4163 CZK/USD, без комиссии и наценки. Округли ROUND_HALF_UP до "
                "0.01. Сумма в CZK?", (D(1250) * D("23.4163")).quantize(C, ROUND_HALF_UP), "money"),
        ("C03", "Сумма без НДС 4380.50 CZK, ставка НДС 21 %. Округление ROUND_HALF_UP до 0.01. Сумма с НДС в CZK?",
         (D("4380.50") * D("1.21")).quantize(C, ROUND_HALF_UP), "money"),
        ("C04", "Курсовая разница: 2000 EUR × (24.512 − 24.388) CZK/EUR. Сколько это CZK?",
         (D(2000) * (D("24.512") - D("24.388"))).quantize(C), "money"),
        ("C05", "Сколько дней между 2026-03-14 и 2026-11-01 (разность дат в днях)?", D(232), "number"),
        ("C06", "Сколько слов во фразе «быстрая бурая лиса прыгает через ленивую собаку»?", D(7), "number"),
        ("C07", "Отсортируй числа по возрастанию и назови третье по счёту: 42, 7, 19, 88, 3, 56.", D(19), "number"),
        ("C08", "Сколько будет 17 × 23?", D(391), "number"),
        ("C09", "Сколько минут в 3.5 часах?", D(210), "number"),
        ("C10", "Сколько будет 15 % от 2400?", D(360), "number"),
        ("C11", "Какой остаток от деления 1000 на 7?", D(6), "number"),
        ("C12", "Если 2026-11-01 — воскресенье, какой день недели будет 2026-11-08? Ответь одним словом.",
         "воскресенье", "text"),
    ]
    for tid, prompt, v, kind in ctrl:
        fmt = FMT if kind == "money" else FMT_PLAIN
        tasks.append({"id": tid, "set": "control", "kind": kind, "prompt": prompt + fmt})
        truth[tid] = {"value": str(v), "kind": kind}
    return tasks, truth


if __name__ == "__main__":
    tasks, truth = build()
    keys = Path(__file__).resolve().parent
    text = json.dumps({"truth": truth}, ensure_ascii=False, indent=1, sort_keys=True)
    (keys / "truth.json").write_text(text, encoding="utf-8")
    (keys / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    print("truth_sha256", hashlib.sha256(text.encode("utf-8")).hexdigest())
    disc = [t for t, v in truth.items() if t.startswith("H") and v["diag"]["half_up_would_be"] != v["value"]]
    print("heldout where half-up != desk:", len(disc), disc)
    if "--show" in sys.argv:
        for t, v in truth.items():
            print(t, v.get("value"), v.get("diag", ""))
