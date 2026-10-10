"""Owner rule 10.10: the Пульт bot writes to the OWNER only — a guest listed in its config gets nothing.

Jeff talks to everyone with his own bot and his own delivery rule; this contract is about the Пульт (Companion) only.
"""
from __future__ import annotations

from types import SimpleNamespace

from bcc.telegram_companion.config import Person
from bcc.telegram_companion.service import Companion

OWNER = Person(11111, 11111, "owner", 7)
GUEST = Person(22222, 22222, "guest", None)
STRANGER = Person(33333, 33333, "owner", 7)


class Telegram:
    def __init__(self):
        self.authorize_delivery = lambda person: True


def companion(people):
    settings = SimpleNamespace(people=tuple(people), pc_control=True)
    return Companion(settings, SimpleNamespace(get=lambda *a, **k: None, put=lambda *a, **k: None),
                     Telegram(), object(), models=object(), policy_provider=lambda: settings)


def test_pult_delivers_to_the_owner():
    assert companion((OWNER, GUEST)).telegram.authorize_delivery(OWNER) is True


def test_pult_never_delivers_to_a_guest_listed_in_its_config():
    assert companion((OWNER, GUEST)).telegram.authorize_delivery(GUEST) is False


def test_pult_never_delivers_to_someone_outside_its_config():
    assert companion((OWNER, GUEST)).telegram.authorize_delivery(STRANGER) is False
