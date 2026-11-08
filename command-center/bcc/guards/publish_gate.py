"""G6 — publish gate: a publish / release action runs only when ALL applicable conditions pass.

Static & build checks (always evaluated):
  1. the working tree is committed (`git status --porcelain` empty — no modified, staged or untracked files) and matches
     `expected_sha` when specified;
  2. the project's build gate passes: a configurable command (default `bash tools/check.sh`) exits 0 AND prints the
     expected marker (default `GATE PASS`);
  3. G3 config references: no undefined `Module.FIELD` use;
  4. G4 media provenance: no audio/video under the project matches the registry of third-party files
     (no registry configured -> the step FAILS: an unverifiable upload is not uploaded);
  5. G5 content policy: no denylist or Paid Random Items violation in code/strings of the diff since `base` (or the
     whole tracked tree when no base), and the reviewer model did not FLAG.

Operational release gates (enforced when `require_release_gates=True` and ALWAYS by `assert_publishable`):
  6. STOP is clear (`stop_active=False`, no `BCC_STOP=1`, no `<root>/.bossman_stop` or `<data_dir>/STOP`);
  7. Project automated tests pass (`test_cmd` exits 0);
  8. Media licenses / provenance manifest verified (`licenses_verified=True` when media files exist);
  9. Live Studio playtest receipt verified on the exact `HEAD` SHA (`simulated=False`, `pid > 0`, real screenshot `> 1x1`);
 10. Rollback target verified (`rollback_ref` resolves to a valid git commit distinct from `HEAD`) and explicit owner
     approval (`owner_approved=True`).

The wording «ПРОЙДЕНА — можно публиковать» is emitted ONLY when both static checks and all operational release gates
pass (`can_publish == True`). Static-only runs never claim «можно публиковать»."""
from __future__ import annotations

import json
import os
import struct
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
    def __init__(self, message: str, result: GateResult | None = None):
        super().__init__(message)
        self.result = result


@dataclass
class Step:
    name: str
    ok: bool
    detail: str


@dataclass
class GateResult:
    steps: list[Step] = field(default_factory=list)
    head_sha: str = ""
    release_gates_verified: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)

    @property
    def can_publish(self) -> bool:
        return self.ok and self.release_gates_verified

    def report_ru(self) -> str:
        if not self.ok:
            header = "Проверка публикации: ОТКАЗ — публикация запрещена"
        elif self.can_publish:
            header = "Проверка публикации: ПРОЙДЕНА — можно публиковать"
        else:
            header = (
                "Проверка публикации: ПРОЙДЕНА СТАТИЧЕСКАЯ ЧАСТЬ (5/5) — публикация НЕ РАЗРЕШЕНА "
                "(требуются: STOP=0, подтверждение владельца, автотесты, лицензии, живой Studio playtest на текущем SHA и точка отката)"
            )
        lines = [header]
        for s in self.steps:
            lines.append(f"{'✔' if s.ok else '✘'} {s.name}: {s.detail.splitlines()[0][:220] if s.detail else ''}")
            if not s.ok:
                lines += ["    " + ln[:200] for ln in s.detail.splitlines()[1:8]]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "can_publish": self.can_publish,
            "release_gates_verified": self.release_gates_verified,
            "head_sha": self.head_sha,
            "steps": [s.__dict__ for s in self.steps],
            "report_ru": self.report_ru(),
        }


def _git(root: Path, *args: str, run: Callable[..., Any] = subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
               errors="replace", timeout=120, check=False)


def tree_clean(root: Path, *, expected_sha: str | None = None,
               run: Callable[..., Any] = subprocess.run) -> tuple[Step, str]:
    r = _git(root, "status", "--porcelain", run=run)
    if r.returncode != 0:
        return Step("Рабочее дерево закоммичено", False, f"git status не выполнен: {(r.stderr or '').strip()[:200]}"), ""
    dirty = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    head_full = (_git(root, "rev-parse", "HEAD", run=run).stdout or "").strip()
    head_short = head_full[:12] if head_full else (_git(root, "rev-parse", "--short", "HEAD", run=run).stdout or "").strip()
    if dirty:
        return (Step("Рабочее дерево закоммичено", False,
                     f"незакоммиченных путей: {len(dirty)}\n" + "\n".join(dirty[:10])), head_full)
    if expected_sha and head_full and not head_full.lower().startswith(expected_sha.strip().lower()):
        return (Step("Рабочее дерево закоммичено", False,
                     f"HEAD {head_short} ({head_full}) не совпадает с ожидаемым SHA {expected_sha}"), head_full)
    return Step("Рабочее дерево закоммичено", True, f"чисто, HEAD {head_short}"), head_full


def build_check(root: Path, command: list[str] | None = None, expect: str = DEFAULT_EXPECT, *,
                timeout: float = 900.0, run: Callable[..., Any] = subprocess.run,
                step_name: str = "Сборочная проверка") -> Step:
    import shutil
    cmd = list(command or DEFAULT_CHECK)
    if cmd and cmd[0] == "bash" and shutil.which("bash") is None:
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if git_bash.is_file():
            cmd[0] = str(git_bash)
    try:
        r = run(cmd, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Step(step_name, False, f"{' '.join(cmd)}: {type(exc).__name__}: {exc}"[:300])
    out = (r.stdout or "") + (r.stderr or "")
    ok = r.returncode == 0 and (not expect or expect in out)
    tail = "\n".join(out.strip().splitlines()[-6:])
    marker_info = f", маркер «{expect}» {'есть' if expect in out else 'НЕТ'}" if expect else ""
    return Step(step_name, ok, f"{' '.join(cmd)} -> код {r.returncode}{marker_info}\n{tail}")


def check_stop(root: Path, *, stop_active: bool = False) -> Step:
    env_stop = os.environ.get("BCC_STOP", "").strip().lower() in ("1", "true", "yes", "on")
    data_dir = Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter")
    stop_files = [root / ".bossman_stop", data_dir / "STOP"]
    found_file = next((p for p in stop_files if p.is_file()), None)
    if stop_active or env_stop or found_file:
        reason = "параметр stop_active=True" if stop_active else (
            "переменная BCC_STOP=1" if env_stop else f"файл STOP ({found_file})"
        )
        return Step("Проверка STOP", False, f"STOP активен ({reason}) — публикация заблокирована")
    return Step("Проверка STOP", True, "STOP не активен")


def check_owner_approval(*, owner_approved: bool = False, approval_ref: str = "") -> Step:
    if not owner_approved:
        return Step("Подтверждение владельца", False,
                    "нет явного разрешения владельца на публикацию (owner_approved=False)")
    return Step("Подтверждение владельца", True,
                f"подтверждено владельцем{f' ({approval_ref})' if approval_ref else ''}")


def _verify_png_not_stub(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, f"скриншот не найден: {path}"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return False, f"ошибка чтения скриншота: {exc}"
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return False, f"некорректный PNG ({len(data)} байт)"
    w, h = struct.unpack(">II", data[16:24])
    if (w <= 1 and h <= 1) or len(data) <= 100:
        return False, f"подставной скриншот 1x1 ({w}x{h}, {len(data)} байт)"
    return True, f"{w}x{h} ({len(data)} байт)"


def check_studio_playtest(studio_receipt: dict[str, Any] | str | Path | None, *, head_sha: str) -> Step:
    if studio_receipt is None:
        return Step("Живой Studio playtest", False, "квитанция живого Studio playtest не предоставлена")
    rec: dict[str, Any]
    if isinstance(studio_receipt, (str, Path)):
        p = Path(studio_receipt)
        if not p.is_file():
            return Step("Живой Studio playtest", False, f"файл квитанции не найден: {p}")
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return Step("Живой Studio playtest", False, f"ошибка чтения квитанции: {exc}")
    elif isinstance(studio_receipt, dict):
        rec = studio_receipt
    else:
        return Step("Живой Studio playtest", False, "неверный формат studio_receipt")

    if rec.get("simulated") is not False:
        return Step("Живой Studio playtest", False,
                    "квитанция помечена как симуляция или не содержит simulated=False (mock не допускается)")
    if not (rec.get("ok") is True or rec.get("verdict") == "PASS"):
        return Step("Живой Studio playtest", False,
                    f"статус playtest не PASS (verdict={rec.get('verdict')!r}, ok={rec.get('ok')!r})")
    rec_sha = str(rec.get("build_sha") or rec.get("git_head_sha") or "").strip().lower()
    if not rec_sha or (head_sha and not head_sha.lower().startswith(rec_sha) and not rec_sha.startswith(head_sha.lower())):
        return Step("Живой Studio playtest", False,
                    f"SHA квитанции ({rec_sha or 'пусто'}) не совпадает с текущим HEAD ({head_sha[:12]})")
    pid = int(rec.get("pid") or rec.get("owned_pid") or 0)
    if pid <= 0:
        return Step("Живой Studio playtest", False, "не указан реальный PID процесса RobloxStudioBeta")
    shot = rec.get("screenshot") or rec.get("out_file") or ""
    if not shot:
        return Step("Живой Studio playtest", False, "в квитанции отсутствует путь к скриншоту")
    shot_ok, shot_detail = _verify_png_not_stub(Path(str(shot)))
    if not shot_ok:
        return Step("Живой Studio playtest", False, shot_detail)
    return Step("Живой Studio playtest", True, f"PASS на SHA {head_sha[:12]}, pid={pid}, скриншот {shot_detail}")


def check_rollback(root: Path, rollback_ref: str | None, *, head_sha: str,
                   run: Callable[..., Any] = subprocess.run) -> Step:
    ref = (rollback_ref or "").strip()
    if not ref:
        return Step("Точка отката", False, "не указан коммит/тег отката (rollback_ref)")
    r = _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}", run=run)
    rb_sha = (r.stdout or "").strip()
    if r.returncode != 0 or not rb_sha:
        return Step("Точка отката", False, f"точка отката {ref!r} не найдена в git")
    if head_sha and rb_sha.lower() == head_sha.lower():
        return Step("Точка отката", False, f"точка отката {rb_sha[:12]} совпадает с текущим HEAD — откат невозможен")
    return Step("Точка отката", True, f"проверена точка отката {ref} ({rb_sha[:12]})")


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
                   fp_fn: Callable[[Path], Any] | None = None,
                   require_release_gates: bool = False,
                   stop_active: bool = False,
                   owner_approved: bool = False,
                   approval_ref: str = "",
                   expected_sha: str | None = None,
                   test_cmd: list[str] | None = None,
                   licenses_verified: bool | None = None,
                   studio_receipt: dict[str, Any] | str | Path | None = None,
                   rollback_ref: str | None = None) -> GateResult:
    root = Path(root)
    res = GateResult(release_gates_verified=bool(require_release_gates))
    clean_step, head_sha = tree_clean(root, expected_sha=expected_sha, run=run)
    res.head_sha = head_sha
    res.steps.append(clean_step)
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
        lic_ok = True if (licenses_verified is None and not require_release_gates) else (
            bool(licenses_verified) if files else True
        )
        detail = media_provenance.summary_ru(g4)
        if not lic_ok:
            detail += " | лицензии медиафайлов не подтверждены (licenses_verified=False)"
        res.steps.append(Step("G4 происхождение медиа", bool(g4["ok"] and lic_ok), detail))
    g5 = await content_policy.check(diff_lines(root, base, run=run), reviewer=reviewer)
    res.steps.append(Step("G5 политика контента", g5.ok, g5.summary_ru()))

    if require_release_gates or stop_active:
        res.steps.append(check_stop(root, stop_active=stop_active))
    if require_release_gates or test_cmd is not None:
        if test_cmd:
            res.steps.append(build_check(root, test_cmd, expect="", run=run, step_name="Автотесты проекта"))
        else:
            res.steps.append(Step("Автотесты проекта", False, "команда автотестов (test_cmd) не задана"))
    if require_release_gates or studio_receipt is not None:
        res.steps.append(check_studio_playtest(studio_receipt, head_sha=head_sha))
    if require_release_gates or rollback_ref is not None:
        res.steps.append(check_rollback(root, rollback_ref, head_sha=head_sha, run=run))
    if require_release_gates or owner_approved:
        res.steps.append(check_owner_approval(owner_approved=owner_approved, approval_ref=approval_ref))
    return res


async def assert_publishable(root: str | Path, **kw: Any) -> GateResult:
    """Enforce ALL static AND operational release gates before any publish action. Raises PublishRefused on any gap."""
    kw.setdefault("require_release_gates", True)
    res = await evaluate(root, **kw)
    if not res.can_publish:
        raise PublishRefused(res.report_ru(), result=res)
    return res
