"""On-disk state of Vibe Studio (JSON + files, atomic writes).

<data>/vibe/jobs/<job_id>/job.json          one build/modify request
<data>/vibe/jobs/<job_id>/events.jsonl      append-only live step log
<data>/vibe/apps/<slug>/vN/<files>          generated static app, immutable once written
<data>/vibe/registry/<slug>.json            versions, current pointer, promotion state
<data>/vibe/promoted/<slug>/                owner-approved copy + app.manifest.yaml
<data>/vibe/tmp/                            staging for atomic version directories

Generated files are written ONLY below ``apps/<slug>/vN/`` through
``safe_rel_path`` + a resolved-path containment check; a version directory is
never overwritten (a rollback moves the ``current`` pointer, nothing is lost).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .safety import UnsafePath, safe_rel_path, valid_slug

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
VERSION_RE = re.compile(r"^v([1-9][0-9]{0,4})$")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class VibeStore:
    def __init__(self, data_dir: str | os.PathLike):
        self.root = Path(data_dir) / "vibe"
        self.jobs_dir = self.root / "jobs"
        self.apps_dir = self.root / "apps"
        self.registry_dir = self.root / "registry"
        self.promoted_dir = self.root / "promoted"
        self.tmp_dir = self.root / "tmp"

    # ------------------------------------------------------------------ jobs
    def job_dir(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(str(job_id)):
            raise KeyError(job_id)
        return self.jobs_dir / job_id

    def save_job(self, job: dict) -> None:
        _atomic_json(self.job_dir(job["job_id"]) / "job.json", job)

    def load_job(self, job_id: str) -> dict:
        try:
            return json.loads((self.job_dir(job_id) / "job.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KeyError(job_id) from None

    def list_jobs(self, limit: int = 100) -> list[dict]:
        out = []
        for p in self.jobs_dir.glob("*/job.json") if self.jobs_dir.is_dir() else []:
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        out.sort(key=lambda j: (j.get("created_at", ""), j.get("job_id", "")), reverse=True)
        return out[:limit]

    def append_event(self, job_id: str, event: dict) -> dict:
        d = self.job_dir(job_id)
        d.mkdir(parents=True, exist_ok=True)
        p = d / "events.jsonl"
        n = 0
        try:
            with open(p, "rb") as fh:
                n = sum(1 for _ in fh)
        except OSError:
            pass
        rec = {"n": n + 1, "ts": now_iso(), **event}
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return rec

    def events(self, job_id: str, after: int = 0, limit: int = 500) -> list[dict]:
        out: list[dict] = []
        try:
            for line in (self.job_dir(job_id) / "events.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and int(rec.get("n", 0)) > after:
                    out.append(rec)
        except OSError:
            pass
        return out[:limit]

    # ------------------------------------------------------------------ apps / versions
    def _app_root(self, slug: str) -> Path:
        return self.apps_dir / valid_slug(slug)

    def version_dir(self, slug: str, n: int) -> Path:
        if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > 99999:
            raise UnsafePath(f"invalid version {n!r}")
        d = (self._app_root(slug) / f"v{n}")
        base = self.apps_dir.resolve()
        if not d.resolve().is_relative_to(base):          # defence in depth
            raise UnsafePath("version path escapes the apps directory")
        return d

    def meta(self, slug: str) -> dict | None:
        try:
            return json.loads((self.registry_dir / f"{valid_slug(slug)}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save_meta(self, slug: str, meta: dict) -> None:
        _atomic_json(self.registry_dir / f"{valid_slug(slug)}.json", meta)

    def list_apps(self) -> list[dict]:
        out = []
        for p in sorted(self.registry_dir.glob("*.json")) if self.registry_dir.is_dir() else []:
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"slug": m.get("slug"), "title": m.get("title"), "current": m.get("current"),
                        "versions": len(m.get("versions") or []), "updated_at": m.get("updated_at"),
                        "promoted": bool((m.get("promotion") or {}).get("promoted_version"))})
        return out

    def write_version(self, slug: str, files: dict[str, str]) -> int:
        """Write a NEW immutable version directory; returns its number."""
        slug = valid_slug(slug)
        clean = {safe_rel_path(p): c for p, c in files.items()}
        meta = self.meta(slug) or {}
        n = max([v["n"] for v in meta.get("versions", [])] or [0]) + 1
        while self.version_dir(slug, n).exists():       # orphan dir from a crash: never overwrite
            n += 1
        stage = self.tmp_dir / uuid.uuid4().hex
        try:
            for rel, content in clean.items():
                dest = stage / rel
                if not dest.resolve().is_relative_to(stage.resolve()):
                    raise UnsafePath(f"path escapes the version directory: {rel}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8", newline="\n")
            final = self.version_dir(slug, n)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.rename(stage, final)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return n

    def read_version(self, slug: str, n: int) -> dict[str, str]:
        d = self.version_dir(slug, n)
        if not d.is_dir():
            raise KeyError(f"{slug} v{n}")
        out: dict[str, str] = {}
        for p in sorted(d.rglob("*")):
            if p.is_file() and not p.is_symlink():
                out[p.relative_to(d).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
        return out

    @staticmethod
    def digest(files: dict[str, str]) -> str:
        h = hashlib.sha256()
        for k in sorted(files):
            h.update(k.encode()), h.update(b"\0"), h.update(files[k].encode("utf-8", "ignore")), h.update(b"\0")
        return h.hexdigest()

    def diff(self, slug: str, a: int, b: int, max_lines: int = 2000) -> dict:
        fa, fb = self.read_version(slug, a), self.read_version(slug, b)
        out: list[dict] = []
        total = 0
        for path in sorted(set(fa) | set(fb)):
            old, new = fa.get(path), fb.get(path)
            if old == new:
                continue
            lines = list(difflib.unified_diff(
                (old or "").splitlines(), (new or "").splitlines(),
                fromfile=f"v{a}/{path}", tofile=f"v{b}/{path}", lineterm=""))
            status = "added" if old is None else "removed" if new is None else "modified"
            total += len(lines)
            out.append({"path": path, "status": status, "diff": "\n".join(lines[:max_lines])})
        return {"slug": slug, "from": a, "to": b, "files": out, "truncated": total > max_lines}

    # ------------------------------------------------------------------ promotion
    def promote_copy(self, slug: str, n: int, manifest_text: str) -> Path:
        slug = valid_slug(slug)
        files = self.read_version(slug, n)
        dest = self.promoted_dir / slug
        stage = self.tmp_dir / ("p-" + uuid.uuid4().hex)
        try:
            for rel, content in files.items():
                p = stage / "site" / safe_rel_path(rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8", newline="\n")
            (stage / "app.manifest.yaml").write_text(manifest_text, encoding="utf-8", newline="\n")
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(stage, dest)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return dest

    def promoted_files(self, slug: str) -> dict[str, str]:
        base = self.promoted_dir / valid_slug(slug) / "site"
        if not base.is_dir():
            raise KeyError(slug)
        return {p.relative_to(base).as_posix(): p.read_text(encoding="utf-8", errors="replace")
                for p in sorted(base.rglob("*")) if p.is_file() and not p.is_symlink()}
