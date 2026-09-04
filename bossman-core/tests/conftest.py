"""Shared test guards for bossman-core.

The only thing here is a safety belt around money: the HARD-$3 Fable ledger is
a single durable file on the real machine, so a test that reserved against it
for real would eat the owner's actual budget and keep it eaten. Every test gets
its own ledger file instead. The redirect is an in-process attribute patch, not
a setting: there is deliberately no environment variable that moves the ledger,
because a relocatable ledger is a raisable cap.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path and (_ROOT / "bossman_shared").is_dir():
    sys.path.insert(0, str(_ROOT))

from bossman_shared import fable_budget  # noqa: E402


@pytest.fixture(autouse=True)
def _fable_ledger_off_the_real_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(fable_budget, "LEDGER_PATH",
                        tmp_path / "fable_hard_cap.json", raising=True)
