"""Учебные бизнес-упражнения для сессий обучения (Codex / Claude / Bossman).

Только синтетика. Никаких реальных платежей, писем, кошельков и аккаунтов:
модуль не делает сетевых вызовов вообще, все компании и адреса вымышлены
(домены `.test`, RFC 2606).

Устройство «посаженной правды»:

* задание (входные CSV + формулировка) отдаётся исполнителю;
* правильный ответ ПОСАЖЕН руками в константы `_PLANTED` и при материализации
  пишется в каталог ключей ВНЕ корней агента (`keys_dir()`): не в репозиторий,
  не в каталог данных Bossman. Если каталог ключей оказался внутри
  запрещённого корня — материализация отказывает;
* независимый чекер — `bcc.business_exercise_checker` — читает ТОЛЬКО файл
  ключа и ответ, без доступа к этому модулю, и выдаёт квитанцию
  с sha256. В квитанции нет ожидаемых значений — только имена полей,
  которые не сошлись: иначе ответ можно было бы списать из отказа.

Тест `test_codex_learning_readiness.py` пересчитывает правду третьим,
независимым способом из входных CSV и сверяет с посаженной.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

SIMULATED = True
NOT_DONE_RU = ("не платили", "не писали лидам", "не входили в аккаунты", "не трогали кошельки")

# ------------------------------------------------------------------ задания

_INVOICES_CSV = """invoice_id,customer,amount_czk,due_date
INV-1001,Alfa Stavby s.r.o.,12500.00,2026-10-05
INV-1002,Beta Pekarna a.s.,8400.00,2026-10-07
INV-1003,Gama Servis s.r.o.,15999.90,2026-10-09
INV-1004,Delta Logistika s.r.o.,22000.00,2026-10-10
INV-1005,Epsilon Kava s.r.o.,3150.00,2026-10-12
INV-1006,Zeta Tisk s.r.o.,7420.50,2026-10-14
INV-1007,Eta Opravy s.r.o.,11800.00,2026-10-15
INV-1008,Theta Media s.r.o.,5600.00,2026-10-16
"""

_PAYMENTS_CSV = """payment_id,reference,amount_czk,date
PAY-501,INV-1001,12500.00,2026-10-04
PAY-502,INV-1002,8400.00,2026-10-06
PAY-503,INV-1004,20749.50,2026-10-10
PAY-504,INV-1005,3150.00,2026-10-11
PAY-505,INV-1002,8400.00,2026-10-12
PAY-506,INV-1007,11800.00,2026-10-15
PAY-507,INV-1008,5600.00,2026-10-16
"""

_OFFERS_CSV = """offer_id,supplier,amount,currency,fee_percent
OFF-A,Supplier A (crypto invoice),1000,USDT,3.0
OFF-B,Supplier B (EU invoice),1020,EUR,1.5
OFF-C,Supplier C (local invoice),25400,CZK,0.5
"""

_RATES_CSV = """pair,rate
USDT/USD,1.00
USD/CZK,25.00
EUR/CZK,24.40
"""

_LEADS_CSV = """lead_id,company,country,employees,budget_czk,email,consent
L-01,Kappa Studio,CZ,4,80000,info@kappa-studio.test,yes
L-02,Lambda Kovo,CZ,45,120000,obchod@lambda-kovo.test,yes
L-03,Mu Retail,DE,30,90000,sales@mu-retail.test,yes
L-04,Nu Agro,SK,12,30000,office@nu-agro.test,yes
L-05,Xi Software,SK,18,65000,hello@xi-software.test,yes
L-06,Omikron Bau,CZ,60,200000,omikron.bau@freemail.test,yes
L-07,Pi Clinic,CZ,25,75000,recepce@pi-clinic.test,no
L-08,Rho Foods,PL,15,55000,biuro@rho-foods.test,yes
L-09,Sigma Energy,CZ,10,50000,energie@sigma-energy.test,yes
L-10,Tau Textil,CZ,9,150000,textil@tau-textil.test,yes
"""

_VAT_LINES_CSV = """invoice_id,item,qty,unit_price_net_czk,vat_percent
F-01,Konzultace,3,1500.00,21
F-01,Licence,1,2499.99,21
F-02,Kniha,4,349.00,12
F-02,Doprava,1,99.00,21
F-03,Export sluzba,2,10000.00,0
F-03,Material,5,123.45,21
"""

EXERCISES: dict[str, dict[str, Any]] = {
    "invoice_reconciliation_v1": {
        "title_ru": "Сверка счетов и платежей",
        "skill": "business",
        "inputs": {"invoices.csv": _INVOICES_CSV, "payments.csv": _PAYMENTS_CSV},
        "task_ru": (
            "Сверьте invoices.csv с payments.csv (сопоставление по reference = invoice_id). "
            "Найдите: unpaid — счета без единого платежа; underpaid — счета, оплаченные "
            "меньше суммы счёта, с недоплатой в CZK строкой '0.00'; duplicate_payments — "
            "id платежей, повторно оплативших уже полностью оплаченный счёт."),
        "answer_schema": {"unpaid": ["INV-…"], "underpaid": {"INV-…": "0.00"},
                          "duplicate_payments": ["PAY-…"]},
    },
    "fx_price_comparison_v1": {
        "title_ru": "Сравнение цен в разных валютах (Decimal)",
        "skill": "business",
        "inputs": {"offers.csv": _OFFERS_CSV, "rates.csv": _RATES_CSV},
        "task_ru": (
            "Переведите каждое предложение в CZK с учётом комиссии: "
            "amount × курс в CZK × (1 + fee_percent/100), округление ROUND_HALF_UP до 0.01. "
            "USDT считать по USDT/USD, затем USD/CZK. Пример: 1000 USDT × 25 CZK/USD × 1.03 = "
            "25750.00 CZK. Верните totals_czk {offer_id: '0.00'} и cheapest — id самого дешёвого."),
        "answer_schema": {"totals_czk": {"OFF-…": "0.00"}, "cheapest": "OFF-…"},
    },
    "lead_qualification_v1": {
        "title_ru": "Квалификация лидов из CSV (без рассылки)",
        "skill": "business",
        "inputs": {"leads.csv": _LEADS_CSV},
        "task_ru": (
            "Лид квалифицирован, если: country ∈ {CZ, SK}; employees ≥ 10; budget_czk ≥ 50000; "
            "consent = yes; домен e-mail не freemail.test. Верните qualified — список lead_id. "
            "Никому не писать: это упражнение на чтение таблицы."),
        "answer_schema": {"qualified": ["L-…"]},
    },
    "vat_invoice_totals_v1": {
        "title_ru": "НДС по счетам (CZ 21/12/0 %)",
        "skill": "business",
        "inputs": {"lines.csv": _VAT_LINES_CSV},
        "task_ru": (
            "Для каждого счёта: net = Σ qty × unit_price_net; НДС считается по каждой ставке "
            "от суммы net этой ставки и округляется ROUND_HALF_UP до 0.01; gross = net + vat. "
            "Верните totals {invoice_id: {net, vat, gross}} строками '0.00'."),
        "answer_schema": {"totals": {"F-…": {"net": "0.00", "vat": "0.00", "gross": "0.00"}}},
    },
}

#: ПОСАЖЕННАЯ правда — записана руками, не вычислена этим модулем.
_PLANTED: dict[str, dict[str, Any]] = {
    "invoice_reconciliation_v1": {
        "unpaid": ["INV-1003", "INV-1006"],
        "underpaid": {"INV-1004": "1250.50"},
        "duplicate_payments": ["PAY-505"],
    },
    "fx_price_comparison_v1": {
        "totals_czk": {"OFF-A": "25750.00", "OFF-B": "25261.32", "OFF-C": "25527.00"},
        "cheapest": "OFF-B",
    },
    "lead_qualification_v1": {"qualified": ["L-02", "L-05", "L-09"]},
    "vat_invoice_totals_v1": {
        "totals": {
            "F-01": {"net": "6999.99", "vat": "1470.00", "gross": "8469.99"},
            "F-02": {"net": "1495.00", "vat": "188.31", "gross": "1683.31"},
            "F-03": {"net": "20617.25", "vat": "129.62", "gross": "20746.87"},
        },
    },
}


# ------------------------------------------------------------------ ключи вне корней агента

class KeysLocationError(RuntimeError):
    """Каталог ключей внутри корня, доступного агенту — правда утекла бы в задание."""


def _default_keys_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "Bossman" / "exercise-keys"
    return Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "bossman" / "exercise-keys"


def keys_dir() -> Path:
    raw = os.environ.get("BCC_EXERCISE_KEYS_DIR", "").strip()
    return (Path(raw).expanduser() if raw else _default_keys_dir()).resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def assert_keys_outside(forbidden_roots: list[Path]) -> Path:
    target = keys_dir()
    for root in forbidden_roots:
        if root and _inside(target, Path(root)):
            raise KeysLocationError(
                f"каталог ключей {target} внутри корня агента {root}: правда была бы видна "
                f"исполнителю. Задайте BCC_EXERCISE_KEYS_DIR вне репозитория и данных Bossman")
    return target


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def materialize_key(exercise_id: str, forbidden_roots: list[Path]) -> dict:
    """Записать ключ упражнения вне корней агента (идемпотентно). Ключ не возвращается."""
    if exercise_id not in EXERCISES:
        raise KeyError(exercise_id)
    target = assert_keys_outside(forbidden_roots)
    target.mkdir(parents=True, exist_ok=True)
    body = {"exercise_id": exercise_id, "inputs_sha256": _sha(EXERCISES[exercise_id]["inputs"]),
            "truth": _PLANTED[exercise_id], "simulated": True}
    path = target / f"{exercise_id}.json"
    data = json.dumps(body, sort_keys=True, ensure_ascii=False, indent=1)
    if not path.is_file() or path.read_text(encoding="utf-8") != data:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)
    return {"exercise_id": exercise_id, "key_sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
            "keys_outside_agent_roots": True}


def public_task(exercise_id: str) -> dict:
    """Задание для исполнителя — БЕЗ правды."""
    spec = EXERCISES[exercise_id]
    return {"exercise_id": exercise_id, "title_ru": spec["title_ru"], "skill": spec["skill"],
            "task_ru": spec["task_ru"], "inputs": dict(spec["inputs"]),
            "inputs_sha256": _sha(spec["inputs"]), "answer_schema": spec["answer_schema"],
            "simulated": True, "not_done_ru": list(NOT_DONE_RU)}


def catalog() -> list[dict]:
    return [{"exercise_id": k, "title_ru": v["title_ru"], "skill": v["skill"]}
            for k, v in EXERCISES.items()]


__all__ = ["EXERCISES", "SIMULATED", "KeysLocationError", "keys_dir", "assert_keys_outside",
           "materialize_key", "public_task", "catalog"]
