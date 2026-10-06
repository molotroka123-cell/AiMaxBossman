"""TableAdapter: detection, calibration, layout profile, and explicit capabilities.

An adapter never claims more than its capabilities say. Unknown layouts are refused until calibrated *and* verified
on held-out labelled frames (see ``VerificationReport``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np

from ..schema import TableState


@dataclass(frozen=True)
class Capabilities:
    observe: bool = False            # read live frames of an allowed surface
    replay: bool = False             # read recorded frames/videos
    act: bool = False                # may drive the UI (only the owner's loopback trainer, never an external client)
    advise_live: bool = False        # may show strategy suggestions while a hand is in progress
    multi_table: bool = False
    reads: tuple[str, ...] = ()      # fields the adapter can read at all
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Frame:
    bgr: np.ndarray
    t_ms: int
    source: str
    frame_id: str = ""
    dpr_hint: float | None = None    # only for synthetic/replay sources that know it; never trusted blindly
    meta: dict = field(default_factory=dict)   # capture facts, e.g. {'rect': window rect in physical px AT CAPTURE TIME}

    @property
    def h(self) -> int: return self.bgr.shape[0]
    @property
    def w(self) -> int: return self.bgr.shape[1]


@dataclass
class DetectResult:
    score: float                      # 0..1 how much the frame looks like this adapter's UI
    reasons: list[str] = field(default_factory=list)
    calibrated: bool = False


@dataclass
class VerificationReport:
    layout_id: str
    n_frames: int
    card_acc: float | None
    money_acc: float | None
    unknown_rate: float | None
    passed: bool
    notes: str = ""


class TableAdapter(ABC):
    id: str = "base"
    title: str = ""
    capabilities: Capabilities = Capabilities()

    @abstractmethod
    def detect(self, frame: Frame) -> DetectResult: ...

    @abstractmethod
    def read(self, frame: Frame) -> TableState: ...

    def calibrate(self, labelled: list[tuple[Frame, dict]]) -> Any:
        raise NotImplementedError(f"{self.id}: calibration not implemented")

    def profile_id(self) -> str:
        return f"{self.id}@uncalibrated"

    def reset(self) -> None:
        """Forget per-session memory (e.g. confirmed scale). Called at every new session/source."""
