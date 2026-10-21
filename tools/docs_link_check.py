"""Tiny relative-link / cited-path checker for repository documentation.

Why this exists (owner request 22.10.2026, «Очисти мусор и ненужные утки ...
Обнови главнейшую документацию»): after documents are moved to
``docs/archive/`` or a new runbook is added, every relative Markdown link and
every backticked repo path in the touched documents must still resolve.

What it checks (read-only, no network, no git writes):

* inline Markdown links ``[text](target)`` and reference definitions
  ``[id]: target`` -- relative targets are resolved against the file's own
  directory (``/x`` targets against the repo root); ``http(s)``, ``mailto``,
  ``tel``, ``data`` and pure ``#anchor`` targets are ignored;
* optionally (``--backticks``) backticked tokens that look like a repo path
  (contain ``/`` and a known extension) are looked up against the repo root and
  the file's directory. Tokens with ``*``, ``<``, ``{``, ``$`` or ``%`` are
  treated as patterns and skipped. These are reported as WARN, never as FAIL,
  because a document may legitimately cite a path that exists only in another
  checkout.

Fenced code blocks and inline code are never parsed as links.

Usage::

    python tools/docs_link_check.py README.md AGENTS.md
    python tools/docs_link_check.py --changed-since origin/main
    python tools/docs_link_check.py --all --backticks

Exit code 0 = no broken Markdown links, 1 = at least one broken link.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[1]

_FENCE = re.compile(r"^(\s*)(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_INLINE_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_REF_DEF = re.compile(r"^\s{0,3}\[[^\]\n]+\]:\s+(<[^>\n]+>|\S+)")
_BACKTICK = re.compile(r"`([^`\n]+)`")
_PATH_EXT = (
    ".md", ".py", ".json", ".jsonl", ".ps1", ".yml", ".yaml", ".sh", ".txt",
    ".csv", ".html", ".js", ".toml", ".cmd", ".diff", ".patch", ".png", ".mp4",
)
_SKIP_SCHEMES = ("http://", "https://", "mailto:", "tel:", "data:", "ftp://", "git@")


def _strip_fences(text: str) -> list[tuple[int, str]]:
    """Return (line_no, line) pairs that are outside fenced code blocks."""
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for n, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if m:
            marker = m.group(2)
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is None:
            out.append((n, line))
    return out


def _clean_target(raw: str) -> str:
    t = raw.strip()
    if t.startswith("<") and t.endswith(">"):
        t = t[1:-1]
    t = t.split("#", 1)[0].split("?", 1)[0]
    return unquote(t)


def _resolve(doc: Path, target: str) -> Path:
    if target.startswith("/"):
        return REPO / target.lstrip("/")
    return (doc.parent / target).resolve()


def check_file(doc: Path, backticks: bool = False) -> tuple[list[str], list[str]]:
    """Return (broken_links, warnings) for one Markdown file."""
    text = doc.read_text(encoding="utf-8", errors="replace")
    rel = doc.resolve().relative_to(REPO).as_posix()
    broken: list[str] = []
    warns: list[str] = []
    for n, line in _strip_fences(text):
        no_code = _INLINE_CODE.sub("", line)
        targets = [m.group(1) for m in _INLINE_LINK.finditer(no_code)]
        m = _REF_DEF.match(no_code)
        if m:
            targets.append(m.group(1))
        for raw in targets:
            if raw.lower().startswith(_SKIP_SCHEMES) or raw.startswith("#"):
                continue
            target = _clean_target(raw)
            if not target:
                continue
            if not _resolve(doc, target).exists():
                broken.append(f"{rel}:{n}: broken link -> {raw}")
        if backticks:
            for tok in _BACKTICK.findall(line):
                tok = tok.strip()
                if "/" not in tok or not tok.lower().endswith(_PATH_EXT):
                    continue
                if any(c in tok for c in "*<>{}$%|:") or " " in tok or tok.startswith(("http", "~")):
                    continue
                cand = tok.lstrip("./") if not tok.startswith("../") else tok
                if not ((REPO / cand).exists() or _resolve(doc, tok).exists()):
                    warns.append(f"{rel}:{n}: cited path not in tree -> {tok}")
    return broken, warns


def _git_lines(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [x for x in out.splitlines() if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", help="Markdown files (repo-relative or absolute)")
    ap.add_argument("--all", action="store_true", help="check every tracked *.md")
    ap.add_argument("--changed-since", metavar="REF", help="check *.md changed vs REF (incl. worktree)")
    ap.add_argument("--backticks", action="store_true", help="also warn on backticked paths not in tree")
    ap.add_argument("--quiet", action="store_true", help="print only failures")
    args = ap.parse_args(argv)

    docs: list[Path] = []
    if args.all:
        docs = [REPO / f for f in _git_lines("ls-files", "*.md")]
    elif args.changed_since:
        names = set(_git_lines("diff", "--name-only", "--diff-filter=ACMR", args.changed_since))
        names |= set(_git_lines("ls-files", "--others", "--exclude-standard"))
        docs = [REPO / f for f in sorted(names) if f.endswith(".md")]
    for f in args.files:
        p = Path(f)
        docs.append(p if p.is_absolute() else REPO / p)
    docs = [d for d in dict.fromkeys(docs) if d.is_file()]
    if not docs:
        print("docs_link_check: no Markdown files to check", file=sys.stderr)
        return 0

    all_broken: list[str] = []
    all_warn: list[str] = []
    for d in docs:
        b, w = check_file(d, backticks=args.backticks)
        all_broken += b
        all_warn += w
    for line in all_broken:
        print("FAIL", line)
    for line in all_warn:
        print("WARN", line)
    if not args.quiet or all_broken:
        print(
            f"docs_link_check: {len(docs)} files, {len(all_broken)} broken links, "
            f"{len(all_warn)} warnings"
        )
    return 1 if all_broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
