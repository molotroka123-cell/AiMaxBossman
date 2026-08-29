"""One label per engine: what actually ran on this host, and what did not.

The failure mode this module exists to prevent: a job finishes, `model.stl`
appears on disk, and a single boolean somewhere says "it worked" — collapsing
"the spec compiled" together with "a CAD kernel ran", "the mesh was validated",
"a slicer ran", "the G-code was scanned" and "a printer printed it".

Those are six different claims with six different amounts of evidence behind
them, and on a host with no CadQuery, no OpenSCAD, no slicer and no printer,
most of them are `NOT_RUN`. A `NOT_RUN` always carries the reason it did not
run, so nobody has to guess whether it was unavailable, not requested, or
skipped after an earlier failure.

`NOT_RUN` is not a soft `PASS` and it is not a `FAIL`. It is the honest third
answer, and the only one this codebase is allowed to give for a stage whose
engine is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PASS = "PASS"
FAIL = "FAIL"
NOT_RUN = "NOT_RUN"

STATUSES = (PASS, FAIL, NOT_RUN)


@dataclass(slots=True)
class EvidenceEntry:
    status: str
    engine: str = ""
    reason: str = ""
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "engine": self.engine,
            "reason": self.reason,
            "detail": self.detail,
        }


# Every key, in report order, with the reason it reads NOT_RUN until something
# actually records a result against it.
DEFAULT_REASONS: dict[str, str] = {
    "spec_compiled": "no DesignSpec was submitted for this job",
    "cad_engine": "no CAD engine was invoked",
    "generative_engine": (
        "Mode B is not built: no text/image-to-3D adapter exists in this build and no "
        "generation vendor is reachable from this host. The validation half of Mode B "
        "is the ordinary mesh path, which every mesh goes through with no exemption"
    ),
    "step_export": "no STEP export was attempted",
    "openscad_render": "OpenSCAD was not invoked",
    "mesh_validation": "no mesh reached the validator",
    "mesh_cross_check": "no independent cross-check was attempted",
    "printability": "the printability gate was not reached",
    "slicer": "slicing was not requested",
    "gcode_safety": "there was no G-code to scan",
    "physical_printer": (
        "no printer is attached and physical action is a separate confirmed call; "
        "nothing in this pipeline can reach a heater or a motor"
    ),
}

KEYS = tuple(DEFAULT_REASONS)


class EvidenceLedger:
    """A fixed set of claims, each answered exactly once."""

    def __init__(self) -> None:
        self._entries: dict[str, EvidenceEntry] = {
            key: EvidenceEntry(NOT_RUN, reason=reason) for key, reason in DEFAULT_REASONS.items()
        }

    def record(
        self,
        key: str,
        status: str,
        *,
        engine: str = "",
        reason: str = "",
        detail: dict | None = None,
    ) -> None:
        if key not in self._entries:
            raise KeyError(f"unknown evidence key {key!r}")
        if status not in STATUSES:
            raise ValueError(f"evidence status must be one of {STATUSES}, got {status!r}")
        if status == NOT_RUN and not reason:
            raise ValueError(f"{key}: NOT_RUN must say why it did not run")
        self._entries[key] = EvidenceEntry(status, engine, reason, dict(detail or {}))

    def not_run(self, key: str, reason: str, *, engine: str = "") -> None:
        self.record(key, NOT_RUN, engine=engine, reason=reason)

    def status(self, key: str) -> str:
        return self._entries[key].status

    def as_dict(self) -> dict:
        return {key: entry.as_dict() for key, entry in self._entries.items()}

    def summary_lines(self) -> list[str]:
        out = []
        for key, entry in self._entries.items():
            tail = f" — {entry.engine}" if entry.engine else ""
            if entry.status == NOT_RUN:
                tail += f" ({entry.reason})"
            out.append(f"- {key}: **{entry.status}**{tail}")
        return out
