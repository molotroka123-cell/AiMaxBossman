"""Regression tests for all 7 Independent Red-Team P0 findings (A.1 .. A.7 / RT20-01 .. RT20-07).

Every test runs in an isolated TemporaryDirectory with synthetic fixtures only (Rule 21.10).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "command-center", ROOT / "apps" / "war-terminal"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bossman_shared.exchange_assistant.agent import Assistant, is_write  # noqa: E402
from bossman_shared.exchange_assistant.quote import QuoteError, build_quote  # noqa: E402
from bossman_shared.exchange_assistant.rates import Rate, RateBook, RateError  # noqa: E402
from bossman_shared.exchange_assistant.store import Store, StoreError  # noqa: E402


# ---------------------------------------------------------------------------
# A.1 (RT20-01): STOP blocks quote/deal writes on all paths + concurrent race
# ---------------------------------------------------------------------------
def test_a1_stop_blocks_quote_and_deal_writes_and_concurrent_race(tmp_path: Path) -> None:
    now = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
    stop_file = tmp_path / "STOP"
    store = Store(tmp_path / "assistant.db", stop_file=stop_file)
    try:
        store.set_manual_rate("USDCZK", Decimal("25.00"), "synthetic-desk", now, "owner")
        store.set_manual_rate("USDTUSD", Decimal("1.000"), "synthetic-desk", now, "owner")
        assistant = Assistant(store, fx_db=tmp_path / "absent.db", clock=lambda: now, stop_file=stop_file)

        # 1. Both purchase ("купить") and sale ("в") commands are classified as writes
        cmd_buy = "купить 1000 usdt за czk при 3%"
        cmd_sell = "1000 usdt в czk при 3%"
        assert is_write(cmd_buy) is True
        assert is_write(cmd_sell) is True

        # 2. Activate STOP and verify zero quotes/deals written via Assistant.handle
        stop_file.write_text("stop", encoding="utf-8")
        q_before = store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        reply_buy = assistant.handle(cmd_buy, "owner")
        reply_sell = assistant.handle(cmd_sell, "owner")
        q_after = store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        assert q_before == 0 and q_after == 0
        assert "СТОП" in reply_buy and "Котировка" not in reply_buy
        assert "СТОП" in reply_sell and "Котировка" not in reply_sell

        # 3. Direct Store.save_quote is also blocked under STOP inside @_locked
        q_obj = build_quote(
            RateBook([
                Rate("USDCZK", Decimal("25.00"), "synthetic", now, "manual", now + timedelta(hours=12)),
                Rate("USDTUSD", Decimal("1.000"), "synthetic", now, "manual", now + timedelta(hours=12)),
            ]),
            give_ccy="CZK",
            get_ccy="USDT",
            get_amount=Decimal("1000"),
            margin=Decimal("0.03"),
            require_explicit_margin=True,
            client_buys_get=True,
            now=now,
        )
        with pytest.raises(StoreError, match="СТОП"):
            store.save_quote(q_obj)

        # 4. Concurrent race: STOP written on a parallel thread while save_quote contends on store.lock
        stop_file.unlink()
        barrier = threading.Barrier(2)
        race_errors: list[Exception] = []

        def hold_lock_and_trigger_stop() -> None:
            with store.lock:
                barrier.wait(timeout=5)
                stop_file.write_text("stop-during-lock", encoding="utf-8")

        t = threading.Thread(target=hold_lock_and_trigger_stop)
        t.start()
        barrier.wait(timeout=5)
        try:
            store.save_quote(q_obj)
        except StoreError as exc:
            race_errors.append(exc)
        t.join(timeout=5)

        assert len(race_errors) == 1
        assert store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# A.2 (RT20-02): No silent 3% default margin on any user path (purchase/sale)
# ---------------------------------------------------------------------------
def test_a2_no_silent_3_percent_margin_on_purchase_or_sale(tmp_path: Path) -> None:
    now = datetime(2026, 10, 23, 8, 5, tzinfo=timezone.utc)
    store = Store(tmp_path / "assistant.db")
    try:
        store.set_manual_rate("USDCZK", Decimal("25.00"), "synthetic-desk", now, "owner")
        store.set_manual_rate("USDTUSD", Decimal("1.000"), "synthetic-desk", now, "owner")
        assistant = Assistant(store, fx_db=tmp_path / "absent.db", clock=lambda: now, stop_file=tmp_path / "STOP")

        for phrase in (
            "купить 1000 usdt за czk",
            "клиент покупает 1000 usdt за czk",
            "получить 1000 usdt за czk",
            "1000 usdt в czk",
            "посчитай 500 eur в czk",
        ):
            ans = assistant.handle(phrase, "owner")
            assert "наценка 3%" not in ans.lower(), phrase
            assert "Q-" not in ans, phrase
            assert "укажите явную ставку" in ans.lower() or "процент комиссии" in ans.lower(), (phrase, ans)

        assert store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0

        # Once explicit margin is provided, quote is built with manager_margin_decision == 'explicit'
        ans_explicit = assistant.handle("купить 1000 usdt за czk при 3%", "owner")
        assert ans_explicit.startswith("Котировка Q-")
        assert "наценка 3%" in ans_explicit
        assert store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    finally:
        store.close()


# ---------------------------------------------------------------------------
# A.3 (RT20-03): Stale or older manual/official rate is never selected
# ---------------------------------------------------------------------------
def test_a3_stale_or_expired_rate_is_never_selected(tmp_path: Path) -> None:
    now = datetime(2026, 10, 23, 8, 10, tzinfo=timezone.utc)
    newer = Rate("USDCZK", Decimal("25.00"), "synthetic-new", now, "manual", now + timedelta(hours=12))
    older = Rate("USDCZK", Decimal("20.00"), "synthetic-old", now - timedelta(hours=1), "manual", now + timedelta(hours=12))

    # Insertion order 1: newer then older
    b1 = RateBook([newer])
    b1.put(older)
    assert b1.get("USDCZK", now).value == Decimal("25.00")

    # Insertion order 2: older then newer
    b2 = RateBook([older])
    b2.put(newer)
    assert b2.get("USDCZK", now).value == Decimal("25.00")

    # Expired manual rate is rejected
    expired_manual = Rate("USDTUSD", Decimal("1.000"), "synthetic", now - timedelta(hours=13), "manual", now - timedelta(minutes=1))
    b_exp = RateBook([expired_manual])
    with pytest.raises(RateError, match="просрочен"):
        b_exp.get("USDTUSD", now)

    # Stale official rate (>96h) is rejected
    stale_official = Rate("USDCZK", Decimal("24.50"), "ЧНБ", now - timedelta(hours=100), "official")
    b_stale = RateBook([stale_official])
    with pytest.raises(RateError, match="устарел"):
        b_stale.get("USDCZK", now)


# ---------------------------------------------------------------------------
# A.4 (RT20-05): MissionReceipt cannot be postcondition_verified=True without
# an observable postcondition or verification command with exit_code == 0
# ---------------------------------------------------------------------------
def test_a4_mission_receipt_requires_observable_postcondition_and_zero_exit() -> None:
    from bcc.features import coding_tasks as ct

    diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n+# change\n"
    base_rec = {
        "id": "a4test000001",
        "synthetic": True,
        "status": "completed",
        "outcome": "COMPLETED",
        "model": "qwen3-coder:30b",
        "source_repo": str(ROOT),
        "changed_files": ["f.py"],
        "diff": diff,
        "evidence_digest": "sha256:" + hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "candidate_files": {"f.py": {"deleted": False}},
        "verify_tests": [],
        "evidence": {"head_before": "abc1234", "head_after": "abc1234"},
        "sandbox_cleanup": {"removed": True},
    }

    # 1. Empty verify_cmd and no observable_postcondition -> postcondition_verified MUST be False
    r_empty = ct.mission_receipt(base_rec)
    assert r_empty["postcondition_verified"] is False

    # 2. Even if postcondition_check claims passed=True and exit_code=0, without cmd or observable_postcondition -> False
    r_unobservable = ct.mission_receipt({
        **base_rec,
        "postcondition_check": {"passed": True, "exit_code": 0},
    })
    assert r_unobservable["postcondition_verified"] is False

    # 3. Boolean False or float 0.0 or string "0" exit_code -> False
    for fake_zero in (False, 0.0, "0", None, 1):
        r_fake = ct.mission_receipt({
            **base_rec,
            "verify_tests": ["tests/test_exchange_assistant.py"],
            "verification": {"ran": True, "passed": True, "exit_code": fake_zero},
        })
        assert r_fake["postcondition_verified"] is False, fake_zero

    # 4. Non-empty verify_cmd + ran=True + passed=True + int exit_code == 0 -> True
    r_valid = ct.mission_receipt({
        **base_rec,
        "verify_tests": ["tests/test_exchange_assistant.py"],
        "verification": {"ran": True, "passed": True, "exit_code": 0},
    })
    assert r_valid["postcondition_verified"] is True
    assert r_valid["verify_exit_code"] == 0


# ---------------------------------------------------------------------------
# A.5 (RT20-06): Historical Telegram STOP separated from fresh live test
# ---------------------------------------------------------------------------
def test_a5_evidence_separation_synthetic_historical_and_current_live(tmp_path: Path) -> None:
    from tools import verify_tier5_argon_e2e as v

    synth = v.run_synthetic_g1_contracts(tmp_path)
    assert synth["evidence_kind"] == "SYNTHETIC"
    assert synth["status"] == "PASS"

    hist = v.inspect_historical_live_telegram_stop(
        comp_db=tmp_path / "absent.sqlite3",
        audit_path=tmp_path / "absent.jsonl",
    )
    assert hist["evidence_kind"] == "HISTORICAL_LIVE"
    assert hist["status"] == "NOT_RUN"

    # Also verify repro_synthetic.py outputs the fixed values
    proc = subprocess.run(
        [sys.executable, str(ROOT / "docs/audits/2026-10-20-independent-redteam/repro_synthetic.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    out = proc.stdout
    assert "STOP_WRITE True quotes_before 0 quotes_after 0 answered_quote False" in out
    assert "NO_MARGIN_AUTO_3_PERCENT False" in out
    assert "STALE_RATE_SELECTED 25" in out


# ---------------------------------------------------------------------------
# A.6 & A.7: War Terminal UTC deadline expiry on read + REVIEW_REQUIRED
# military/dual-use tenders excluded from sales pitch recommendations
# ---------------------------------------------------------------------------
def test_a6_and_a7_war_terminal_expired_and_review_required_tenders(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from warterminal import contacts as C, rules
    from warterminal.api import create_app

    ref_now = datetime(2026, 10, 23, 14, 0, 0, tzinfo=timezone.utc)
    tokens = {"viewer": "v-tok", "analyst": "a-tok", "compliance": "c-tok", "owner": "o-tok"}
    app = create_app(data_dir=tmp_path / "wt", live=False, tokens=tokens, today=ref_now, bossman=False)
    client = TestClient(app)
    store = app.state.store

    def put_tender(notice_id: str, title: str, cpv: list[str], deadline: str, status: str = "open") -> None:
        store.upsert_tender({
            "key": f"EU:{notice_id}",
            "id": notice_id,
            "official_notice_id": notice_id,
            "source_id": "ted",
            "source": "ted",
            "source_url": f"https://ted.europa.eu/en/notice/-/detail/{notice_id}",
            "retrieved_at": "2026-10-23T10:00:00Z",
            "snapshot_sha256": "a" * 64,
            "demo": False,
            "title": title,
            "description": title,
            "buyer": "Ministry of Interior",
            "country": "CZ",
            "jurisdiction": "EU",
            "category": "supplies",
            "cpv": cpv,
            "status": status,
            "deadline": deadline,
            "estimated_value": 150000.0,
            "currency": "EUR",
        })

    # 1. Tender stored as status='open' in DB, but its UTC deadline expired 2 hours ago on the SAME calendar day!
    put_tender("EXP-SAME-DAY", "Field generators and power cables", ["31120000"], "2026-10-23T12:00:00Z", status="open")
    # 2. Tender stored as status='open' in DB with future deadline, civilian CPV (no military/dual-use)
    put_tender("OPEN-CIVILIAN", "Field generators and power cables lot 2", ["31120000"], "2026-11-15T12:00:00Z", status="open")
    # 3. Tender stored as status='open' in DB with future deadline, military/dual-use CPV (REVIEW_REQUIRED)
    put_tender("OPEN-MILITARY", "Military tactical radar system", ["35722000"], "2026-11-20T12:00:00Z", status="open")

    # A.6 check 1: rules.is_deadline_expired & rules.effective_status
    assert rules.is_deadline_expired("2026-10-23T12:00:00Z", ref_now) is True
    assert rules.effective_status({"status": "open", "deadline": "2026-10-23T12:00:00Z"}, ref_now) == "closed"

    # A.6 check 2: GET /api/v1/tenders?status=open must NOT return the expired tender
    r_open = client.get("/api/v1/tenders?status=open", headers={"Authorization": "Bearer v-tok"})
    assert r_open.status_code == 200
    open_keys = {t["key"] for t in r_open.json()["items"]}
    assert "EU:EXP-SAME-DAY" not in open_keys
    assert "EU:OPEN-CIVILIAN" in open_keys

    # A.6 check 3: GET /api/v1/tenders/EU:EXP-SAME-DAY shows effective status 'closed' and BLOCKED deadline gate
    r_det = client.get("/api/v1/tenders/EU:EXP-SAME-DAY", headers={"Authorization": "Bearer v-tok"})
    assert r_det.status_code == 200
    det = r_det.json()
    assert det["tender"]["status"] == "closed"
    deadline_gate = next(g for g in det["assessment"]["gates"] if g["id"] == "deadline")
    assert deadline_gate["status"] == "BLOCKED"

    # Create a contact matching both civilian ("3112") and military ("3572") CPVs
    r_contact = client.post(
        "/api/v1/contacts",
        headers={"Authorization": "Bearer a-tok"},
        json={
            "company": "Prague Tech Systems s.r.o.",
            "country": "CZ",
            "cpv_prefixes": ["3112", "3572"],
            "keywords": {"en": ["generators", "radar*"]},
        },
    )
    assert r_contact.status_code == 200
    cid = r_contact.json()["id"]

    # A.6 check 4: GET /api/v1/contacts/{cid}/matches (status=open) must NOT include expired tender
    r_matches = client.get(f"/api/v1/contacts/{cid}/matches", headers={"Authorization": "Bearer v-tok"})
    assert r_matches.status_code == 200
    match_items = {it["tender"]["key"]: it for it in r_matches.json()["items"]}
    assert "EU:EXP-SAME-DAY" not in match_items
    assert match_items["EU:OPEN-CIVILIAN"]["recommendable"] is True
    assert match_items["EU:OPEN-MILITARY"]["recommendable"] is False
    assert match_items["EU:OPEN-MILITARY"]["recommendation_status"] == "REVIEW_REQUIRED_DO_NOT_PITCH"

    # A.7 check: Sales pitch draft excludes both the expired tender AND the REVIEW_REQUIRED military tender
    # when recommendable tenders exist (and under recommendations_only=true even for defence-only contacts)
    r_pitch = client.get(f"/api/v1/contacts/{cid}/pitch", headers={"Authorization": "Bearer v-tok"})
    assert r_pitch.status_code == 200
    pitch_md = r_pitch.text
    assert "Field generators and power cables lot 2" in pitch_md
    assert "EU:EXP-SAME-DAY" not in pitch_md
    assert "Military tactical radar system" not in pitch_md

    # Even for a purely military contact, recommendations_only=true excludes REVIEW_REQUIRED military tenders
    r_mil_contact = client.post(
        "/api/v1/contacts",
        headers={"Authorization": "Bearer a-tok"},
        json={
            "company": "Defence Only s.r.o.",
            "country": "CZ",
            "cpv_prefixes": ["3572"],
            "keywords": {"en": ["radar*"]},
        },
    )
    mil_cid = r_mil_contact.json()["id"]
    r_mil_pitch_rec = client.get(
        f"/api/v1/contacts/{mil_cid}/pitch?recommendations_only=true",
        headers={"Authorization": "Bearer v-tok"},
    )
    assert r_mil_pitch_rec.status_code == 200
    assert "Military tactical radar system" not in r_mil_pitch_rec.text
    assert "Открытых подходящих тендеров сейчас нет." in r_mil_pitch_rec.text
    assert C.is_pitch_recommendable(match_items["EU:OPEN-MILITARY"], ref_now) is False
