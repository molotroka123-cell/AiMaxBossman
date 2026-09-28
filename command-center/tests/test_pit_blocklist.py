"""Owner's private Jeff block rule (synthetic IDs only — never a real account).

The rule lives outside Git; Jeff keeps only salted keys in memory. An open
allowlist, a configured person entry or the owner role never bypass it.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os

import pytest

from bcc.pit import blocklist as bl
from bcc.pit import runtime as rt
from bcc.telegram_companion.adapters import Telegram
from bcc.telegram_companion.config import CompanionError, Person

from .test_pit_runtime import FakeAdapter, make_settings
from .test_pit_telegram_fake import FakeBotAPI, run_until

SALT = bytes.fromhex("ab" * 32)
BLOCKED = 555000111          # synthetic
FRIEND = 101                 # synthetic, same as the shared fake harness


def _rule(tmp_path, text: str):
    path = tmp_path / "private" / "blocked.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _update(update_id: int, user: int, text: str) -> dict:
    return {"update_id": update_id, "message": {
        "message_id": update_id, "text": text,
        "from": {"id": user, "is_bot": False}, "chat": {"id": user, "type": "private"}}}


# -- the rule itself ---------------------------------------------------------------------------
def test_rule_keeps_only_salted_keys_and_reports_no_ids(tmp_path):
    path = _rule(tmp_path, f"# private\n{BLOCKED}  # comment\n\n")
    rule = bl.PrivateBlocklist(path, SALT, required=True)
    assert rule.blocks_user(BLOCKED) and not rule.blocks_user(FRIEND)
    assert rule.blocks_user(FRIEND, BLOCKED)          # chat id counts too
    status = rule.status()
    assert status == {"source": "private_file", "configured": True, "entries": 1,
                      "reload_error": None}
    assert str(BLOCKED) not in repr(vars(rule)) + repr(status)


def test_malformed_or_unreadable_rule_fails_closed_at_start(tmp_path):
    with pytest.raises(bl.BlocklistError) as caught:
        bl.PrivateBlocklist(_rule(tmp_path, f"{BLOCKED}\nnot-an-id\n"), SALT)
    assert str(caught.value) == "BLOCKLIST_INVALID_LINE:2"     # line number, never content
    with pytest.raises(bl.BlocklistError, match="BLOCKLIST_FILE_MISSING"):
        bl.PrivateBlocklist(tmp_path / "absent.txt", SALT, required=True)
    # The default location missing means a fresh install without a ban.
    assert bl.PrivateBlocklist(tmp_path / "absent.txt", SALT).status()["entries"] == 0


def test_owner_edit_is_picked_up_and_a_typo_never_lifts_the_ban(tmp_path):
    path = _rule(tmp_path, "")
    rule = bl.PrivateBlocklist(path, SALT, required=True)
    assert not rule.blocks_user(BLOCKED)
    path.write_text(f"{BLOCKED}\n", encoding="utf-8")
    os.utime(path, ns=(1, 10**18))
    assert rule.blocks_user(BLOCKED)
    path.write_text(f"{BLOCKED}x\n", encoding="utf-8")         # typo while editing
    os.utime(path, ns=(1, 2 * 10**18))
    assert rule.blocks_user(BLOCKED)
    assert rule.status()["reload_error"] == "BLOCKLIST_INVALID_LINE:1"
    path.unlink()
    assert rule.blocks_user(BLOCKED)                           # vanished file keeps the ban


def test_path_resolution_prefers_env_then_config_then_private_default(tmp_path, monkeypatch):
    default_path, required = bl.resolve_blocklist_path("")
    assert not required and default_path.name == bl.DEFAULT_FILE_NAME
    assert bl.resolve_blocklist_path(str(tmp_path / "c.txt")) == (tmp_path / "c.txt", True)
    monkeypatch.setenv(bl.BLOCKLIST_ENV, str(tmp_path / "e.txt"))
    assert bl.resolve_blocklist_path(str(tmp_path / "c.txt")) == (tmp_path / "e.txt", True)


def test_rule_file_inside_a_git_checkout_is_refused(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(ValueError, match="outside any Git checkout"):
        dataclasses.replace(make_settings(tmp_path),
                            blocked_ids_file=str(tmp_path / "repo" / "ids.txt"))


# -- the runtime ---------------------------------------------------------------------------------
def _runtime(tmp_path, api, *, people=None, allowlist_open=True):
    settings = dataclasses.replace(
        make_settings(tmp_path, people=people), allowlist_open=allowlist_open,
        blocked_ids_file=str(_rule(tmp_path, f"{BLOCKED}\n")))
    runtime = rt.ParticipantRuntime(settings)
    runtime.telegram = Telegram(rt._transport_settings(settings), transport=api)
    runtime.adapter = FakeAdapter("Ответ другу")
    return runtime


def test_open_allowlist_never_answers_the_blocked_account(tmp_path):
    api = FakeBotAPI([_update(1, BLOCKED, "/start"), _update(2, BLOCKED, "Привет"),
                      _update(3, FRIEND, "/start")])
    runtime = _runtime(tmp_path, api)
    run_until(runtime, api, replies=1)
    assert [row["chat_id"] for row in api.sent] == [FRIEND]
    # Nothing of the blocked account is queued or kept; the offset still moved on.
    rows = runtime.store.db.execute("SELECT who FROM inbox").fetchall()
    assert all(str(BLOCKED) not in str(who) for (who,) in rows)
    assert runtime.store.get("offset", 0) == 4
    # No persona namespace was created for the blocked account.
    key = runtime.vault.key_for_telegram(BLOCKED)
    assert not runtime.vault.person_dir(key).exists()


def test_configured_owner_entry_does_not_bypass_the_block(tmp_path):
    people = (Person(user_id=BLOCKED, chat_id=BLOCKED, role="owner"),
              Person(user_id=FRIEND, chat_id=FRIEND, role="guest"))
    api = FakeBotAPI([_update(1, BLOCKED, "/start"), _update(2, FRIEND, "/start")])
    runtime = _runtime(tmp_path, api, people=people, allowlist_open=False)
    run_until(runtime, api, replies=1)
    assert [row["chat_id"] for row in api.sent] == [FRIEND]


def test_message_queued_before_the_block_is_dropped_by_the_worker(tmp_path):
    api = FakeBotAPI([])
    runtime = _runtime(tmp_path, api)
    person = Person(user_id=BLOCKED, chat_id=BLOCKED, role="guest")
    body = {"text": "старое сообщение", "_user_id": BLOCKED, "_chat_id": BLOCKED,
            "_message_id": 1}
    assert runtime.store.ingest(1, person.key, body)          # queued earlier

    async def drain():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(runtime._worker(person, "chat"), timeout=1.5)
    asyncio.run(drain())
    assert api.sent == [] and runtime.adapter.calls == []
    phase = runtime.store.db.execute("SELECT phase FROM inbox WHERE id=1").fetchone()
    assert phase is None or phase[0] == "failed"


def test_every_egress_path_refuses_the_blocked_chat(tmp_path):
    api = FakeBotAPI([])
    runtime = _runtime(tmp_path, api)
    blocked = Person(user_id=BLOCKED, chat_id=BLOCKED, role="guest")
    friend = Person(user_id=FRIEND, chat_id=FRIEND, role="guest")
    assert runtime.telegram.authorize_delivery(friend)
    assert not runtime.telegram.authorize_delivery(blocked)

    async def go():
        with pytest.raises(CompanionError, match="IDENTITY_REVOKED"):
            await runtime.telegram.send(blocked, "нельзя")
        with pytest.raises(CompanionError, match="IDENTITY_REVOKED"):
            await runtime.telegram.send_document(blocked, "x.json", b"{}")
    asyncio.run(go())
    assert api.sent == []
    # A transport swapped in later carries the same guard.
    runtime.telegram = Telegram(rt._transport_settings(runtime.settings), transport=api)
    assert not runtime.telegram.authorize_delivery(blocked)


def test_jeff_refuses_to_start_with_an_unreadable_rule(tmp_path):
    settings = dataclasses.replace(make_settings(tmp_path),
                                   blocked_ids_file=str(_rule(tmp_path, "abc\n")))
    with pytest.raises(bl.BlocklistError):
        rt.ParticipantRuntime(settings)
