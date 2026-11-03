"""pc.studio — drive Roblox Studio on the owner's PC (owner table 03.11: «Bossman pc.* для Roblox Studio»).

Ported from the hand-run scratchpad scripts of the SwapMe sessions (ui.ps1, front.ps1, shot2.ps1, copyout.ps1,
relaunch.sh) so Bossman, not Claude, can: open a local build, bring Studio to the front, press Play / Stop, run a Lua
snippet in the command bar, copy the Output window to a file and take a screenshot.

Safety rules (they are why most actions refuse instead of guessing):
  * BUSY GUARD — every acting action refuses while a Studio process exists that this module did not start
    (`launched_by_bossman`). Another session or the owner may be play-testing; stealing focus or sending keys
    there would break their work. `status` and `screenshot` are read-only and always allowed.
  * Only local place files (.rbxl/.rbxlx) that exist; Studio from the newest local Versions folder.
  * `close` is graceful first (CloseMainWindow); a forced kill is only for a process this module launched — the
    place is a build artefact regenerated from source, nothing of the owner's is lost.
  * Command-bar / Output coordinates are layout-specific (1920x1080, default Studio layout in the 02.11 runs); they
    are arguments, not magic, and `run_lua` proves itself by a marker printed to Output and read back.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..guards import foreground as _fg

PROCESS = "RobloxStudioBeta"
PLACE_SUFFIXES = (".rbxl", ".rbxlx")
#: default layout of the 02.11 SwapMe runs (1920x1080): command bar, Output pane
COMMAND_BAR_XY = (900, 970)
OUTPUT_XY = (900, 900)
ACTIONS = ("status", "open", "front", "play", "stop", "run_lua", "copy_output", "screenshot", "close")
READ_ONLY = frozenset({"status", "screenshot"})
#: G1: the only window pc.studio may type into (title of a Studio window with a place open)
STUDIO_TITLE_RX = r"Roblox Studio|\.rbxlx?\b"

_PS_INPUT = r"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public class BX {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, int f, int e);
  [DllImport("user32.dll")] public static extern void mouse_event(int f,int dx,int dy,int d,int e);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
}
'@
function Click($x,$y){ [BX]::SetCursorPos($x,$y); Start-Sleep -m 150; [BX]::mouse_event(2,0,0,0,0); [BX]::mouse_event(4,0,0,0,0); Start-Sleep -m 300 }
function Shot($out){ Start-Sleep -m 500; $b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; $bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; $g=[System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size); $bmp.Save($out); $g.Dispose(); $bmp.Dispose() }
function Front($h){ $t=[IntPtr][Int64]$h; $fg=[BX]::GetForegroundWindow(); if ($fg -ne $t) { [BX]::ShowWindow($fg, 6) | Out-Null }; [BX]::keybd_event(0x12,0,0,0); [BX]::keybd_event(0x12,0,2,0); [BX]::ShowWindow($t, 3) | Out-Null; [BX]::SetForegroundWindow($t) | Out-Null; Start-Sleep -m 600; return ([BX]::GetForegroundWindow() -eq $t) }
"""


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_script(action: str, *, hwnd: int = 0, lua: str = "", out: str = "",
                 command_bar: tuple[int, int] = COMMAND_BAR_XY, output_xy: tuple[int, int] = OUTPUT_XY,
                 expect_rx: str = STUDIO_TITLE_RX) -> str:
    """The PowerShell for one input action (pure — unit-tested; values are single-quoted, never interpolated).

    G1 (03.11): every key batch is preceded by `AssertFg` — the foreground window must be THIS Studio hwnd and its
    title must match `expect_rx` (and not be a chat app), otherwise the script stops before the key is sent."""
    g = _fg.ps_assert(hwnd, expect_rx)
    body = {
        "front": f"'front=' + (Front {int(hwnd)})",
        "play": f"Front {int(hwnd)} | Out-Null; {g}; [System.Windows.Forms.SendKeys]::SendWait('{{F5}}'); 'sent F5'",
        "stop": f"Front {int(hwnd)} | Out-Null; {g}; [System.Windows.Forms.SendKeys]::SendWait('+{{F5}}'); 'sent Shift+F5'",
        "run_lua": (f"Front {int(hwnd)} | Out-Null; Click {int(command_bar[0])} {int(command_bar[1])}; "
                    f"Set-Clipboard -Value {_ps_quote(lua)}; "
                    f"{g}; [System.Windows.Forms.SendKeys]::SendWait('^a'); Start-Sleep -m 100; "
                    f"{g}; [System.Windows.Forms.SendKeys]::SendWait('^v'); Start-Sleep -m 200; "
                    f"{g}; [System.Windows.Forms.SendKeys]::SendWait('{{ENTER}}'); Start-Sleep -s 2; 'ran'"),
        "copy_output": (f"Front {int(hwnd)} | Out-Null; Click {int(output_xy[0])} {int(output_xy[1])}; "
                        f"{g}; [System.Windows.Forms.SendKeys]::SendWait('^a'); Start-Sleep -m 200; "
                        f"{g}; [System.Windows.Forms.SendKeys]::SendWait('^c'); Start-Sleep -m 400; "
                        f"Get-Clipboard | Out-File -Encoding utf8 {_ps_quote(out)}; 'copied'"),
        "screenshot": f"Shot {_ps_quote(out)}; 'saved'",
    }.get(action)
    if body is None:
        raise ValueError(f"no input script for action {action!r}")
    return _PS_INPUT + _fg.PS_GUARD + "\ntry { " + body + " } catch { Write-Output $_.Exception.Message }\n"


def make_marker() -> str:
    return "BOSSMAN_STUDIO_" + secrets.token_hex(4).upper()


def marker_lua(marker: str, snippet: str = "") -> str:
    """A command-bar snippet that proves it ran: prints the marker before and after the owner's snippet."""
    return f'print("{marker}:begin") {snippet} print("{marker}:end")'


def output_has_marker(text: str, marker: str) -> bool:
    return f"{marker}:begin" in text and f"{marker}:end" in text


def validate_place(path: str) -> Path:
    p = Path(path)
    if p.suffix.lower() not in PLACE_SUFFIXES:
        raise ValueError(f"not a Roblox place file: {p.name}")
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return p


def studio_exe(versions_dir: Path | None = None) -> Path | None:
    root = versions_dir or Path(os.environ.get("LOCALAPPDATA", "")) / "Roblox" / "Versions"
    found = [d / f"{PROCESS}.exe" for d in root.glob("version-*") if (d / f"{PROCESS}.exe").is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _run_ps(script: str, timeout: float = 60.0) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, timeout=timeout, check=False)
    return (r.stdout or "").strip() + (("\n" + r.stderr.strip()) if r.returncode and r.stderr else "")


def list_studio() -> list[dict[str, Any]]:
    out = _run_ps(f"Get-Process {PROCESS} -ErrorAction SilentlyContinue | "
                  "Select-Object Id,@{n='hwnd';e={[int64]$_.MainWindowHandle}},MainWindowTitle | ConvertTo-Json -Compress",
                  timeout=20)
    if not out.strip():
        return []
    data = json.loads(out.splitlines()[0])
    rows = data if isinstance(data, list) else [data]
    return [{"pid": int(r["Id"]), "hwnd": int(r.get("hwnd") or 0), "title": str(r.get("MainWindowTitle") or "")}
            for r in rows]


@dataclass
class StudioPC:
    """One Studio session owned by Bossman. `ps` / `lister` are injectable for tests."""
    state_file: Path
    ps: Callable[[str], str] = _run_ps
    lister: Callable[[], list[dict[str, Any]]] = list_studio
    receipts: list[dict[str, Any]] = field(default_factory=list)

    # ---- ownership: which Studio pid this module launched (survives CLI calls)
    def _owned(self) -> int | None:
        try:
            return int(json.loads(self.state_file.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _set_owned(self, pid: int | None) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"pid": pid, "at": time.time()}), encoding="utf-8")

    def _receipt(self, action: str, ok: bool, **extra: Any) -> dict[str, Any]:
        rec = {"action": action, "ok": ok, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **extra}
        self.receipts.append(rec)
        return rec

    def status(self) -> dict[str, Any]:
        procs = self.lister()
        owned = self._owned()
        foreign = [p for p in procs if p["pid"] != owned]
        return self._receipt("status", True, studio=procs, owned_pid=owned if any(p["pid"] == owned for p in procs) else None,
                             busy=bool(foreign),
                             busy_reason=("Studio is open in another session (pid "
                                          + ", ".join(str(p["pid"]) for p in foreign) + ")") if foreign else "")

    def _mine(self, action: str) -> dict[str, Any]:
        """The Studio window this module launched, or a refusal receipt (BUSY GUARD)."""
        st = self.status()
        if st["busy"]:
            raise PermissionError(f"{action}: refused — {st['busy_reason']}; pc.studio acts only on a Studio it launched")
        owned = [p for p in st["studio"] if p["pid"] == st["owned_pid"]]
        if not owned:
            raise RuntimeError(f"{action}: no Bossman-launched Studio is running; call open first")
        return owned[0]

    def open(self, place: str, *, wait_s: float = 45.0, exe: Path | None = None) -> dict[str, Any]:
        st = self.status()
        if st["studio"]:
            raise PermissionError("open: refused — Studio is already running "
                                  f"({st['busy_reason'] or 'a Bossman session; close it first'})")
        p = validate_place(place)
        exe = exe or studio_exe()
        if exe is None:
            raise FileNotFoundError("Roblox Studio is not installed (no Versions/*/RobloxStudioBeta.exe)")
        proc = subprocess.Popen([str(exe), str(p)])
        self._set_owned(proc.pid)
        deadline = time.time() + wait_s
        win = None
        while time.time() < deadline:
            time.sleep(3)
            win = next((w for w in self.lister() if w["hwnd"] and p.name.lower() in w["title"].lower()), None)
            if win:
                break
        if win:
            self._set_owned(win["pid"])
        return self._receipt("open", bool(win), place=str(p), pid=(win or {}).get("pid", proc.pid),
                             title=(win or {}).get("title", ""))

    def _input(self, action: str, **kw: Any) -> dict[str, Any]:
        win = self._mine(action)
        out = self.ps(build_script(action, hwnd=win["hwnd"], **kw))
        guard = _fg.refused(out)
        if guard:
            self._receipt(action, False, pid=win["pid"], refused=guard)
            raise PermissionError(f"{action}: {guard}")
        return self._receipt(action, "front=False" not in out, pid=win["pid"], output=out[-300:])

    def front(self) -> dict[str, Any]:
        return self._input("front")

    def play(self) -> dict[str, Any]:
        return self._input("play")

    def stop(self) -> dict[str, Any]:
        return self._input("stop")

    def run_lua(self, snippet: str, *, out_file: str, command_bar: tuple[int, int] = COMMAND_BAR_XY,
                output_xy: tuple[int, int] = OUTPUT_XY) -> dict[str, Any]:
        """Run `snippet` in the command bar and prove it: the marker printed before/after must come back in Output."""
        marker = make_marker()
        self._input("run_lua", lua=marker_lua(marker, snippet), command_bar=command_bar)
        self._input("copy_output", out=out_file, output_xy=output_xy)
        try:
            text = Path(out_file).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            text = ""
        return self._receipt("run_lua", output_has_marker(text, marker), marker=marker, out_file=out_file,
                             output_chars=len(text))

    def copy_output(self, out_file: str, *, output_xy: tuple[int, int] = OUTPUT_XY) -> dict[str, Any]:
        return self._input("copy_output", out=out_file, output_xy=output_xy)

    def screenshot(self, out_file: str) -> dict[str, Any]:
        self.ps(build_script("screenshot", out=out_file))
        return self._receipt("screenshot", Path(out_file).is_file(), out_file=out_file)

    def close(self, *, wait_s: float = 15.0) -> dict[str, Any]:
        win = self._mine("close")
        self.ps(f"(Get-Process -Id {int(win['pid'])}).CloseMainWindow() | Out-Null")
        deadline = time.time() + wait_s
        while time.time() < deadline and any(p["pid"] == win["pid"] for p in self.lister()):
            time.sleep(1)
        forced = False
        if any(p["pid"] == win["pid"] for p in self.lister()):
            # still open (a «save changes?» dialog): the place is a regenerated build artefact and the pid is ours
            self.ps(f"Stop-Process -Id {int(win['pid'])} -Force")
            forced = True
        self._set_owned(None)
        return self._receipt("close", True, pid=win["pid"], forced=forced)
