"""V1 invariants: the sandbox holds and nothing can start a print by itself.

These are the promises the README, `app.manifest.yaml` and `PHYSICAL_SAFETY.md`
make. They held when this file was written; the point of the file is that they
keep holding, because every one of them is the kind of thing a plausible-looking
refactor quietly removes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ai_3d_maker import main as cli
from ai_3d_maker.control import OPERATIONS

APP_ROOT = Path(__file__).resolve().parents[1]
SRC = APP_ROOT / "src" / "ai_3d_maker"
MANIFEST = APP_ROOT / "app.manifest.yaml"

# Names that would let a model-authored string become running code.
# `compile` is deliberately absent: `re.compile` and this package's own
# `compile_mesh` are unrelated, and a test that cries wolf gets deleted.
FORBIDDEN_CALLS = {"eval", "exec", "system", "popen", "spawnl", "spawnv", "spawnlp"}
FORBIDDEN_MODULES = {"pickle", "marshal", "shelve", "dill"}


def python_sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


# --------------------------------------------------------------- the sandbox
def test_no_module_can_execute_a_string_as_code():
    """The constrained DSL is only a sandbox while nothing can escape it."""
    offenders = []
    for path in python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in FORBIDDEN_CALLS:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno} {name}()")
    assert offenders == [], f"arbitrary code execution reachable: {offenders}"


def test_no_module_deserialises_untrusted_objects():
    offenders = []
    for path in python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for n in names:
                if n in FORBIDDEN_MODULES:
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno} {n}")
    assert offenders == [], f"object deserialisation reachable: {offenders}"


def test_the_manifest_still_denies_arbitrary_cad_code():
    text = MANIFEST.read_text(encoding="utf-8")
    assert "cad.execute_arbitrary_code: deny" in text


# ------------------------------------------------- no autonomous print start
def test_the_manifest_still_denies_starting_a_physical_print():
    text = MANIFEST.read_text(encoding="utf-8")
    assert "printer.start_physical_print: deny" in text


def test_the_pipeline_cannot_reach_the_hardware_funnel():
    """`pipeline.py` must neither import nor name execute_physical in code.

    Checked over the syntax tree, not the text: the module docstring is allowed
    to explain the rule, it just may not break it.
    """
    tree = ast.parse((SRC / "pipeline.py").read_text(encoding="utf-8"))
    referenced = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            referenced.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
    assert "execute_physical" not in referenced


def test_only_the_control_plane_calls_the_hardware_funnel():
    callers = []
    for path in python_sources():
        if path.name in {"printer.py", "control.py"}:
            continue
        if "execute_physical(" in path.read_text(encoding="utf-8"):
            callers.append(path.relative_to(SRC).as_posix())
    assert callers == [], f"unexpected callers of execute_physical: {callers}"


def test_no_cli_verb_starts_a_print():
    parser = cli.build_parser()
    subparsers = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    verbs = set()
    for action in subparsers:
        verbs.update(action.choices)
    assert verbs
    for banned in ("print", "start", "preheat", "home", "move", "run-print"):
        assert banned not in verbs, f"CLI verb {banned!r} exists"
    # And no verb, whatever it is named, is wired to the funnel.
    tree = ast.parse((SRC / "main.py").read_text(encoding="utf-8"))
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    referenced |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "execute_physical" not in referenced


def test_the_only_contract_operation_that_touches_hardware_is_printer_confirm():
    hardware_ops = [op for op in OPERATIONS if op.startswith("printer.")]
    assert hardware_ops == ["printer.confirm"]


def test_physical_printing_is_off_by_default(settings):
    assert settings.allow_physical_print is False
    assert settings.printer_transport == "simulator"


def test_capabilities_never_advertise_a_physical_print(control):
    caps = control.capabilities()
    assert caps["features"]["physical_print"] is False
    physical = [c for c in caps["capabilities"] if c["name"] == "physical-printer"]
    assert physical and physical[0]["available"] is False
    assert "BLOCKED BY HARDWARE" in physical[0]["reason"]


@pytest.mark.parametrize("action", ["start_print", "preheat", "move_axes"])
def test_no_action_reaches_hardware_through_the_default_configuration(control, action):
    import asyncio

    asyncio.run(control.jobs_create({
        "kind": "design", "job_id": f"noauto-{action}",
        "spec": {
            "name": "plate",
            "features": [{"primitive": {"id": "b", "kind": "box", "size_mm": [20, 20, 5]},
                          "operation": "add"}],
        },
    }))
    token = control.confirmation_for(f"noauto-{action}")["confirmation"]
    result = control.printer_confirm({
        "job_id": f"noauto-{action}", "action": action, "confirmation": token,
    })
    assert result["status"] == "SIMULATED"
    assert result["performed_physical_action"] is False
