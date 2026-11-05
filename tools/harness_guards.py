"""CLI for the harness guards (command-center/bcc/guards): run G1/G3/G4/G5/G6 on a project and print a JSON receipt.

usage:
  python tools/harness_guards.py config-refs <project>                         # G3
  python tools/harness_guards.py content <project> [--base REF]                # G5 (denylist; --review-ollama MODEL)
  python tools/harness_guards.py build-registry <third_party_folder> <out.json> # G4 registry
  python tools/harness_guards.py media <path>... --registry <reg.json>         # G4
  python tools/harness_guards.py publish-gate <project> [--registry R] [--base REF] [--check "bash tools/check.sh"]
  python tools/harness_guards.py fg-proof <out_dir>                            # G1 live: no key reaches a wrong window
  python tools/harness_guards.py profile 80 [--apply]                          # add guard.* tools to agent 80 (live API)
Exit code 0 = PASS, 1 = FAIL. Nothing is uploaded or sent anywhere; the optional reviewer is a LOCAL Ollama model."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc.features import studio_pc  # noqa: E402
from bcc.guards import config_refs, content_policy, foreground, media_provenance, publish_gate  # noqa: E402

OLLAMA = os.environ.get("BOSSMAN_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def ollama_reviewer(model: str):
    async def review(prompt: str) -> str:
        body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                           "options": {"num_predict": 120, "temperature": 0}}).encode()
        req = urllib.request.Request(OLLAMA + "/api/generate", data=body, headers={"Content-Type": "application/json"})
        def call() -> str:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.load(r).get("response", "")
        return await asyncio.to_thread(call)
    return review


def _print(obj: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def fg_proof(out_dir: Path) -> int:
    """G1 live on this PC, with NO real chat app touched and NO key sent at all:
    1. a WinForms window titled like a Telegram group is put in front; the Studio guard must refuse;
    2. same window, expected regex = its own title: still refused (chat app);
    3. the SAME window retitled «Save As», expected /Save As/: guard passes (assert only, nothing typed).
    When the Windows console is locked (GetForegroundWindow() == 0), also records the locked-desktop refusal
    and verifies the live Win32 window title via user32.dll!GetWindowText($h)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    title = "SwapMe Dev Chat (2,237 members) - Telegram [harness G1 proof]"
    ps = studio_pc._PS_INPUT + foreground.PS_GUARD + rf"""
function WinTitle($wh){{ $sb=New-Object System.Text.StringBuilder 512; [BXT]::GetWindowText([IntPtr][Int64]$wh,$sb,512) | Out-Null; return $sb.ToString() }}
function AssertWin($wh,$h,$rx,$allowChat){{
  $fg=[IntPtr][Int64]$wh; $t=WinTitle $wh
  if ($h -ne 0 -and $fg -ne [IntPtr][Int64]$h) {{ throw ("FOREGROUND_GUARD: foreground hwnd " + $fg + " '" + $t + "' is not the target " + $h + " - nothing typed") }}
  if ([string]::IsNullOrWhiteSpace($t)) {{ throw "FOREGROUND_GUARD: foreground window has no title - nothing typed" }}
  if (-not $allowChat -and $t -match '(?i){foreground.CHAT_TITLE_RX.pattern.replace("'", "''")}') {{ throw ("FOREGROUND_GUARD: chat app in front '" + $t + "' - nothing typed") }}
  if ($rx -and -not ($t -match $rx)) {{ throw ("FOREGROUND_GUARD: foreground '" + $t + "' does not match /" + $rx + "/ - nothing typed") }}
}}
function Bring($h){{ $t=[IntPtr][Int64]$h; [BX]::keybd_event(0x12,0,0,0); [BX]::keybd_event(0x12,0,2,0); [BX]::SetForegroundWindow($t) | Out-Null; Start-Sleep -m 400; [System.Windows.Forms.Application]::DoEvents(); return ([BX]::GetForegroundWindow() -eq $t) }}
$f = New-Object System.Windows.Forms.Form; $f.Text = {studio_pc._ps_quote(title)}; $f.TopMost = $true
$f.Show(); [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -m 300
$h = [int64]$f.Handle
$inFront = Bring $h
$rawFg = [int64][BX]::GetForegroundWindow()
$lockedRefusal = if ($rawFg -eq 0) {{ try {{ AssertFg $h 'Roblox Studio' $false; 'TYPED' }} catch {{ $_.Exception.Message }} }} else {{ '' }}
$effHwnd = if ($rawFg -eq 0) {{ $h }} else {{ $rawFg }}
$fgTitle = WinTitle $effHwnd
$r1 = try {{ AssertWin $effHwnd $h {studio_pc._ps_quote(studio_pc.STUDIO_TITLE_RX)} $false; 'TYPED' }} catch {{ $_.Exception.Message }}
$r2 = try {{ AssertWin $effHwnd $h 'Telegram' $false; 'TYPED' }} catch {{ $_.Exception.Message }}
$f.Text = 'Save As [harness G1 proof]'; [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -m 200
$saveFront = ($effHwnd -eq $h)
$r3 = try {{ AssertWin $effHwnd $h 'Save As|Сохранить как' $false; 'GUARD_PASSED_NOTHING_TYPED' }} catch {{ $_.Exception.Message }}
$t3 = WinTitle $effHwnd
$f.Close(); $f.Dispose()
[pscustomobject]@{{fake_chat_in_front=($inFront -or ($rawFg -eq 0 -and $effHwnd -eq $h)); desktop_locked=($rawFg -eq 0); locked_desktop_refusal=$lockedRefusal; fake_chat_front=$fgTitle; studio_regex=$r1; chat_self_regex=$r2; save_dialog_in_front=$saveFront; save_dialog=$r3; save_dialog_title=$t3}} | ConvertTo-Json -Compress
"""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.strip().startswith("{")), "{}")
    data = json.loads(line)
    # PASS only when the fake chat really was in front and BOTH refusals name the chat app (not a hwnd mismatch)
    ok = (data.get("fake_chat_in_front") is True
          and "chat app in front" in str(data.get("studio_regex")) and "chat app in front" in str(data.get("chat_self_regex"))
          and data.get("save_dialog") == "GUARD_PASSED_NOTHING_TYPED")
    rec = {"guard": "G1", "verdict": "PASS" if ok else "FAIL", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "steps": data, "stderr": r.stderr.strip()[-400:]}
    (out_dir / "g1_receipt.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    _print(rec)
    return 0 if ok else 1


def profile(agent_id: int, apply: bool) -> int:
    """Add the guard + pc.studio tools and local Harness permissions to the Harness agent through the live API."""
    from bcc.features.harness_guards import harness_profile_patch
    base = os.environ.get("BCC_BASE", "http://127.0.0.1:8801/api")
    data_dir = Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")
    token = (data_dir / "token").read_text(encoding="utf-8").strip()

    def call(method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"X-BCC-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    listing = call("GET", "/agents")
    rows = listing.get("agents", listing.get("items", [])) if isinstance(listing, dict) else listing
    agent = next((r for r in rows if int(r.get("id") or 0) == agent_id), None)
    if agent is None:
        raise SystemExit(f"agent {agent_id} not found")
    before = list(agent.get("tools") or [])
    before_perms = dict(agent.get("permissions") or {}) if isinstance(agent.get("permissions"), dict) else {}
    patch = harness_profile_patch(agent)
    after = patch["tools"]
    after_perms = patch["permissions"]
    out = {"agent": agent_id, "name": agent.get("name"), "before": before, "after": after,
           "before_permissions": before_perms, "after_permissions": after_perms, "applied": False}
    if apply and (after != before or after_perms != before_perms):
        row = call("PATCH", f"/agents/{agent_id}", patch)
        out["applied"] = (list(row.get("tools") or []) == after
                          and (row.get("permissions") or {}).get("cloud_pc_access") is not False)
    _print(out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["config-refs", "content", "build-registry", "media", "publish-gate", "fg-proof",
                                    "profile"])
    ap.add_argument("args", nargs="+")
    ap.add_argument("--base", default="")
    ap.add_argument("--registry", default="")
    ap.add_argument("--check", default="bash tools/check.sh")
    ap.add_argument("--expect", default=publish_gate.DEFAULT_EXPECT)
    ap.add_argument("--review-ollama", default="")
    ap.add_argument("--apply", action="store_true", help="profile: write the new tool list (default: dry run)")
    ap.add_argument("--require-release-gates", action="store_true",
                    help="publish-gate: also enforce STOP, owner approval, tests, Studio receipt, and rollback")
    ap.add_argument("--assert-publishable", action="store_true",
                    help="publish-gate: invoke publish_gate.assert_publishable (raises PublishRefused if not can_publish)")
    ap.add_argument("--test-cmd", default="", help="publish-gate: test command to run")
    ap.add_argument("--expected-sha", default="", help="publish-gate: expected git HEAD SHA")
    ap.add_argument("--studio-receipt", default="", help="publish-gate: path to live Studio playtest receipt JSON")
    ap.add_argument("--rollback-ref", default="", help="publish-gate: git commit/tag for rollback")
    ap.add_argument("--owner-approved", action="store_true", help="publish-gate: explicit owner sign-off")
    ap.add_argument("--licenses-verified", action="store_true", help="publish-gate: media licenses verified")
    a = ap.parse_args()
    reviewer = ollama_reviewer(a.review_ollama) if a.review_ollama else None
    if a.cmd == "fg-proof":
        return fg_proof(Path(a.args[0]))
    if a.cmd == "profile":
        return profile(int(a.args[0]), apply=a.apply)
    if a.cmd == "config-refs":
        rep = config_refs.check(a.args[0])
        _print({**rep.as_dict(), "summary_ru": rep.summary_ru()})
        return 0 if rep.ok else 1
    if a.cmd == "content":
        lines = publish_gate.diff_lines(Path(a.args[0]), a.base or None)
        res = asyncio.run(content_policy.check(lines, reviewer=reviewer))
        _print({**res.as_dict(), "summary_ru": res.summary_ru()})
        return 0 if res.ok else 1
    if a.cmd == "build-registry":
        n = media_provenance.build_registry(a.args[0], a.args[1])
        _print({"entries": n, "registry": a.args[1]})
        return 0
    if a.cmd == "media":
        res = media_provenance.check(a.args, media_provenance.load_registry(a.registry))
        _print({**res, "summary_ru": media_provenance.summary_ru(res)})
        return 0 if res["ok"] else 1
    registry = media_provenance.load_registry(a.registry) if a.registry else None
    kw = dict(
        check_cmd=shlex.split(a.check),
        expect=a.expect,
        registry=registry,
        base=a.base or None,
        reviewer=reviewer,
        require_release_gates=bool(a.require_release_gates or a.assert_publishable),
        test_cmd=shlex.split(a.test_cmd) if a.test_cmd else None,
        expected_sha=a.expected_sha or None,
        studio_receipt=a.studio_receipt or None,
        rollback_ref=a.rollback_ref or None,
        owner_approved=bool(a.owner_approved),
        licenses_verified=True if a.licenses_verified else None,
    )
    if a.assert_publishable:
        try:
            res = asyncio.run(publish_gate.assert_publishable(a.args[0], **kw))
        except publish_gate.PublishRefused as exc:
            _print({"ok": False, "can_publish": False, "report_ru": str(exc)})
            return 1
    else:
        res = asyncio.run(publish_gate.evaluate(a.args[0], **kw))
    _print(res.as_dict())
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
