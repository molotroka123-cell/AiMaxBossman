#!/usr/bin/env python3
"""Stage 3 (zone 'oss'): integration receipts for OSS projects Bossman really uses.

For each leaf below, run Bossman's OWN tests for that integration (and, where noted, a safe
local probe of Bossman's integration code against the real upstream tool) at the current HEAD:

  * Bossman code comes from this checkout (pytest pythonpath = command-center, bossman-core);
  * third-party packages come from the INSTALLED build's site-packages (first on PYTHONPATH), so
    the upstream libraries are the exact versions Bossman ships (docling, qdrant-client,
    opentimelineio, faster-whisper, mcp, prompt_toolkit, rich, playwright ...);
  * after the session the child prints, from sys.modules, which integration modules were
    loaded and where the upstream package resolved from: the receipt shows the integration
    code was exercised, not just that some test passed.

Temp HOME/APPDATA/LOCALAPPDATA/BCC_DATA_DIR, secrets stripped from env, no external network
calls, never touches :8801. Writes evidence/out/<node>.txt (refuses to overwrite) and
evidence/stage3.json (kind 'integration').

    python tools/tree_proof/stage3_oss_integration.py --list
    python tools/tree_proof/stage3_oss_integration.py --only oss-17 --out-dir <scratch>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CC = ROOT / "command-center"
EVID = ROOT / "docs" / "architecture" / "bossman-tree-20261005" / "evidence"
BUILD_SP = Path(r"C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-eba6dc592ad9\runtime\Lib\site-packages")
TIMEOUT = 900
SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)

# node_id -> project, pytest args (relative to command-center), integration code (repo paths),
# upstream top-level module (None = external service/CLI), optional extra probe name.
LEAVES: dict[str, dict] = {
    "oss-14": dict(project="searxng/searxng", upstream=None,
                   tests=["tests/test_searxng_setup.py", "tests/test_web_research_net.py", "-k", "searx"],
                   code=["command-center/bcc/features/web_research/net.py",
                         "command-center/bcc/features/web_research/config.py",
                         "command-center/bcc/features/web_research/sources.py"]),
    "oss-16": dict(project="ggml-org/llama.cpp", upstream=None,
                   tests=["tests/test_llamacpp_catalog.py"],
                   code=["command-center/bcc/providers.py", "command-center/bcc/discovery.py"]),
    "oss-17": dict(project="docling-project/docling", upstream="docling",
                   tests=["tests/test_oss_docling.py", "tests/test_master_parser_docling_adapter.py"],
                   code=["command-center/bcc/oss/docling.py",
                         "command-center/bcc/pit/master_parser/documents.py"]),
    "oss-19": dict(project="SYSTRAN/faster-whisper", upstream="faster_whisper",
                   tests=["tests/test_oss_whisper.py"],
                   code=["command-center/bcc/oss/whisper.py"]),
    "oss-20": dict(project="Comfy-Org/ComfyUI", upstream=None,
                   tests=["tests/test_oss_comfyui.py"],
                   code=["command-center/bcc/oss/comfyui.py"]),
    "oss-22": dict(project="GrapesJS/grapesjs", upstream=None,
                   tests=["tests/test_web_designer_visual.py", "tests/test_web_designer_grapesjs_ui.py"],
                   code=["command-center/bcc/web_designer_visual.py",
                         "command-center/ui/vendor/grapesjs/grapes.min.js",
                         "command-center/ui/pages/web_designer_visual.js"]),
    "oss-24": dict(project="hyperfield/ai-file-sorter", upstream=None,
                   tests=["tests/test_file_intelligence_contract.py"],
                   code=["command-center/bcc/file_intelligence/service.py",
                         "command-center/bcc/file_intelligence/discovery.py",
                         "integrations/ai-file-sorter/integration.json"]),
    "oss-28": dict(project="qdrant/qdrant-client", upstream="qdrant_client",
                   tests=["tests/test_oss_qdrant.py"],
                   code=["command-center/bcc/oss/qdrant.py"]),
    "oss-29": dict(project="openclaw/openclaw", upstream=None,
                   tests=["tests/test_v23_openclaw_bridge.py", "tests/test_v23_openclaw_tools.py"],
                   code=["command-center/bcc/v2/openclaw_bridge.py",
                         "command-center/bcc/features/tools_openclaw.py"]),
    "oss-50": dict(project="mltframework/shotcut", upstream=None,
                   tests=["tests/test_video_studio_shotcut.py"],
                   code=["command-center/bcc/video_studio/shotcut.py"]),
    "oss-51": dict(project="AcademySoftwareFoundation/OpenTimelineIO", upstream="opentimelineio",
                   tests=["tests/test_video_studio_interchange.py", "tests/test_video_studio_otio_blocked.py"],
                   code=["command-center/bcc/video_studio/interchange.py"]),
    "oss-67": dict(project="prompt-toolkit/python-prompt-toolkit", upstream="prompt_toolkit",
                   tests=["tests/test_terminal_cli_unit.py", "-k", "history"],
                   code=["command-center/bcc/terminal_cli/chat.py"], probe="prompt_toolkit_history"),
    "oss-68": dict(project="Textualize/rich", upstream="rich",
                   tests=["tests/test_terminal_cli_unit.py"],
                   code=["command-center/bcc/terminal_cli/console.py",
                         "command-center/bcc/terminal_cli/human.py",
                         "command-center/bcc/terminal_cli/theme.py"]),
    "oss-69": dict(project="openai/codex", upstream=None,
                   tests=["tests/test_autonomy_workers.py"],
                   code=["command-center/bcc/autonomy/workers.py", "command-center/bcc/rave/connectors.py"],
                   probe="codex_cli"),
    "oss-117": dict(project="modelcontextprotocol/python-sdk", upstream="mcp",
                    tests=["tests/test_v21_mcp.py", "tests/test_v26_mcp_sdk_path.py"],
                    code=["command-center/bcc/v2/mcp_runtime.py"]),
}


def _module_of(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    for prefix in ("command-center/", "bossman-core/"):
        if path.startswith(prefix):
            return path[len(prefix):-3].replace("/", ".")
    return None


# ------------------------------------------------------------------ child (inside the env)
def _probe_codex_cli() -> list[str]:
    import asyncio
    from bcc.autonomy import workers as W
    from bcc.rave.connectors import codex_version, resolve_cli
    lines, ok = [], True
    ver = asyncio.run(codex_version())
    lines.append(f"bcc.rave.connectors.codex_version() -> {json.dumps(ver, ensure_ascii=False)[:300]}")
    ok &= bool(ver.get("installed", True)) and bool(ver.get("version") or ver.get("raw"))
    base = resolve_cli("codex")
    lines.append(f"resolve_cli('codex') -> {base}")
    with tempfile.TemporaryDirectory() as td:
        flags = set()
        for role in ("writer", "reviewer"):
            argv = W.cli_argv("codex", role, cwd=Path(td), last_message=Path(td) / "m")
            flags |= {a.split("=")[0] for a in argv if a.startswith("--")}
            lines.append(f"workers.cli_argv('codex', {role!r}) flags: {sorted(a for a in argv if a.startswith('-'))}")
        help_text = subprocess.run([*base, "exec", "--help"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60).stdout
        missing = sorted(f for f in flags if f not in help_text)
        lines.append(f"`codex exec --help` knows every Bossman flag: {not missing} missing={missing}")
        ok &= not missing
    lines.append(f"PROBE {'PASS' if ok else 'FAIL'}")
    return lines


def _probe_prompt_toolkit_history() -> list[str]:
    from prompt_toolkit.history import FileHistory
    from bcc.terminal_cli.chat import _make_history
    lines = []
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "terminal" / "history.txt"
        hist = _make_history(path, True)
        secret = "sk-" + "ant-abcdefghijklmnop1234567890ABCDEF"
        hist.store_string("ключ " + secret + " и обычный текст")
        body = path.read_text(encoding="utf-8")
        ok = isinstance(hist, FileHistory) and secret not in body and "обычный текст" in body
        lines.append(f"_make_history -> {type(hist).__mro__[1].__module__}.{type(hist).__mro__[1].__name__} subclass;"
                     f" stored line redacted={secret not in body}; text kept={'обычный текст' in body}")
        mem = _make_history(path, False)
        lines.append(f"_make_history(enabled=False) -> {type(mem).__module__}.{type(mem).__name__}")
        ok &= type(mem).__name__ == "InMemoryHistory"
    lines.append(f"PROBE {'PASS' if ok else 'FAIL'}")
    return lines


def child(node: str) -> int:
    spec = LEAVES[node]
    os.chdir(CC)
    sys.path[:0] = [str(CC), str(ROOT / "bossman-core")]  # Bossman code from this checkout, never the build's copy
    import pytest
    rc = int(pytest.main(["-q", "-rs", "-p", "no:cacheprovider", "--timeout=600", *spec["tests"]]))
    print("\n@@ INTEGRATION CHECK (sys.modules after the test session) @@")
    loaded_all = True
    for path in spec["code"]:
        mod = _module_of(path)
        if mod is None:
            print(f"  asset {path}: exists={(ROOT / path).is_file()}")
            continue
        m = sys.modules.get(mod)
        f = getattr(m, "__file__", None) if m else None
        inside = bool(f) and os.path.normcase(os.path.abspath(f)).startswith(os.path.normcase(str(ROOT)))
        print(f"  {mod}: loaded={m is not None} from_checkout={inside} file={f}")
        loaded_all &= inside
    up = spec.get("upstream")
    if up:
        m = sys.modules.get(up)
        f = getattr(m, "__file__", None) if m else None
        ver = getattr(m, "__version__", None) if m else None
        if m is not None and not ver:
            try:
                from importlib.metadata import version
                ver = version({"docling": "docling-slim", "qdrant_client": "qdrant-client",
                               "faster_whisper": "faster-whisper", "prompt_toolkit": "prompt_toolkit"}.get(up, up))
            except Exception:  # noqa: BLE001
                ver = "?"
        print(f"  upstream {up}: loaded={m is not None} version={ver} file={f}")
    probe_ok = True
    if spec.get("probe"):
        print(f"\n@@ PROBE {spec['probe']} @@")
        try:
            lines = globals()["_probe_" + spec["probe"]]()
        except Exception as e:  # noqa: BLE001
            lines = [f"probe error {type(e).__name__}: {e}", "PROBE FAIL"]
        print("\n".join("  " + x for x in lines))
        probe_ok = lines[-1] == "PROBE PASS"
    print(f"\n@@ RESULT pytest_exit={rc} integration_modules_loaded={loaded_all} probe_ok={probe_ok} @@")
    return 0 if (rc == 0 and loaded_all and probe_ok) else 1


# ------------------------------------------------------------------ parent
def _env(tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    real_local = os.environ.get("LOCALAPPDATA", "")
    if real_local and "PLAYWRIGHT_BROWSERS_PATH" not in env and (Path(real_local) / "ms-playwright").is_dir():
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(real_local) / "ms-playwright")
    for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "BCC_DATA_DIR"):
        d = tmp / k.lower()
        d.mkdir(parents=True, exist_ok=True)
        env[k] = str(d)
    env["PYTHONPATH"] = str(BUILD_SP)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _scrub(text: str) -> str:
    sys.path.insert(0, str(ROOT / "tools"))
    from tree_apply_evidence import scrub
    return scrub(text)


def run(node: str, out_dir: Path, sha: str) -> dict:
    spec = LEAVES[node]
    for p in spec["code"]:
        if not (ROOT / p).exists():
            raise SystemExit(f"{node}: integration path missing: {p}")
    out = out_dir / f"{node}.txt"
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    cmd = [sys.executable, str(Path(__file__).relative_to(ROOT)).replace("\\", "/"), "--child", node]
    shown = (f"PYTHONPATH=<installed build site-packages> python tools/tree_proof/stage3_oss_integration.py "
             f"--child {node}   # = cd command-center && pytest -q -rs {' '.join(spec['tests'])} + sys.modules check"
             + (f" + probe {spec['probe']}" if spec.get("probe") else ""))
    started = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="stage3-") as td:
        try:
            p = subprocess.run(cmd, cwd=ROOT, env=_env(Path(td)), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=TIMEOUT)
            code, body = p.returncode, (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr.strip() else "")
        except subprocess.TimeoutExpired as e:
            code, body = 124, f"TIMEOUT after {TIMEOUT}s\n{e.stdout or ''}"
    finished = datetime.now(timezone.utc)
    header = (f"# stage3 oss integration receipt output\nnode: {node}\nproject: {spec['project']}\n"
              f"sha: {sha}\nbuild_site_packages: {BUILD_SP}\nintegration_code: {', '.join(spec['code'])}\n"
              f"command: {shown}\nexit_code: {code}\n\n")
    text = _scrub(header + body.replace("\r\n", "\n"))
    out.write_bytes(text.encode("utf-8"))
    passed = re.search(r"(\d+) passed", body)
    summary = re.findall(r"^=*\s*(\d+ (?:passed|failed|error|skipped|deselected).*?) in [\d.]+s", body, re.M)
    probe = (f"Bossman's own tests for its {spec['project']} integration at HEAD with the installed build's "
             f"third-party packages: {summary[-1] if summary else 'no summary'}; integration modules loaded from checkout"
             + (f"; live local probe {spec['probe']}" if spec.get("probe") else ""))
    return dict(node_id=node, sha=sha, probe=probe[:400], command=shown, exit_code=code,
                started_at=started.strftime("%Y-%m-%dT%H:%M:%SZ"), finished_at=finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
                output_sha256=hashlib.sha256(out.read_bytes()).hexdigest(), output_tail=text[-1500:],
                verdict="PASS" if code == 0 and passed else "FAIL", kind="integration",
                integration_code=list(spec["code"]), project=spec["project"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--child")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--out-dir", type=Path, default=EVID / "out")
    ap.add_argument("--receipts", type=Path, default=EVID / "stage3.json")
    a = ap.parse_args(argv)
    if a.child:
        return child(a.child)
    if a.list:
        for k, v in LEAVES.items():
            print(k, v["project"], v["tests"])
        return 0
    nodes = [n for n in a.only.split(",") if n] or list(LEAVES)
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    existing = json.loads(a.receipts.read_text(encoding="utf-8")) if a.receipts.is_file() else []
    for n in nodes:
        t0 = time.time()
        rc = run(n, a.out_dir, sha)
        existing = [r for r in existing if r.get("node_id") != n] + [rc]
        a.receipts.write_text(json.dumps(existing, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"{n} {rc['project']}: {rc['verdict']} exit={rc['exit_code']} ({time.time() - t0:.0f}s) {rc['probe'][-160:]}",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
