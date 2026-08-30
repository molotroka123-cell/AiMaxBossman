"""Coding worktree sessions — изоляция агента в отдельном git-worktree.

POLISH Wave 3. Расширяет существующую модель tasks/runs/checkpoints/forks
(forks.py остаётся авторитетом по lineage чекпоинтов), НЕ заменяя её и НЕ
создавая второй session-engine. Здесь — только git-изоляция рабочей копии:

* один branch+worktree на сессию; исходный репозиторий воркеру READ-ONLY
  (его рабочее дерево не трогаем — git worktree add создаёт отдельную копию);
* безопасное имя ветки/каталога; worktree в confined-root;
* status: dirty/uncommitted + список изменённых файлов;
* diff: настоящий `git diff` (stat + patch) против базы;
* merge conflict-aware через `git merge-tree` (preview без касания дерева) +
  сериализованный реальный merge; никакого auto-delete до merge/явного discard;
* durable-метаданные (JSON в root) — переживают рестарт; cleanup орфанов.

Всё через argv-only git (никакого shell), пути — под root.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")
_MERGE_LOCK = asyncio.Lock()   # сериализуем merge'и (reference: serialize merges)


class CodingSessionError(RuntimeError):
    pass


def safe_name(raw: str) -> str:
    """Безопасное имя ветки/каталога из произвольной строки."""
    s = _SAFE.sub("-", str(raw or "").strip()).strip("-.")
    if not s:
        raise CodingSessionError("empty session id")
    return s[:64]


@dataclass
class SessionMeta:
    session_id: str
    source_repo: str
    base_ref: str
    branch: str
    worktree: str
    status: str = "active"          # active | merged | discarded
    created_at: float = field(default_factory=lambda: 0.0)


async def _git(repo: str | Path, *args: str, check: bool = True,
               timeout: float = 60.0) -> tuple[int, str, str]:
    """argv-only git; без shell. Возвращает (code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise CodingSessionError(f"git timeout: {' '.join(args)}") from exc
    o, e = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    if check and proc.returncode:
        raise CodingSessionError(f"git {' '.join(args)} failed: {e.strip()[:500]}")
    return proc.returncode, o, e


class CodingWorktreeManager:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._store = self.root / "sessions.json"

    # ---- durable metadata ----

    def _load(self) -> dict[str, dict]:
        if not self._store.exists():
            return {}
        try:
            return json.loads(self._store.read_text("utf-8"))
        except (ValueError, OSError):
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        tmp = self._store.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self._store)

    def get(self, session_id: str) -> SessionMeta | None:
        row = self._load().get(safe_name(session_id))
        return SessionMeta(**row) if row else None

    def list_sessions(self) -> list[dict]:
        return list(self._load().values())

    # ---- lifecycle ----

    async def create(self, session_id: str, source_repo: str | Path,
                     *, base_ref: str = "HEAD", branch_prefix: str = "bossman/session") -> SessionMeta:
        sid = safe_name(session_id)
        data = self._load()
        if sid in data and data[sid].get("status") == "active":
            raise CodingSessionError(f"session already active: {sid}")
        src = Path(source_repo).resolve()
        if not (src / ".git").exists():
            raise CodingSessionError("source is not a git repository")
        branch = f"{branch_prefix}/{sid}"
        wt = (self.root / sid).resolve()
        if self.root not in wt.parents:            # confinement
            raise CodingSessionError("worktree escapes root")
        if wt.exists():
            raise CodingSessionError(f"worktree path exists: {wt}")
        # Пиним базу к КОНКРЕТНОМУ коммиту: diff/merge-preview должны сравнивать с
        # исходной точкой, а не с движущимся HEAD (после коммитов сессии HEAD уедет).
        _, base_sha, _ = await _git(src, "rev-parse", base_ref)
        base_sha = base_sha.strip()
        # git worktree add создаёт ОТДЕЛЬНУЮ копию — исходное рабочее дерево не трогается
        await _git(src, "worktree", "add", "-b", branch, str(wt), base_sha)
        meta = SessionMeta(session_id=sid, source_repo=str(src), base_ref=base_sha,
                           branch=branch, worktree=str(wt), status="active",
                           created_at=time.time())
        data[sid] = asdict(meta)
        self._save(data)
        return meta

    async def status(self, session_id: str) -> dict:
        meta = self._require(session_id)
        _, porcelain, _ = await _git(meta.worktree, "status", "--porcelain")
        changed = [ln[3:] for ln in porcelain.splitlines() if ln.strip()]
        _, names, _ = await _git(meta.worktree, "diff", "--name-only", meta.base_ref, check=False)
        committed_changed = [f for f in names.splitlines() if f.strip()]
        return {"session_id": meta.session_id, "branch": meta.branch,
                "status": meta.status, "dirty": bool(changed),
                "uncommitted_files": changed,
                "changed_vs_base": sorted(set(committed_changed) | set(changed))}

    async def diff(self, session_id: str, *, max_bytes: int = 400_000) -> dict:
        meta = self._require(session_id)
        _, stat, _ = await _git(meta.worktree, "diff", "--stat", meta.base_ref, check=False)
        _, patch, _ = await _git(meta.worktree, "diff", meta.base_ref, check=False)
        _, names, _ = await _git(meta.worktree, "diff", "--name-only", meta.base_ref, check=False)
        truncated = len(patch) > max_bytes
        return {"stat": stat.strip(), "patch": patch[:max_bytes], "truncated": truncated,
                "files": [f for f in names.splitlines() if f.strip()]}

    async def merge_preview(self, session_id: str, *, into: str | None = None) -> dict:
        """Conflict-aware превью без касания рабочего дерева (git merge-tree)."""
        meta = self._require(session_id)
        target = into or meta.base_ref
        # git merge-tree --write-tree отдаёт ненулевой код при конфликте
        code, out, err = await _git(meta.source_repo, "merge-tree", "--write-tree",
                                    target, meta.branch, check=False)
        clean = code == 0
        conflicts: list[str] = []
        if not clean:
            # во второй секции вывода перечислены конфликтные пути (best-effort)
            for ln in (out + "\n" + err).splitlines():
                ln = ln.strip()
                if ln and ("CONFLICT" in ln or ln.endswith(".py") or "/" in ln):
                    conflicts.append(ln)
        return {"session_id": meta.session_id, "into": target, "clean": clean,
                "conflicts": conflicts[:200]}

    async def merge(self, session_id: str, *, into: str, allow_conflicts: bool = False) -> dict:
        """Реальный merge ветки сессии в into. Сериализован; конфликты → abort."""
        meta = self._require(session_id)
        async with _MERGE_LOCK:
            preview = await self.merge_preview(session_id, into=into)
            if not preview["clean"] and not allow_conflicts:
                return {"merged": False, "reason": "conflicts", "conflicts": preview["conflicts"]}
            # мержим в отдельном временном worktree на into — рабочее дерево источника цело
            tmp = (self.root / f".merge-{meta.session_id}").resolve()
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
            await _git(meta.source_repo, "worktree", "add", "--detach", str(tmp), into)
            try:
                code, _, err = await _git(tmp, "merge", "--no-edit", meta.branch, check=False)
                if code:
                    await _git(tmp, "merge", "--abort", check=False)
                    return {"merged": False, "reason": "merge failed", "detail": err.strip()[:500]}
                _, head, _ = await _git(tmp, "rev-parse", "HEAD")
            finally:
                await _git(meta.source_repo, "worktree", "remove", "--force", str(tmp), check=False)
                shutil.rmtree(tmp, ignore_errors=True)
            data = self._load()
            data[meta.session_id]["status"] = "merged"
            self._save(data)
            return {"merged": True, "into": into, "head": head.strip()}

    async def discard(self, session_id: str) -> dict:
        """Явный discard: снять worktree + удалить ветку. Не авто, только по команде."""
        meta = self._require(session_id)
        await _git(meta.source_repo, "worktree", "remove", "--force", meta.worktree, check=False)
        await _git(meta.source_repo, "branch", "-D", meta.branch, check=False)
        data = self._load()
        if meta.session_id in data:
            data[meta.session_id]["status"] = "discarded"
            self._save(data)
        return {"discarded": True, "session_id": meta.session_id}

    async def cleanup_orphans(self) -> dict:
        """Удалить worktree-каталоги без активной сессии в метаданных."""
        data = self._load()
        active = {sid for sid, m in data.items() if m.get("status") == "active"}
        removed = []
        for child in self.root.iterdir():
            if child.is_dir() and child.name not in active and not child.name.startswith("."):
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child.name)
        return {"removed": removed}

    def _require(self, session_id: str) -> SessionMeta:
        meta = self.get(session_id)
        if meta is None:
            raise CodingSessionError(f"unknown session: {session_id}")
        return meta


# ------------------------------------------------------------- diff-aware reviewer

def diff_aware_review(*, claim_done: bool, diff_files: list[str], diff_stat: str,
                      tests_passed: bool | None, diagnostics_errors: int = 0,
                      sensitive_paths: tuple[str, ...] = (
                          ".github/workflows", "bossman/approvals", "bossman/llm",
                          "secret", "auth", "perimeter")) -> dict:
    """Ревью на основе НАСТОЯЩИХ артефактов (diff/тесты/диагностика), а не прозы.

    Негативный инвариант: агент говорит DONE, а evidence не подтверждает —
    reject. diff как артефакт, а не слова агента.
    """
    findings: list[str] = []
    if claim_done and not diff_files:
        findings.append("claimed DONE, но diff пуст — нет доказательства изменений")
    if claim_done and tests_passed is False:
        findings.append("claimed DONE, но тесты красные")
    if claim_done and tests_passed is None:
        findings.append("claimed DONE без результата тестов")
    if diagnostics_errors > 0:
        findings.append(f"LSP-диагностика: {diagnostics_errors} ошибок")
    touched_sensitive = sorted({p for f in diff_files for p in sensitive_paths if p in f})
    if touched_sensitive:
        findings.append(f"затронуты чувствительные пути: {', '.join(touched_sensitive)}")
    approved = claim_done and bool(diff_files) and tests_passed is True and not [
        f for f in findings if "чувствительн" not in f]
    return {"approved": approved, "findings": findings,
            "requires_human": bool(touched_sensitive),
            "evidence": {"files": len(diff_files), "stat": diff_stat.strip()[:400],
                         "tests_passed": tests_passed, "diagnostics_errors": diagnostics_errors}}
