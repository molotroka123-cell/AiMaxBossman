"""tools/lora_dataset.py — Bossman LoRA/QLoRA dataset "database" (stdlib only + the harvester import).

    python tools/lora_dataset.py build [--data-dir <BCC data>] [--root C:/Users/asd/Bossman/train-snapshots]
                                       [--extra-sources lessons,skills,rules,owner_docs,tools|none] [--repo-root .]
    python tools/lora_dataset.py gate <dataset_id> --lock requirements-train.lock [--require-offline --for-weights]
    python tools/lora_dataset.py list | verify <dataset_id>

Drafted 23.10 by the GLM 5.3 Flash cloud worker (Bossman task 199, from a spec), finished and tested by Claude.

Pipeline (the local training loop, WEIGHTS_UNCHANGED until the owner explicitly gates a dataset):

  1. harvest    reuse the collectors/filters of bcc.pit.master_parser.training_harvester
                (_collect_jeff_kb, _collect_training_drops, _collect_web_learn_candidates,
                _collect_war_terminal_and_news; injection drop via _is_injected; redaction via
                _clean_and_redact; dedup by sha256 of casefolded text) — WITHOUT the 16k packing.
                Rows shorter than --min-chars are dropped; rows are capped by --token-budget.
                PLUS owner-provenance REPOSITORY sources (--extra-sources, default all ON):
                  lessons     docs/owner/bossman_lessons_*.json + bossman_critical_errors.json entries
                              -> "Как исправить: <title>?" / "Как распознать ...?" pairs
                  skills      owner SKILL.md files (.agents/skills, .claude/skills, bossman-core/skills);
                              skills adapted from third parties (frontmatter `upstream:` or a foreign
                              provenance.json) and command-center/bcc/skills_catalog are NOT used
                  rules       AGENTS.md / CLAUDE.md sections -> "Какое правило ... про «X»?"
                  owner_docs  docs/owner/*.md runbooks (top level only), chunked by heading
                  tools       every literal ToolSpec(name=, description=) under command-center/bcc,
                              read statically with ast (the app is never started)
                Repository text never comes from %LOCALAPPDATA%, chats, photos, voice, keys or any
                path with a private/personal/keys/vault segment. Every row (old and new sources)
                additionally drops on the gate's own INJECTION / PERSONAL_HINTS / SECRET patterns
                and e-mail addresses, so a built snapshot cannot fail the gate on those checks.
  2. snapshot   write an immutable, content-addressed dataset snapshot under --root:
                corpus.jsonl (id/source/topic/text/sha256/tokens), train.jsonl + holdout.jsonl
                (chat SFT rows), cpt.jsonl (continued-pretraining rows, train ids only) and a
                manifest.json listing every data file with sha256 + bytes (schema
                "bossman.lora-dataset/1"). Every file is made read-only (stat.S_IREAD).
  3. gate       run tools/training_secure_gate.py as a subprocess over the snapshot; the gate
                report is written OUTSIDE the dataset dir (<root>/<id>.gate.json) so the
                manifest stays exact; verdict + report sha256 are recorded in registry.json.
  4. train      (out of scope here) LoRA/QLoRA fine-tune locally on train.jsonl + cpt.jsonl,
                offline, against the hash-pinned requirements.lock the gate validated.
  5. eval       score the fine-tuned model on holdout.jsonl (rows never seen in training).

Registry: <root>/registry.json is a JSON list of per-dataset rows (counts + hashes only).
Rules: dataset text is NEVER printed or logged by this tool — only counts, ids and hashes.
All writes are atomic (temp file + os.replace, fsync). Exit codes: 0 ok, 1 failure, 2 usage error.
Windows paths are used throughout.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA = "bossman.lora-dataset/1"
DATA_FILES = ("corpus.jsonl", "train.jsonl", "holdout.jsonl", "cpt.jsonl")
DEFAULT_HOLDOUT = 0.1
DEFAULT_TOKEN_BUDGET = 2_000_000
DEFAULT_MIN_CHARS = 40
FALLBACK_TOPIC = "Расскажи по теме"

#: Owner-provenance repository sources (collector name == corpus ``source`` value).
EXTRA_SOURCES = ("lessons", "skills", "rules", "owner_docs", "tools")
LESSON_GLOBS = ("docs/owner/bossman_lessons_*.json", "docs/owner/bossman_critical_errors.json")
SKILL_DIRS = (".agents/skills", ".claude/skills", "bossman-core/skills")
OWNER_REPOS = ("molotroka123-cell/aimaxbossman",)          # provenance.json repos that count as owner-authored
RULE_FILES = ("AGENTS.md", "CLAUDE.md")
OWNER_DOCS_GLOB = "docs/owner/*.md"
TOOLS_SCAN_DIR = "command-center/bcc"
TOOLS_SKIP_PARTS = frozenset({"tests", "test", "_vendor", "__pycache__", "node_modules", "skills_catalog"})
#: A repository file under any of these path segments is never read.
PRIVATE_PATH_PARTS = frozenset({"private", "personal", "secrets", "keys", "vault", ".env", "datasets",
                                "photos", "voice", "grechka", "гречка"})
CHUNK_CHARS = 3000                                         # < the harvester's 4000-char clean cap
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_EMAIL_OK = ("noreply@",)
_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")


# --------------------------------------------------------------------------- helpers

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _import_harvester():
    """Import the Bossman training harvester (command-center/bcc/pit/master_parser)."""
    cc = _repo_root() / "command-center"
    if cc.is_dir() and str(cc) not in sys.path:
        sys.path.insert(0, str(cc))
    from bcc.pit.master_parser import training_harvester as th  # noqa: PLC0415
    return th


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, data: str) -> None:
    """Atomic write: temp file in the same dir, fsync, os.replace (Windows-safe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _make_read_only(path: Path) -> None:
    try:
        os.chmod(str(path), stat.S_IREAD)
    except OSError:
        pass


def _registry_path(root: Path) -> Path:
    return root / "registry.json"


def _load_registry(root: Path) -> list[dict[str, Any]]:
    p = _registry_path(root)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_registry(root: Path, rows: list[dict[str, Any]]) -> None:
    _atomic_write(_registry_path(root), json.dumps(rows, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def _upsert_registry(root: Path, row: dict[str, Any]) -> None:
    reg = _load_registry(root)
    reg = [r for r in reg if r.get("dataset_id") != row["dataset_id"]]
    reg.append(row)
    reg.sort(key=lambda r: str(r.get("dataset_id") or ""))
    _save_registry(root, reg)


# --------------------------------------------------------------------------- repository sources

_GATE_MOD: Any = None


def _gate():
    """The secure gate module (tools/training_secure_gate.py): one copy of its patterns, never a fork."""
    global _GATE_MOD
    if _GATE_MOD is None:
        path = _repo_root() / "tools" / "training_secure_gate.py"
        spec = importlib.util.spec_from_file_location("_bossman_training_secure_gate", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        _GATE_MOD = mod
    return _GATE_MOD


def _is_private_path(path: Path, repo: Path) -> bool:
    try:
        rel = path.resolve().relative_to(repo.resolve())
    except ValueError:
        return True                                         # outside the repository: never read
    return any(part.casefold() in PRIVATE_PATH_PARTS for part in rel.parts)


def _read_repo_text(path: Path, repo: Path) -> str | None:
    if not path.is_file() or path.is_symlink() or _is_private_path(path, repo):
        return None
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _rel(path: Path, repo: Path) -> str:
    return path.resolve().relative_to(repo.resolve()).as_posix()


def _slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", text.casefold(), flags=re.UNICODE).strip("-")
    return s[:60] or "section"


def _split_frontmatter(text: str) -> tuple[str, str]:
    """('---' YAML frontmatter, body). Frontmatter is returned raw (stdlib only, no yaml)."""
    if text.startswith("---"):
        lines = text.splitlines()
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return "", text


def _fm_value(frontmatter: str, key: str) -> str:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", frontmatter, re.M)
    return m.group(1).strip().strip("'\"") if m else ""


def _md_sections(text: str, default_title: str) -> list[tuple[str, str]]:
    """Split markdown into (heading path, body) by #..#### headings, ignoring headings inside code fences."""
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    in_code = False

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            path = " › ".join(h for _, h in stack) or default_title
            sections.append((path, body))
        buf.clear()

    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buf.append(line)
    flush()
    return sections


def _chunks(body: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Paragraph-preserving chunks of at most ``limit`` chars (an oversized paragraph is hard-split)."""
    out: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        while len(para) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:limit])
            para = para[limit:]
        if cur and len(cur) + 2 + len(para) > limit:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def _md_items(text: str, *, source: str, id_prefix: str, title: str, topic_fmt: str,
              text_prefix: str) -> list[dict[str, Any]]:
    """Q/A rows from markdown: one row per heading section chunk, id = <id_prefix>:<anchor>[:pN]."""
    items: list[dict[str, Any]] = []
    used: set[str] = set()
    for path, body in _md_sections(text, title):
        heading = path.rsplit(" › ", 1)[-1]
        anchor = _slug(heading)
        base, n = anchor, 1
        while anchor in used:
            n += 1
            anchor = f"{base}-{n}"
        used.add(anchor)
        parts = _chunks(body)
        for i, part in enumerate(parts, 1):
            suffix = f":p{i}" if len(parts) > 1 else ""
            items.append({
                "ref": f"{id_prefix}:{anchor}{suffix}",
                "source": source,
                "topic": topic_fmt.format(heading=heading, title=title)[:120],
                "text": f"{text_prefix} — {path}{f' (часть {i})' if len(parts) > 1 else ''}:\n{part}",
            })
    return items


def _collect_lessons(repo: Path) -> list[dict[str, Any]]:
    """LessonBook JSON (schema bossman.*, ``entries`` list) -> fix + detect instruction pairs."""
    items: list[dict[str, Any]] = []
    files: list[Path] = []
    for pattern in LESSON_GLOBS:
        files.extend(sorted(repo.glob(pattern)))
    for path in dict.fromkeys(files):
        raw = _read_repo_text(path, repo)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            continue
        stem = path.stem
        for idx, e in enumerate(entries, 1):
            if not isinstance(e, dict):
                continue
            s = {k: str(v).strip() for k, v in e.items() if isinstance(v, str) and str(v).strip()}
            title = s.get("title") or s.get("id") or f"урок {idx}"
            lid = s.get("id") or f"e{idx}"
            head = f"Урок {lid}" + (f" ({s['class']}, {s.get('severity', '')})" if s.get("class") else "") + f": {title}."
            fix_parts = [head]
            for key, label in (("symptom", "Симптом"), ("root_cause", "Причина"), ("fix", "Исправление"),
                               ("prevention", "Профилактика")):
                if s.get(key):
                    fix_parts.append(f"{label}: {s[key]}")
            if len(fix_parts) > 1:
                items.append({"ref": f"lessons:{stem}:{lid}:fix", "source": "lessons",
                              "topic": f"Как исправить: {title}?"[:120], "text": "\n".join(fix_parts)})
            det_parts = [head]
            if s.get("symptom"):
                det_parts.append(f"Симптом: {s['symptom']}")
            examples = [str(x) for x in e.get("symptom_examples") or [] if isinstance(x, str)]
            if examples:
                det_parts.append("Примеры: " + "; ".join(examples[:5]))
            if s.get("detection"):
                det_parts.append(f"Как обнаружить: {s['detection']}")
            keywords = [str(x) for x in e.get("keywords") or [] if isinstance(x, str)]
            if keywords:
                det_parts.append("Ключевые слова: " + ", ".join(keywords[:12]))
            if len(det_parts) > 2:
                items.append({"ref": f"lessons:{stem}:{lid}:detect", "source": "lessons",
                              "topic": f"Как распознать проблему «{title}»?"[:120], "text": "\n".join(det_parts)})
    return items


def _skill_is_owner(skill_dir: Path, frontmatter: str) -> bool:
    """Owner-authored only: no `upstream:` adaptation and no provenance.json pointing at a foreign repo."""
    if re.search(r"^\s*upstream\s*:", frontmatter, re.M):
        return False
    prov = skill_dir / "provenance.json"
    if prov.is_file():
        try:
            repo_url = str((json.loads(prov.read_text(encoding="utf-8")).get("source") or {}).get("repo") or "")
        except (OSError, ValueError, AttributeError):
            return False
        if not any(r in repo_url.casefold() for r in OWNER_REPOS):
            return False
    return True


def _collect_skills(repo: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for base in SKILL_DIRS:
        root = repo / base
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            raw = _read_repo_text(path, repo)
            if raw is None:
                continue
            fm, body = _split_frontmatter(raw)
            if not _skill_is_owner(path.parent, fm):
                continue
            name = _fm_value(fm, "name") or path.parent.name
            desc = _fm_value(fm, "description")
            if desc:
                items.append({"ref": f"skills:{name}:when", "source": "skills",
                              "topic": f"Когда использовать навык {name}?"[:120],
                              "text": f"Навык {name} ({_rel(path, repo)}): {desc}"})
            items.extend(_md_items(body, source="skills", id_prefix=f"skills:{name}", title=name,
                                   topic_fmt="Навык {title}: как устроено «{heading}»?",
                                   text_prefix=f"Навык {name}"))
    return items


def _collect_rules(repo: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name in RULE_FILES:
        path = repo / name
        raw = _read_repo_text(path, repo)
        if raw is None:
            continue
        items.extend(_md_items(raw, source="rules", id_prefix=f"rules:{name}", title=name,
                               topic_fmt="Какое правило в {title} про «{heading}»?", text_prefix=name))
    return items


def _collect_owner_docs(repo: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(repo.glob(OWNER_DOCS_GLOB)):
        raw = _read_repo_text(path, repo)
        if raw is None:
            continue
        _, body = _split_frontmatter(raw)
        items.extend(_md_items(body, source="owner_docs", id_prefix=f"owner_docs:{path.name}", title=path.stem,
                               topic_fmt="Что сказано в {title} про «{heading}»?", text_prefix=path.name))
    return items


def _first_sentence(text: str, limit: int = 90) -> str:
    first = re.split(r"(?<=[.!?;])\s", text.strip(), maxsplit=1)[0].rstrip(".;")
    return first if len(first) <= limit else first[:limit].rstrip() + "…"


def _is_toolspec_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return (isinstance(fn, ast.Name) and fn.id == "ToolSpec") or (isinstance(fn, ast.Attribute) and fn.attr == "ToolSpec")


class _ToolModule:
    """Static view of one module: module-level constants and same-module ToolSpec factory helpers."""

    def __init__(self, tree: ast.Module) -> None:
        self.consts: dict[str, ast.AST] = {}
        self.factories: dict[str, tuple[list[str], ast.Call]] = {}
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                self.consts[stmt.targets[0].id] = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                self.consts[stmt.target.id] = stmt.value
            elif isinstance(stmt, ast.FunctionDef):
                params = [a.arg for a in stmt.args.posonlyargs + stmt.args.args + stmt.args.kwonlyargs]
                calls = [n for n in ast.walk(stmt) if _is_toolspec_call(n)]
                if len(calls) == 1:
                    kw = {k.arg: k.value for k in calls[0].keywords if k.arg}
                    if isinstance(kw.get("name"), ast.Name) and kw["name"].id in params:
                        self.factories[stmt.name] = (params, calls[0])

    def node(self, node: ast.AST | None, env: dict[str, ast.AST] | None = None) -> ast.AST | None:
        for _ in range(4):                                  # follow Name -> param/constant, bounded
            if isinstance(node, ast.Name):
                if env and node.id in env:
                    node = env[node.id]
                elif node.id in self.consts:
                    node = self.consts[node.id]
                else:
                    return node
            else:
                return node
        return node

    def string(self, node: ast.AST | None, env: dict[str, ast.AST] | None = None) -> str | None:
        node = self.node(node, env)
        if node is None:
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.string(node.left, env), self.string(node.right, env)
            return left + right if left is not None and right is not None else None
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return None
        return value if isinstance(value, str) else None


def _tool_item_pair(name: str, desc: str, kw: dict[str, ast.AST], mod: _ToolModule, env: dict[str, ast.AST] | None,
                    rel: str) -> list[dict[str, Any]]:
    category = mod.string(kw.get("category"), env) or "read"
    tsource = mod.string(kw.get("source"), env) or "builtin"
    effect = mod.string(kw.get("default_effect"), env) or "ask"
    perm = mod.string(kw.get("permission"), env) or ""
    args: list[str] = []
    schema = mod.node(kw.get("input_schema"), env)
    if isinstance(schema, ast.Dict):
        args = [k.value for k in schema.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    required: list[str] = []
    req = mod.node(kw.get("required"), env)
    if isinstance(req, (ast.List, ast.Tuple)):
        required = [e.value for e in req.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    meta = f"категория {category}, источник {tsource}, по умолчанию {effect}" + (f", право {perm}" if perm else "")
    arg_line = ""
    if args:
        arg_line = " Аргументы: " + ", ".join(args) + ("; обязательные: " + ", ".join(required) if required else "") + "."
    return [
        {"ref": f"tools:{name}:need", "source": "tools",
         "topic": f"Какой инструмент Bossman нужен для задачи «{_first_sentence(desc)}»?"[:120],
         "text": f"Для этого есть инструмент `{name}` ({meta}). {desc}{arg_line}"},
        {"ref": f"tools:{name}:what", "source": "tools",
         "topic": f"Что делает инструмент {name}?"[:120],
         "text": f"Инструмент `{name}` ({rel}): {desc}{arg_line}"},
    ]


def _collect_tools(repo: Path) -> list[dict[str, Any]]:
    """Every statically resolvable ToolSpec under command-center/bcc (ast only — no app start, no import).

    Resolves string literals, implicit/explicit ``+`` concatenation, module-level constants
    (``name=TOOL_NAME``) and same-module factory helpers (``def _tool(name, description, ...)``
    returning one ToolSpec). f-strings and runtime-built specs (plugins, MCP) are skipped."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    root = repo / TOOLS_SCAN_DIR
    if not root.is_dir():
        return items
    for path in sorted(root.rglob("*.py")):
        if any(p in TOOLS_SKIP_PARTS for p in path.relative_to(root).parts):
            continue
        src = _read_repo_text(path, repo)
        if not src or "ToolSpec(" not in src:
            continue
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            continue
        mod = _ToolModule(tree)
        factory_calls = {id(call) for _, call in mod.factories.values()}
        rel = _rel(path, repo)
        found: list[tuple[str | None, str | None, dict[str, ast.AST], dict[str, ast.AST] | None]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _is_toolspec_call(node) and id(node) not in factory_calls:
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                found.append((mod.string(kw.get("name") or (node.args[0] if node.args else None)),
                              mod.string(kw.get("description") or (node.args[1] if len(node.args) > 1 else None)),
                              kw, None))
            elif isinstance(node.func, ast.Name) and node.func.id in mod.factories:
                params, spec = mod.factories[node.func.id]
                env: dict[str, ast.AST] = dict(zip(params, node.args))
                env.update({k.arg: k.value for k in node.keywords if k.arg})
                kw = {k.arg: k.value for k in spec.keywords if k.arg}
                found.append((mod.string(kw.get("name"), env), mod.string(kw.get("description"), env), kw, env))
        for name, desc, kw, env in found:
            if not name or not desc or name in seen:
                continue
            seen.add(name)
            items.extend(_tool_item_pair(name, desc, kw, mod, env, rel))
    return items


REPO_COLLECTORS = {
    "lessons": _collect_lessons,
    "skills": _collect_skills,
    "rules": _collect_rules,
    "owner_docs": _collect_owner_docs,
    "tools": _collect_tools,
}


def _parse_extra_sources(value: str) -> tuple[str, ...]:
    names = tuple(dict.fromkeys(x.strip() for x in str(value or "").split(",") if x.strip()))
    if names in ((), ("none",)):
        return ()
    unknown = [n for n in names if n not in REPO_COLLECTORS]
    if unknown:
        raise ValueError(f"unknown --extra-sources: {unknown}; known: {sorted(REPO_COLLECTORS)}")
    return names


def _has_private_marker(text: str) -> bool:
    """Gate PERSONAL_HINTS (protected owner locations) or an e-mail address."""
    folded = text.casefold()
    if any(h.casefold() in folded for h in _gate().PERSONAL_HINTS):
        return True
    return any(not m.group(0).casefold().startswith(_EMAIL_OK) for m in _EMAIL.finditer(text))


# --------------------------------------------------------------------------- harvest

def _harvest_rows(data_dir: Path, *, min_chars: int, token_budget: int, repo_root: Path | None = None,
                  extra_sources: tuple[str, ...] = EXTRA_SOURCES) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Collect + sanitize + dedup rows (no packing). Returns (rows, drop/skip counters)."""
    th = _import_harvester()
    gate = _gate()
    raw_items = (
        th._collect_jeff_kb(data_dir)
        + th._collect_training_drops(data_dir)
        + th._collect_web_learn_candidates(data_dir)
        + th._collect_war_terminal_and_news(data_dir)
    )
    repo = Path(repo_root) if repo_root is not None else _repo_root()
    for name in extra_sources:
        raw_items += REPO_COLLECTORS[name](repo)

    stats = {"dropped_injections": 0, "dropped_personal": 0, "dropped_secrets": 0, "skipped_token_budget": 0}
    seen_text: set[str] = set()
    seen_ids: set[str] = set()
    rows: list[dict[str, Any]] = []

    for item in raw_items:
        raw_text = str(item.get("text") or "")
        raw_topic = str(item.get("topic") or "")
        if (th._is_injected(raw_text) or th._is_injected(raw_topic)
                or gate.INJECTION.search(raw_text) or gate.INJECTION.search(raw_topic)):
            stats["dropped_injections"] += 1
            continue
        clean_text = th._clean_and_redact(raw_text)
        if _has_private_marker(clean_text) or _has_private_marker(raw_topic):
            stats["dropped_personal"] += 1
            continue
        if gate.SECRET.search(clean_text) or gate.SECRET.search(raw_topic):
            stats["dropped_secrets"] += 1
            continue
        if len(clean_text) < max(1, int(min_chars)):
            continue
        norm_sha = hashlib.sha256(clean_text.casefold().encode("utf-8")).hexdigest()
        if norm_sha in seen_text:
            continue
        seen_text.add(norm_sha)
        ref = str(item.get("ref") or f"row:{len(rows) + 1}")
        row_id = ref
        n = 1
        while row_id in seen_ids:
            n += 1
            row_id = f"{ref}:{n}"
        seen_ids.add(row_id)
        rows.append({
            "id": row_id,
            "source": str(item.get("source") or "unknown"),
            "topic": str(item.get("topic") or "")[:120],
            "text": clean_text,
            "sha256": norm_sha,
            "tokens": th._estimate_tokens(clean_text),
        })

    rows.sort(key=lambda r: (r["source"], r["id"]))
    if token_budget > 0:
        used = 0
        kept: list[dict[str, Any]] = []
        for r in rows:
            if used + r["tokens"] > int(token_budget):
                stats["skipped_token_budget"] += 1
                continue
            used += r["tokens"]
            kept.append(r)
        rows = kept
    return rows, stats


def _dataset_id(rows: list[dict[str, Any]], created_at: str) -> str:
    stamp = created_at.replace("-", "").replace(":", "").replace("T", "-").replace("Z", "")
    blob = "\n".join(sorted(r["sha256"] for r in rows)).encode("utf-8")
    return "ds-" + stamp + "-" + hashlib.sha256(blob).hexdigest()[:8]


# --------------------------------------------------------------------------- build

def cmd_build(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).expanduser()
    root = Path(args.root).expanduser()
    if not data_dir.is_dir():
        print(f"usage: data dir does not exist: {data_dir}", file=sys.stderr)
        return 2
    if not 0.0 <= args.holdout < 1.0:
        print("usage: --holdout must be in [0, 1)", file=sys.stderr)
        return 2

    try:
        extra_sources = _parse_extra_sources(args.extra_sources)
    except ValueError as exc:
        print(f"usage: {exc}", file=sys.stderr)
        return 2
    repo_root = Path(args.repo_root).expanduser()
    if extra_sources and not repo_root.is_dir():
        print(f"usage: repo root does not exist: {repo_root}", file=sys.stderr)
        return 2

    rows, stats = _harvest_rows(
        data_dir, min_chars=args.min_chars, token_budget=args.token_budget,
        repo_root=repo_root, extra_sources=extra_sources)
    dropped_injections = stats["dropped_injections"]
    if not rows:
        print("build: no rows survived filtering; nothing to snapshot", file=sys.stderr)
        return 1

    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ds_id = _dataset_id(rows, created_at)
    ds_dir = root / ds_id

    holdout_bucket = int(args.holdout * 1000)
    source_counts: dict[str, int] = {}
    train_rows: list[str] = []
    holdout_rows: list[str] = []
    cpt_rows: list[str] = []

    for r in rows:
        source_counts[r["source"]] = source_counts.get(r["source"], 0) + 1
        prompt = r["topic"] if r["topic"] else FALLBACK_TOPIC
        sft = {"id": r["id"], "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": r["text"]},
        ]}
        is_holdout = int(r["sha256"][:8], 16) % 1000 < holdout_bucket
        if is_holdout:
            holdout_rows.append(json.dumps(sft, ensure_ascii=False, sort_keys=True))
        else:
            train_rows.append(json.dumps(sft, ensure_ascii=False, sort_keys=True))
            cpt_rows.append(json.dumps({"id": r["id"], "text": r["text"]}, ensure_ascii=False, sort_keys=True))

    corpus_rows = [
        json.dumps({"id": r["id"], "source": r["source"], "topic": r["topic"], "text": r["text"],
                    "sha256": r["sha256"], "tokens": r["tokens"]}, ensure_ascii=False, sort_keys=True)
        for r in rows
    ]
    files_payload: dict[str, str] = {
        "corpus.jsonl": "\n".join(corpus_rows) + ("\n" if corpus_rows else ""),
        "train.jsonl": "\n".join(train_rows) + ("\n" if train_rows else ""),
        "holdout.jsonl": "\n".join(holdout_rows) + ("\n" if holdout_rows else ""),
        "cpt.jsonl": "\n".join(cpt_rows) + ("\n" if cpt_rows else ""),
    }

    if ds_dir.exists():
        print(f"build: dataset dir already exists: {ds_dir}", file=sys.stderr)
        return 1
    files_meta: list[dict[str, Any]] = []
    for name in DATA_FILES:
        p = ds_dir / name
        _atomic_write(p, files_payload[name])
        files_meta.append({"path": name, "sha256": _sha256_file(p), "bytes": p.stat().st_size})

    manifest = {
        "schema": SCHEMA,
        "dataset_id": ds_id,
        "created_at": created_at,
        "source_counts": source_counts,
        "rows": len(rows),
        "train_rows": len(train_rows),
        "holdout_rows": len(holdout_rows),
        "dropped_injections": dropped_injections,
        "dropped_personal": stats["dropped_personal"],
        "dropped_secrets": stats["dropped_secrets"],
        "extra_sources": list(extra_sources),
        "files": files_meta,
    }
    manifest_path = ds_dir / "manifest.json"
    _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n")

    # snapshot immutable: read-only every data file + the manifest itself
    for name in DATA_FILES + ("manifest.json",):
        _make_read_only(ds_dir / name)

    _upsert_registry(root, {
        "dataset_id": ds_id,
        "created_at": created_at,
        "rows": len(rows),
        "train_rows": len(train_rows),
        "holdout_rows": len(holdout_rows),
        "source_counts": source_counts,
        "manifest_sha256": _sha256_file(manifest_path),
        "gate": None,
    })

    print(f"dataset_id={ds_id}")
    print(f"rows={len(rows)} train={len(train_rows)} holdout={len(holdout_rows)} "
          f"dropped_injections={dropped_injections} dropped_personal={stats['dropped_personal']} "
          f"dropped_secrets={stats['dropped_secrets']} skipped_token_budget={stats['skipped_token_budget']}")
    print("source_counts=" + json.dumps(source_counts, ensure_ascii=False, sort_keys=True))
    print(f"dir={ds_dir}")
    return 0


# --------------------------------------------------------------------------- gate

def cmd_gate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    ds_dir = root / args.dataset_id
    if not ds_dir.is_dir():
        print(f"usage: dataset dir does not exist: {ds_dir}", file=sys.stderr)
        return 2
    gate_script = _repo_root() / "tools" / "training_secure_gate.py"
    if not gate_script.is_file():
        print(f"gate: script not found: {gate_script}", file=sys.stderr)
        return 2

    # gate report OUTSIDE the dataset dir so the manifest stays exact
    report_path = root / f"{args.dataset_id}.gate.json"
    cmd = [sys.executable, str(gate_script),
           "--dataset", str(ds_dir),
           "--out", str(report_path),
           "--lock", str(Path(args.lock).expanduser())]
    if args.venv:
        cmd += ["--venv", str(Path(args.venv).expanduser())]
    if args.require_offline:
        cmd += ["--require-offline"]
    if args.allow_source:
        for src in args.allow_source:
            cmd += ["--allow-source", src]
    if args.max_injection_rate is not None:
        cmd += ["--max-injection-rate", str(args.max_injection_rate)]
    if args.for_weights:
        cmd += ["--for-weights"]

    proc = subprocess.run(cmd, cwd=str(_repo_root()), check=False)  # gate prints counts/ids only
    verdict = "FAIL"
    report_sha = ""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        verdict = str(report.get("verdict", "FAIL"))
    except (OSError, ValueError):
        pass
    if report_path.is_file():
        report_sha = _sha256_file(report_path)

    reg = _load_registry(root)
    for row in reg:
        if row.get("dataset_id") == args.dataset_id:
            row["gate"] = {"verdict": verdict, "report_sha256": report_sha,
                           "report_path": str(report_path),
                           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "exit_code": proc.returncode}
            break
    _save_registry(root, reg)

    print(f"dataset_id={args.dataset_id} verdict={verdict} report={report_path}")
    if report_sha:
        print(f"report_sha256={report_sha}")
    return 0 if verdict == "PASS" else 1


# --------------------------------------------------------------------------- list / verify

def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    reg = _load_registry(root)
    if not reg:
        print("(registry empty)")
        return 0
    for row in reg:
        gate = row.get("gate") or {}
        print(f"{row.get('dataset_id', '?'):30s} created={row.get('created_at', '?')} "
              f"rows={row.get('rows', 0)} train={row.get('train_rows', 0)} holdout={row.get('holdout_rows', 0)} "
              f"manifest={str(row.get('manifest_sha256', ''))[:12]} gate={gate.get('verdict') or 'null'}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    ds_dir = root / args.dataset_id
    mpath = ds_dir / "manifest.json"
    if not ds_dir.is_dir() or not mpath.is_file():
        print(f"usage: dataset or manifest missing: {mpath}", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        entries = {e["path"]: e for e in manifest["files"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"verify: manifest unreadable: {type(exc).__name__}", file=sys.stderr)
        return 2

    on_disk = {p.relative_to(ds_dir).as_posix() for p in ds_dir.rglob("*") if p.is_file()}
    mismatched: list[str] = []
    for rel, meta in sorted(entries.items()):
        p = ds_dir / rel
        if not p.is_file():
            mismatched.append(f"missing:{rel}")
        elif _sha256_file(p) != meta.get("sha256") or p.stat().st_size != meta.get("bytes"):
            mismatched.append(f"changed:{rel}")
    for rel in sorted(on_disk - set(entries) - {"manifest.json"}):
        mismatched.append(f"extra:{rel}")

    if mismatched:
        for m in mismatched:
            print(f"MISMATCH {m}")
        return 1
    print(f"verify: OK {len(entries)} files match manifest ({args.dataset_id})")
    return 0


# --------------------------------------------------------------------------- CLI

def _default_data_dir() -> str:
    return os.environ.get("BCC_DATA_DIR") or str(Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter")


DEFAULT_ROOT = "C:/Users/asd/Bossman/train-snapshots"   # outside git, owner ACL (TRAINING_WORKFLOW_SECURE_20261023_RU)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bossman LoRA/QLoRA dataset database (harvest -> snapshot -> gate -> train -> eval)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="harvest + immutable snapshot + registry row")
    b.add_argument("--data-dir", default=_default_data_dir())
    b.add_argument("--root", default=DEFAULT_ROOT)
    b.add_argument("--holdout", type=float, default=DEFAULT_HOLDOUT)
    b.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    b.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS)
    b.add_argument("--extra-sources", default=",".join(EXTRA_SOURCES),
                   help="comma list of owner-provenance repository sources "
                        f"({','.join(EXTRA_SOURCES)}); 'none' disables them")
    b.add_argument("--repo-root", default=str(_repo_root()),
                   help="repository the extra sources are read from (default: this checkout)")
    g = sub.add_parser("gate", help="run tools/training_secure_gate.py on a snapshot, record the verdict")
    g.add_argument("dataset_id")
    g.add_argument("--root", default=DEFAULT_ROOT)
    g.add_argument("--lock", required=True)
    g.add_argument("--venv", default=None)
    g.add_argument("--require-offline", action="store_true")
    g.add_argument("--for-weights", action="store_true")
    g.add_argument("--allow-source", action="append", default=[])
    g.add_argument("--max-injection-rate", type=float, default=None)
    ls = sub.add_parser("list", help="registry rows (counts and hashes only)")
    ls.add_argument("--root", default=DEFAULT_ROOT)
    v = sub.add_parser("verify", help="recompute sha256 of a snapshot against its manifest")
    v.add_argument("dataset_id")
    v.add_argument("--root", default=DEFAULT_ROOT)
    args = ap.parse_args(argv)
    return {"build": cmd_build, "gate": cmd_gate, "list": cmd_list, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
