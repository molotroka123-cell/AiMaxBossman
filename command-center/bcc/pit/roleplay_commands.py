from __future__ import annotations

from .roleplay import RolePlayMode, RolePlayState
from .vault import PersonaVault


def load_roleplay(vault: PersonaVault, person_key: str) -> RolePlayState:
    data = vault.roleplay_state(person_key)
    try:
        mode = RolePlayMode(str(data.get("mode", "off")))
    except ValueError:
        mode = RolePlayMode.OFF
    return RolePlayState(
        enabled=bool(data.get("enabled", False)),
        mode=mode,
        participant_consented=bool(data.get("participant_consented", False)),
        persona_label=str(data.get("persona_label", ""))[:80],
    )


def set_roleplay(
    vault: PersonaVault,
    person_key: str,
    *,
    enabled: bool,
    mode: RolePlayMode,
    participant_consented: bool,
    persona_label: str = "",
) -> RolePlayState:
    state = RolePlayState(
        enabled=bool(enabled),
        mode=mode if enabled else RolePlayMode.OFF,
        participant_consented=bool(participant_consented) if enabled else False,
        persona_label=str(persona_label)[:80] if enabled else "",
    )
    vault.set_roleplay_state(person_key, {
        "enabled": state.enabled,
        "mode": state.mode.value,
        "participant_consented": state.participant_consented,
        "persona_label": state.persona_label,
    })
    return state


def parse_roleplay_command(text: str) -> tuple[bool, RolePlayMode, str]:
    """Parse participant request; consent confirmation is handled separately."""
    parts = str(text or "").strip().split(maxsplit=2)
    command = parts[0].lower() if parts else ""
    arg = parts[1].lower() if len(parts) > 1 else ""
    label = parts[2] if len(parts) > 2 else ""

    if command == "/parody":
        if arg in {"off", "0", "нет", "stop"}:
            return False, RolePlayMode.OFF, ""
        return True, RolePlayMode.PARODY, label

    if command == "/roleplay":
        if arg in {"off", "0", "нет", "stop"}:
            return False, RolePlayMode.OFF, ""
        if arg in {"parody", "пародия"}:
            return True, RolePlayMode.PARODY, label
        if arg in {"mirror", "mirror_style", "зеркало"}:
            return True, RolePlayMode.MIRROR_STYLE, label
        return True, RolePlayMode.CHARACTER, " ".join(parts[1:])[:80]

    raise ValueError("not a roleplay command")
