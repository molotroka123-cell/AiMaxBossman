"""Participant passport commands (Jeff 1.5): view, personalisation, revoke consent.

Shared by Telegram (``/passport``, ``/personalization``, ``/revoke_consent``) and the Jeff
window API. Every function takes ONE person key and touches only that namespace. State is
in the participant's own files (consent.json, facts, audit), so it survives a restart.
Each call is audited through ``PersonaVault.audit`` (never stores fact values).

Consent gating: viewing evidence needs memory consent (otherwise only the consent state is
shown); enabling personalisation needs memory consent; restricting or revoking is never
blocked (the participant can always take consent back).
"""
from __future__ import annotations

from typing import Any

from . import passport
from .models import ConsentState
from .secret_filter import redact_secrets

_ALL_FLAGS = ("memory_enabled", "raw_history_enabled", "sensitive_memory_enabled",
              "remote_processing_enabled", "remote_personalization_enabled",
              "training_use_enabled", "discovery_enabled", "personalization_enabled")
_FRESH_RU = {"fresh": "актуален", "stale": "мог устареть", "expired": "истёк", "unknown": "дата неизвестна"}
_SOURCE_RU = {"message": "сообщение", "voice": "голос", "command": "твоя команда",
              "master_parser": "разбор переписки", "legacy": "старая запись"}
_MAX_LISTED = 15


def consent_flags(consent: ConsentState) -> dict[str, bool]:
    return {name: bool(getattr(consent, name)) for name in _ALL_FLAGS}


def passport_view(vault: Any, person_key: str, *, surface: str = "telegram") -> dict[str, Any]:
    """Structured own-passport view for the window API; facts only with memory consent."""
    consent = vault.consent(person_key)
    if vault.person_dir(person_key).is_dir():
        passport.migrate_person(vault, person_key)
    view: dict[str, Any] = {"schema": passport.JEFF_PASSPORT_SCHEMA,
                            "consent": consent_flags(consent), "facts": [],
                            "layers": passport.layer_summary(vault, person_key)
                            if vault.person_dir(person_key).is_dir() else {},
                            "consent_history": passport.consent_history(vault, person_key, 20)}
    if consent.memory_enabled:
        for row in passport.facts_layer(vault, person_key):
            env = row["passport"]
            value = row.get("value")
            text, _ = redact_secrets(value if isinstance(value, str) else str(value))
            view["facts"].append({
                "id": str(row.get("id", "")), "category": str(row.get("category", "")),
                "key": str(row.get("key", "")), "value": text[:300],
                "confidence": env.get("confidence"), "source": env.get("source"),
                "scope": env.get("scope"), "freshness": passport.freshness_status(row),
                "version": env.get("version", 1),
                "corrections": max(0, len(env.get("history") or []) - 1)})
    if vault.person_dir(person_key).is_dir():
        vault.audit(person_key, "passport_view", actor="participant", surface=surface,
                    fact_ids=[f["id"] for f in view["facts"]])
    return view


def view_text(vault: Any, person_key: str) -> str:
    data = passport_view(vault, person_key)
    consent = vault.consent(person_key)
    if not consent.memory_enabled:
        return ("Память выключена, поэтому я не показываю и не использую факты. "
                "Включить — /resume_memory. Что разрешено — /privacy.")
    facts = data["facts"]
    if not facts:
        return "Паспорт пока пуст: я ничего о тебе не записал."
    lines = [f"Что я помню (версия паспорта {passport.JEFF_PASSPORT_SCHEMA}), фактов: {len(facts)}."]
    for fact in facts[:_MAX_LISTED]:
        src = fact["source"] or {}
        lines.append(
            f"• {fact['value']} — {_SOURCE_RU.get(src.get('kind', ''), 'источник')}"
            f"{' ' + src['date'][:10] if src.get('date') else ''}, уверенность "
            f"{float(fact['confidence'] or 0):.2f}, {_FRESH_RU.get(fact['freshness'], '')}"
            f"{', исправлений: ' + str(fact['corrections']) if fact['corrections'] else ''}")
    if len(facts) > _MAX_LISTED:
        lines.append(f"…и ещё {len(facts) - _MAX_LISTED}.")
    if not consent.personalization_enabled:
        lines.append("Персонализация выключена: в ответах я эти факты не использую.")
    lines.append("Исправить — /correct было => стало; забыть — /forget что; "
                 "отозвать согласие — /revoke_consent.")
    return "\n".join(lines)


def set_personalization(vault: Any, person_key: str, enabled: bool, *,
                        surface: str = "telegram") -> tuple[bool, str]:
    """Turn personalisation off/on. On requires memory consent (never re-grants it)."""
    consent = vault.consent(person_key)
    if enabled and not consent.memory_enabled:
        return False, "Сначала включи память (/resume_memory): без неё персонализировать нечего."
    if consent.personalization_enabled == enabled:
        return True, ("Персонализация уже включена." if enabled else "Персонализация уже выключена.")
    consent.personalization_enabled = enabled
    vault.set_consent(person_key, consent)
    vault.audit(person_key, "consent", actor="participant", surface=surface,
                categories=["personalization_on" if enabled else "personalization_off"])
    return True, ("Персонализация включена." if enabled else
                  "Персонализация выключена: память остаётся, но в ответах я её не использую.")


def revoke_consent(vault: Any, person_key: str, *, surface: str = "telegram") -> tuple[bool, str]:
    """Withdraw EVERY consent flag at once. Stored data stays until the participant deletes it,
    but nothing new is written and nothing stored is used (all gates read these flags)."""
    if not vault.person_dir(person_key).is_dir():
        return False, "Согласия ещё нет, отзывать нечего."
    consent = vault.consent(person_key)
    for name in _ALL_FLAGS:
        if name != "personalization_enabled":  # a style switch, not a consent grant
            setattr(consent, name, False)
    vault.set_consent(person_key, consent)
    vault.audit(person_key, "consent", actor="participant", surface=surface,
                categories=["revoke_all"])
    return True, ("Согласие отозвано: ничего нового не записываю, сохранённое не использую и "
                  "не передаю моделям. Стереть уже сохранённое — /delete_me; вернуть память — "
                  "/resume_memory.")
