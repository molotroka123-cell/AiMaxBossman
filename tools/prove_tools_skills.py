#!/usr/bin/env python
"""Independent proof that Bossman TOOLS and SKILLS work (verifier, 2026-10-22). WIP driver.

Usage:  python tools/prove_tools_skills.py [--with-forks]
Runs the in-repo tool probes against a synthetic sandbox (temp LOCALAPPDATA/BCC_DATA_DIR),
fills every registry tool that has no probe yet with NOT_RUN, adds the Argon-claims table and
writes docs/audits/2026-10-22-tools-skills-proof/{proof_matrix.json,PROOF_MATRIX_RU.md}.
Exit code 1 if any row is FAILED. Statuses are never upgraded: no probe = NOT_RUN.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.tools_skills_proof import common  # noqa: E402
from tools.tools_skills_proof.common import (FAILED, NOT_RUN, PROVEN_EXECUTED, PROVEN_GATED, STATUSES,  # noqa: E402
                                              STRUCTURAL_ONLY, Row, counts)

OUT = REPO / common.OUT_REL

#: Argon claims re-verified by reading the tests (see evidence/argon_*.log for the real runs).
ARGON = [
    ("test_mobile_opencode_agent.py runs green", PROVEN_EXECUTED, "19 passed in 50s (16 scenarios, test_08 has 4 params); apps/bossman-mobile test_mobile_opencode.py 2 passed; 0 skips", "evidence/argon_*.log"),
    ("01 chat basic response", STRUCTURAL_ONLY, "asserts session/mode/route tier/task_id only; workers are not started, no model reply is checked", ""),
    ("02 multi-step agent tool execution", STRUCTURAL_ONLY, "asserts a 'task.progress' event exists; no tool is called, no model runs", ""),
    ("03 file read + patch + test", STRUCTURAL_ONLY, "file READ via /api/terminal/files is real; patch+tests are only a posted message (mode asserted), nothing patched", ""),
    ("04 web terminal run + WS + diff", PROVEN_EXECUTED, "real 'git status --short' subprocess, WebSocket first frame and /terminal/diff ok", ""),
    ("05 Ctrl+C and STOP", STRUCTURAL_ONLY, "sigint/stop endpoints return ok:true; the test never checks the process actually ended", ""),
    ("06 screenshot + window list (COMPUTER)", STRUCTURAL_ONLY, "only checks the 10 tool groups and that computer.observe is LISTED; no screenshot is taken", ""),
    ("07 computer action approval", STRUCTURAL_ONLY, "test inserts its own approval row and approves it; does not show computer.act is gated (that gate is proven separately: computer.act PROVEN_GATED)", ""),
    ("08 dangerous commands -> 403", PROVEN_EXECUTED, "4 real POSTs through TerminalPolicy all 403", ""),
    ("09 local->cloud fallback router", PROVEN_EXECUTED, "real select_route_for_request logic; it is route SELECTION, no model call/fallback happens", "selection only"),
    ("10 budget stop + RunPod guard", STRUCTURAL_ONLY, "402/403 come from the router PREVIEW endpoint; no real run is stopped by a budget", ""),
    ("11 WS reconnect + replay", PROVEN_EXECUTED, "real WebSocket, seq replay with replayed:true", ""),
    ("12 persistence after restart", PROVEN_EXECUTED, "second app on same data_dir loads session/budget/turns", ""),
    ("13 parallel isolation", STRUCTURAL_ONLY, "two sessions keep their own turn text; nothing runs concurrently", ""),
    ("14 /improve 16 steps + MissionReceipt", NOT_RUN, "pipeline body not re-verified in this WIP (test passes; its depth not read)", ""),
    ("15 secret redaction", PROVEN_EXECUTED, "emit() with an sk- key: stored event has no key (regex redact_structure, event level only)", "regex/event level; terminal output not tested"),
    ("16 AUTO bypass keeps DENY", PROVEN_EXECUTED, "python -c print runs with bypass_permissions; rm -rf / stays 403", ""),
    ("/m/api gateway test", STRUCTURAL_ONLY, "bossman-mobile test talks to a FakeBossman upstream: proves proxying, not the backend", ""),
    ("live :8801 exposes /api/agent/*", FAILED, "live runtime build 288f79f56a1a (v2.6.0) answers 404 for /api/agent/tools; that commit predates agent_opencode.py. Acceptance doc cites 099b497f", "checked read-only 2026-10-21"),
    ("Stage 6 +40% learning gain", NOT_RUN, "not re-verified in this WIP (skills fork incomplete)", ""),
]

PHONE_AGENT = ["computer.observe", "computer.act", "terminal.run", "terminal.status", "code.search", "code.expand", "code.status",
               "browser.open", "browser.read_dom", "browser.screenshot"]


async def tools_section(with_forks: bool) -> list[Row]:
    from tools.tools_skills_proof.harness import Lab, tool_row
    from tools.tools_skills_proof import probes_core
    from bcc.tools import REGISTRY
    root = common.new_sandbox()
    common.isolate_env(root)
    common.assert_isolated()
    rows: list[Row] = []
    async with Lab(root) as lab:
        rows += await probes_core.run(lab, root / "core")
        if with_forks:
            for mod in ("probes_browser", "probes_video", "probes_external"):
                try:
                    m = importlib.import_module(f"tools.tools_skills_proof.{mod}")
                    rows += await asyncio.wait_for(m.run(lab, root / mod), 240)
                except Exception as exc:  # noqa: BLE001
                    rows.append(Row(kind="tool", id=f"[{mod}]", status=NOT_RUN, evidence=f"module not run: {type(exc).__name__}: {exc}"[:200]))
        have = {r.id for r in rows}
        for spec in REGISTRY.all():
            if spec.name not in have:
                rows.append(tool_row(spec.name, NOT_RUN, "no probe completed for this tool in this WIP run (not claimed)",
                                     "probe family unfinished at shutdown; nothing is claimed for this tool"))
    for r in rows:
        r.live_note = r.live_note or ("in the phone Gemma agent tool list" if r.id in PHONE_AGENT else "")
    return rows


def skills_section() -> tuple[list[Row], dict]:
    """Catalog/local skills via tools_skills_proof.skills_section (reads saved pytest logs, re-runs selection)."""
    try:
        from tools.tools_skills_proof import skills_section as ss
        root = common.new_sandbox("proof-skills-")
        return ss.run(root)
    except Exception as exc:  # noqa: BLE001 - a crashed section is reported, never hidden
        return ([Row(kind="skill", id="[skills_section]", status=NOT_RUN,
                     evidence=f"section did not run: {type(exc).__name__}: {exc}"[:200])],
                {"outcome": {"verdict": "OUTCOME_NOT_RUN", "reason": f"{type(exc).__name__}: {exc}"[:200]}})


def write_outputs(tools: list[Row], claims: list[Row], skills: list[Row], outcome: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    allrows = {"tools": [r.as_dict() for r in tools], "claims": [r.as_dict() for r in claims],
               "skills": [r.as_dict() for r in skills], "outcome": outcome,
               "counts": {"tools": counts(tools), "claims": counts(claims), "skills": counts(skills)}}
    common.dump_json(OUT / "proof_matrix.json", allrows)
    ct, cc, cs = counts(tools), counts(claims), counts(skills)
    verdict = str((outcome.get("outcome") or outcome).get("verdict") or (outcome.get("outcome") or outcome).get("status") or "OUTCOME_NOT_RUN")
    md = ["# Матрица доказательств: инструменты и навыки Bossman (WIP, 22.10.2026)", "",
          "Независимая проверка на `origin/main 09d4a025`, синтетические данные, песочница вместо LOCALAPPDATA. "
          "Статус не повышался: нет пробы = NOT_RUN. PROVEN_EXECUTED у навыка = доказан ВЫБОР и ДОСТАВКА текста, "
          "не изменение результата.", "",
          f"**Инструменты ({ct['TOTAL']}):** " + ", ".join(f"{s}={ct[s]}" for s in STATUSES),
          f"**Навыки ({cs['TOTAL']}):** " + ", ".join(f"{s}={cs[s]}" for s in STATUSES),
          f"**Заявления Argon ({cc['TOTAL']}):** " + ", ".join(f"{s}={cc[s]}" for s in STATUSES),
          f"**Меняет ли навык результат:** `{verdict}`", "",
          "## Инструменты", "", "| tool | группа | право | эффект | статус | доказательство | оговорка |", "|---|---|---|---|---|---|---|"]
    for r in tools:
        md.append(f"| {r.id} | {r.group} | {r.permission} | {r.effect} | {r.status} | {r.evidence[:170].replace('|', '/')} | {r.caveat[:110].replace('|', '/')} |")
    md += ["", "## Навыки", "", "| skill | статус | доказательство | оговорка |", "|---|---|---|---|"]
    for r in skills:
        md.append(f"| {r.id} | {r.status} | {r.evidence[:170].replace('|', '/')} | {r.caveat[:110].replace('|', '/')} |")
    md += ["", "## Заявления Argon", "", "| заявление | статус | доказательство |", "|---|---|---|"]
    for r in claims:
        md.append(f"| {r.id} | {r.status} | {r.evidence[:200].replace('|', '/')} |")
    md += ["", "## Что НЕ доказано", "",
           f"- Изменение результата навыком: `{verdict}`. Выбор и доставка текста доказаны для каталога; "
           "49 локальных .agents/skills только распарсены (STRUCTURAL_ONLY); чат-агент Gemma на телефоне текст навыков не получает вовсе.",
           "- Инструменты со статусом NOT_RUN выше: пробы browser/video/plugin/openclaw/opencode/studio/war_terminal/code:*/web.* не завершены.",
           "- computer.observe не запускался (снимает реальный экран владельца).",
           "- code.expand: обработчик работает, но code.search не печатает chunk_hash, модель не может получить аргумент.",
           "- Живой рантайм :8801 (288f79f56a1a) не содержит /api/agent/*.",
           "- Сравнение с моделью (A/B): OUTCOME_NOT_RUN (свободно ~46.9 ГБ < 50 ГБ)."]
    (OUT / "PROOF_MATRIX_RU.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> int:
    tools = asyncio.run(tools_section("--with-forks" in sys.argv))
    claims = [Row(kind="claim", id=c, status=s, evidence=e, caveat=cv) for c, s, e, cv in ARGON]
    skills, outcome = skills_section()
    write_outputs(tools, claims, skills, outcome)
    ct, cc, cs = counts(tools), counts(claims), counts(skills)
    print("TOOLS ", ct)
    print("SKILLS", cs)
    print("CLAIMS", cc)
    print("OUTCOME", json.dumps(outcome.get("outcome", outcome), ensure_ascii=False)[:300])
    return 1 if (ct[FAILED] or cc[FAILED] or cs[FAILED]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
