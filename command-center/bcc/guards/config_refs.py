"""G3 — static «config reference» check for Luau / Python projects.

Incident 03.11.2026: MatchService used `MatchConfig.MAX_PAID_WINS_PER_DAY` / `MatchConfig.DAILY_MATCH_POINT_CAP`,
which MatchConfig never defined — `nil < number` crashed the reward handout after every match. Selene and
luau-compile do not see it (the field access is legal; the value is nil at runtime).

Generic rule: for every module variable bound to a project module
  Luau   `local X = require(<...>"Name")` / `require(path.Name)`  -> file Name.luau/.lua or Name/init.luau
  Python `import pkg.name as X` / `from pkg import name as X` / `import name` -> name.py or name/__init__.py
collect the fields DEFINED in that module (table-constructor keys of the returned table, `M.F = ...`,
`function M.F` / `M:F`; Python top-level names) and report every `X.FIELD` USE that is not defined.
A module whose table has a metatable / __index (Luau) or `__getattr__` (Python) is dynamic and skipped (reported).
Comments and string literals are ignored. Pure static analysis; reads only the files under `root`."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

LUAU_SUFFIXES = (".luau", ".lua")
SKIP_DIRS = frozenset({".git", "node_modules", "build", "Packages", "DevPackages", "__pycache__", ".venv", "venv"})
_IDENT = r"[A-Za-z_]\w*"


@dataclass
class Missing:
    file: str
    line: int
    module_var: str
    field: str
    module_file: str


@dataclass
class Report:
    missing: list[Missing] = field(default_factory=list)
    #: undefined but nil-tolerant uses (`X.F or default`): warnings, not failures
    guarded: list[Missing] = field(default_factory=list)
    checked_modules: dict[str, int] = field(default_factory=dict)    # module file -> defined field count
    dynamic: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    uses: int = 0

    @property
    def ok(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict:
        return {"ok": self.ok, "uses": self.uses, "missing": [m.__dict__ for m in self.missing],
                "guarded": [m.__dict__ for m in self.guarded],
                "checked_modules": self.checked_modules, "dynamic": self.dynamic, "unresolved": self.unresolved}

    def summary_ru(self) -> str:
        if self.ok:
            return (f"G3 PASS: {self.uses} обращений к полям {len(self.checked_modules)} модулей — все поля определены"
                    + (f" (динамические пропущены: {', '.join(self.dynamic)})" if self.dynamic else "")
                    + (f"; предупреждений (поля нет, но обращение терпит nil): {len(self.guarded)}"
                       if self.guarded else ""))
        lines = [f"G3 FAIL: {len(self.missing)} обращений к неопределённым полям:"]
        lines += [f"  {m.file}:{m.line} {m.module_var}.{m.field} — нет в {m.module_file}" for m in self.missing[:40]]
        return "\n".join(lines)


# ---------------------------------------------------------------- Luau

def strip_luau(src: str) -> str:
    """Comments and string literals replaced by spaces (newlines kept, so line numbers stay exact)."""
    out: list[str] = []
    i, n = 0, len(src)

    def blank(s: str) -> str:
        return re.sub(r"[^\n]", " ", s)

    while i < n:
        if src.startswith("--", i):
            m = re.match(r"--\[(=*)\[", src[i:])
            if m:
                end = src.find("]" + m.group(1) + "]", i)
                end = n if end < 0 else end + len(m.group(1)) + 2
            else:
                end = src.find("\n", i)
                end = n if end < 0 else end
            out.append(blank(src[i:end]))
            i = end
            continue
        m = re.match(r"\[(=*)\[", src[i:])
        if m:
            end = src.find("]" + m.group(1) + "]", i)
            end = n if end < 0 else end + len(m.group(1)) + 2
            out.append(blank(src[i:end]))
            i = end
            continue
        c = src[i]
        if c in "\"'`":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(c + blank(src[i + 1:j]) + (c if j < n else ""))
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


_REQ_RX = re.compile(r"\blocal\s+(" + _IDENT + r")\s*(?::[^=\n]+)?=\s*require\s*\(")


def _close_paren(clean: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(clean)):
        if clean[i] == "(":
            depth += 1
        elif clean[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(clean)


def _require_name(args_original: str) -> str | None:
    """Module name of ONE require's arguments: its last WaitForChild/FindFirstChild("Name") or last `.Name`."""
    names = re.findall(r"(?:WaitForChild|FindFirstChild)\(\s*[\"']([^\"']+)[\"']", args_original)
    if names:
        return names[-1]
    segs = re.findall(r"[.:]?(" + _IDENT + r")", args_original)
    return segs[-1] if segs else None


def _luau_table_keys(clean: str, open_idx: int) -> set[str]:
    """First-level keys of the table constructor whose `{` is at open_idx."""
    keys: set[str] = set()
    depth = 0
    i = open_idx
    start = open_idx + 1
    while i < len(clean):
        ch = clean[i]
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = clean[start:i]
    # walk at depth 0 inside body
    d = 0
    seg_start = 0
    for j, ch in enumerate(body + ","):
        if ch in "{([":
            d += 1
        elif ch in "})]":
            d -= 1
        elif ch in ",;" and d == 0:
            seg = body[seg_start:j]
            seg_start = j + 1
            m = re.match(r"\s*(" + _IDENT + r")\s*=(?!=)", seg) or re.match(r"\s*\[\s*[\"'](" + _IDENT + r")", seg)
            if m:
                keys.add(m.group(1))
    return keys


def luau_module_fields(src: str) -> tuple[set[str], bool]:
    """(defined first-level fields of the returned table, dynamic?)."""
    clean = strip_luau(src)
    # string keys `["Key"] = ...` are blanked by strip_luau; recover them from the original text
    ret = re.findall(r"^\s*return\s+(?:table\.freeze\s*\(\s*)?(" + _IDENT + r")\s*\)?\s*(?:::.*)?$", clean, re.M)
    # `export type T` is reachable as `Mod.T` in type annotations
    fields: set[str] = set(re.findall(r"^\s*export\s+type\s+(" + _IDENT + r")", clean, re.M))
    dynamic = bool(re.search(r"\bsetmetatable\s*\(|__index", clean))
    if not ret:
        m = re.search(r"^\s*return\s*\{", clean, re.M)
        if m:
            fields |= _luau_table_keys_with_strings(src, clean, m.end() - 1)
        return fields, dynamic
    var = ret[-1]
    for m in re.finditer(r"\b" + re.escape(var) + r"\s*(?::[^=\n]+)?=\s*\{", clean):
        fields |= _luau_table_keys_with_strings(src, clean, m.end() - 1)
    for m in re.finditer(r"(?<![\w.])" + re.escape(var) + r"\.(" + _IDENT + r")\s*=(?!=)", clean):
        fields.add(m.group(1))
    for m in re.finditer(r"\bfunction\s+" + re.escape(var) + r"[.:](" + _IDENT + r")", clean):
        fields.add(m.group(1))
    for m in re.finditer(r"(?<![\w.])" + re.escape(var) + r"\[\s*[\"'](" + _IDENT + r")[\"']\s*\]\s*=(?!=)", src):
        fields.add(m.group(1))
    return fields, dynamic


def _luau_table_keys_with_strings(src: str, clean: str, open_idx: int) -> set[str]:
    keys = _luau_table_keys(clean, open_idx)
    # same span in the original source: `["Key"] =` string keys
    depth, i = 0, open_idx
    while i < len(clean):
        if clean[i] in "{([":
            depth += 1
        elif clean[i] in "})]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    span = src[open_idx:i]
    keys |= set(re.findall(r"[\{,;]\s*\[\s*[\"'](" + _IDENT + r")[\"']\s*\]\s*=(?!=)", span))
    return keys


_GUARD_AFTER = re.compile(r"\s*(?:or\b|and\b|~=\s*nil|==\s*nil)")
_GUARD_BEFORE = re.compile(r"(?:\band\s+|\bor\s+|\bnot\s+|\btypeof\s*\(\s*|\btype\s*\(\s*)$")
_IF_BEFORE = re.compile(r"\b(?:if|elseif|while)\s+$")
_THEN_AFTER = re.compile(r"\s*(?:then|do)\b")


def _luau_uses(clean: str, var: str) -> list[tuple[int, str, bool]]:
    """(line, field, guarded). Guarded = nil-tolerant use: `X.F or d`, `X.F and ...`, `if X.F then` (the WHOLE
    condition), `X.F ~= nil`. `if a < X.F then` is NOT guarded: comparing nil crashes (the 03.11 incident)."""
    out = []
    for m in re.finditer(r"(?<![\w.:])" + re.escape(var) + r"\.(" + _IDENT + r")", clean):
        before = clean[max(0, m.start() - 20):m.start()]
        guarded = bool(_GUARD_AFTER.match(clean, m.end()) or _GUARD_BEFORE.search(before)
                       or (_IF_BEFORE.search(before) and _THEN_AFTER.match(clean, m.end())))
        out.append((clean.count("\n", 0, m.start()) + 1, m.group(1), guarded))
    return out


def _luau_local_defs(clean: str, var: str) -> set[str]:
    """Fields a CONSUMER file assigns itself (`X.F = ...`): defined dynamically, not missing."""
    return set(re.findall(r"(?<![\w.])" + re.escape(var) + r"\.(" + _IDENT + r")\s*=(?!=)", clean))


# ---------------------------------------------------------------- Python

def python_module_fields(src: str) -> tuple[set[str], bool]:
    tree = ast.parse(src)
    names: set[str] = set()
    dynamic = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            dynamic |= node.name == "__getattr__"
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.If, ast.Try, ast.With)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    names.add(sub.id)
                elif isinstance(sub, (ast.FunctionDef, ast.ClassDef)):
                    names.add(sub.name)
    return names, dynamic


def _python_bindings(tree: ast.AST) -> dict[str, str]:
    """module var -> dotted module path."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                elif "." not in a.name:
                    out[a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                base = ("." * node.level) + (node.module or "")
                out[a.asname or a.name] = (base + "." if base and not base.endswith(".") else base) + a.name
    return out


# ---------------------------------------------------------------- project walk

def _iter_files(root: Path, suffixes: tuple[str, ...]):
    for p in sorted(root.rglob("*")):
        if p.suffix in suffixes and p.is_file() and not (set(p.relative_to(root).parts[:-1]) & SKIP_DIRS):
            yield p


def _index_by_stem(files: list[Path]) -> dict[str, list[Path]]:
    idx: dict[str, list[Path]] = {}
    for p in files:
        stem = p.parent.name if p.stem == "init" or p.stem == "__init__" else p.stem.split(".")[0]
        idx.setdefault(stem, []).append(p)
    return idx


def check(root: str | Path, *, max_files: int = 5000) -> Report:
    root = Path(root)
    rep = Report()
    cache: dict[Path, tuple[set[str], bool]] = {}

    def rel(p: Path) -> str:
        return p.relative_to(root).as_posix()

    lua_files = list(_iter_files(root, LUAU_SUFFIXES))[:max_files]
    by_stem = _index_by_stem(lua_files)
    for f in lua_files:
        src = f.read_text(encoding="utf-8", errors="replace")
        clean = strip_luau(src)
        for m in _REQ_RX.finditer(clean):
            var = m.group(1)
            line_no = clean.count("\n", 0, m.start()) + 1
            # strip_luau keeps offsets, so the original text of THIS require's (...) is the same slice of `src`
            name = _require_name(src[m.end() - 1:_close_paren(clean, m.end() - 1) + 1])
            cands = by_stem.get(name or "", [])
            if len(cands) != 1:
                rep.unresolved.append(f"{rel(f)}:{line_no} {var}=require({name})"
                                      + (" — несколько файлов" if cands else " — не найден"))
                continue
            mod = cands[0]
            if mod not in cache:
                cache[mod] = luau_module_fields(mod.read_text(encoding="utf-8", errors="replace"))
            fields, dynamic = cache[mod]
            if dynamic:
                if rel(mod) not in rep.dynamic:
                    rep.dynamic.append(rel(mod))
                continue
            rep.checked_modules[rel(mod)] = len(fields)
            own = _luau_local_defs(clean, var)
            for ln, fld, guarded in _luau_uses(clean, var):
                rep.uses += 1
                if fld not in fields and fld not in own:
                    (rep.guarded if guarded else rep.missing).append(Missing(rel(f), ln, var, fld, rel(mod)))

    py_files = list(_iter_files(root, (".py",)))[:max_files]
    py_by_stem = _index_by_stem(py_files)
    for f in py_files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        binds = _python_bindings(tree)
        uses: dict[str, list[tuple[int, str]]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in binds \
                    and isinstance(node.ctx, ast.Load):
                uses.setdefault(node.value.id, []).append((node.lineno, node.attr))
        for var, items in uses.items():
            last = binds[var].lstrip(".").split(".")[-1]
            cands = [p for p in py_by_stem.get(last, []) if p != f]
            if len(cands) != 1:
                continue        # third-party / stdlib module, or ambiguous: not ours to judge
            mod = cands[0]
            if mod not in cache:
                try:
                    cache[mod] = python_module_fields(mod.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue
            fields, dynamic = cache[mod]
            if dynamic:
                if rel(mod) not in rep.dynamic:
                    rep.dynamic.append(rel(mod))
                continue
            rep.checked_modules[rel(mod)] = len(fields)
            for ln, fld in items:
                rep.uses += 1
                if fld not in fields:
                    rep.missing.append(Missing(rel(f), ln, var, fld, rel(mod)))
    return rep
