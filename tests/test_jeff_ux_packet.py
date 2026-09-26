from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("jeff_ux_packet", ROOT / "tools" / "jeff_ux_packet.py")
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_allowed_surface_is_explicit_and_small():
    assert len(mod.ALLOWED_BRANCH_FILES) <= 20
    assert "command-center/ui/jeff.js" in mod.ALLOWED_BRANCH_FILES
    assert "command-center/bcc/jeff_desktop.py" in mod.ALLOWED_BRANCH_FILES
    assert "command-center/bcc/api.py" not in mod.ALLOWED_BRANCH_FILES


def test_runtime_scan_finds_no_owner_authority_on_current_branch():
    findings = mod.scan_authority(mod.RUNTIME_FILES)
    assert findings == ()


def test_recommended_tests_are_targeted_not_full_repo():
    commands = mod.recommended_tests()
    assert commands
    joined = "\n".join(commands)
    assert "test_jeff_ux_isolation.py" in joined
    assert "test_jeff_ux_browser.py" in joined
    assert "pytest -q" not in joined
    assert "command-center/tests -q" not in joined


def test_markdown_packet_is_compact_and_actionable():
    packet = mod.Packet(
        canonical_branch="main",
        canonical_sha="a" * 40,
        jeff_branch="jeff",
        jeff_sha="b" * 40,
        merge_base="c" * 40,
        changed_files=("command-center/ui/jeff.js",),
        unexpected_files=(),
        authority_findings=(),
        targeted_tests=("python -m pytest x -q",),
        status="PASS",
    )
    rendered = mod.render_markdown(packet)
    assert "STATUS=PASS" in rendered
    assert "Do not read the whole repo" in rendered
    assert len(rendered) < 5000
