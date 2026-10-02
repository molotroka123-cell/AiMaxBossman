from __future__ import annotations

from bcc.pit.presentation_profile import (
    PresentationProfile,
    clear_presentation,
    load_presentation,
    normalize_profile,
    save_presentation,
)
from bcc.pit.vault import PersonaVault

SALT = bytes.fromhex("ab" * 32)
A = "a" * 64  # PersonaVault only accepts derived 64-hex person keys
B = "b" * 64


def test_defaults_are_safe_and_text_only(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    p = load_presentation(vault, A)
    assert p == PresentationProfile()
    assert p.voice_reply_mode == "TEXT_ONLY"
    assert p.avatar_id == "aurora"


def test_profile_persists_per_participant_without_cross_user_leak(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    save_presentation(
        vault, A, avatar_id="ember", voice_id="Voice A",
        voice_rate=1.1, voice_reply_mode="VOICE_ON_REQUEST",
    )
    save_presentation(
        vault, B, avatar_id="cobalt", voice_id="Voice B",
        voice_pitch=0.9, voice_reply_mode="VOICE_AUTO",
    )

    a = load_presentation(vault, A)
    b = load_presentation(vault, B)
    assert (a.avatar_id, a.voice_id, a.voice_reply_mode) == (
        "ember", "Voice A", "VOICE_ON_REQUEST")
    assert (b.avatar_id, b.voice_id, b.voice_reply_mode) == (
        "cobalt", "Voice B", "VOICE_AUTO")
    assert a != b


def test_invalid_values_fail_to_safe_defaults():
    p = normalize_profile({
        "avatar_id": "../../owner",
        "voice_id": "voice\x00name",
        "voice_rate": 99,
        "voice_pitch": -1,
        "voice_reply_mode": "OWNER_CONTROL",
        "permissions": ["shell.exec"],
    })
    assert p.avatar_id == "aurora"
    assert p.voice_id == "voicename"
    assert p.voice_rate == 1.0
    assert p.voice_pitch == 1.0
    assert p.voice_reply_mode == "TEXT_ONLY"
    assert "permissions" not in p.public()


def test_clear_only_removes_presentation_profile(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    person_key = A
    person_dir = vault.ensure(person_key)
    keep = person_dir / "keep.txt"
    keep.write_text("participant memory fixture", encoding="utf-8")
    save_presentation(vault, person_key, avatar_id="mono")

    clear_presentation(vault, person_key)

    assert load_presentation(vault, person_key) == PresentationProfile()
    assert keep.read_text(encoding="utf-8") == "participant memory fixture"
