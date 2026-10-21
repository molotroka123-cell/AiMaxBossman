"""Shared contract for the tools/skills proof (independent verifier, 2026-10-22).

Five statuses only, never upgraded:

* PROVEN_EXECUTED - the real handler ran through the real engine/registry path with
  synthetic input and a correct, independently observed effect.
* PROVEN_GATED    - the correct behaviour of the tool IS to ask/refuse; the gate fired
  (approval created with the right preview / refusal recorded) and the handler was
  unreachable until approval; a canary side effect proves it.
* STRUCTURAL_ONLY - registered / parses / schema valid, but not executed (reason given).
* FAILED          - a proof ran and the observed behaviour was wrong.
* NOT_RUN         - not exercised here (reason given), nothing claimed.

Everything runs against in-repo code with SYNTHETIC inputs in temp directories. The
owner's chats, data and photos are never read; the live LOCALAPPDATA is never used.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROVEN_EXECUTED = "PROVEN_EXECUTED"
PROVEN_GATED = "PROVEN_GATED"
STRUCTURAL_ONLY = "STRUCTURAL_ONLY"
FAILED = "FAILED"
NOT_RUN = "NOT_RUN"
STATUSES = (PROVEN_EXECUTED, PROVEN_GATED, STRUCTURAL_ONLY, FAILED, NOT_RUN)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_REL = "tools/prove_tools_skills.py"
OUT_REL = "docs/audits/2026-10-22-tools-skills-proof"


def add_repo_paths() -> None:
    for rel in ("", "command-center", "bossman-core"):
        p = str((REPO_ROOT / rel).resolve())
        if p not in sys.path:
            sys.path.insert(0, p)


@dataclass
class Row:
    """One line of the proof matrix (a tool, a skill or an Argon claim)."""
    kind: str                      # tool | skill | claim
    id: str
    status: str
    group: str = ""
    category: str = ""
    permission: str = ""
    effect: str = ""               # default/granted effect: auto | ask | deny
    evidence: str = ""             # one observable line (test id / output line)
    caveat: str = ""               # what the proof does NOT cover
    live_note: str = ""            # relation to the owner's live runtime/config
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"bad status {self.status!r} for {self.id}")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def counts(rows: list[Row]) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for r in rows:
        out[r.status] += 1
    out["TOTAL"] = len(rows)
    return out


#: Environment that could point a tool at the owner's real data. Every key here is
#: forced to a value inside the sandbox root before any product code runs.
def isolate_env(root: Path) -> dict[str, str]:
    root = Path(root).resolve()
    (root / "lad").mkdir(parents=True, exist_ok=True)
    env = {
        "LOCALAPPDATA": str(root / "lad"),
        "BCC_DATA_DIR": str(root / "bcc-data"),
        "BCC_PROBE_CACHE": "off",
        "BOSSMAN_ALLOW_PAID_CLOUD": "1",
        "OBSIDIAN_VAULT": str(root / "plugin-vault"),
        "WAR_TERMINAL_DATA": str(root / "war-terminal-data"),
        "BOSSMAN_TELEGRAM_CONFIG": str(root / "tg" / "telegram-companion" / "config.json"),
        "BOSSMAN_COMPANION_CONFIG": str(root / "tg" / "telegram-companion" / "config.json"),
        "BCC_BROWSER_ALLOW_PRIVATE": "1",     # local fixture site on 127.0.0.1 only
    }
    os.environ.update(env)
    for stale in ("BOSSMAN_PIT_BLOCKED_IDS_FILE", "OPENROUTER_API_KEY", "GITHUB_TOKEN",
                  "TELEGRAM_BOT_TOKEN", "N8N_API_KEY", "GOOGLE_OAUTH", "SQL_PLUGIN_DSN",
                  "LSP_SERVERS"):
        os.environ.pop(stale, None)
    return env


def assert_isolated() -> None:
    """Refuse to run if LOCALAPPDATA is the real one (a test could touch live data)."""
    tmp = Path(tempfile.gettempdir()).resolve()
    lad = Path(os.environ.get("LOCALAPPDATA", "")).resolve()
    data = Path(os.environ.get("BCC_DATA_DIR", "")).resolve()
    ok = all(str(p).lower().startswith(str(tmp).lower()) for p in (lad, data))
    if not ok:
        raise RuntimeError(f"proof refuses to run: LOCALAPPDATA={lad} BCC_DATA_DIR={data} are not under {tmp}")


def new_sandbox(prefix: str = "proof-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def short(text: Any, n: int = 160) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
