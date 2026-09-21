"""Deterministic, bounded presets for the existing preview/export compiler.

Manual BPM and offset are an owner-supplied timing grid, NOT audio analysis.
No file, provider, model, network, random download or executable expression input.
"""
from __future__ import annotations

from copy import deepcopy
import math

VERSION = "viral-vfx-1"
# Presets are our compositions, not claims of proprietary Higgsfield effects.
ATOMS = {
    "punch_zoom": "Punch zoom", "beat_shake": "Beat shake", "micro_jitter": "Micro jitter",
    "rgb_split": "RGB split", "chromatic_soft": "Chromatic soft", "glitch_burst": "Glitch burst",
    "motion_trail": "Motion trail", "soft_focus": "Soft focus", "focus_hit": "Focus hit",
    "film_grain": "Film grain", "vhs": "VHS texture", "scanlines": "Scanlines",
    "sharpen_hit": "Sharpen hit", "contrast_punch": "Contrast punch", "exposure_pulse": "Exposure pulse",
    "vignette": "Vignette", "dark_crush": "Dark crush", "neon_cyan": "Neon cyan",
    "neon_magenta": "Neon magenta", "warm_film": "Warm film", "cold_steel": "Cold steel",
    "bleach_bypass": "Bleach bypass", "monochrome": "Monochrome", "mirror": "Mirror",
}
STYLES = {
    "phonk_hard": ("PHONK HARD", ["punch_zoom", "rgb_split", "contrast_punch", "dark_crush"]),
    "phonk_dark": ("PHONK DARK", ["beat_shake", "dark_crush", "vignette", "film_grain"]),
    "drift": ("DRIFT", ["beat_shake", "glitch_burst", "cold_steel"]),
    "luxury_fast": ("LUXURY FAST", ["punch_zoom", "warm_film", "vignette"]),
    "crypto_hype": ("CRYPTO HYPE", ["punch_zoom", "neon_cyan", "sharpen_hit"]),
    "cinematic_viral": ("CINEMATIC VIRAL", ["micro_jitter", "warm_film", "film_grain"]),
}
PARAMETERS = {"preset", "intensity", "bpm", "offset"}


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError("VFX requires finite numeric parameters in the declared range")
    return float(value)


def validate_params(params):
    if type(params) is not dict or set(params) - PARAMETERS:
        raise ValueError("Unsupported VFX parameters; expressions and URLs are not accepted")
    preset = params.get("preset")
    if type(preset) is not str or preset not in ATOMS and preset not in STYLES:
        raise ValueError("Unknown VFX preset")
    return {"preset": preset, "intensity": _number(params.get("intensity", .5), 0, 1),
            "bpm": _number(params.get("bpm", 140), 60, 200),
            "offset": _number(params.get("offset", 0), -60, 60)}


def _f(value):
    return format(value, ".9g")


def compile_filter(params, *, start_seconds=0):
    """Filter fragment only. Does not change FPS, timestamps, duration or audio.

    All FFmpeg syntax is generated from bounded numeric values and fixed atoms.
    Pulse uses sequence time, so independently cut clips share the same grid.
    No full-screen flashing/strobe preset is supplied.
    """
    p = validate_params(params)
    start = _number(start_seconds, 0, 7 * 86400)
    amount = p["intensity"]
    if amount == 0:
        return "null"
    period = 60 / p["bpm"]
    phase = (start - p["offset"]) % period
    # Smooth positive envelope with a bounded rise/fall, not a white/black flash.
    pulse = f"pow((1+cos(2*PI*(T+{_f(phase)})/{_f(period)}))/2,6)"
    hit = f"gt(pow((1+cos(2*PI*(t+{_f(phase)})/{_f(period)}))/2,6),0.45)"
    ids = STYLES[p["preset"]][1] if p["preset"] in STYLES else [p["preset"]]
    filters = []
    for kind in ids:
        if kind in {"punch_zoom", "beat_shake", "micro_jitter"}:
            zoom = f"(1+{_f(.12*amount)}*({pulse}))" if kind == "punch_zoom" else f"(1+{_f(.04*amount)})"
            if kind == "punch_zoom":
                dx = dy = "0"
            else:
                a = amount * (.012 if kind == "beat_shake" else .003)
                env = pulse if kind == "beat_shake" else "1"
                dx, dy = f"W*{_f(a)}*sin(T*71)*({env})", f"H*{_f(a)}*cos(T*59)*({env})"
            # Each plane/thread starts every row at X=0. Cache frame-constant
            # trigonometry once per row, not once per pixel/channel. Registers
            # are private to each FFmpeg expression evaluation context.
            init = f"if(eq(X,0),st(0,{zoom})+st(1,{dx})+st(2,{dy}),0);"
            x = "clip((X-W/2)/ld(0)+W/2+ld(1),0,W-1)"
            y = "clip((Y-H/2)/ld(0)+H/2+ld(2),0,H-1)"
            filters.append("geq=" + ":".join(f"{c}='{init}{c}({x},{y})'" for c in "rgb") + f":a='{init}alpha({x},{y})'")
        elif kind in {"rgb_split", "chromatic_soft", "glitch_burst"}:
            shift = max(1, round(amount * (2 if kind == "chromatic_soft" else 6)))
            enable = f":enable='{hit}'" if kind == "glitch_burst" else ""
            filters.append(f"rgbashift=rh={shift}:bh={-shift}:edge=smear{enable}")
        elif kind == "motion_trail":
            filters.append(f"tmix=frames=3:weights='1 {_f(amount*.45)} {_f(amount*.2)}'")
        elif kind in {"soft_focus", "focus_hit"}:
            filters.append(f"gblur=sigma={_f(.1+amount*2)}" + (f":enable='{hit}'" if kind == "focus_hit" else ""))
        elif kind in {"film_grain", "vhs"}:
            strength = max(1,round(amount*12))
            filters.append(f"format=gbrap,noise=c0s={strength}:c1s={strength}:c2s={strength}:c3s=0:allf=t+u:all_seed=42,format=rgba")
            if kind == "vhs":
                filters.extend([f"hue=s={_f(1-.5*amount)}", f"drawgrid=w=iw:h=4:t=1:c=black@{_f(.18*amount)}"])
        elif kind == "scanlines":
            filters.append(f"drawgrid=w=iw:h=4:t=1:c=black@{_f(.25*amount)}")
        elif kind == "sharpen_hit":
            filters.append(f"unsharp=5:5:{_f(amount*1.4)}:5:5:0:enable='{hit}'")
        elif kind == "contrast_punch":
            filters.append(f"eq=contrast='1+{_f(amount*.45)}*({pulse.replace('T','t')})':eval=frame")
        elif kind == "exposure_pulse":
            filters.append(f"eq=brightness='{_f(amount*.035)}*({pulse.replace('T','t')})':eval=frame")
        elif kind == "vignette":
            # Native vignette discards alpha on some FFmpeg builds. Preserve
            # transparent layers explicitly while shading only colour planes.
            shade = f"(1-{_f(.6*amount)}*clip(pow((X-W/2)/(W/2),2)+pow((Y-H/2)/(H/2),2),0,1))"
            filters.append("geq=" + ":".join(f"{c}='{c}(X,Y)*{shade}'" for c in "rgb") + ":a='alpha(X,Y)'")
        elif kind == "dark_crush":
            filters.append(f"eq=brightness={_f(-.025*amount)}:contrast={_f(1+.28*amount)}:saturation={_f(1-.25*amount)}")
        elif kind in {"neon_cyan", "neon_magenta", "warm_film", "cold_steel"}:
            gains = {"neon_cyan": (-.1,.08,.12), "neon_magenta": (.12,-.05,.12),
                     "warm_film": (.1,.03,-.06), "cold_steel": (-.04,.02,.1)}[kind]
            filters.append("colorbalance=" + ":".join(f"{k}={_f(v*amount)}" for k,v in zip(("rm","gm","bm"),gains)))
        elif kind == "bleach_bypass":
            filters.append(f"eq=contrast={_f(1+.4*amount)}:saturation={_f(1-.7*amount)}")
        elif kind == "monochrome":
            filters.append(f"hue=s={_f(1-amount)}")
        elif kind == "mirror":
            filters.append("hflip")  # binary effect; any positive intensity enables it
    return ",".join(filters)


def catalog():
    presets = [{"id": k, "name": v, "category": "effect"} for k,v in ATOMS.items()]
    presets += [{"id": k, "name": v[0], "category": "style", "effects": list(v[1])} for k,v in STYLES.items()]
    return {"version": VERSION, "presets": presets, "timing": "MANUAL_BPM_OFFSET",
            "automatic_beat_detection": False, "generation": False,
            "bounds": {"intensity": [0,1], "bpm": [60,200], "offset": [-60,60]},
            "warning": "Motion and brightness changes: preview at low intensity. No virality or photosensitivity-safety guarantee.",
            "higgsfield_status": "DRAFT_RECIPES_ONLY; use configured Studio provider with normal approval/budget gates",
            "shots": [{"start_s": n*5, "duration_s": 5, "prompt": prompt} for n,prompt in enumerate((
                "Vertical 9:16. Establish the owner-supplied subject with a deliberate slow push-in. Preserve identity and brand. Strong first-second composition, no text or logos invented.",
                "Vertical 9:16. Same subject and lighting continuity. Controlled orbit into a whip-pan, clear silhouette, room for beat-synced editing. No flashing lights.",
                "Vertical 9:16. Hero close-up and a decisive camera settle, final composition echoing the opening for an editable loop. Reserve space for the owner's real logo."))]}
