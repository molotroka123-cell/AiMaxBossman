from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class ContextTelemetry:
    raw_tokens: int = 0
    deduped_tokens: int = 0
    compressed_tokens: int = 0
    final_tokens: int = 0
    retrieved_sources: int = 0
    stale_removed: int = 0
    duplicates_removed: int = 0
    memories_injected: int = 0

    @property
    def saved_percent(self) -> float:
        if not self.raw_tokens: return 0.0
        return round((1-self.final_tokens/self.raw_tokens)*100,2)

    def to_dict(self):
        out=asdict(self); out["saved_percent"]=self.saved_percent; return out
