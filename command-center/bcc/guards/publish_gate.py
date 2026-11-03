"""G6 — publish gate: a publish / release action runs only when ALL of these pass.

  1. the working tree is committed (`git status --porcelain` empty — no modified, staged or untracked files);
  2. the project's build gate passes: a configurable command (default `bash tools/check.sh`) exits 0 AND prints the
     expected marker (default `GATE PASS`);
  3. G3 config references: no undefined `Module.FIELD` use;
  4. G4 media provenance: no audio/video under the project matches the registry of third-party files
     (no registry configured -> the step FAILS: an unverifiable upload is not uploaded);
  5. G5 content policy: no denylist hit in code/strings of the diff since `base` (or the whole tracked tree when no
     base), and the reviewer model did not FLAG.
Produces a short Russian report. `assert_publishable` raises PublishRefused — callers that publish must call it."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import config_refs, content_policy, media_provenance

DEFAULT_CHECK = ["bash", "tools/check.sh"]
DEFAULT_EXPECT = "GATE PASS"
#: files that SHIP with the game (code, data, strings). Design docs (.md) discuss the rules themselves («без крипты»)
#: and are not published to players — scanning them is noise (03.11 live run: 311 hits, mostly docs).
TEXT_SUFFIXES = (".luau", ".lua", ".json", ".csv", ".txt", ".rbxmx", ".model.json")


class PublishRefused(PermissionError):
    pass


@dataclass
class Step:
    name: str
    ok: bool
    detail: str


@dataclass
class GateResult:
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)

    def report_ru(self) -> str:
        lines = [f"Проверка публикации: {'ПРОЙДЕНА — можно публиковать' if self.ok else 'ОТКАЗ — публикация запрещена'}"]
        for s in self.steps:
            lines.append(f"{'✔' if s.ok else '✘'} {s.name}: {s.detail.splitlines()[0][:220] if s.detail else ''}")
            if not s.ok:
                lines += ["    " + ln[:200] for ln in s.detail.splitlines()[1:8]]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "steps": [s.__dict__ for s in self.steps], "report_ru": self.report_ru()}


def _git(root: Path, *args: str, run: Callable[..., Any] = subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
               errors="replace", timeout=120, check=False)


def tree_clean(root: Path, *, run: Callable[..., Any] = subprocess.run) -> Step:
    r = _git(root, "status", "--porcelain", run=run)
    if r.returncode != 0:
        return Step("Рабочее дерево закоммичено", False, f"git status не выполнен: {(r.stderr or '').strip()[:200]}")
    dirty = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if dirty:
        return Step("Рабочее дерево закоммичено", False,
                    f"незакоммиченных путей: {len(dirty)}\n" + "\n".join(dirty[:10]))
    head = _git(root, "rev-parse", "--short", "HEAD", run=run)
    return Step("Рабочее дерево закоммичено", True, f"чисто, HEAD {(head.stdout or '').strip()}")


def build_check(root: Path, command: list[str] | None = None, expect: str = DEFAULT_EXPECT, *,
                timeout: float = 900.0, run: Callable[..., Any] = subprocess.run) -> Step:
    cmd = list(command or DEFAULT_CHECK)
    try:
        r = run(cmd, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Step("Сборочная проверка", False, f"{' '.join(cmd)}: {type(exc).__name__}: {exc}"[:300])
    out = (r.stdout or "") + (r.stderr or "")
    ok = r.returncode == 0 and expect in out
    tail = "\n".join(out.strip().splitlines()[-6:])
    return Step("Сборочная проверка", ok,
                f"{' '.join(cmd)} -> код {r.returncode}, маркер «{expect}» {'есть' if expect in out else 'НЕТ'}\n{tail}")


def diff_lines(root: Path, base: str | None, *, run: Callable[..., Any] = subprocess.run) -> list[tuple[str, int, str]]:
    """Added lines of shipped files since `base`; without a base — every line of the tracked shipped files."""
    if base:
        r = _git(root, "diff", "--unified=0", "--no-color", f"{base}...HEAD", run=run)
        return [ln for ln in content_policy.added_lines(r.stdout or "") if ln[0].lower().endswith(TEXT_SUFFIXES)]
    files = (_git(root, "ls-files", run=run).stdout or "").splitlines()
    out: list[tuple[str, int, str]] = []
    for f in files:
        if not f.lower().endswith(TEXT_SUFFIXES):
            continue
        try:
            text = (root / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out += [(f, i + 1, t) for i, t in enumerate(text.splitlines())]
    return out


async def evaluate(root: str | Path, *, check_cmd: list[str] | None = None, expect: str = DEFAULT_EXPECT,
                   registry: list[dict[str, Any]] | None = None, media_paths: list[str | Path] | None = None,
                   base: str | None = None, reviewer: Callable[[str], Awaitable[str]] | None = None,
                   skip_build: bool = False, run: Callable[..., Any] = subprocess.run,
                   fp_fn: Callable[[Path], Any] | None = None) -> GateResult:
    root = Path(root)
    res = GateResult()
    res.steps.append(tree_clean(root, run=run))
    if skip_build:
        res.steps.append(Step("Сборочная проверка", False, "пропущена по запросу — публикация без неё запрещена"))
    else:
        res.steps.append(build_check(root, check_cmd, expect, run=run))
    g3 = config_refs.check(root)
    res.steps.append(Step("G3 ссылки на поля конфигов", g3.ok, g3.summary_ru()))
    files = media_provenance.media_files(list(media_paths) if media_paths else [root])
    if registry is None:
        res.steps.append(Step("G4 происхождение медиа", not files,
                              "медиафайлов нет" if not files else
                              f"медиафайлов {len(files)}, а реестр чужих файлов не задан — проверить нечем"))
    else:
        kw = {"fp_fn": fp_fn} if fp_fn else {}
        g4 = media_provenance.check(files, registry, **kw)
        res.steps.append(Step("G4 происхождение медиа", g4["ok"], media_provenance.summary_ru(g4)))
    g5 = await content_policy.check(diff_lines(root, base, run=run), reviewer=reviewer)
    res.steps.append(Step("G5 политика контента", g5.ok, g5.summary_ru()))
    return res


async def assert_publishable(root: str | Path, **kw: Any) -> GateResult:
    res = await evaluate(root, **kw)
    if not res.ok:
        raise PublishRefused(res.report_ru())
    return res
