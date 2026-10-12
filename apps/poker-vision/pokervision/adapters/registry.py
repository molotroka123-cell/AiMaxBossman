from __future__ import annotations

from pathlib import Path

from .base import Frame, TableAdapter
from .poker_train import PokerTrainAdapter
from .roi import RoiAdapter, RoiProfile
from .ton_poker import TonPokerAdapter


def available() -> dict[str, dict]:
    return {a.id: {"title": a.title, "capabilities": a.capabilities.to_dict()} for a in (PokerTrainAdapter, TonPokerAdapter)}


def get(adapter_id: str, profile_path: Path | None = None) -> TableAdapter:
    if adapter_id == "poker_train":
        return PokerTrainAdapter()
    if adapter_id == "ton_poker":
        prof = RoiProfile.load(profile_path) if profile_path and Path(profile_path).exists() else None
        return TonPokerAdapter(prof)
    raise KeyError(f"unknown adapter {adapter_id!r}: only {sorted(available())} exist; a new layout needs calibration")


def for_recording(adapter_id: str, default: TableAdapter, source) -> TableAdapter:
    """A recording is read with the profile of the renderer that produced it (labels.jsonl: platform), not of
    the host OS: the win32 profile on Linux-rendered frames gave rank_ambiguous (k-NN margin 0.067, 11.10).
    Live sources keep the host profile; a recording without a platform label keeps the default adapter."""
    plat = getattr(source, "platform", None)
    if adapter_id != "poker_train" or not plat:
        return default
    from .poker_train import Profile, platform_profile
    return PokerTrainAdapter(Profile.load(platform_profile("poker_train.json", plat)))


def detect_best(frame: Frame, adapters: list[TableAdapter]) -> tuple[TableAdapter | None, list[dict]]:
    """Never guess: an adapter is chosen only if its detect score is clearly highest and >= 0.75."""
    scored = [(a, a.detect(frame)) for a in adapters]
    rows = [{"adapter": a.id, "score": d.score, "reasons": d.reasons} for a, d in scored]
    scored.sort(key=lambda x: -x[1].score)
    if scored and scored[0][1].score >= 0.75 and (len(scored) == 1 or scored[0][1].score - scored[1][1].score >= 0.25):
        return scored[0][0], rows
    return None, rows
