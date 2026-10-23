#!/usr/bin/env python3
"""Сканер экспозиции к скомпрометированным компонентам («Чёрный октябрь», 23.10.2026).

Что делает (только чтение, без сети, без вывода содержимого файлов):
  1. Манифесты репозитория (requirements*, pyproject, package.json, Dockerfile*, docker-compose*, workflows, *.lock):
     DENY      — закреплён известно-плохой релиз (например, litellm==1.107.0 или образ :latest/:main-latest);
     UNPINNED  — компонент из deny-списка без точного безопасного закрепления (fail closed: мог подтянуть плохой релиз);
     PRESENT   — компонент найден, но безопасен/из списка наблюдения (nginx, gitlab, php, wordpress, plex).
  2. Окружения Python (venv): установленный плохой релиз и признаки закрепления в старте интерпретатора
     (.pth / sitecustomize / usercustomize с сетевыми или исполняющими вызовами) — INDICATOR.
Код возврата: 0 — PASS, 1 — FAIL (DENY/UNPINNED), 2 — требуется ручной разбор (INDICATOR).

Источник правил — docs/owner/advisories/*.json. Статус каждого правила в нём честный: CORROBORATED/UNVERIFIED.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHLIST = ROOT / "docs" / "owner" / "advisories" / "black-october-20261023.json"

PRUNE_DIRS = {".git", "node_modules", ".venv", "venv", "site-packages", "build", "dist", "__pycache__", ".tox",
              ".mypy_cache", ".pytest_cache"}
MANIFEST_GLOBS = ("requirements*.txt", "requirements*.lock", "constraints*.txt", "pyproject.toml", "package.json",
                  "Dockerfile*", "docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml", "*.lock")
MAX_MANIFEST_BYTES = 2_000_000
MAX_HOOK_BYTES = 65_536
# Сильные признаки: сеть, процессы, декодирование. Слабые (exec/eval/__import__) встречаются в легитимных .pth.
_STRONG = re.compile(r"(subprocess|os\.system|popen|urlopen|urllib|requests\.|socket|https?://|\bcurl\b|powershell|"
                     r"ctypes|marshal|zlib\.decompress|base64|b64decode|litellm)", re.IGNORECASE)
_WEAK = re.compile(r"(\bexec\s*\(|\beval\s*\(|__import__\s*\()")
_BENIGN_MARKERS = ("_distutils_hack", "coverage.process_startup", "pywin32_bootstrap", "__editable__",
                   "sys._getframe(1).f_locals['sitedir']")      # последний — шаблон setuptools для *-nspkg.pth
_DIST_INFO = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+?)-(?P<ver>[0-9][\w.!+]*)\.(?:dist-info|egg-info)$")


class WatchlistError(ValueError):
    pass


@dataclass(frozen=True)
class Finding:
    kind: str          # DENY | UNPINNED | PRESENT | INDICATOR
    component: str
    path: str
    line: int | None = None
    detail: str = ""


def load_watchlist(path: Path | str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WatchlistError(f"watchlist не читается: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("deny"), list) or not isinstance(data.get("watch"), list):
        raise WatchlistError("watchlist: нужны списки deny и watch")
    for rule in data["deny"]:
        if (not isinstance(rule, dict) or not isinstance(rule.get("component"), str)
                or not isinstance(rule.get("versions"), list) or not rule["versions"]
                or not isinstance(rule.get("status"), str)):
            raise WatchlistError("deny-правило: нужны component, versions (непустой список) и status")
    for item in data["watch"]:
        if not isinstance(item, dict) or not isinstance(item.get("component"), str):
            raise WatchlistError("watch-элемент: нужен component")
    return data


def _name_re(name: str) -> re.Pattern[str]:
    return re.compile(r"(?i)(?<![\w.\-])" + re.escape(name) + r"(?![\w\-])")


def _is_manifest(name: str, rel_parts: tuple[str, ...]) -> bool:
    if any(fnmatch.fnmatch(name, g) for g in MANIFEST_GLOBS):
        return True
    return ".github" in rel_parts and "workflows" in rel_parts and name.endswith((".yml", ".yaml"))


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS and not d.lower().startswith(("venv-", "venv_"))
                       and not d.lower().endswith(("-venv", "_venv", ".venv"))]
        yield Path(dirpath), filenames


_REQ_FILES = ("requirements*.txt", "requirements*.lock", "constraints*.txt", "pyproject.toml", "package.json", "*.lock")
_IMAGE_LINE = re.compile(r"(?i)(\bimage\s*:|^\s*from\s|docker\s+(pull|run|compose)|\bcompose\b)")
_PIP_LINE = re.compile(r"(?i)(pip3?\s+install|uv\s+(pip\s+)?(install|add)|poetry\s+add|pipx\s+install)")


def _classify_deny(line: str, rule: dict, name_re: re.Pattern[str], fname: str = "") -> str | None:
    m = name_re.search(line)
    if not m:
        return None
    versions = {str(v) for v in rule["versions"]}
    tags = {str(t) for t in rule.get("docker_tags", [])}
    rest = line[m.end():]
    # 1) явное закрепление pip/npm-стиля — оцениваем на любой строке
    pin = re.match(r"\s*(?:\[[^\]]*\])?\s*={2,3}\s*v?(?P<v>[\w.]+)", rest)
    if pin:
        return "DENY" if pin.group("v") in versions else "PRESENT"
    is_image = bool(_IMAGE_LINE.search(line))
    if is_image:
        if re.match(r"@sha256:[a-f0-9]{64}\b", rest):
            return "PRESENT"                                               # digest — неизменяемая ссылка
        var = re.match(r":\$\{[A-Za-z_]\w*(?P<op>:?[?\-=+])(?P<arg>[^}]*)\}", rest)
        if var:
            if var.group("op").endswith("?"):
                return "PRESENT"                                           # тег обязателен: без него compose не стартует
            default = var.group("arg")
            return "DENY" if (default in tags or default.lstrip("v") in versions) else "UNPINNED"
        tag = re.match(r":(?P<tag>[\w.\-]+)", rest)
        if tag:
            t = tag.group("tag")
            return "DENY" if (t in tags or t.lstrip("v") in versions) else "PRESENT"
        return "UNPINNED"                                                  # образ без тега = latest
    # 2) объявление зависимости: имя в начале строки манифеста или pip/uv/poetry install
    if _PIP_LINE.search(line):
        return "UNPINNED"
    if any(fnmatch.fnmatch(fname, g) for g in _REQ_FILES) and re.match(r"""\s*[-\"']*\s*[\"']?\s*""" + re.escape(rule["component"]),
                                                                       line, re.IGNORECASE):
        return "UNPINNED"
    return None


def scan_manifests(root: Path | str, watchlist: dict) -> list[Finding]:
    root = Path(root)
    deny = [(r, _name_re(r["component"])) for r in watchlist["deny"]]
    watch = []
    for w in watchlist["watch"]:
        pats = w.get("patterns") or [re.escape(w["component"])]
        watch.append((w["component"], re.compile(r"(?i)(?<![\w.\-])(?:" + "|".join(pats) + r")(?![\w\-])")))
    findings: list[Finding] = []
    for folder, files in _walk(root):
        for fname in files:
            full = folder / fname
            rel = full.relative_to(root)
            if not _is_manifest(fname, rel.parts):
                continue
            try:
                if full.stat().st_size > MAX_MANIFEST_BYTES:
                    continue
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            seen_watch: set[str] = set()
            for n, raw in enumerate(text.splitlines(), 1):
                stripped = raw.strip()
                if not stripped or stripped.startswith(("#", "//")):
                    continue
                for rule, rx in deny:
                    kind = _classify_deny(raw, rule, rx, fname)
                    if kind:
                        findings.append(Finding(kind, rule["component"], rel.as_posix(), n))
                for comp, rx in watch:
                    if comp not in seen_watch and rx.search(raw):
                        seen_watch.add(comp)
                        findings.append(Finding("PRESENT", comp, rel.as_posix(), n))
    return findings


def find_site_packages(roots: list[Path | str], max_depth: int = 6) -> list[Path]:
    found: list[Path] = []
    for root in map(Path, roots):
        if not root.exists():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if "pyvenv.cfg" in filenames:
                for cand in [here / "Lib" / "site-packages", *sorted((here / "lib").glob("python*/site-packages"))]:
                    if cand.is_dir():
                        found.append(cand)
                dirnames[:] = []
                continue
            if len(here.parts) - base_depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
    return sorted(set(found))


def _hook_verdict(text: str) -> str | None:
    strong = _STRONG.findall(text)
    weak = _WEAK.findall(text)
    if strong:
        return "network/process/decode calls"
    if weak and not any(mk in text for mk in _BENIGN_MARKERS):
        return "dynamic exec without a known benign marker"
    return None


def scan_site_packages(site_dirs: list[Path | str], watchlist: dict) -> list[Finding]:
    findings: list[Finding] = []
    rules = {r["component"].lower().replace("_", "-"): r for r in watchlist["deny"]}
    for sp in map(Path, site_dirs):
        try:
            entries = list(sp.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                m = _DIST_INFO.match(entry.name)
                if m:
                    name = m.group("name").lower().replace("_", "-")
                    rule = rules.get(name)
                    if rule:
                        bad = m.group("ver") in {str(v) for v in rule["versions"]}
                        findings.append(Finding("DENY" if bad else "PRESENT", rule["component"], str(entry), None,
                                                f"installed {m.group('ver')}"))
            elif entry.suffix == ".pth" or entry.name in ("sitecustomize.py", "usercustomize.py"):
                try:
                    if entry.stat().st_size > MAX_HOOK_BYTES:
                        findings.append(Finding("INDICATOR", "python-startup-hook", str(entry), None, "unusually large"))
                        continue
                    text = entry.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if entry.suffix == ".pth" and not any(
                        ln.strip().startswith("import ") or ln.strip().startswith("import\t") for ln in text.splitlines()):
                    # чистый список путей: исполняемых строк нет, но подозрительные токены всё равно проверим
                    why = "network/process/decode calls" if _STRONG.search(text) else None
                else:
                    why = _hook_verdict(text)
                if why:
                    findings.append(Finding("INDICATOR", "python-startup-hook", str(entry), None, why))
    return findings


def build_report(findings: list[Finding], *, advisory_id: str) -> dict:
    counts = {k: sum(1 for f in findings if f.kind == k) for k in ("DENY", "UNPINNED", "INDICATOR", "PRESENT")}
    verdict = "FAIL" if counts["DENY"] or counts["UNPINNED"] else "REVIEW" if counts["INDICATOR"] else "PASS"
    return {"schema": "bossman.advisory_exposure.v1", "advisory_id": advisory_id,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "verdict": verdict, "counts": counts, "findings": [asdict(f) for f in findings]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST))
    ap.add_argument("--env-root", action="append", default=[], help="папка, где искать venv (можно несколько раз)")
    ap.add_argument("--system-python", action="store_true", help="проверить и site-packages текущего интерпретатора")
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--json", help="куда записать отчёт")
    args = ap.parse_args(argv)
    try:
        wl = load_watchlist(args.watchlist)
    except WatchlistError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    findings = scan_manifests(args.repo, wl)
    sites = find_site_packages(args.env_root, args.max_depth) if args.env_root else []
    if args.system_python:
        import site
        sites += [Path(p) for p in site.getsitepackages() if Path(p).is_dir()]
    findings += scan_site_packages(sites, wl)
    report = build_report(findings, advisory_id=str(wl.get("advisory_id", "unknown")))
    report["environments_scanned"] = len(sites)
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    c = report["counts"]
    print(f"advisory={report['advisory_id']} verdict={report['verdict']} DENY={c['DENY']} UNPINNED={c['UNPINNED']} "
          f"INDICATOR={c['INDICATOR']} PRESENT={c['PRESENT']} environments={report['environments_scanned']}")
    for f in findings:
        if f.kind != "PRESENT":
            where = f"{f.path}:{f.line}" if f.line else f.path
            print(f"  {f.kind:9} {f.component}  {where}  {f.detail}".rstrip())
    return {"FAIL": 1, "REVIEW": 2, "PASS": 0}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
