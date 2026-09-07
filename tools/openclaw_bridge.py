#!/usr/bin/env python3
"""Owner-invoked OpenClaw bridge for Bossman.

OpenClaw itself is installed separately. This command never installs OpenClaw;
it only probes the runtime and gates curated skill installation.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "bossman-core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from bossman.openclaw_bridge import OpenClawBridge, OpenClawBridgeError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Bossman security gate for an optional OpenClaw runtime")
    parser.add_argument("--openclaw", help="explicit OpenClaw executable path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor")
    p_doctor.add_argument("--workspace", type=Path)

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("slug")

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("slug")
    p_verify.add_argument("--workspace", type=Path)

    p_install = sub.add_parser("install")
    p_install.add_argument("slug")
    p_install.add_argument("--workspace", required=True, type=Path)
    p_install.add_argument("--owner-approved", action="store_true")

    args = parser.parse_args()
    bridge = OpenClawBridge(executable=args.openclaw)
    try:
        if args.command == "doctor":
            probe = bridge.probe()
            out = {"probe": asdict(probe)}
            if probe.available:
                try:
                    out["skills"] = bridge.inventory(workspace=args.workspace)
                except OpenClawBridgeError as exc:
                    out["skills_error"] = str(exc)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if probe.available else 2
        if args.command == "plan":
            print(json.dumps(asdict(bridge.plan(args.slug)), ensure_ascii=False, indent=2))
            return 0
        if args.command == "verify":
            plan, payload = bridge.verify(args.slug, workspace=args.workspace)
            print(json.dumps({"plan": asdict(plan), "verification": payload}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "install":
            receipt = bridge.install(args.slug, workspace=args.workspace, owner_approved=args.owner_approved)
            print(json.dumps(asdict(receipt), ensure_ascii=False, indent=2))
            return 0
    except OpenClawBridgeError as exc:
        print(f"OPENCLAW_BRIDGE=REFUSED: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
