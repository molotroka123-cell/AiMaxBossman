"""ClickFix / dangerous-clipboard-command protection, implemented natively.

Threat: a web page ("Verify you are human") copies a hostile one-liner to the
clipboard and tells the user (or an agent that read the page) to open Win+R or a
terminal and paste it. Ideas taken from CryptoClipGuard, reimplemented here as
plain text checks with no third-party binary:

* a command a page wants executed is inspected BEFORE it can run
  (``dangerous_command_reason`` feeds the ``terminal.run`` hard deny);
* the lure itself is recognised and the command is removed from what the model
  reads (``scan_page_text``), so page text cannot smuggle an instruction;
* a crypto address that differs from the copied one only slightly is a
  clipboard hijack (``address_hijack``).

Pure functions: no I/O, no clipboard access, no network.
"""
from __future__ import annotations

import re

_I = re.I | re.S

# (pattern, reason). Order does not matter; first hit is reported.
_DANGEROUS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*?\s-(?:e|en|enc|enco|encod|encode|encoded|encodedc\w*)\s+[A-Za-z0-9+/=]{16,}", _I),
     "encoded PowerShell command (-enc)"),
    (re.compile(r"\b(?:iex|invoke-expression)\b", _I), "Invoke-Expression / iex"),
    (re.compile(r"\.downloadstring\s*\(", _I), "PowerShell download cradle"),
    (re.compile(r"\bmshta(?:\.exe)?\b[^\n]*(?:https?://|vbscript:|javascript:)", _I), "mshta remote/script payload"),
    (re.compile(r"\bcertutil(?:\.exe)?\b[^\n]*-urlcache", _I), "certutil download"),
    (re.compile(r"\bbitsadmin(?:\.exe)?\b[^\n]*/transfer", _I), "bitsadmin download"),
    (re.compile(r"\bregsvr32(?:\.exe)?\b[^\n]*(?:/i:\s*https?://|scrobj)", _I), "regsvr32 scriptlet"),
    (re.compile(r"\brundll32(?:\.exe)?\b[^\n]*(?:javascript:|mshtml)", _I), "rundll32 script payload"),
    (re.compile(r"\bcmd(?:\.exe)?\s*/[ck]\b[^\n]*\b(?:curl|wget|bitsadmin|certutil)\b[^\n]*https?://", _I),
     "cmd /c download-and-run"),
    (re.compile(r"\b(?:curl|wget|iwr|irm)\b[^\n|]*\|\s*(?:powershell|pwsh|cmd|iex)\b", _I),
     "download piped into a Windows shell"),
)

_HIDDEN_WINDOW_PS = re.compile(r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]*-w(?:indowstyle)?\s+hidden", _I)

# The social-engineering part: press Win+R / open Run / Terminal and paste.
_LURE = re.compile(
    r"(?:\bwin(?:dows)?\s*(?:key)?\s*\+\s*r\b|\bopen\s+(?:the\s+)?run\b|\bpress\s+ctrl\s*\+\s*v\b.{0,80}\benter\b"
    r"|\bpaste\b.{0,80}\b(?:run|terminal|powershell|command prompt)\b)", _I)
_LURE_CONTEXT = re.compile(
    r"(?:verify|verification|captcha|not a robot|human|fix (?:it|the error)|to continue|to fix)", _I)

_REDACTED = "[REDACTED: dangerous command removed by ClickFix guard]"


def dangerous_command_reason(text: str) -> str:
    """Non-empty reason when ``text`` looks like a ClickFix-style payload."""
    t = str(text or "")
    for rx, reason in _DANGEROUS:
        if rx.search(t):
            return reason
    if _HIDDEN_WINDOW_PS.search(t):
        return "hidden-window PowerShell"
    return ""


def scan_page_text(text: str) -> dict:
    """Inspect page text before a model reads it.

    Returns ``{"lure": bool, "reasons": [...], "text": sanitized}``. Every line
    that carries a dangerous command is replaced; a lure without a command is
    reported but the text is kept.
    """
    raw = str(text or "")
    reasons: list[str] = []
    lines: list[str] = []
    for line in raw.split("\n"):
        reason = dangerous_command_reason(line)
        if reason:
            reasons.append(reason)
            lines.append(_REDACTED)
        else:
            lines.append(line)
    social = bool(_LURE.search(raw)) and bool(_LURE_CONTEXT.search(raw))
    if social:
        reasons.append("page asks to paste a command into Run/terminal")
    return {"lure": bool(reasons), "reasons": reasons, "text": "\n".join(lines) if reasons else raw}


_ADDRESS = re.compile(r"^(?:0x[0-9a-fA-F]{40}|(?:bc1|tb1)[0-9a-z]{25,60}|[13][1-9A-HJ-NP-Za-km-z]{25,34}|T[1-9A-HJ-NP-Za-km-z]{33}"
                      r"|[1-9A-HJ-NP-Za-km-z]{32,44})$")


def address_hijack(copied: str, pasted: str) -> bool:
    """True when both are crypto-address-shaped, differ, and look alike.

    A clipboard hijacker swaps the address for one sharing the same prefix and
    suffix so a glance at the pasted value does not notice.
    """
    a, b = str(copied or "").strip(), str(pasted or "").strip()
    if a == b or not (_ADDRESS.match(a) and _ADDRESS.match(b)):
        return False
    n = 4
    return a[:n].lower() == b[:n].lower() and (a[-n:].lower() == b[-n:].lower() or
                                                sum(x != y for x, y in zip(a, b)) <= 3 and len(a) == len(b))
