"""Bossman 1.6 game bootstrap plan.

Deterministic acquisition plan for BossBlocks-001. This module does not bypass
Bossman's existing browser/download, terminal, approval or verification paths.
It gives Jev a pinned, finite plan so the four-hour game run does not waste time
searching for basic tooling.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import Feature

BootstrapState = Literal[
    "DISCOVER", "DOWNLOAD", "VERIFY_HASH", "UNPACK", "VERSION_SMOKE",
    "EXPORT_SMOKE", "READY", "FALLBACK", "BLOCKED"
]

VOXEL_RELEASE = "v1.7"
VOXEL_RELEASE_PAGE = "https://github.com/Zylann/godot_voxel/releases/tag/v1.7"

WINDOWS_EDITOR = {
    "name": "godot.windows.editor.x86_64.exe.zip",
    "url": "https://github.com/Zylann/godot_voxel/releases/download/v1.7/godot.windows.editor.x86_64.exe.zip",
    "sha256": "e2292adb0b671508ab220fce5bcd052b1c20674a3385d89c6b38601e6e1f956b",
}
WINDOWS_TEMPLATE = {
    "name": "godot.windows.template_release.x86_64.exe.zip",
    "url": "https://github.com/Zylann/godot_voxel/releases/download/v1.7/godot.windows.template_release.x86_64.exe.zip",
    "sha256": "86fe371ed80948ccb4b4ac78a0e62e2917bc257890bdaa62b4f6e7aa402f0775",
}

OFFICIAL_GODOT_RELEASE = "4.7.2-stable"
OFFICIAL_RELEASE_PAGE = "https://github.com/godotengine/godot/releases/tag/4.7.2-stable"

DEFAULT_PLUGIN_BUDGET_MIN = 15
DEFAULT_BOOTSTRAP_BUDGET_MIN = 20


@dataclass(frozen=True)
class BootstrapStep:
    state: BootstrapState
    action: str
    verify: str
    telegram: str
    max_minutes: int
    approval: str = "AUTO_WITHIN_GAME_SCOPE"

    def public(self) -> dict:
        return asdict(self)


class BootstrapRequest(BaseModel):
    mission_id: str = Field(default="BOSSBLOCKS-001", min_length=1, max_length=128)
    platform: str = Field(default="win32", pattern=r"^(win32|linux|darwin)$")
    prefer_voxel_tools: bool = True
    already_has_compatible_engine: bool = False
    plugin_budget_minutes: int = Field(default=DEFAULT_PLUGIN_BUDGET_MIN, ge=1, le=60)
    bootstrap_budget_minutes: int = Field(default=DEFAULT_BOOTSTRAP_BUDGET_MIN, ge=5, le=60)


def bossblocks_bootstrap_plan(req: BootstrapRequest) -> dict:
    """Return a finite Jev-executable plan with explicit fallback.

    The caller must execute each action through the real Bossman tool path and
    attach actual receipts. A returned plan is not evidence that software was
    downloaded or installed.
    """
    if req.platform != "win32":
        return {
            "mission_id": req.mission_id,
            "status": "FALLBACK",
            "reason": "Pinned BossBlocks fast path is Windows-specific; discover an equivalent official artifact or use pure-Godot fallback.",
            "steps": [],
            "telegram_control": telegram_control(),
        }

    steps: list[BootstrapStep] = []
    if not req.already_has_compatible_engine and req.prefer_voxel_tools:
        steps += [
            BootstrapStep(
                "DOWNLOAD",
                f"browser.download {WINDOWS_EDITOR['url']}",
                f"downloaded bytes SHA-256 == {WINDOWS_EDITOR['sha256']}",
                "BOOTSTRAP 1/6: Voxel Tools editor downloaded; verifying hash.",
                4,
            ),
            BootstrapStep(
                "VERIFY_HASH",
                f"verify_sha256 {WINDOWS_EDITOR['name']} {WINDOWS_EDITOR['sha256']}",
                "digest matches pinned GitHub release asset",
                "BOOTSTRAP 2/6: editor digest verified.",
                1,
            ),
            BootstrapStep(
                "DOWNLOAD",
                f"browser.download {WINDOWS_TEMPLATE['url']}",
                f"downloaded bytes SHA-256 == {WINDOWS_TEMPLATE['sha256']}",
                "BOOTSTRAP 3/6: Windows export template downloaded; verifying hash.",
                3,
            ),
            BootstrapStep(
                "VERIFY_HASH",
                f"verify_sha256 {WINDOWS_TEMPLATE['name']} {WINDOWS_TEMPLATE['sha256']}",
                "digest matches pinned GitHub release asset",
                "BOOTSTRAP 4/6: export-template digest verified.",
                1,
            ),
            BootstrapStep(
                "UNPACK",
                "extract both ZIPs into mission-scoped Bossman tools/cache directory",
                "no traversal/symlink escape; executable/templates exist inside approved root",
                "BOOTSTRAP 5/6: portable engine unpacked; running smoke.",
                3,
            ),
            BootstrapStep(
                "VERSION_SMOKE",
                "run pinned Godot/Voxel executable --version and open an empty project",
                "reports Godot 4.7.2 custom build; process starts/exits cleanly",
                "BOOTSTRAP 6/6: engine smoke running.",
                4,
            ),
            BootstrapStep(
                "EXPORT_SMOKE",
                "export tiny empty project with pinned Windows template",
                "new executable exists, launches, and is bound to this mission artifact identity",
                "BOOTSTRAP READY: editor + export path verified.",
                4,
            ),
        ]

    return {
        "mission_id": req.mission_id,
        "status": "PLAN_ONLY",
        "primary": {
            "engine": "Godot 4.7.2 stable custom build + Voxel Tools 1.7",
            "release": VOXEL_RELEASE,
            "release_page": VOXEL_RELEASE_PAGE,
            "editor": WINDOWS_EDITOR,
            "template": WINDOWS_TEMPLATE,
        },
        "fallback": {
            "engine": "Official Godot 4.7.2 stable + bounded pure-Godot voxel implementation",
            "release_page": OFFICIAL_RELEASE_PAGE,
            "trigger": f"primary bootstrap/plugin path not READY inside {req.plugin_budget_minutes} minutes",
        },
        "hard_deadline_minutes": req.bootstrap_budget_minutes,
        "steps": [s.public() for s in steps],
        "telegram_control": telegram_control(),
        "truth_rule": "PLAN_ONLY until every executed step has Bossman task/evidence receipts.",
    }


def telegram_control() -> dict:
    return {
        "owner_surface": "Telegram Companion",
        "commands": {
            "/menu": "owner control panel",
            "/status": "Bossman/PC health",
            "/queue": "current queued work",
            "/approvals": "owner-only approvals when an action leaves the delegated scope",
            "/pause": "pause new work",
            "/resume": "owner-only resume",
            "/stop": "emergency stop",
            "/screen": "visual spot check",
            "/jev <request>": "Jev proposes one existing Telegram action; cannot approve/confirm/resume itself",
        },
        "milestone_only": True,
        "notify_on": [
            "BOOTSTRAP_STARTED", "DOWNLOAD_VERIFIED", "FALLBACK_SELECTED",
            "MILESTONE_GREEN", "ASTER_AT_RISK", "APPROVAL_REQUIRED",
            "FINAL_ACCEPT", "FINAL_REJECT"
        ],
        "no_spam": "Do not notify for every trivial edit/model call.",
    }


router = APIRouter()


@router.get("/game-bootstrap/status")
async def game_bootstrap_status():
    return {
        "schema": "bossman.v1.6.game-bootstrap/1",
        "voxel_release": VOXEL_RELEASE,
        "official_godot_release": OFFICIAL_GODOT_RELEASE,
        "telegram_control": telegram_control(),
        "execution": "PLAN_ONLY; use existing Bossman download/terminal/verifier paths",
    }


@router.post("/game-bootstrap/plan")
async def game_bootstrap_plan(body: BootstrapRequest):
    return bossblocks_bootstrap_plan(body)


FEATURE = Feature(name="game_bootstrap_v16", router=router)
