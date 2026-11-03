"""G1 — foreground-window guard for keyboard typing / paste.

Incident 03.11.2026: a paste meant for a «Save as» dialog went into a 2,237-member Telegram group — the script
focused a window, did not check that it actually came to the front, and pasted into whatever was there.

Rule: right before every keystroke batch (type, paste, Enter) the title of the CURRENT foreground window must match
the regex the action expects; a chat app (Telegram, WhatsApp, Discord, Slack, ...) is refused unless the task
explicitly targets it. A mismatch aborts the action with a clear error; nothing is typed.

Two forms of the same rule:
  * `check_title` — pure Python (tools_computer last-look check, unit tests);
  * `PS_GUARD` + `ps_assert` — PowerShell snippet for scripts that send keys themselves (pc.studio).
"""
from __future__ import annotations

import re

#: chat / messenger windows — a stray paste there is a public message
CHAT_TITLE_RX = re.compile(
    r"telegram|whatsapp|discord|slack|viber|messenger|microsoft teams|\bteams\b|skype|wechat|"
    r"вконтакте|телеграм|ватсап", re.I)
GUARD_TAG = "FOREGROUND_GUARD"


def check_title(title: str | None, expect_rx: str | None = None, *, allow_chat: bool = False) -> str | None:
    """None when typing into a window with this title is allowed, else the refusal reason."""
    t = str(title or "")
    if not t.strip():
        return f"{GUARD_TAG}: окно переднего плана без заголовка — ввод не отправлен"
    if not allow_chat and CHAT_TITLE_RX.search(t):
        return (f"{GUARD_TAG}: впереди мессенджер «{t[:80]}» — ввод в чаты только когда задача явно на него "
                f"нацелена; ничего не введено")
    if expect_rx:
        try:
            ok = re.search(expect_rx, t, re.I) is not None
        except re.error as exc:
            return f"{GUARD_TAG}: неверное регулярное выражение окна ({exc}) — ввод не отправлен"
        if not ok:
            return (f"{GUARD_TAG}: впереди «{t[:80]}», а ожидалось окно /{expect_rx}/ — ввод не отправлен")
    return None


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


#: PowerShell: AssertFg <hwnd> <expect_rx> <allow_chat 0|1> — throws FOREGROUND_GUARD before any key is sent.
#: Needs the BX type (GetForegroundWindow) from the caller's preamble; adds GetWindowText itself.
PS_GUARD = r"""
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public class BXT { [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }
'@
function FgTitle(){ $sb=New-Object System.Text.StringBuilder 512; [BXT]::GetWindowText([BXT]::GetForegroundWindow(),$sb,512) | Out-Null; return $sb.ToString() }
function AssertFg($h,$rx,$allowChat){
  $fg=[BXT]::GetForegroundWindow(); $t=FgTitle
  if ($h -ne 0 -and $fg -ne [IntPtr][Int64]$h) { throw ("FOREGROUND_GUARD: foreground hwnd " + $fg + " '" + $t + "' is not the target " + $h + " - nothing typed") }
  if ([string]::IsNullOrWhiteSpace($t)) { throw "FOREGROUND_GUARD: foreground window has no title - nothing typed" }
  if (-not $allowChat -and $t -match '(?i)""" + CHAT_TITLE_RX.pattern.replace("'", "''") + r"""') { throw ("FOREGROUND_GUARD: chat app in front '" + $t + "' - nothing typed") }
  if ($rx -and -not ($t -match $rx)) { throw ("FOREGROUND_GUARD: foreground '" + $t + "' does not match /" + $rx + "/ - nothing typed") }
}
"""


def ps_assert(hwnd: int, expect_rx: str, *, allow_chat: bool = False) -> str:
    """One PowerShell statement asserting the foreground window right before a key batch."""
    return f"AssertFg {int(hwnd)} {_ps_quote(expect_rx)} ${'true' if allow_chat else 'false'}"


def refused(output: str) -> str | None:
    """The guard's refusal line from a script's output, if it fired."""
    for line in str(output or "").splitlines():
        if GUARD_TAG in line:
            return line.strip()[:300]
    return None
