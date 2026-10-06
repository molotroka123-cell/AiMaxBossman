"""Lane jeffa audit: AST importer scan for lane modules. Prints, per module, importers outside tests.
Deterministic: python tools/tree_proof/jeffa_audit.py [module_path ...]"""
from __future__ import annotations
import ast, importlib.util, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sp = importlib.util.spec_from_file_location("jeffa_probe", HERE / "jeffa_probe.py")
jp = importlib.util.module_from_spec(sp); sp.loader.exec_module(jp)
ROOT = jp.ROOT
BASE = ROOT / "command-center"

def dotted(path: Path):
    rel = path.relative_to(BASE).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__": parts = parts[:-1]
    return ".".join(parts)

def imports_of(path: Path):
    """Set of absolute dotted names imported by file (resolving relative imports)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    pkg = dotted(path).split(".")
    if path.name != "__init__.py": pkg = pkg[:-1]
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = pkg[: len(pkg) - (n.level - 1)]
                mod = ".".join(base + ([n.module] if n.module else []))
            else:
                mod = n.module or ""
            out.add(mod)
            out |= {mod + "." + a.name for a in n.names}
    return out

def scan(target_mods):
    files = [p for p in BASE.rglob("*.py") if "tests" not in p.parts and "__pycache__" not in p.parts]
    imp = {p: imports_of(p) for p in files}
    res = {}
    for m in target_mods:
        res[m] = sorted(dotted(p) for p, s in imp.items() if m in s and dotted(p) != m)
    return res

if __name__ == "__main__":
    nodes = jp.lane_nodes()
    paths = {n["id"]: jp.module_path(n) for n in nodes if jp.module_path(n)}
    mods = {nid: dotted(ROOT / p) for nid, p in paths.items()}
    r = scan(set(mods.values()))
    out = {nid: {"module": m, "importers": r[m]} for nid, m in mods.items()}
    print(json.dumps(out, indent=1))
