"""Registry of the competing «operator» implementations (architecture P0 step 2, 12.11.2026).

Bossman is ONE local PC operator: ``pc_operator``. ``qwen_operator``, ``pc_agent`` and ``agent_opencode`` stay mounted
(nothing deleted, Argon's WIP untouched) but are flagged ``legacy`` here and are never resolved on the pult operator
path. Config: ``bcc/operator_registry.json``; env ``BCC_OPERATOR_REGISTRY_FILE`` points to another file;
``BCC_OPERATOR_LEGACY_VISIBLE=a,b`` re-opens listed legacy names (rollback without editing the file).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_FILE = Path(__file__).with_name("operator_registry.json")
FALLBACK = {"entry": "pc_operator", "implementations": {"pc_operator": {"role": "entry", "legacy": False}}}


def load() -> dict:
    path = Path(os.environ.get("BCC_OPERATOR_REGISTRY_FILE") or DEFAULT_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = dict(FALLBACK)
    impls = {k: dict(v) for k, v in (data.get("implementations") or {}).items() if isinstance(v, dict)}
    visible = {x.strip() for x in os.environ.get("BCC_OPERATOR_LEGACY_VISIBLE", "").split(",") if x.strip()}
    for name in visible & set(impls):
        impls[name]["legacy"] = False
    entry = str(data.get("entry") or "pc_operator")
    impls.setdefault(entry, {"role": "entry", "legacy": False})
    impls[entry]["legacy"] = False                      # the entry can never be hidden
    return {"entry": entry, "implementations": impls}


def operator_entry() -> str:
    return load()["entry"]


def legacy_names() -> list[str]:
    return sorted(k for k, v in load()["implementations"].items() if v.get("legacy"))


def is_pult_resolvable(name: str) -> bool:
    """True only for the entry and for non-legacy implementations."""
    impls = load()["implementations"]
    return name in impls and not impls[name].get("legacy")
