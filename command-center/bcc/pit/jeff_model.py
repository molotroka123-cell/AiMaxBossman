"""Owner's choice of Jeff's LOCAL chat model (owner request 21.10: «переключи Jeff на локальную Gemma»).

    python -m bcc.pit.jeff_model set --data-dir <Jeff data dir> --model gemma4-31b-uncensored:latest
    python -m bcc.pit.jeff_model status --data-dir <Jeff data dir>
    python -m bcc.pit.jeff_model clear --data-dir <Jeff data dir>

HTTP (owner session / token only): ``GET|PUT|DELETE /api/jeff-model`` (bcc.features.jeff_kb).

Rules:
- the file ``<pit home>/jeff-model.json`` (next to jeff-settings.json) names ONE local model id; absent = the configured
  one (config.json ``local_models`` / ``BOSSMAN_PIT_LOCAL_MODELS``, default ``bossman-uncensored-chat:latest``) —
  the default is unchanged;
- it only replaces WHICH local model answers on the configured loopback URL (default Ollama
  http://127.0.0.1:11434/v1); it can never add a cloud route, change the URL or the cloud budget;
- a banned model family (Liquid/LFM) or a malformed id is refused on write and ignored on read;
- the runtime re-reads it on every turn (mtime cache), so a change applies to the next message without a restart.
  Jeff 2.0 helper modules that captured the model at start (model_guard probe) follow after the next restart.
- owner 21.10 (Jeff keeps answering during dense photo generation): helpers that unload Ollama models (direct-gen
  prompt assist) never unload Jeff's current model or a model named in ``BOSSMAN_OLLAMA_KEEP_MODELS``
  (``ollama_keep_models``). The resource governor (tools/resource_governor, stdlib only) mirrors ``effective_model``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILE_NAME = "jeff-model.json"
SCHEMA = "bossman.pit.jeff-model/1"
GEMMA_LOCAL = "gemma4-31b-uncensored:latest"
KEEP_ENV = "BOSSMAN_OLLAMA_KEEP_MODELS"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,119}$")


class ModelChoiceError(ValueError):
    pass


def model_path(data_dir: Path | str) -> Path:
    from .config import pit_home
    return pit_home(Path(data_dir)) / FILE_NAME


def validate_model_id(value: Any) -> str:
    from .model_policy import is_banned_model
    if not isinstance(value, str) or not _ID.match(value.strip()):
        raise ModelChoiceError("model id must be 1..120 chars: letters, digits, . _ : / -")
    model = value.strip()
    if is_banned_model(model):
        raise ModelChoiceError("banned model family (Liquid/LFM) is refused on every route")
    return model


_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], str]] = {}


def read_override(data_dir: Path | str | None) -> str:
    """The owner's local model id or '' (no file / unreadable / invalid). Never raises."""
    if not data_dir:
        return ""
    try:
        path = model_path(data_dir)
        stat = path.stat()
    except OSError:
        return ""
    sig = (stat.st_mtime_ns, stat.st_size)
    key = str(path)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == sig:
            return hit[1]
    try:
        if stat.st_size > 4096:
            raise ModelChoiceError("file too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        model = validate_model_id(data.get("local_model") if isinstance(data, dict) else None)
    except Exception:  # noqa: BLE001 - a broken file means "configured model", never a broken reply
        model = ""
    with _lock:
        _cache[key] = (sig, model)
    return model


def set_override(data_dir: Path | str, model: str) -> dict:
    chosen = validate_model_id(model)
    path = model_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"schema": SCHEMA, "local_model": chosen,
           "set_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return rec


def clear_override(data_dir: Path | str) -> bool:
    try:
        model_path(data_dir).unlink()
        return True
    except FileNotFoundError:
        return False


def status(data_dir: Path | str, configured: list[str] | tuple[str, ...] = ()) -> dict:
    """Owner-visible state: what the config names, what the owner chose, what answers."""
    from .config import DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_URL
    configured = [m for m in configured if isinstance(m, str) and m.strip()]
    override = read_override(data_dir)
    base = configured[0] if configured else DEFAULT_LOCAL_MODEL
    return {"schema": SCHEMA, "configured": base, "override": override or None,
            "effective": override or base, "default_url": DEFAULT_LOCAL_URL, "suggested": GEMMA_LOCAL}


def ollama_key(name: Any) -> str:
    """Comparable Ollama model name: trimmed, lower case, the implicit ``:latest`` tag made explicit."""
    key = str(name or "").strip().lower()
    if key and ":" not in key.rsplit("/", 1)[-1]:
        key += ":latest"
    return key


def effective_model(cc_data_dir: Path | str) -> str:
    """Jeff's local model right now, resolved like ``GET /api/jeff-model`` (bcc.features.jeff_kb): the PIT config may
    repoint Jeff's data dir; the owner's choice (jeff-model.json) wins over BOSSMAN_PIT_LOCAL_MODELS / config.json
    ``local_models``; neither = the default local model."""
    from .config import pit_home
    cc = Path(cc_data_dir)
    try:
        cfg = json.loads((pit_home(cc) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    raw = str(cfg.get("data_dir") or "").strip()
    env = [m.strip() for m in os.environ.get("BOSSMAN_PIT_LOCAL_MODELS", "").split(",") if m.strip()]
    listed = cfg.get("local_models") if isinstance(cfg.get("local_models"), list) else []
    return status(Path(raw) if raw else cc, env or [m for m in listed if isinstance(m, str)])["effective"]


def ollama_keep_models(cc_data_dir: Path | str | None) -> set[str]:
    """Ollama models a helper must never unload (owner 21.10): Jeff's current local model plus the comma list in
    ``BOSSMAN_OLLAMA_KEEP_MODELS``, as ``ollama_key`` values. Never raises (an unreadable Jeff config = env list only)."""
    keep = {ollama_key(m) for m in os.environ.get(KEEP_ENV, "").split(",") if m.strip()}
    if cc_data_dir:
        try:
            keep.add(ollama_key(effective_model(cc_data_dir)))
        except Exception:  # noqa: BLE001 - a broken Jeff config must not break a helper
            pass
    keep.discard("")
    return keep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bcc.pit.jeff_model")
    ap.add_argument("action", choices=("set", "status", "clear"))
    ap.add_argument("--data-dir", required=True, help="Jeff's data dir (the folder that holds pit-v1.7)")
    ap.add_argument("--model", default=GEMMA_LOCAL)
    a = ap.parse_args(argv)
    if a.action == "set":
        try:
            print(json.dumps(set_override(a.data_dir, a.model), ensure_ascii=False))
        except ModelChoiceError as exc:
            print(f"jeff_model: {exc}", file=sys.stderr)
            return 2
    elif a.action == "clear":
        print(json.dumps({"cleared": clear_override(a.data_dir)}))
    else:
        cfg: dict = {}
        try:
            from .config import pit_home
            cfg = json.loads((pit_home(Path(a.data_dir)) / "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        env = [m.strip() for m in os.environ.get("BOSSMAN_PIT_LOCAL_MODELS", "").split(",") if m.strip()]
        print(json.dumps(status(a.data_dir, env or list(cfg.get("local_models") or [])), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
