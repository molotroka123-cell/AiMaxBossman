"""Category rules: extension first, then keyword scoring. Rules live in an owner-editable JSON file."""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_RULES_FILE = Path(__file__).with_name("category_rules.default.json")
OWNER_RULES_NAME = "category_rules.json"
_SAFE_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_HITS_PER_KEYWORD = 5


class RulesError(ValueError):
    pass


@dataclass(frozen=True)
class Decision:
    category: str
    score: int
    matched: tuple[str, ...]
    by: str            # "extension" | "keywords" | "default"


def valid_category(name: str) -> bool:
    return bool(_SAFE_CATEGORY.match(name or ""))


def validate(rules: Any) -> dict:
    if not isinstance(rules, dict) or not isinstance(rules.get("categories"), list):
        raise RulesError("rules must be an object with a 'categories' list")
    default = str(rules.get("default") or "other")
    if not valid_category(default):
        raise RulesError(f"bad default category {default!r}")
    for item in rules["categories"]:
        if not isinstance(item, dict) or not valid_category(str(item.get("name") or "")):
            raise RulesError(f"bad category entry {item!r}: name must match {_SAFE_CATEGORY.pattern}")
        for key in ("keywords", "extensions"):
            value = item.get(key, [])
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise RulesError(f"category {item['name']}: {key} must be a list of strings")
    return rules


def rules_path(library_root: Path) -> Path:
    return Path(library_root) / OWNER_RULES_NAME


def load_rules(library_root: Path) -> tuple[dict, Path]:
    """The owner's copy in the library root; created from the shipped default on first use.

    A broken owner file is an error the owner must see, not a silent fallback:
    silently categorising with other rules would scatter the library.
    """
    path = rules_path(library_root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_RULES_FILE, path)
    try:
        rules = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RulesError(f"cannot read {path}: {exc}") from exc
    return validate(rules), path


def _pattern(keyword: str) -> re.Pattern:
    # Start-of-word match: stems work ("договор" -> "договора"), substrings inside
    # other words do not ("api" must not fire on "capital").
    return re.compile(r"(?<!\w)" + re.escape(keyword.casefold()))


def categorize(rules: dict, *, name: str, title: str, text: str, ext: str) -> Decision:
    ext = (ext or "").lower()
    for item in rules["categories"]:
        if ext and ext in [e.lower() for e in item.get("extensions", [])]:
            return Decision(item["name"], 0, (ext,), "extension")
    head = f"{name}\n{title}".casefold()
    body = (text or "")[: int(rules.get("text_chars", 20000))].casefold()
    weight = int(rules.get("name_weight", 3))
    best: Decision | None = None
    for item in rules["categories"]:
        score, matched = 0, []
        for keyword in item.get("keywords", []):
            if not keyword.strip():
                continue
            rx = _pattern(keyword)
            in_head = len(rx.findall(head))
            in_body = min(len(rx.findall(body)), MAX_HITS_PER_KEYWORD)
            if in_head or in_body:
                score += weight * min(in_head, MAX_HITS_PER_KEYWORD) + in_body
                matched.append(keyword)
        if score and (best is None or score > best.score):
            best = Decision(item["name"], score, tuple(matched), "keywords")
    if best is not None and best.score >= int(rules.get("min_score", 2)):
        return best
    return Decision(str(rules.get("default") or "other"), best.score if best else 0,
                    best.matched if best else (), "default")
