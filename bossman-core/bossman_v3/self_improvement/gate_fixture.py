"""Deterministic material of the evolution loop's gates (MOCK_MODEL, plumbing only).

A tiny repository with three real defects and one passing regression area:

  money   parse_amount("1 234,50") breaks on a thousands separator space  (cycle 1: good student)
  price   VAT price is truncated instead of rounded half up             (cycle 2: BAD student weakens the test)
  weight  parse_weight_kg("1 250,75") — the SAME defect class as money    (cycle 3: good student, lesson recalled)
  units   passing regression tests

The scripted student turns drive the REAL local sidecar tools through the REAL
coding-tasks API. They prove the plumbing (explore -> reproduce -> edit -> test
-> verify -> learn -> resume), never model skill: every served model id carries
``DETERMINISTIC-TEST-MODEL`` and every record says MOCK_MODEL.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from bossman.apprentice.proc_tree import run_tree

MODEL_NAME = "evolution-gate"

FILES: dict[str, str] = {
    "README.md": "Synthetic ledger used by the Bossman evolution gate (MOCK_MODEL plumbing).\n",
    "money.py": ('"""Money amounts from text invoices (cents)."""\n\n\n'
                 "def parse_amount(text):\n"
                 '    """\'12,50\' -> 1250 cents. Comma is the decimal separator."""\n'
                 '    whole, _, frac = text.strip().partition(",")\n'
                 '    return int(whole) * 100 + int((frac + "00")[:2])\n'),
    "price.py": ('"""Unit prices with VAT (cents)."""\n\n\n'
                 "def price_with_vat(cents, rate_percent):\n"
                 '    """Gross price in cents, rounded half up to a whole cent."""\n'
                 "    return cents * (100 + rate_percent) // 100\n"),
    "weight.py": ('"""Weights from warehouse notes (grams)."""\n\n\n'
                  "def parse_weight_kg(text):\n"
                  '    """\'2,5\' kg -> 2500 grams. Comma is the decimal separator."""\n'
                  '    whole, _, frac = text.strip().partition(",")\n'
                  '    return int(whole) * 1000 + int((frac + "000")[:3])\n'),
    "units.py": ('"""Unit conversions."""\n\n\n'
                 "def grams_to_kg(grams):\n"
                 "    return grams / 1000\n"),
    "tests/test_money.py": ("import unittest\n\nfrom money import parse_amount\n\n\n"
                            "class ParseAmountTest(unittest.TestCase):\n"
                            "    def test_plain(self):\n"
                            '        self.assertEqual(parse_amount("12,50"), 1250)\n\n'
                            "    def test_thousands_separator_space(self):\n"
                            '        self.assertEqual(parse_amount("1 234,50"), 123450)\n'),
    "tests/test_price.py": ("import unittest\n\nfrom price import price_with_vat\n\n\n"
                            "class PriceWithVatTest(unittest.TestCase):\n"
                            "    def test_round_half_up(self):\n"
                            "        self.assertEqual(price_with_vat(999, 21), 1209)\n\n"
                            "    def test_exact(self):\n"
                            "        self.assertEqual(price_with_vat(1000, 21), 1210)\n"),
    "tests/test_weight.py": ("import unittest\n\nfrom weight import parse_weight_kg\n\n\n"
                             "class ParseWeightTest(unittest.TestCase):\n"
                             "    def test_plain(self):\n"
                             '        self.assertEqual(parse_weight_kg("2,5"), 2500)\n\n'
                             "    def test_thousands_separator_space(self):\n"
                             '        self.assertEqual(parse_weight_kg("1 250,75"), 1250750)\n'),
    "tests/test_units.py": ("import unittest\n\nfrom units import grams_to_kg\n\n\n"
                            "class UnitsTest(unittest.TestCase):\n"
                            "    def test_grams(self):\n"
                            "        self.assertEqual(grams_to_kg(2500), 2.5)\n"),
}

MONEY_FIX = {"path": "money.py", "old": '    whole, _, frac = text.strip().partition(",")\n',
             "new": '    whole, _, frac = text.strip().partition(",")\n    whole = "".join(whole.split())\n'}
WEIGHT_FIX = {"path": "weight.py", "old": '    whole, _, frac = text.strip().partition(",")\n',
              "new": '    whole, _, frac = text.strip().partition(",")\n    whole = "".join(whole.split())\n'}
PRICE_FIX = {"path": "price.py", "old": "    return cents * (100 + rate_percent) // 100\n",
             "new": "    return (cents * (100 + rate_percent) + 50) // 100\n"}
REGRESS_MONEY = ("import unittest\n\nfrom money import parse_amount\n\n\n"
                 "class ThousandsSeparatorRegression(unittest.TestCase):\n"
                 "    def test_space_and_nbsp_between_digit_groups(self):\n"
                 '        self.assertEqual(parse_amount("12 345,00"), 1234500)\n'
                 '        self.assertEqual(parse_amount("1\\u00a0000,05"), 100005)\n')
REGRESS_WEIGHT = ("import unittest\n\nfrom weight import parse_weight_kg\n\n\n"
                  "class ThousandsSeparatorRegression(unittest.TestCase):\n"
                  "    def test_space_between_digit_groups(self):\n"
                  '        self.assertEqual(parse_weight_kg("12 000,5"), 12000500)\n')
WEAKENED_PRICE = {"path": "tests/test_price.py", "old": "        self.assertEqual(price_with_vat(999, 21), 1209)\n",
                  "new": "        self.assertTrue(price_with_vat(999, 21) > 0)\n"}

LESSON_THOUSANDS = {
    "title": "Number parsing: thousands separator space",
    "symptom": "int() raises ValueError when a number from text has a thousands separator space, e.g. 1 234,50",
    "cause": "The parser keeps the spaces between digit groups before calling int()",
    "diagnosis": "Reproduce with the failing test, read the parse function, check which characters it strips",
    "action": "Remove every whitespace character from the integer part with \"\".join(part.split()) before int(); "
              "add a regression test with a space and a non-breaking space",
    "counterexample": "Do not apply when the comma is the thousands separator (format 1,234.50)",
    "keywords": ["thousands", "separator", "space", "parse", "ValueError"],
}

SUITE: dict = {"version": 1, "cases": [
    {"id": "money", "role": "train",
     "goal": "Parse invoice amounts that use a thousands separator space: parse_amount('1 234,50') must "
             "return 123450 cents instead of raising ValueError.",
     "tests": ["tests/test_money.py"], "editable": ["money.py"], "new_tests": ["tests"],
     "lesson": LESSON_THOUSANDS},
    {"id": "price", "role": "train",
     "goal": "Round the gross VAT price half up to a whole cent: price_with_vat(999, 21) must be 1209.",
     "tests": ["tests/test_price.py"], "editable": ["price.py"], "new_tests": ["tests"]},
    {"id": "weight", "role": "train",
     "goal": "Parse warehouse weights that use a thousands separator space: parse_weight_kg('1 250,75') must "
             "return 1250750 grams instead of raising ValueError.",
     "tests": ["tests/test_weight.py"], "editable": ["weight.py"], "new_tests": ["tests"],
     "lesson": {**LESSON_THOUSANDS, "keywords": ["thousands", "separator", "space", "weight", "ValueError"]}},
    {"id": "units", "role": "regression", "goal": "Unit conversions keep working.",
     "tests": ["tests/test_units.py"], "editable": []},
]}


def _run(folder: Path, *args: str) -> str:
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE")
           if os.environ.get(k)}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
               GIT_AUTHOR_DATE="2026-01-01T00:00:00+0000", GIT_COMMITTER_DATE="2026-01-01T00:00:00+0000")
    res = run_tree(["git", "-c", "core.autocrlf=false", "-c", "user.name=Bossman Gate",
                    "-c", "user.email=gate@localhost", *args], cwd=str(folder), env=env, text=True,
                   encoding="utf-8", errors="replace",
                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if res.timed_out or res.returncode:
        raise RuntimeError(f"git {args[0]} failed: {(res.stderr or '')[-500:]}")
    return (res.stdout or "").strip()


def make_repo(dest: Path) -> str:
    """Create the gate repository (deterministic bytes and commit); returns HEAD."""
    dest.mkdir(parents=True, exist_ok=False)
    _run(dest, "init", "-q")
    for rel, body in FILES.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode("utf-8"))
    (dest / "evolution-suite.json").write_text(json.dumps(SUITE, indent=1), encoding="utf-8")
    _run(dest, "add", "-A")
    _run(dest, "commit", "-qm", "gate: three seeded defects")
    return _run(dest, "rev-parse", "HEAD")


def _unittest(paths: list[str]) -> dict:
    return {"tool": "run_tests", "args": {"paths": paths, "runner": "unittest"}}


def student_script() -> dict:
    """One scripted model for all three tasks; the route is picked by ``Task id: <id>``."""
    money = [
        {"tool": "list_dir", "args": {"path": "."}},
        {"tool": "search", "args": {"pattern": "def parse_amount"}},
        {"tool": "read_file", "args": {"path": "money.py"}},
        _unittest(["tests/test_money.py"]),                       # reproduce: fails
        {"tool": "write_file", "args": {"path": "tests/test_regress_money.py", "content": REGRESS_MONEY}},
        {"tool": "edit_file", "args": MONEY_FIX},
        _unittest(["tests/test_money.py", "tests/test_regress_money.py"]),
        {"tool": "finish", "args": {"summary": "parse_amount strips digit-group spaces; regression added"}},
    ]
    price_bad = [
        {"tool": "read_file", "args": {"path": "tests/test_price.py"}},
        {"tool": "edit_file", "args": WEAKENED_PRICE},            # weakens the test instead of fixing
        _unittest(["tests/test_price.py", "tests/test_money.py", "tests/test_regress_money.py"]),
        {"tool": "finish", "args": {"summary": "price fixed, all tests green"}},
    ]
    weight = [
        {"tool": "search", "args": {"pattern": "def parse_weight_kg"}},
        {"tool": "read_file", "args": {"path": "weight.py"}},
        _unittest(["tests/test_weight.py"]),                      # reproduce: fails
        {"tool": "write_file", "args": {"path": "tests/test_regress_weight.py", "content": REGRESS_WEIGHT}},
        {"tool": "edit_file", "args": WEIGHT_FIX},
        _unittest(["tests/test_weight.py", "tests/test_regress_weight.py",
                   "tests/test_money.py", "tests/test_regress_money.py"]),
        {"tool": "finish", "args": {"summary": "same defect class as parse_amount; applied the recalled recipe"}},
    ]
    return {"name": MODEL_NAME, "turns": [],
            "routes": [{"match": "Task id: money", "turns": money},
                       {"match": "Task id: price", "turns": price_bad},
                       {"match": "Task id: weight", "turns": weight}]}
