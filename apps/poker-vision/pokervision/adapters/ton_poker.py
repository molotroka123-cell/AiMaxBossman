"""Adapter #2: TON Poker inside Telegram — OBSERVE and REPLAY only.

No layout is shipped: none was provided, and none is invented. Until the owner supplies recordings (or a permitted test
table) and the profile passes held-out verification, every field is UNKNOWN. This adapter can never act and never gives
live advice: connecting an external client does not grant betting or any bypass of its restrictions.
"""
from __future__ import annotations

from .base import Capabilities
from .roi import RoiAdapter


class TonPokerAdapter(RoiAdapter):
    id = "ton_poker"
    title = "TON Poker (Telegram) — observe/replay only"
    capabilities = Capabilities(observe=True, replay=True, act=False, advise_live=False, multi_table=False,
                                reads=("hero_cards", "board", "pot", "to_call", "hero_stack"),
                                notes="needs owner-provided recordings or an allowed test table; calibration + held-out verification required")
