"""Login state machine, error mapping by class NAME, contacts and peer resolution - on a fake Telethon client."""
from __future__ import annotations

import sys

import pytest

from bcc.secrets import Vault
from bcc.telegram_calls.account.credentials import CredentialStore
from bcc.telegram_calls.account.login import (LoginFlow, default_client_factory, list_contacts, map_telethon_error,
                                              resolve_peer)
from bcc.telegram_calls.types import CallError

from .fakes_a import API_HASH, Channel, FakeClient, Factory, User, tg_error


def flow(tmp_path, **client_kw):
    store = CredentialStore(tmp_path, Vault(tmp_path))
    factory = Factory(**client_kw)
    return LoginFlow(store, factory), store, factory


async def code_of(coro) -> str:
    with pytest.raises(CallError) as ei:
        await coro
    return ei.value.code


async def test_happy_path_phone_code_ready_and_secrets_are_not_returned(tmp_path):
    lf, store, factory = flow(tmp_path)
    assert lf.status()["state"] == "no_credentials"
    store.save_api(123456, API_HASH)
    assert lf.state.value == "logged_out"
    st = await lf.start("+79001234567")
    assert st["state"] == "code_sent"
    st = await lf.submit_code("12345")
    assert st == {"state": "ready", "has_credentials": True, "has_session": True}
    assert store.get().session == "SESSION-STRING-XYZ" and store.self_id() == 111
    blob = repr(st)
    assert "SESSION" not in blob and "7900" not in blob and API_HASH not in blob
    assert factory.made[0].disconnected >= 1                       # the login client is closed afterwards
    assert factory.args[0] == (123456, API_HASH, "")               # login starts with an EMPTY session


async def test_two_factor_branch(tmp_path):
    lf, store, factory = flow(tmp_path, script={"sign_in": [tg_error("SessionPasswordNeededError")]})
    store.save_api(123456, API_HASH)
    await lf.start("+79001234567")
    assert (await lf.submit_code("12345"))["state"] == "password_needed"
    assert not store.has_session()
    assert (await lf.submit_password("hunter2"))["state"] == "ready"
    signs = [c for c in factory.made[0].calls if c[0] == "sign_in"]
    assert signs[-1][2] == {"password": "hunter2"}


async def test_out_of_order_steps_are_rejected(tmp_path):
    lf, store, _ = flow(tmp_path)
    store.save_api(123456, API_HASH)
    assert await code_of(lf.submit_code("12345")) == "LOGIN_NOT_PENDING"
    assert await code_of(lf.submit_password("x")) == "LOGIN_NOT_PENDING"
    await lf.start("+79001234567")
    assert await code_of(lf.submit_password("x")) == "LOGIN_NOT_PENDING"


async def test_start_requires_credentials_and_valid_phone(tmp_path):
    lf, store, _ = flow(tmp_path)
    assert await code_of(lf.start("+79001234567")) == "NO_CREDENTIALS"
    store.save_api(123456, API_HASH)
    assert await code_of(lf.start("hello")) == "LOGIN_PHONE_INVALID"
    assert await code_of(lf.start(None)) == "LOGIN_PHONE_INVALID"


@pytest.mark.parametrize("name,code", [
    ("PhoneCodeInvalidError", "LOGIN_CODE_INVALID"), ("PhoneCodeExpiredError", "LOGIN_CODE_EXPIRED"),
    ("FloodWaitError", "LOGIN_FLOOD_WAIT"), ("SomeNewRpcError", "TELEGRAM_RPC")])
async def test_code_errors_mapped_by_class_name_and_text_is_discarded(tmp_path, name, code):
    lf, store, _ = flow(tmp_path, script={"sign_in": [tg_error(name, seconds=42)]})
    store.save_api(123456, API_HASH)
    await lf.start("+79001234567")
    with pytest.raises(CallError) as ei:
        await lf.submit_code("12345")
    err = ei.value
    assert err.code == code
    assert "79001234567" not in repr(err) and "79001234567" not in str(err.as_dict())
    if name == "FloodWaitError":
        assert err.detail == "42s"


async def test_invalid_code_keeps_flow_pending_and_expired_code_resets_it(tmp_path):
    lf, store, _ = flow(tmp_path, script={"sign_in": [tg_error("PhoneCodeInvalidError")]})
    store.save_api(123456, API_HASH)
    await lf.start("+79001234567")
    assert await code_of(lf.submit_code("11111")) == "LOGIN_CODE_INVALID"
    assert lf.state.value == "code_sent"                            # may retry with the right code
    assert (await lf.submit_code("22222"))["state"] == "ready"

    lf2, store2, _ = flow(tmp_path / "b", script={"sign_in": [tg_error("PhoneCodeExpiredError")]})
    store2.save_api(123456, API_HASH)
    await lf2.start("+79001234567")
    assert await code_of(lf2.submit_code("11111")) == "LOGIN_CODE_EXPIRED"
    assert lf2.state.value == "logged_out"
    assert await code_of(lf2.submit_code("22222")) == "LOGIN_NOT_PENDING"


async def test_wrong_password_keeps_password_state(tmp_path):
    lf, store, _ = flow(tmp_path, script={"sign_in": [tg_error("SessionPasswordNeededError"),
                                                     tg_error("PasswordHashInvalidError")]})
    store.save_api(123456, API_HASH)
    await lf.start("+79001234567")
    await lf.submit_code("12345")
    assert await code_of(lf.submit_password("bad")) == "LOGIN_PASSWORD_INVALID"
    assert lf.state.value == "password_needed"
    assert (await lf.submit_password("good"))["state"] == "ready"


async def test_send_code_failures_leave_logged_out(tmp_path):
    for name, code in (("PhoneNumberInvalidError", "LOGIN_PHONE_INVALID"), ("FloodWaitError", "LOGIN_FLOOD_WAIT"),
                       ("ApiIdInvalidError", "NO_CREDENTIALS")):
        lf, store, factory = flow(tmp_path / name, script={"send_code_request": [tg_error(name)]})
        store.save_api(123456, API_HASH)
        assert await code_of(lf.start("+79001234567")) == code
        assert lf.state.value == "logged_out" and factory.made[0].disconnected >= 1


def test_map_errors_network_internal_and_session():
    assert map_telethon_error(ConnectionError("boom +7900")).code == "TELEGRAM_NETWORK"
    assert map_telethon_error(TimeoutError()).code == "TELEGRAM_NETWORK"
    assert map_telethon_error(tg_error("AuthKeyUnregisteredError")).code == "SESSION_REVOKED"
    e = map_telethon_error(KeyError("secret-token"))
    assert e.code == "INTERNAL" and e.detail == "KeyError" and "secret-token" not in repr(e)
    already = CallError("PEER_INVALID")
    assert map_telethon_error(already) is already


async def test_open_session_not_logged_in_and_revoked(tmp_path):
    lf, store, _ = flow(tmp_path)
    assert await code_of(lf.open_session()) == "NO_CREDENTIALS"
    store.save_api(123456, API_HASH)
    assert await code_of(lf.open_session()) == "NOT_LOGGED_IN"
    store.save_session("S", 111)
    lf2 = LoginFlow(store, Factory(authorized=False))
    assert await code_of(lf2.open_session()) == "SESSION_REVOKED"
    assert not store.has_session()                                   # a dead session is forgotten
    store.save_session("S", 111)
    client = await LoginFlow(store, Factory()).open_session()
    assert client.connected


async def test_logout_forgets_everything_even_if_telegram_is_unreachable(tmp_path):
    lf, store, factory = flow(tmp_path)
    store.save_api(123456, API_HASH)
    store.save_session("S", 111)
    st = await lf.logout()
    assert st["state"] == "no_credentials" and factory.made[0].logged_out
    lf2, store2, _ = flow(tmp_path / "x", script={"connect": [tg_error("ConnectionError", base=ConnectionError)]})
    store2.save_api(123456, API_HASH)
    store2.save_session("S", 111)
    assert (await lf2.logout())["state"] == "no_credentials"


async def test_cancel_resets_pending_login(tmp_path):
    lf, store, factory = flow(tmp_path)
    store.save_api(123456, API_HASH)
    await lf.start("+79001234567")
    assert (await lf.cancel())["state"] == "logged_out"
    assert await code_of(lf.submit_code("12345")) == "LOGIN_NOT_PENDING"


async def test_default_factory_without_telethon_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "telethon", None)
    with pytest.raises(CallError) as ei:
        default_client_factory(1, API_HASH, "")
    assert ei.value.code == "DEPENDENCIES_MISSING"


# ---------------------------------------------------------------- contacts / peers
CONTACTS = [User(4242, "Second", "Acc", "second_acc"), User(1, "Robo", bot=True), User(2, "Gone", deleted=True),
            User(111, "Me", is_self=True), User(7, "Alice", "A")]


async def test_contacts_exclude_bots_deleted_and_self_and_hide_phones():
    out = await list_contacts(FakeClient(contacts=CONTACTS), 111)
    assert [c["user_id"] for c in out] == [7, 4242]
    assert all(set(c) == {"user_id", "label", "username"} for c in out)
    assert "9999" not in repr(out) and "79990000000" not in repr(out)


async def test_contacts_flag_self_by_id_even_without_is_self_attribute():
    users = [User(111, "Me"), User(5, "Bob")]
    assert [c["user_id"] for c in await list_contacts(FakeClient(contacts=users), 111)] == [5]


async def test_resolve_peer_accepts_a_normal_user():
    c = FakeClient(entities={4242: User(4242, "Second", username="s")})
    card = await resolve_peer(c, 4242, 111)
    assert card == {"user_id": 4242, "label": "Second", "username": "s"}


@pytest.mark.parametrize("entity,code", [(User(5, "Bot", bot=True), "PEER_INVALID"),
                                          (User(5, "Gone", deleted=True), "PEER_INVALID"),
                                          (User(5, "Me", is_self=True), "PEER_IS_SELF"),
                                          (Channel(5), "PEER_INVALID")])
async def test_resolve_peer_rejects_bad_kinds(entity, code):
    assert await code_of(resolve_peer(FakeClient(entities={5: entity}), 5, 111)) == code


async def test_resolve_peer_rejects_self_id_unknown_and_garbage():
    c = FakeClient(entities={111: User(111, "Me")})
    assert await code_of(resolve_peer(c, 111, 111)) == "PEER_IS_SELF"      # before any lookup
    assert await code_of(resolve_peer(c, 999, 111)) == "PEER_NOT_FOUND"
    for bad in (0, -1, "5", True, None, 2 ** 60):
        assert await code_of(resolve_peer(c, bad, 111)) == "PEER_INVALID"
