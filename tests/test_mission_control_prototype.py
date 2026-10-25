"""Structural smoke checks for standalone Mission Control prototype.
Does not imply Windows runtime/hardware acceptance.
"""
from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "tools" / "mission_control_prototype" / "index.html"

def test_three_panels_and_sources():
    html = HTML.read_text(encoding="utf-8")
    for marker in ("01 / Hardware", "02 / Agents & Tasks", "03 / Tokens & Cost",
                   "DEMO SIMULATION", "REAL / VERIFIED", "NOT CONNECTED", "source:mode"):
        assert marker in html, marker

def test_demo_only_mutation_and_no_network():
    html = HTML.read_text(encoding="utf-8")
    assert "mode!=='demo'" in html
    assert "disabled=!demo" in html
    assert "fetch(" not in html
    assert "WebSocket(" not in html
    assert "api_key" not in html
