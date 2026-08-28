"""The application must stay a standalone workload.

BOSSMAN is the control plane and talks to this service over HTTP. Any import
of ``bcc.*`` (or of the surrounding monorepo) in business logic would turn an
independent service into a coupled module, so it is checked mechanically.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "ai_webcam_vision"

FORBIDDEN_ROOTS = {"bcc", "bossman", "bossman_core", "command_center"}


def module_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_source_tree_is_non_empty():
    assert len(module_files()) >= 10


def test_no_control_plane_imports():
    offenders = {}
    for path in module_files():
        bad = imported_roots(path) & FORBIDDEN_ROOTS
        if bad:
            offenders[str(path)] = sorted(bad)
    assert not offenders, f"control-plane imports found: {offenders}"


def test_every_module_parses_and_imports_cleanly():
    import importlib

    for path in module_files():
        relative = path.relative_to(SRC.parent)
        module = ".".join(relative.with_suffix("").parts)
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        importlib.import_module(module)


def test_only_one_url_assembly_site():
    """Credentials may be concatenated into a URL in exactly one function."""
    offenders = []
    for path in module_files():
        text = path.read_text(encoding="utf-8")
        if path.name == "secretstore.py":
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if "://" in line and ("reveal()" in line or "password" in line.lower()):
                if "build_stream_url" in line or line.lstrip().startswith("#"):
                    continue
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, f"URL assembly outside secretstore.build_stream_url: {offenders}"
