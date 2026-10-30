"""Freefield-style Studio Apps catalog: every app is one folder with ``app.json`` on open weights.

Each folder ``apps/bossman-mobile/bossmobile/studio_apps/<app_id>/app.json`` defines:
  - ``id`` (must match the folder name),
  - ``kind`` (``"video"`` or ``"photo"``),
  - ``model`` + ``fallback_models`` (open-weights models in Bossman's Direct Gen catalog),
  - ``camera_moves`` and ``tempos`` (for Cinema Studio Video workflows),
  - ``lab_example`` (filled into the form when the owner taps «Загрузить пример» / «Load this example»,
    with ``peak_vram_gb``, ``run_time_s``, and an honest ``measurement_kind``: ``"measured"`` only when
    Bossman's local timing history has >= 3 real runs for that model on this PC, otherwise ``"estimate"``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

APPS_DIR = Path(__file__).parent / "studio_apps"
APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
ORDER_PRIORITY = (
    "cinema-studio-video",
    "product-spin-360",
    "hand-reveal-macro",
    "cinema-film-still",
    "qwen-multi-ref-edit",
)


def _validate_app_dict(folder_name: str, data: dict[str, Any]) -> dict[str, Any]:
    app_id = str(data.get("id") or "").strip()
    if not app_id or app_id != folder_name or not APP_ID_RE.match(app_id):
        raise ValueError(f"studio app folder {folder_name!r} has mismatched or invalid id {app_id!r}")
    kind = str(data.get("kind") or "").strip()
    if kind not in ("video", "photo"):
        raise ValueError(f"studio app {app_id!r} has invalid kind {kind!r}")
    ex = data.get("lab_example")
    if not isinstance(ex, dict) or not str(ex.get("scene") or "").strip():
        raise ValueError(f"studio app {app_id!r} is missing lab_example.scene")
    return {
        "id": app_id,
        "folder": f"studio_apps/{app_id}/app.json",
        "title": str(data.get("title") or app_id),
        "title_ru": str(data.get("title_ru") or data.get("title") or app_id),
        "headline": str(data.get("headline") or ""),
        "headline_ru": str(data.get("headline_ru") or data.get("headline") or ""),
        "subtitle_ru": str(data.get("subtitle_ru") or ""),
        "kind": kind,
        "model": str(data.get("model") or ""),
        "fallback_models": [str(x) for x in (data.get("fallback_models") or []) if str(x).strip()],
        "open_weights": bool(data.get("open_weights", True)),
        "license": str(data.get("license") or "Apache-2.0"),
        "studio_preset": data.get("studio_preset"),
        "camera_moves": [
            {
                "id": str(c.get("id") or ""),
                "label": str(c.get("label") or c.get("id") or ""),
                "prompt_clause": str(c.get("prompt_clause") or ""),
            }
            for c in (data.get("camera_moves") or [])
            if isinstance(c, dict) and c.get("id")
        ],
        "tempos": [
            {
                "id": str(t.get("id") or ""),
                "label": str(t.get("label") or t.get("id") or ""),
                "prompt_clause": str(t.get("prompt_clause") or ""),
            }
            for t in (data.get("tempos") or [])
            if isinstance(t, dict) and t.get("id")
        ],
        "lab_example": {
            "title_ru": str(ex.get("title_ru") or ""),
            "scene": str(ex.get("scene") or "").strip(),
            "negative": str(ex.get("negative") or "").strip(),
            "camera_move": ex.get("camera_move"),
            "tempo": ex.get("tempo"),
            "aspect": str(ex.get("aspect") or "16:9"),
            "side": int(ex.get("side") or (480 if kind == "video" else 1024)),
            "duration_s": int(ex["duration_s"]) if ex.get("duration_s") is not None else None,
            "steps": int(ex.get("steps") or (20 if kind == "video" else 8)),
            "seed": int(ex.get("seed") or 42),
            "peak_vram_gb": round(float(ex.get("peak_vram_gb") or 8.0), 1),
            "run_time_s": int(ex.get("run_time_s") or 60),
        },
    }


def load_raw_apps(apps_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read and validate every ``<folder>/app.json`` from disk."""
    root = Path(apps_dir) if apps_dir is not None else APPS_DIR
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "app.json"
        if not manifest.is_file():
            continue
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            items.append(_validate_app_dict(child.name, raw))

    order_map = {aid: idx for idx, aid in enumerate(ORDER_PRIORITY)}
    items.sort(key=lambda a: (order_map.get(a["id"], 999), a["id"]))
    return items


def enrich_apps(
    apps: list[dict[str, Any]],
    *,
    photo_catalog: list[dict[str, Any]] | None = None,
    video_catalog: list[dict[str, Any]] | None = None,
    history: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach live Bossman model readiness and honest timing status (measured vs estimate)."""
    p_map = {str(m.get("id")): m for m in (photo_catalog or []) if isinstance(m, dict)}
    v_map = {str(m.get("id")): m for m in (video_catalog or []) if isinstance(m, dict)}
    hist = history or {}

    out: list[dict[str, Any]] = []
    for app in apps:
        catalog = v_map if app["kind"] == "video" else p_map
        candidates = [app["model"], *app["fallback_models"]]
        resolved_model = app["model"]
        model_spec = catalog.get(resolved_model)
        if not (model_spec and model_spec.get("available")):
            for cand in candidates:
                cspec = catalog.get(cand)
                if cspec and cspec.get("available"):
                    resolved_model = cand
                    model_spec = cspec
                    break

        timing = (model_spec or {}).get("timing") or hist.get(resolved_model) or {}
        is_measured = timing.get("kind") == "measured"
        ex = dict(app["lab_example"])
        ex["measurement_kind"] = "measured" if is_measured else "estimate"
        ex["measurement_label"] = (
            timing.get("label")
            if is_measured and timing.get("label")
            else "оценка по каталогу (на этом ПК ещё < 3 замеров)"
        )
        out.append({
            **app,
            "resolved_model": resolved_model,
            "model_label": (model_spec or {}).get("label") or resolved_model,
            "model_available": bool((model_spec or {}).get("available")),
            "model_ready": bool((model_spec or {}).get("ready", (model_spec or {}).get("available"))),
            "lab_example": ex,
        })
    return out


def compose_scene_prompt(
    scene: str,
    camera_move: str | None = None,
    tempo: str | None = None,
    app_spec: dict[str, Any] | None = None,
) -> str:
    """Combine the user's scene description with optional camera move and tempo clauses from ``app.json``."""
    base = str(scene or "").strip()
    if not base:
        return ""
    clauses: list[str] = []
    moves = (app_spec or {}).get("camera_moves") or []
    tempos = (app_spec or {}).get("tempos") or []
    if camera_move:
        m = next((c for c in moves if c.get("id") == camera_move), None)
        clause = str((m or {}).get("prompt_clause") or "").strip()
        if clause and clause.lower() not in base.lower():
            clauses.append(clause)
    if tempo:
        t = next((x for x in tempos if x.get("id") == tempo), None)
        clause = str((t or {}).get("prompt_clause") or "").strip()
        if clause and clause.lower() not in base.lower():
            clauses.append(clause)
    if not clauses:
        return base
    suffix = ". ".join(clauses)
    return f"{base.rstrip('. ')}. {suffix}."
