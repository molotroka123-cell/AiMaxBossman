"""Jeff reply formatting for Telegram: beautiful, never brittle.

The model writes free-form Markdown-ish text. Telegram's parse modes reject
the WHOLE message on one bad entity, so formatting must never be able to lose
a reply:

* everything is HTML-escaped first;
* only a small whitelist of entities is produced
  (<b>, <code>, <pre>, <blockquote>, <tg-spoiler>);
* `to_telegram_html` is applied PER PART after `split_message`, so a split can
  never cut an entity in half;
* the sender retries the plain text once if Telegram still refuses to parse.
"""
from __future__ import annotations

import html
import re

_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
_CODE_RE = re.compile(r"`([^`\n]+)`")
_SPOILER_RE = re.compile(r"\|\|(?=\S)(.+?)(?<=\S)\|\|", re.DOTALL)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$")


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def _blockquotes(escaped: str) -> str:
    """Consecutive "> …" lines become one <blockquote> (Telegram quote bar)."""
    lines = escaped.split("\n")
    out: list[str] = []
    quote: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("&gt; "):
            quote.append(stripped[5:].rstrip())
        elif stripped == "&gt;":
            quote.append("")
        else:
            if quote:
                out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
                quote = []
            out.append(line)
    if quote:
        out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
    return "\n".join(out)


def to_telegram_html(text: str) -> str:
    """Escape everything, then apply the safe whitelist of entities."""
    raw = str(text or "")
    parts: list[str] = []
    pos = 0
    for match in _FENCE_RE.finditer(raw):
        parts.append(_inline(_escape(_readable_blocks(raw[pos:match.start()]))))
        parts.append("<pre>" + _escape(match.group(1).rstrip("\n")) + "</pre>")
        pos = match.end()
    parts.append(_inline(_escape(_readable_blocks(raw[pos:]))))
    return "".join(parts)


def _readable_blocks(raw: str) -> str:
    """Turn Markdown blocks Telegram cannot display into readable text."""
    lines = raw.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING_RE.match(line)
        if heading:
            out.append("**" + heading.group(1) + "**")
            index += 1
            continue
        if (index + 1 < len(lines) and "|" in line
                and _TABLE_SEPARATOR_RE.match(lines[index + 1])):
            headers = _table_cells(line)
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                cells = _table_cells(lines[index])
                if cells:
                    title = cells[0]
                    details = [f"{headers[n]}: {cell}" for n, cell in enumerate(cells[1:], 1)
                               if n < len(headers) and cell]
                    out.append("• **" + title + "**" + (" — " + "; ".join(details) if details else ""))
                index += 1
            continue
        out.append(line)
        index += 1
    return "\n".join(out)


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _inline(escaped: str) -> str:
    escaped = _blockquotes(escaped)
    escaped = _CODE_RE.sub(r"<code>\1</code>", escaped)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", escaped)
    return escaped
