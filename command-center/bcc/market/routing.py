"""Jev routing for market observation (owner scenario 14).

Jev Ultrafast is DOM/state driven. A DOM task (dismiss a cookie banner, check
the player state) stays with Jev's observed-target action space. A value drawn
inside <video>/<canvas> is NOT in the DOM: Jev must escalate to the local
screenshot/vision path instead of inventing a selector or a number.
"""
from __future__ import annotations

from typing import Any

from ..jev.browser_fastpath import unsupported_reasons

JEV_DOM = "jev_dom"
ESCALATE_VISION = "escalate_local_vision"


def route(observation: dict[str, Any], *, target: str) -> dict[str, Any]:
    """Decide who handles `target` on this page.

    `target` = "dom" for page-state/controls, "pixels" for values rendered in
    the player. `observation["features"]` uses the Jev probe keys plus `video`.
    """
    features = observation.get("features") if isinstance(observation.get("features"), dict) else None
    video = int((features or {}).get("video") or 0)
    canvas = int((features or {}).get("canvas") or 0)
    if target == "pixels":
        if video or canvas:
            return {"route": ESCALATE_VISION, "reason": "value_rendered_in_video_or_canvas",
                    "jev_may_read_value": False}
        return {"route": ESCALATE_VISION, "reason": "no_dom_source_for_value", "jev_may_read_value": False}
    reasons = unsupported_reasons({**observation, "features": {k: v for k, v in (features or {}).items()
                                                                if k != "video"}} if features else observation)
    if reasons:
        return {"route": ESCALATE_VISION if ("canvas" in reasons) else "escalate_full_browser",
                "reason": ",".join(reasons), "jev_may_read_value": False}
    return {"route": JEV_DOM, "reason": "dom_supported", "jev_may_read_value": False}
