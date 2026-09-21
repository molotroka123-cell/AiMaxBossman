"""Offline Mimik guide adapter, not an installed browser recorder or replay agent.

Reads supplied Markdown exports or the pinned upstream Snapshot shape. No file
access, browser, model, network, screenshots decoding, or action execution.
Block numbering follows westpoint-io/mimik core/guides/blocks.ts (MIT).
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
from urllib.parse import urlsplit

UPSTREAM_SHA = "905098ac005a7caad68949189a81e43ac8c327a1"
MAX_MARKDOWN = 2_097_152
MAX_STEPS = 80
MAX_DESCRIPTION = 2000
MAX_VISIBLE_TEXT = 40_000
MAX_OUTPUT = 240_000
BLOCKS = {"heading", "callout"}
VARIANTS = {"info", "warning", "error", "success", "custom"}
SNAPSHOT_KEYS = {"id", "guideId", "createdAt", "contentHash", "name", "title",
                 "stepIds", "steps", "screenshots"}
STEP_KEYS = {"id", "guideId", "index", "description", "action", "url", "timestamp",
             "screenshotId", "elementMeta", "inputValue", "descriptionSource",
             "aiPending", "blockType", "calloutVariant", "calloutColor"}
STEP_HEADING = re.compile(r"^## ([^:\d]{1,32}) ([0-9]{1,3}):\s*(.*)$")
URL = re.compile(r"https?://[^\s<>\[\]\"']+", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
CREDENTIAL = re.compile(
    r"(?i)\b(?:password|passwd|token|api[_ -]?key|secret|пароль|токен)"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)")
OPAQUE_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{16,})\b")
INPUT_WORDS = re.compile(r"(?i)^(?:type|enter|fill|paste|input|введите|ввести|вставить|"
                         r"вставьте|saisir|saisissez|eingeben|digite|pegar|escriba)\b")


class GuideInputError(ValueError):
    """A bounded, non-sensitive error message; never echo untrusted input."""


def _text(value: object, cap: int, *, empty: bool = False) -> str:
    if type(value) is not str or len(value) > cap:
        raise GuideInputError("invalid_or_oversize_text")
    if any((ord(c) < 32 and c not in "\n\r\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise GuideInputError("unsupported_text_characters")
    if not empty and not value.strip():
        raise GuideInputError("empty_required_text")
    return value.strip()


def _origin(value: str) -> str:
    """Display-only origin. Never preserve credentials, paths, query or fragment."""
    try:
        part = urlsplit(value)
        if part.scheme.lower() not in {"http", "https"} or not part.hostname:
            return "[URL omitted]"
        host = part.hostname.encode("idna").decode("ascii").lower()
        if not re.fullmatch(r"[a-z0-9.:-]+", host) or "\\" in value:
            return "[URL omitted]"
        port = part.port
        host = f"[{host}]" if ":" in host else host
        return f"{part.scheme.lower()}://{host}" + (f":{port}" if port is not None else "")
    except (ValueError, UnicodeError):
        return "[URL omitted]"


def _clean(value: str, *, input_step: bool = False) -> str:
    # This is a minimizer, NOT a promise to detect every secret/PII. Review is
    # required before sharing. Input values, selectors, screenshots never leave
    # this adapter. HTML and Markdown are escaped before rendering elsewhere.
    if input_step or INPUT_WORDS.match(value.strip()):
        return "Ввод значения — содержимое скрыто; проверить вручную."
    value = CREDENTIAL.sub("[credential omitted]", value)
    value = OPAQUE_KEY.sub("[key omitted]", value)
    value = EMAIL.sub("[email omitted]", value)
    value = URL.sub(lambda match: _origin(match.group()), value)
    value = " ".join(value.split())
    value = html.escape(value, quote=True)
    return re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", value)


def _blocks_from_snapshot(snapshot: object) -> tuple[str, list[dict], int]:
    if type(snapshot) is not dict or set(snapshot) - SNAPSHOT_KEYS:
        raise GuideInputError("snapshot_shape_or_unsupported_fields")
    title = _text(snapshot.get("title"), 200)
    ids, steps = snapshot.get("stepIds"), snapshot.get("steps")
    if (type(ids) is not list or type(steps) is not list
            or len(ids) != len(steps) or len(steps) > MAX_STEPS):
        raise GuideInputError("snapshot_step_count")
    ids = [_text(sid, 128) for sid in ids]
    if len(ids) != len(set(ids)):
        raise GuideInputError("duplicate_step_id")
    rows: dict[str, dict] = {}
    omitted = 0
    total = len(title)
    for step in steps:
        if type(step) is not dict or set(step) - STEP_KEYS:
            raise GuideInputError("step_shape_or_unsupported_fields")
        sid = _text(step.get("id"), 128)
        if sid in rows:
            raise GuideInputError("duplicate_step_id")
        desc = _text(step.get("description"), MAX_DESCRIPTION)
        index = step.get("index")
        if type(index) is not int or not 0 <= index <= MAX_STEPS:
            raise GuideInputError("invalid_step_index")
        timestamp = step.get("timestamp")
        if type(timestamp) not in (int, float) or not 0 <= timestamp <= 9_007_199_254_740_991 or not math.isfinite(timestamp):
            raise GuideInputError("invalid_step_timestamp")
        action = _text(step.get("action"), 80, empty=True)
        url = _text(step.get("url"), 2048, empty=True)
        # Optional in TS means absent, not null/unknown. A success callout is
        # a document block, NEVER a successful execution result.
        kind = step.get("blockType", "action")
        if "blockType" in step and (type(kind) is not str or kind not in BLOCKS):
            raise GuideInputError("unknown_block_type")
        variant = step.get("calloutVariant", "info")
        if type(variant) is not str or variant not in VARIANTS:
            raise GuideInputError("unknown_callout_variant")
        meta = step.get("elementMeta", {})
        if type(meta) is not dict:
            raise GuideInputError("element_metadata_shape")
        input_step = (action.lower() in {"input", "change", "type", "paste", "fill"}
                      or meta.get("inputType") == "password" or "inputValue" in step)
        total += len(desc) + len(url)
        if total > MAX_VISIBLE_TEXT:
            raise GuideInputError("visible_text_limit")
        omitted += int("screenshotId" in step)
        rows[sid] = {"kind": kind, "description": _clean(desc, input_step=input_step),
                     "action": _clean(action), "origin": _origin(url) if url else None,
                     "variant": variant if kind == "callout" else None}
    if set(ids) != set(rows):
        raise GuideInputError("missing_or_extra_step_id")
    # stepIds is upstream guide/snapshot order; never silently sort timestamps.
    return _clean(title), [rows[sid] for sid in ids], omitted


def _blocks_from_markdown(value: object) -> tuple[str, list[dict], int]:
    text = _text(value, MAX_MARKDOWN)
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines[0].startswith("# "):
        raise GuideInputError("mimik_markdown_title_required")
    title = _text(lines[0][2:], 200)
    blocks: list[dict] = []
    total = len(title)
    images = 0
    expected = 1
    body_started = False
    for line in lines[1:]:
        stripped = line.strip()
        if not body_started:
            if stripped == "---":
                body_started = True
            # The pinned export has a description and locale-specific metadata
            # before the separator. Neither proves capture or execution.
            continue
        if not stripped:
            continue
        if stripped.startswith("!["):
            images += 1
            continue  # no decode, download or model transfer, including data URLs
        total += len(line)
        if total > MAX_VISIBLE_TEXT:
            raise GuideInputError("visible_text_limit")
        match = STEP_HEADING.fullmatch(line)
        if match:
            number = int(match.group(2))
            if number != expected:
                raise GuideInputError("nonconsecutive_action_numbers")
            expected += 1
            description = _text(match.group(3), MAX_DESCRIPTION)
            blocks.append({"kind": "action", "description": _clean(description),
                           "action": "input" if INPUT_WORDS.match(description) else "recorded_instruction",
                           "origin": None, "variant": None})
        elif line.startswith("## "):
            blocks.append({"kind": "heading", "description": _clean(_text(line[3:], MAX_DESCRIPTION)),
                           "action": "", "origin": None, "variant": None})
        elif stripped.startswith(">"):
            # Callouts are explanatory document data, even if they say SUCCESS.
            note = _clean(_text(stripped[1:], MAX_DESCRIPTION, empty=True))
            if blocks and blocks[-1]["kind"] == "callout":
                blocks[-1]["description"] += " " + note
            else:
                blocks.append({"kind": "callout", "description": note, "action": "",
                               "origin": None, "variant": "info"})
        elif blocks and blocks[-1]["kind"] == "action":
            continuation = _text(line, MAX_DESCRIPTION)
            if blocks[-1]["action"] != "input":
                blocks[-1]["description"] += " " + _clean(continuation)
        else:
            raise GuideInputError("unsupported_markdown_structure")
        if len(blocks) > MAX_STEPS:
            raise GuideInputError("too_many_steps")
        if blocks and len(blocks[-1]["description"]) > MAX_DESCRIPTION * 6:
            raise GuideInputError("step_text_limit")
    if not body_started:
        raise GuideInputError("mimik_markdown_separator_required")
    return _clean(title), blocks, images


def prepare_guide(args: dict) -> dict:
    """Produce a manual replay checklist. Never execute a supplied instruction."""
    if type(args) is not dict or set(args) - {"markdown", "snapshot"} or len(args) != 1:
        raise GuideInputError("provide_exactly_one_markdown_or_snapshot")
    source = "mimik_markdown" if "markdown" in args else "mimik_snapshot"
    title, blocks, omitted = (_blocks_from_markdown(args["markdown"]) if "markdown" in args
                              else _blocks_from_snapshot(args["snapshot"]))
    actions = 0
    lines = [f"# {title}", "", "Статус выполнения: NOT_RUN. Это инструкция, не результат GUI-приёмки.", ""]
    for block in blocks:
        if block["kind"] == "action":
            actions += 1
            block["number"] = actions
            block["execution_status"] = "NOT_RUN"
            lines.append(f"- [ ] {actions:02d}. {block['description']}")
        elif block["kind"] == "heading":
            lines.extend(["", f"## {block['description']}"])
        else:
            lines.append(f"> Примечание: {block['description']}")
    checklist = "\n".join(lines) + "\n"
    report = {
        "status": "GUIDE_PREPARED" if actions else "NO_ACTION_STEPS",
        "source_kind": source, "upstream_sha": UPSTREAM_SHA, "title": title,
        "action_count": actions, "block_count": len(blocks) - actions,
        "steps": blocks, "checklist_markdown": checklist,
        "screenshots_omitted": omitted, "screenshots_verified": False,
        "capture_verified": False, "execution_status": "NOT_RUN",
        "network_requests": 0, "untrusted_content": True,
        "privacy_review_required": True,
        "limitations": ["text-only adapter; recorder/Guide Me are not installed",
                        "redaction is conservative, not a guarantee to remove all secrets",
                        "imported guide and success callouts do not prove an action occurred"],
        "sanitized_sha256": hashlib.sha256(checklist.encode("utf-8")).hexdigest(),
    }
    if len(json.dumps(report, ensure_ascii=False)) > MAX_OUTPUT:
        raise GuideInputError("output_limit")
    return report
