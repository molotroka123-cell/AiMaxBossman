"""Vibe Studio job service: queue, STOP, cancel, resume, versions, promotion.

One job at a time (FIFO).  The worker task exists only while there is work:
it starts on ``create``/``resume``/``kick`` and exits when the queue is empty or
a STOP file is present (queued jobs then simply wait; the feature tick re-kicks
the worker once STOP is cleared).  A job that was live when the process died is
re-queued on the next start (bounded by ``MAX_RESUMES``), never silently lost.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from typing import Any, Callable

from ..single_flight import await_shared

from .generator import (Candidate, Ladder, ParseError, assert_cloud_payload, build_messages,
                        build_spec, parse_output)
from .safety import (MAX_REQUEST_CHARS, UnsafePath, inline_for_preview, preview_csp, scan_files,
                     secret_findings, slugify, valid_slug)
from .store import VibeStore, now_iso

LIVE = ("queued", "planning", "generating", "checking")
TERMINAL = ("completed", "failed", "cancelled", "stopped")
MAX_RESUMES = 3
REPAIRS_PER_CANDIDATE = 1
CALL_TIMEOUT_S = 900.0
MAX_OUT_TOKENS = 12000


class VibeError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    @property
    def detail(self) -> dict:
        return {"code": self.code, "message": self.message}


class VibeService:
    def __init__(self, data_dir, *, ladder: Ladder, stop_check: Callable[[], str] | None = None,
                 bus: Any = None, network_allowlist: list[str] | None = None,
                 approvals: Any = None, call_timeout: float = CALL_TIMEOUT_S):
        self.store = VibeStore(data_dir)
        try:                       # the launcher lists promoted apps from this folder
            from ..features.apps import register_promoted_dir
            register_promoted_dir(self.store.promoted_dir)
        except Exception:  # noqa: BLE001 - the launcher is optional for the studio
            pass
        self.ladder = ladder
        self.stop_check = stop_check or (lambda: "")
        self.bus = bus
        self.allowlist = [h.lower() for h in (network_allowlist or [])]
        self.approvals = approvals
        self.call_timeout = call_timeout
        self._worker: asyncio.Task | None = None
        self._current: tuple[str, asyncio.Task] | None = None
        self._cancel: set[str] = set()

    # ------------------------------------------------------------------ helpers
    async def _emit(self, job: dict, kind: str, message: str, **extra: Any) -> None:
        self.store.append_event(job["job_id"], {"kind": kind, "message": message[:600], **extra})
        if self.bus is not None:
            with contextlib.suppress(Exception):
                await self.bus.emit("vibe.job", job_id=job["job_id"], stage=kind, status=job["status"],
                                    message=message[:200])

    def _set(self, job: dict, **fields: Any) -> dict:
        job.update(fields, updated_at=now_iso())
        self.store.save_job(job)
        return job

    # ------------------------------------------------------------------ API
    def create(self, body: dict) -> dict:
        request = str(body.get("request") or "").strip()
        if not request or len(request) > MAX_REQUEST_CHARS:
            raise VibeError(422, "invalid_request", f"request must be 1..{MAX_REQUEST_CHARS} characters")
        privacy = str(body.get("privacy") or "default")
        if privacy not in ("default", "local_only"):
            raise VibeError(422, "invalid_privacy", "privacy must be 'default' or 'local_only'")
        slug_in = body.get("slug")
        base_version = body.get("base_version")
        kind = "build"
        if slug_in:
            try:
                slug = valid_slug(slug_in)
            except UnsafePath as exc:
                raise VibeError(422, "invalid_slug", str(exc)) from None
            meta = self.store.meta(slug)
            if meta is not None:
                kind = "modify"
                if base_version is None:
                    base_version = meta.get("current")
                if base_version not in [v["n"] for v in meta.get("versions", [])]:
                    raise VibeError(404, "unknown_version", f"{slug} has no version {base_version!r}")
        else:
            slug = slugify(str(body.get("title") or request[:40]))
            if self.store.meta(slug) is not None:          # never silently modify another app
                slug = f"{slug[:32]}-{uuid.uuid4().hex[:6]}"
        title = str(body.get("title") or (self.store.meta(slug) or {}).get("title") or slug)[:80]
        job = {"job_id": uuid.uuid4().hex, "kind": kind, "slug": slug, "title": title, "request": request,
               "base_version": base_version, "privacy": privacy, "status": "queued", "stage": "queued",
               "created_at": now_iso(), "updated_at": now_iso(), "resumes": 0, "result": None, "error": None}
        self.store.save_job(job)
        self.store.append_event(job["job_id"], {"kind": "queued",
                                                "message": f"{kind} request accepted for app '{slug}' (privacy={privacy})"})
        self.kick()
        return job

    def get(self, job_id: str) -> dict:
        try:
            return self.store.load_job(job_id)
        except KeyError:
            raise VibeError(404, "unknown_job", "job not found") from None

    def list(self) -> list[dict]:
        return self.store.list_jobs()

    def events(self, job_id: str, after: int = 0) -> dict:
        job = self.get(job_id)
        return {"job_id": job_id, "status": job["status"], "events": self.store.events(job_id, after)}

    async def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["status"] in TERMINAL:
            raise VibeError(409, "already_finished", f"job is already {job['status']}")
        self._cancel.add(job_id)
        if self._current and self._current[0] == job_id:
            self._current[1].cancel()               # in flight: the run loop records "cancelled"
            return self.get(job_id)
        self._set(job, status="cancelled", stage="cancelled")
        await self._emit(job, "cancelled", "cancelled by the owner before it started")
        return job

    def retry(self, job_id: str) -> dict:
        old = self.get(job_id)
        if old["status"] not in ("failed", "stopped", "cancelled"):
            raise VibeError(409, "not_retryable", f"job is {old['status']}")
        return self.create({"request": old["request"], "slug": old["slug"], "title": old["title"],
                            "privacy": old["privacy"], "base_version": old.get("base_version")})

    # ------------------------------------------------------------------ queue
    async def resume(self) -> int:
        """Re-queue jobs that were live when the previous process died."""
        n = 0
        for job in self.store.list_jobs(limit=1000):
            if job.get("status") not in LIVE or job["status"] == "queued":
                continue
            resumes = int(job.get("resumes") or 0) + 1
            if resumes > MAX_RESUMES:
                self._set(job, status="failed", stage="failed",
                          error={"code": "too_many_restarts", "message": "interrupted by restarts too often"})
                await self._emit(job, "failed", "interrupted by restarts too many times")
                continue
            self._set(job, status="queued", stage="queued", resumes=resumes)
            await self._emit(job, "resumed", f"process restarted while '{job['status']}': re-queued "
                                              f"(restart {resumes}/{MAX_RESUMES})")
            n += 1
        self.kick()
        return n

    def kick(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if any(j.get("status") == "queued" for j in self.store.list_jobs(limit=1000)):
            self._worker = loop.create_task(self._run_queue())

    async def wait_idle(self, timeout: float = 30.0) -> None:
        w = self._worker
        if w is not None:
            # await_shared, not asyncio.shield (Python 3.14 logs a false "exception in shielded future"):
            # the timeout cancels only this wait, the queue worker keeps running; its error/cancel still reaches us.
            await asyncio.wait_for(await_shared(w), timeout)

    async def close(self) -> None:
        w, self._worker = self._worker, None
        if w is not None and not w.done():
            w.cancel()
            await asyncio.gather(w, return_exceptions=True)

    def stop_reason(self) -> str:
        try:
            return self.stop_check() or ""
        except Exception as exc:  # noqa: BLE001 - fail closed: unreadable STOP state = stopped
            return f"STOP state unreadable: {type(exc).__name__}"

    async def _run_queue(self) -> None:
        while True:
            if self.stop_reason():
                return                                  # jobs stay queued; tick re-kicks after STOP clears
            queued = sorted((j for j in self.store.list_jobs(limit=1000) if j.get("status") == "queued"),
                            key=lambda j: j.get("created_at", ""))
            if not queued:
                return
            job = queued[0]
            task = asyncio.ensure_future(self._run_job(job))
            self._current = (job["job_id"], task)
            try:
                await task
            finally:
                self._current = None

    # ------------------------------------------------------------------ the build
    async def _guard(self, job: dict) -> None:
        if job["job_id"] in self._cancel:
            raise asyncio.CancelledError()
        why = self.stop_reason()
        if why:
            raise _Stopped(why)

    async def _run_job(self, job: dict) -> None:
        try:
            await self._build(job)
        except asyncio.CancelledError:
            if job["job_id"] not in self._cancel:
                raise                      # shutdown, not an owner cancel: stays live, resumed on restart
            self._cancel.discard(job["job_id"])
            cur = self.store.load_job(job["job_id"])
            if cur["status"] not in TERMINAL:
                self._set(cur, status="cancelled", stage="cancelled")
                await self._emit(cur, "cancelled", "cancelled by the owner")
        except _Stopped as exc:
            self._set(job, status="stopped", stage="stopped",
                      error={"code": "stopped", "message": str(exc)})
            await self._emit(job, "stopped", f"STOP file present, job halted: {exc}")
        except _Failed as exc:
            self._set(job, status="failed", stage="failed", error={"code": exc.code, "message": exc.message})
            await self._emit(job, "failed", exc.message, code=exc.code)
        except Exception as exc:  # noqa: BLE001 - a job must always end in a terminal state
            self._set(job, status="failed", stage="failed",
                      error={"code": "internal_error", "message": f"{type(exc).__name__}: {str(exc)[:300]}"})
            await self._emit(job, "failed", f"internal error: {type(exc).__name__}: {str(exc)[:300]}")

    async def _build(self, job: dict) -> None:
        from bossman_shared.privacy import execution_privacy
        await self._guard(job)
        self._set(job, status="planning", stage="planning")
        slug = job["slug"]
        meta = self.store.meta(slug) or {"slug": slug, "title": job["title"], "versions": [],
                                         "current": None, "requests": [], "history": []}
        requests = list(meta.get("requests") or [])
        base_files: dict[str, str] | None = None
        if job["kind"] == "modify" and job.get("base_version"):
            # the cumulative request list belongs to the version being modified
            ver = next((v for v in meta["versions"] if v["n"] == job["base_version"]), None)
            requests = list(ver.get("requests") or []) if ver else requests
            base_files = self.store.read_version(slug, int(job["base_version"]))
        requests.append(job["request"])
        spec = build_spec(job["title"], requests)
        await self._emit(job, "planning", f"spec ready: {len(requests)} request(s), title '{job['title']}', "
                                          f"privacy={job['privacy']}")
        findings = secret_findings("\n".join(requests))
        local_only = job["privacy"] == "local_only"
        if findings and not local_only:
            await self._emit(job, "privacy", "request text looks like it contains secrets/paths "
                                             f"({', '.join(findings)}): cloud models are excluded for this job",
                             findings=findings)
            local_only = True
        cands: list[Candidate] = await self.ladder.candidates(local_only=local_only)
        if local_only:
            cands = [c for c in cands if c.is_local]
        if not cands:
            raise _Failed("no_model", "no usable model on the ladder"
                          + (" (local_only: a local model is required)" if local_only else ""))
        await self._emit(job, "ladder", "model ladder: " + " -> ".join(c.label for c in cands),
                         ladder=[{"tier": c.tier, "model": c.model} for c in cands])
        last_error = "no candidate produced a valid app"
        for cand in cands:
            rejected: list[str] | None = None
            for attempt in range(1 + REPAIRS_PER_CANDIDATE):
                await self._guard(job)
                self._set(job, status="generating", stage="generating")
                prev = base_files if cand.is_local else None       # prior code never leaves the PC
                messages = build_messages(spec, previous_files=prev, rejected=rejected)
                try:
                    if not cand.is_local:
                        assert_cloud_payload(messages, spec)
                except PermissionError as exc:
                    await self._emit(job, "privacy", f"{cand.label}: payload refused locally: {exc}")
                    last_error = str(exc)
                    break
                await self._emit(job, "generating",
                                 f"asking {cand.label}" + (f" (repair {attempt})" if attempt else ""),
                                 tier=cand.tier, model=cand.model)
                privacy = "local_only" if local_only else "public"
                try:
                    with execution_privacy(privacy):
                        result = await asyncio.wait_for(
                            cand.adapter.chat(cand.model, messages, max_tokens=MAX_OUT_TOKENS, temperature=0.2),
                            self.call_timeout)
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    last_error = f"{cand.label}: timed out after {int(self.call_timeout)} s"
                    await self._emit(job, "model_error", last_error)
                    break
                except Exception as exc:  # noqa: BLE001 - ProviderError, budget refusal, privacy boundary
                    text = f"{type(exc).__name__}: {str(exc)[:400]}"
                    last_error = f"{cand.label}: {text}"
                    loud = "CAP HIT" in text or "REFUSED" in text
                    await self._emit(job, "cap_hit" if loud else "model_error", last_error)
                    break
                await self._emit(job, "model_reply", f"{cand.label} answered "
                                 f"({getattr(result, 'tokens_in', 0)} in / {getattr(result, 'tokens_out', 0)} out tokens)",
                                 tokens_in=getattr(result, "tokens_in", 0),
                                 tokens_out=getattr(result, "tokens_out", 0))
                try:
                    parsed = parse_output(getattr(result, "text", "") or "")
                except ParseError as exc:
                    rejected = [f"output format: {exc}"]
                    last_error = f"{cand.label}: unparsable output: {exc}"
                    await self._emit(job, "check_failed", last_error)
                    continue
                for plan_line in parsed.plan:
                    await self._emit(job, "plan", plan_line)
                self._set(job, status="checking", stage="checking")
                report = scan_files(parsed.files, allowlist=self.allowlist)
                if not report.ok:
                    rejected = report.errors
                    last_error = f"{cand.label}: safety scan rejected the app: " + "; ".join(report.errors[:3])
                    await self._emit(job, "check_failed", last_error, errors=report.errors[:20])
                    continue
                await self._guard(job)
                n = self.store.write_version(slug, parsed.files)
                for path in sorted(parsed.files):
                    await self._emit(job, "file_written", f"apps/{slug}/v{n}/{path} "
                                     f"({len(parsed.files[path].encode('utf-8'))} bytes)", path=path)
                await self._emit(job, "check_passed", "safety scan passed: no foreign hosts, no eval, "
                                 f"{report.files} file(s), {report.bytes} bytes", scan=report.as_dict())
                entry = {"n": n, "created_at": now_iso(), "job_id": job["job_id"], "request": job["request"],
                         "requests": requests, "tier": cand.tier, "model": cand.model,
                         "sha256": self.store.digest(parsed.files), "scan": report.as_dict(),
                         "files": sorted(parsed.files), "parent": job.get("base_version")}
                meta.setdefault("versions", []).append(entry)
                meta.update(slug=slug, title=job["title"], current=n, requests=requests, updated_at=now_iso())
                meta.setdefault("history", []).append({"at": now_iso(), "action": "version", "n": n})
                self.store.save_meta(slug, meta)
                self._set(job, status="completed", stage="completed",
                          result={"slug": slug, "version": n, "tier": cand.tier, "model": cand.model,
                                  "scan": report.as_dict()})
                await self._emit(job, "completed", f"app '{slug}' v{n} is ready for preview "
                                 f"(built by {cand.label})", slug=slug, version=n)
                return
        raise _Failed("generation_failed", last_error)

    # ------------------------------------------------------------------ apps
    def apps(self) -> list[dict]:
        return self.store.list_apps()

    def app(self, slug: str) -> dict:
        meta = self._meta(slug)
        return meta

    def _meta(self, slug: str) -> dict:
        try:
            meta = self.store.meta(slug)
        except UnsafePath as exc:
            raise VibeError(422, "invalid_slug", str(exc)) from None
        if meta is None:
            raise VibeError(404, "unknown_app", "app not found")
        return meta

    def _version(self, slug: str, n: int) -> dict[str, str]:
        self._meta(slug)
        try:
            return self.store.read_version(slug, int(n))
        except (KeyError, UnsafePath, ValueError):
            raise VibeError(404, "unknown_version", f"{slug} v{n} not found") from None

    def preview_html(self, slug: str, n: int) -> tuple[str, str]:
        """(self-contained html, CSP header) for the sandboxed preview. Re-scans on every
        request: a file edited on disk after the build is not served."""
        files = self._version(slug, n)
        report = scan_files(files, allowlist=self.allowlist)
        if not report.ok:
            raise VibeError(409, "scan_failed", "stored version no longer passes the safety scan: "
                            + "; ".join(report.errors[:3]))
        return inline_for_preview(files), preview_csp(self.allowlist)

    def promoted_html(self, slug: str) -> tuple[str, str]:
        try:
            files = self.store.promoted_files(slug)
        except (KeyError, UnsafePath):
            raise VibeError(404, "not_promoted", "this app was not promoted by the owner") from None
        report = scan_files(files, allowlist=self.allowlist)
        if not report.ok:
            raise VibeError(409, "scan_failed", "promoted copy no longer passes the safety scan")
        return inline_for_preview(files), preview_csp(self.allowlist)

    def diff(self, slug: str, a: int, b: int) -> dict:
        self._version(slug, a)
        self._version(slug, b)
        return self.store.diff(slug, int(a), int(b))

    def rollback(self, slug: str, n: int) -> dict:
        meta = self._meta(slug)
        if n not in [v["n"] for v in meta["versions"]]:
            raise VibeError(404, "unknown_version", f"{slug} has no version {n}")
        before = meta.get("current")
        meta["current"] = n
        meta["updated_at"] = now_iso()
        meta.setdefault("history", []).append({"at": now_iso(), "action": "rollback", "from": before, "to": n})
        self.store.save_meta(slug, meta)
        return meta

    # ------------------------------------------------------------------ promotion (owner approve)
    async def request_promotion(self, slug: str, n: int) -> dict:
        meta = self._meta(slug)
        if n not in [v["n"] for v in meta["versions"]]:
            raise VibeError(404, "unknown_version", f"{slug} has no version {n}")
        report = scan_files(self._version(slug, n), allowlist=self.allowlist)
        if not report.ok:
            raise VibeError(409, "scan_failed", "only a version that passes the safety scan can be promoted")
        if self.approvals is None:
            raise VibeError(503, "approvals_unavailable", "approval service is not wired")
        row = await self.approvals.create(
            "vibe.promote", f"Promote Vibe Studio app '{slug}' v{n} into the Bossman apps list "
                            f"(static page served by Command Center, sha256 "
                            f"{next(v for v in meta['versions'] if v['n'] == n)['sha256'][:12]})")
        meta["promotion"] = {**(meta.get("promotion") or {}), "pending": {"version": n, "approval_id": row["id"],
                                                                         "at": now_iso()}}
        self.store.save_meta(slug, meta)
        return {"approval_id": row["id"], "status": "pending", "version": n}

    async def finish_promotion(self, slug: str) -> dict:
        meta = self._meta(slug)
        pend = (meta.get("promotion") or {}).get("pending")
        if not pend:
            raise VibeError(409, "no_pending_promotion", "no promotion was requested for this app")
        rows = await self.approvals.list(status=None, limit=500) if self.approvals else []
        row = next((r for r in rows if r.get("id") == pend["approval_id"]), None)
        status = (row or {}).get("status")
        if status == "pending" or row is None:
            return {"status": "pending", "approval_id": pend["approval_id"]}
        if status != "approved" or (row or {}).get("kind") != "vibe.promote":
            meta["promotion"]["pending"] = None
            self.store.save_meta(slug, meta)
            raise VibeError(409, "promotion_refused", f"the owner did not approve (status={status})")
        n = int(pend["version"])
        manifest = _manifest(slug, meta, n)
        dest = self.store.promote_copy(slug, n, manifest)
        meta["promotion"] = {"promoted_version": n, "approval_id": pend["approval_id"], "at": now_iso(),
                             "path": str(dest), "pending": None}
        meta.setdefault("history", []).append({"at": now_iso(), "action": "promote", "n": n})
        self.store.save_meta(slug, meta)
        return {"status": "promoted", "version": n, "path": str(dest)}


def _manifest(slug: str, meta: dict, n: int) -> str:
    title = re.sub(r"[\r\n\"']", " ", str(meta.get("title") or slug))[:80]
    return (f"id: vibe-{slug}\nname: \"{title}\"\nversion: \"{n}\"\nkind: static_web\ncontract: \"1.0\"\n"
            f"description: >\n  Vibe Studio app, promoted by the owner (version {n}). Static page, no network.\n"
            f"runtime:\n  language: html\n  standalone: true\n  imports_bossman: false\n"
            f"permissions:\n  external.upload: deny\n  filesystem.write: deny\n"
            f"ui:\n  subtitle: \"Vibe Studio · v{n}\"\n  icon: box\n  order: 80\n"
            f"  static_url: /api/vibe/promoted/{slug}/\n")


class _Stopped(Exception):
    pass


class _Failed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message
