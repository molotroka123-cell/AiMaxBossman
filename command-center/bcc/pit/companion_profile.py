from __future__ import annotations

from dataclasses import dataclass

from .models import ConsentState
from .router import PrivacyClass


PIT_BLOCKED_COMMANDS = frozenset({
    "/task", "/confirm", "/approvals", "/approve", "/reject",
    "/status", "/queue", "/menu", "/result", "/agents", "/audits",
    "/stop", "/pause", "/resume", "/screen", "/pc",
    "/claude", "/codex", "/sh", "/mode", "/fill", "/input",
    "/evolution_status", "/evolution_start", "/evolution_pause",
    "/evolution_resume", "/evolution_stop", "/evolution_report",
    "/cloud", "/watch", "/lock", "/market_verbose", "/model", "/jev",
})

PIT_ALLOWED_COMMANDS = frozenset({
    "/start", "/help", "/privacy", "/memory", "/why_memory", "/forget",
    "/pause_memory", "/resume_memory", "/export_me", "/delete_me", "/style",
    "/search", "/best", "/fast", "/roleplay", "/parody", "/photoedit", "/editphoto",
})


@dataclass(frozen=True, slots=True)
class PitParticipantPolicy:
    local_model_available: bool
    remote_model: bool
    role: str = "participant"

    def privacy_class(self, consent: ConsentState) -> PrivacyClass:
        if self.remote_model and not consent.remote_processing_enabled:
            return PrivacyClass.LOCAL_ONLY
        return PrivacyClass.PERSONAL

    def may_send_persona_to_selected_model(self, consent: ConsentState) -> bool:
        if not self.remote_model:
            return bool(consent.memory_enabled)
        return bool(consent.memory_enabled and consent.remote_personalization_enabled)


def command_allowed_in_pit(command: str) -> bool:
    name = str(command or "").strip().partition(" ")[0].lower()
    if not name:
        return True
    if name in PIT_BLOCKED_COMMANDS:
        return False
    return name in PIT_ALLOWED_COMMANDS


def strip_pit_commands(commands: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(name, desc) for name, desc in commands if command_allowed_in_pit("/" + name)]
