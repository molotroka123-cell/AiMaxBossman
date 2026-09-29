"""Private, bounded voice-style profiles for owner-approved local synthesis.

These values are control requests, not a claim that any model can reliably
express a specific emotion. Voice references stay in the host vault, never in
this module or the repository.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    mood: int = 5
    aggression: int = 5

    def __post_init__(self) -> None:
        if self.name not in {"Acid", "Voice Ember"}:
            raise ValueError("VOICE_PROFILE_UNKNOWN")
        for value in (self.mood, self.aggression):
            if type(value) is not int or not 1 <= value <= 10:
                raise ValueError("VOICE_PROFILE_SCALE_INVALID")

    def text_style_instruction(self) -> str:
        """Give the language model a bounded style request, never a new authority."""
        mood = ("сдержанное" if self.mood <= 3 else
                "энергичное" if self.mood >= 8 else "ровное")
        force = ("мягкая" if self.aggression <= 3 else
                 "резкая" if self.aggression >= 8 else "спокойная")
        return (f"Голосовой стиль {self.name}: настроение {self.mood}/10 ({mood}), "
                f"напор {self.aggression}/10 ({force} подача). "
                "Факты, границы доступа и смысл ответа не меняй.")

    def chatterbox_parameters(self) -> tuple[float, float]:
        """Experimental bounded knobs; audible effect requires owner A/B test."""
        exaggeration = round(0.5 + (self.mood - 5) * 0.02 +
                             (self.aggression - 5) * 0.01, 2)
        cfg_weight = round(0.5 + (self.aggression - 5) * 0.01, 2)
        return max(0.37, min(0.67, exaggeration)), max(0.46, min(0.55, cfg_weight))
