"""HARD-$3 budget: cross-process lock, conservative reconciliation, strict pricing."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bossman.apprentice.errors import BudgetExhausted, FallbackRefused
from bossman.apprentice.fable_direct import DirectApiBudget, estimate_worst_case_usd

CORE = Path(__file__).resolve().parents[1]


_WORKER = """import json, sys
sys.path.insert(0, sys.argv[3])
from bossman.apprentice.errors import BudgetExhausted
from bossman.apprentice.fable_direct import DirectApiBudget
try:
    b = DirectApiBudget(sys.argv[1], total_usd=3.0, mission_id="m1")
    print(b.reserve(float(sys.argv[2])))
except BudgetExhausted:
    print("REFUSED")
"""


def test_cross_process_concurrent_reserve_never_exceeds_cap(tmp_path: Path):
    import subprocess
    import sys
    path = str(tmp_path / "budget.json")
    worker = tmp_path / "worker.py"
    worker.write_text(_WORKER, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(CORE)}
    procs = [subprocess.Popen([sys.executable, str(worker), path, "1.0", str(CORE)],
                              stdout=subprocess.PIPE, text=True, env=env) for _ in range(5)]
    outcomes = [p.communicate(timeout=60)[0].strip().splitlines()[-1] for p in procs]
    granted = [o for o in outcomes if o != "REFUSED" and o.startswith("rsv-")]
    assert len(granted) == 3, outcomes                       # exactly $3.00 of $3.00 granted
    final = DirectApiBudget(path, total_usd=3.0, mission_id="m1")
    assert final.remaining() == 0.0                          # cap holds across processes


def test_restart_reloads_and_reconciling_holds_budget(tmp_path: Path):
    path = tmp_path / "budget.json"
    b1 = DirectApiBudget(path, total_usd=3.0, mission_id="LFZ")
    rid = b1.reserve(2.0, purpose="teacher")
    b1.mark_reconciling(rid)
    b2 = DirectApiBudget(path, total_usd=3.0, mission_id="LFZ")
    assert b2.remaining() == 1.0                             # RECONCILING hold survives restart (conservative)
    with pytest.raises(BudgetExhausted):
        b2.reserve(1.01)
    assert b2.trusted_reconcile(rid, request_id="req_abc", actual_usd=None) == "RELEASED"
    assert b2.remaining() == 3.0                             # only the trusted reconciler freed it
    with pytest.raises(BudgetExhausted):
        DirectApiBudget(path, total_usd=3.0, mission_id="OTHER")


def test_commit_bounds_and_reconciliation_settlement(tmp_path: Path):
    b = DirectApiBudget(tmp_path / "budget.json", total_usd=3.0, mission_id="m")
    rid = b.reserve(0.05)
    with pytest.raises(BudgetExhausted):
        b.commit(rid, 0.06)                                  # actual > reserved forbidden
    b.commit(rid, 0.05, request_id="req_1")
    with pytest.raises(BudgetExhausted):
        b.commit(rid, 0.05)                                  # double commit refused
    assert b.remaining() == 2.95
    rid2 = b.reserve(1.0)
    b.mark_reconciling(rid2)
    with pytest.raises(BudgetExhausted):
        b.trusted_reconcile(rid2, request_id="req_2", actual_usd=1.5)   # settlement above hold forbidden


def test_unknown_model_price_is_refused(tmp_path: Path):
    with pytest.raises(BudgetExhausted):
        estimate_worst_case_usd("skynet-9", 1000, 100)
    from bossman.apprentice.fable_direct import FableDirectClient
    with pytest.raises(BudgetExhausted):
        FableDirectClient(model="skynet-9")


def test_cache_tokens_are_billed_separately(tmp_path: Path):
    """5m cache write is ~1.25x input; read is ~0.1x input — rates must differ."""
    from bossman.apprentice.fable_direct import PRICE_TABLE
    sonnet = PRICE_TABLE["claude-sonnet-4-5"]
    assert sonnet[2] < sonnet[0] < sonnet[3] < sonnet[1]     # read < input < write < output
    assert sonnet[3] > sonnet[0]                             # cache_write premium over base input


# ---------------------------------------------------------------- canonical HARD-$3

_CANONICAL_WORKER = """import sys
sys.path.insert(0, sys.argv[3])
sys.path.insert(0, sys.argv[4])
from pathlib import Path
from bossman_shared import fable_budget
fable_budget.LEDGER_PATH = Path(sys.argv[1])
from bossman_shared.fable_budget import BudgetExhausted, canonical_budget
try:
    print(canonical_budget().reserve(float(sys.argv[2])))
except BudgetExhausted:
    print("REFUSED")
"""


def test_the_canonical_cap_holds_across_processes(tmp_path: Path):
    """Пять параллельных процессов делят ОДИН потолок в 3.00 USD.

    Второй процесс — самый честный способ проверить, что потолок держится не
    на удаче внутрипроцессной блокировки: у каждого свой интерпретатор, общий
    у них только файл.
    """
    import subprocess
    import sys
    root = str(CORE.parent)
    path = str(tmp_path / "fable_hard_cap.json")
    worker = tmp_path / "worker.py"
    worker.write_text(_CANONICAL_WORKER, encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, str(worker), path, "1.0", str(CORE), root],
                              stdout=subprocess.PIPE, text=True) for _ in range(5)]
    outcomes = [p.communicate(timeout=60)[0].strip().splitlines()[-1] for p in procs]
    granted = [o for o in outcomes if o.startswith("rsv-")]
    assert len(granted) == 3, outcomes           # ровно 3.00 из 3.00, ни центом больше


def test_a_direct_call_is_refused_before_the_network_when_the_cap_is_full(monkeypatch, tmp_path):
    """Потолок выбран — HTTP-запрос не отправляется вовсе.

    Проверяется счётчик обращений к транспорту: «ноль» значит, что платного
    вызова не было, а не что он был и вернул отказ.
    """
    from bossman_shared.fable_budget import BudgetExhausted, canonical_budget
    from bossman.apprentice import fable_direct as fd

    monkeypatch.setenv("ANTHROPIC_API_KEY", "не-ключ-для-теста")
    sent = []
    monkeypatch.setattr(fd.httpx, "post",
                        lambda *a, **k: sent.append(1) or (_ for _ in ()).throw(
                            AssertionError("платный запрос всё-таки ушёл")))

    b = canonical_budget()
    rid = b.reserve(3.0, purpose="уже занято")
    b.commit(rid, 3.0, request_id="req-прошлый")

    client = fd.FableDirectClient(model="claude-sonnet-4-5")
    with pytest.raises(BudgetExhausted):
        client.run({"ROLE": "teacher", "PROBLEM_ID": "P1"})
    assert sent == [], "запрос ушёл в сеть при выбранном потолке"
    assert client.calls == 0


def test_the_direct_client_reserves_on_the_canonical_cap_without_a_mission_budget(monkeypatch):
    """Без бюджета миссии клиент всё равно резервирует на общем потолке:
    «не передали бюджет» не означает «трать сколько хочешь»."""
    from bossman_shared.fable_budget import canonical_budget
    from bossman.apprentice import fable_direct as fd

    monkeypatch.setenv("ANTHROPIC_API_KEY", "не-ключ-для-теста")

    class _Boom(Exception):
        pass

    def explode(*a, **k):
        raise _Boom("обрыв связи")

    monkeypatch.setattr(fd.httpx, "post", explode)
    client = fd.FableDirectClient(model="claude-sonnet-4-5", max_output_tokens=1000)
    with pytest.raises(FallbackRefused):
        client.run({"ROLE": "teacher"})

    b = canonical_budget()
    assert b.remaining() < 3.0, "вызов не занял ни цента общего потолка"
    # обрыв — исход неизвестен, деньги остаются висеть до разбора
    assert [r["status"] for r in b._records] == ["RECONCILING"]
