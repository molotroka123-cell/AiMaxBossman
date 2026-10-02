"""Login flow and lookups against a fake Telethon client (no network, Telethon not required).

Errors are mapped by class NAME, exactly as real Telethon raises them; the fake exceptions below reuse the names.
"""
from __future__ import annotations

import json
import logging

import pytest

from bcc.secrets import Vault
from bcc.telegram_calls.account.credentials import CredentialStore
from bcc.telegram_calls.account.login import TelegramAccount, map_error, normalize_phone
from bcc.telegram_calls.types import AccountState, CallError

API_ID, API_HASH = 4242, "abcdef0123456789" * 2
PHONE = "+79001234567"
CODE, PASSWORD = "48213", "correct horse battery"     # fixtures, not credentials


def make_exc(name, **attrs):
    exc = type(name, (Exception,), {})("secret-looking text " + PHONE)
    for k, v in attrs.items():
        setattr(exc, k, v)
    return exc


class FakeSession:
    def save(self):
        return "SESSION-" + "x" * 40


class User:
    def __init__(self, id, first="", last="", username=None, bot=False, deleted=False):
        self.id, self.first_name, self.last_name, self.username, self.bot, self.deleted = id, first, last, username, bot, deleted


class Dialog:
    def __init__(self, entity):
        self.entity = entity


class FakeClient:
    def __init__(self, session, api_id, api_hash, script=None):
        self.session, self.connected, self.authorised = FakeSession(), False, bool(session)
        self.script, self.calls = script if script is not None else {}, []
        self.password_needed = False
        self.dialogs = [Dialog(User(11, "Вторая", "Учётка", "second_acc")), Dialog(User(12, "Бот", bot=True)), Dialog(User(1, "Я"))]

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        err = self.script.get("is_user_authorized")
        if err:
            raise err
        return self.authorised

    async def send_code_request(self, phone):
        self.calls.append(("send_code", phone))
        if "send_code" in self.script:
            raise self.script["send_code"]
        return type("Sent", (), {"phone_code_hash": "hash-1"})()

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        self.calls.append(("sign_in", bool(password)))
        if password is not None:
            if password != PASSWORD:
                raise make_exc("PasswordHashInvalidError")
            self.authorised = True
            return None
        if code != CODE:
            raise make_exc("PhoneCodeInvalidError")
        if self.password_needed:
            raise make_exc("SessionPasswordNeededError")
        self.authorised = True

    async def get_me(self):
        return User(1, "Я")

    async def log_out(self):
        self.authorised = False

    async def iter_dialogs(self, limit=200):
        for d in self.dialogs:
            yield d

    async def get_contacts(self):
        return [User(13, "Контакт", "Два", "contact_two")]

    async def get_entity(self, key):
        if key in ("second_acc", 11):
            return User(11, "Вторая", "Учётка", "second_acc")
        raise make_exc("UsernameNotOccupiedError")


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    store = CredentialStore(tmp_path / "calls", vault=Vault(tmp_path))
    store.save_api(API_ID, API_HASH)
    clients = []

    def factory(session, api_id, api_hash):
        c = FakeClient(session, api_id, api_hash)
        clients.append(c)
        return c
    acc = TelegramAccount(store, client_factory=factory)
    acc.clients = clients
    return acc


async def test_full_login_with_code_only(account, caplog):
    caplog.set_level(logging.DEBUG)
    assert account.state() == AccountState.LOGGED_OUT
    await account.start_login("+7 900 123-45-67")
    assert account.state() == AccountState.CODE_SENT and account.clients[0].calls[0] == ("send_code", PHONE)
    assert await account.submit_code("48-213") == AccountState.READY
    creds = account.store.load()
    assert creds.session.startswith("SESSION-") and creds.me_id == 1 and creds.phone_last4 == "4567"
    assert account.state() == AccountState.READY and account.me_id() == 1
    pub = json.dumps(account.public())
    for secret in (PHONE, CODE, "SESSION-", API_HASH):
        assert secret not in pub and secret not in caplog.text


async def test_two_factor_password_step(account):
    await account.start_login(PHONE)
    account.clients[0].password_needed = True
    assert await account.submit_code(CODE) == AccountState.PASSWORD_NEEDED
    assert account.state() == AccountState.PASSWORD_NEEDED and account.store.load().session == ""
    with pytest.raises(CallError) as ei:
        await account.submit_password("wrong")
    assert ei.value.code == "LOGIN_PASSWORD_INVALID"
    assert account.state() == AccountState.PASSWORD_NEEDED                    # can retry
    assert await account.submit_password(PASSWORD) == AccountState.READY
    assert account.store.load().session


async def test_wrong_code_is_refused_and_keeps_the_flow_open(account):
    await account.start_login(PHONE)
    with pytest.raises(CallError) as ei:
        await account.submit_code("00000")
    assert ei.value.code == "LOGIN_CODE_INVALID" and PHONE not in json.dumps(ei.value.as_dict())
    assert account.state() == AccountState.CODE_SENT
    assert await account.submit_code(CODE) == AccountState.READY


async def test_steps_out_of_order_are_refused(account):
    with pytest.raises(CallError) as ei:
        await account.submit_code(CODE)
    assert ei.value.code == "LOGIN_NOT_PENDING"
    with pytest.raises(CallError) as ei2:
        await account.submit_password(PASSWORD)
    assert ei2.value.code == "LOGIN_NOT_PENDING"


async def test_flood_wait_and_bad_phone_map_to_stable_codes_without_text_leak(account):
    account._factory = lambda *a: type("C", (FakeClient,), {})(*a, script={"send_code": make_exc("FloodWaitError", seconds=300)})
    with pytest.raises(CallError) as ei:
        await account.start_login(PHONE)
    assert ei.value.code == "LOGIN_FLOOD_WAIT" and ei.value.detail == "wait_300s" and PHONE not in json.dumps(ei.value.as_dict())
    with pytest.raises(CallError) as bad:
        await account.start_login("12345")
    assert bad.value.code == "LOGIN_PHONE_INVALID"


def test_map_error_by_class_name_and_unknown_errors_are_generic():
    assert map_error(make_exc("PhoneCodeExpiredError")).code == "LOGIN_CODE_EXPIRED"
    assert map_error(make_exc("SessionRevokedError")).code == "SESSION_REVOKED"
    assert map_error(ConnectionError("x")).code == "TELEGRAM_NETWORK"
    unknown = map_error(make_exc("WeirdNewError"))
    assert unknown.code == "TELEGRAM_RPC" and PHONE not in json.dumps(unknown.as_dict())


@pytest.mark.parametrize("raw,ok", [("+79001234567", True), ("8 (900) 123-45-67", False), ("79001234567", False),
                                    ("+1 415 555 0100", True), ("abc", False), ("+12", False), ("", False)])
def test_phone_normalisation(raw, ok):
    if ok:
        assert normalize_phone(raw).startswith("+")
    else:
        with pytest.raises(CallError):
            normalize_phone(raw)


async def test_revoked_session_is_cleared_and_reported(account):
    account.store.save_session("SESSION-old" + "y" * 30, 1, PHONE)
    account._factory = lambda *a: type("C", (FakeClient,), {})(*a, script={"is_user_authorized": make_exc("AuthKeyUnregisteredError")})
    with pytest.raises(CallError) as ei:
        await account.ensure_client()
    assert ei.value.code == "SESSION_REVOKED" and account.store.load().session == ""
    assert account.state() == AccountState.LOGGED_OUT


async def test_logout_forgets_the_session_even_when_telegram_is_unreachable(account):
    await account.start_login(PHONE)
    await account.submit_code(CODE)

    async def boom():
        raise ConnectionError("offline")
    account.clients[0].log_out = boom
    await account.logout()
    assert account.store.load().session == "" and account.store.load().api_id == API_ID
    assert account.state() == AccountState.LOGGED_OUT


async def test_contacts_exclude_self_and_bots_and_never_expose_phones(account):
    await account.start_login(PHONE)
    await account.submit_code(CODE)
    people = await account.contacts()
    ids = {p["id"] for p in people}
    assert ids == {11, 13}
    assert all(set(p) == {"id", "label", "username", "bot", "deleted", "is_self", "is_user"} for p in people)
    assert [p["id"] for p in await account.contacts("second")] == [11]
    assert await account.contacts("нет такого") == []


async def test_resolve_username_and_confirm_user(account):
    await account.start_login(PHONE)
    await account.submit_code(CODE)
    assert (await account.resolve_username("@second_acc"))["id"] == 11
    assert (await account.confirm_user(11))["label"] == "Вторая Учётка"
    with pytest.raises(CallError) as ei:
        await account.resolve_username("@nobody_here")
    assert ei.value.code == "PEER_NOT_FOUND"
    with pytest.raises(CallError):
        await account.resolve_username("bad name!")


async def test_missing_credentials_are_reported_before_any_network(tmp_path):
    acc = TelegramAccount(CredentialStore(tmp_path / "c", vault=Vault(tmp_path)),
                          client_factory=lambda *a: pytest.fail("no client may be built without credentials"))
    assert acc.state() == AccountState.NO_CREDENTIALS
    with pytest.raises(CallError) as ei:
        await acc.start_login(PHONE)
    assert ei.value.code == "NO_CREDENTIALS"
