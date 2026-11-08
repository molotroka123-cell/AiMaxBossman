"""Tests for Creator Mode (bcc/creator_mode, bcc/features/creator_mode.py, ui/pages/creator_mode.js)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from bcc.creator_mode.blueprints import (
    VALID_KINDS,
    default_weight_telemetry,
    ensure_complete_build,
    parse_creator_prompt,
    score_build,
)
from bcc.creator_mode.service import CreatorModeService

UI_CREATOR = Path(__file__).resolve().parents[1] / "ui" / "pages" / "creator_mode.js"
UI_INDEX = Path(__file__).resolve().parents[1] / "ui" / "pages" / "index.js"


def test_parse_creator_prompt_slash_and_context():
    spec = parse_creator_prompt("/3d @weights @memory Собери 3D LLM визуализатор весов")
    assert spec.kind == "visualizer_3d"
    assert spec.slash_command == "/3d"
    assert "@weights" in spec.context_tags
    assert "@memory" in spec.context_tags
    assert "3D LLM" in spec.clean_request


def test_all_four_blueprints_pass_six_gate_evaluator():
    prompts = {
        "plugin": "/plugin @guards @memory Собери плагин проверки G1-G7",
        "design_console": "/console @memory Собери дизайн-консоль владельца",
        "game": "/game @swapme Собери аркаду SwapMe Gate Runner",
        "visualizer_3d": "/3d @weights Собери 3D LLM визуализатор весов и дельт LoRA",
    }
    telemetry = default_weight_telemetry()
    for kind in VALID_KINDS:
        files, used_llm = ensure_complete_build(
            kind,
            None,
            title=f"Test {kind}",
            slug=f"test-{kind.replace('_', '-')}",
            request=prompts[kind],
            telemetry=telemetry,
        )
        assert used_llm is False
        score = score_build(kind, files, elapsed_s=0.05, tokens_out=400, used_llm=False)
        assert score["passed_all_gates"] is True, f"{kind} failed: {score}"
        assert score["total_score"] == 100


def test_creator_service_lifecycle_and_preview(tmp_path: Path):
    svc = CreatorModeService(data_dir=tmp_path)
    meta1 = asyncio.run(
        svc.create_project(
            {
                "request": "/plugin @guards @memory Собери плагин контроля дельт",
                "model": "instant",
            }
        )
    )
    slug = meta1["slug"]
    assert meta1["current"] == 1
    assert meta1["versions"][-1]["score"]["total_score"] == 100

    # Iterate v2 on same project
    meta2 = asyncio.run(
        svc.create_project(
            {
                "request": "/plugin @guards Добавь проверку git status и SHA",
                "slug": slug,
                "model": "instant",
            }
        )
    )
    assert meta2["current"] == 2

    html, csp = svc.preview_html(slug, 2)
    assert "<!doctype html>" in html.lower()
    assert "connect-src 'none'" in csp


def test_creator_ui_static_safety():
    src = UI_CREATOR.read_text(encoding="utf-8")
    assert "export const PREVIEW_SANDBOX = 'allow-scripts'" in src
    assert "allow-same-origin" not in src
    assert "srcdoc" not in src
    assert "innerHTML" not in src
    assert "eval(" not in src

    idx = UI_INDEX.read_text(encoding="utf-8")
    assert "id: 'creator-mode'" in idx
    assert "import('./creator_mode.js')" in idx
