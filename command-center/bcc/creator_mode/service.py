"""Creator Mode Service: project synthesis, sandbox preview, skill installation, Pult delivery, and multi-agent arena."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from ..vibe.generator import ParseError, parse_output
from ..vibe.safety import (
    MAX_REQUEST_CHARS,
    UnsafePath,
    inline_for_preview,
    preview_csp,
    safe_rel_path,
    slugify,
    valid_slug,
)
from .blueprints import (
    KIND_LABELS_RU,
    VALID_KINDS,
    build_creator_messages,
    default_weight_telemetry,
    ensure_complete_build,
    make_skill_markdown,
    parse_creator_prompt,
    score_build,
)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class CreatorError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    @property
    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class CreatorModeService:
    def __init__(
        self,
        data_dir: str | os.PathLike,
        *,
        ladder: Any = None,
        stop_check: Callable[[], str] | None = None,
        bus: Any = None,
        ollama_url: str = "http://127.0.0.1:11434",
        call_timeout: float = 45.0,
    ):
        self.root = Path(data_dir) / "creator_mode"
        self.projects_dir = self.root / "projects"
        self.registry_dir = self.root / "registry"
        self.arena_dir = self.root / "arena"
        self.telemetry_path = self.root / "weights_telemetry.json"
        for d in (self.projects_dir, self.registry_dir, self.arena_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.ladder = ladder
        self.stop_check = stop_check or (lambda: "")
        self.bus = bus
        self.ollama_url = ollama_url.rstrip("/")
        self.call_timeout = call_timeout

    def stop_reason(self) -> str:
        try:
            return self.stop_check() or ""
        except Exception as exc:  # noqa: BLE001
            return f"STOP state unreadable: {type(exc).__name__}"

    def load_telemetry(self) -> dict[str, Any]:
        if self.telemetry_path.is_file():
            try:
                return json.loads(self.telemetry_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return default_weight_telemetry()

    def save_telemetry(self, telemetry: dict[str, Any]) -> dict[str, Any]:
        _atomic_json(self.telemetry_path, telemetry)
        return telemetry

    async def list_local_models(self) -> list[str]:
        """Return local Ollama chat/coder models suitable for Creator Mode."""
        skip = ("embed", "whisper", "tts", "rerank", "diffusion", "flux", "sdxl", "ocr")
        out: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
                r = await client.get(f"{self.ollama_url}/api/tags")
                if r.status_code == 200:
                    for m in r.json().get("models", []):
                        name = str(m.get("name") or "")
                        if name and not any(s in name.lower() for s in skip):
                            out.append(name)
        except Exception:  # noqa: BLE001
            pass
        if self.ladder is not None:
            try:
                for c in await self.ladder.candidates(local_only=True):
                    if c.model not in out:
                        out.append(c.model)
            except Exception:  # noqa: BLE001
                pass
        return out

    async def _call_local_ollama(self, model: str, messages: list[dict[str, str]]) -> tuple[str, int, float]:
        """Call local Ollama `/v1/chat/completions` directly (loopback only, cloud_policy=never)."""
        if "cloud" in model.lower():
            raise CreatorError(403, "cloud_forbidden", "Облачные модели запрещены в локальном вызове Ollama")
        t0 = time.monotonic()
        fast_messages = [dict(m) for m in messages]
        if fast_messages and fast_messages[-1].get("role") == "user":
            if "/no_think" not in str(fast_messages[-1].get("content", "")):
                fast_messages[-1]["content"] = str(fast_messages[-1].get("content", "")) + "\n/no_think"
        payload = {
            "model": model,
            "messages": fast_messages,
            "temperature": 0.2,
            "max_tokens": 900,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.call_timeout, connect=5.0), trust_env=False) as client:
            r = await client.post(f"{self.ollama_url}/v1/chat/completions", json=payload)
            r.raise_for_status()
            data = r.json()
        elapsed = time.monotonic() - t0
        choices = data.get("choices") or []
        text = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
        usage = data.get("usage") or {}
        tok_out = int(usage.get("completion_tokens") or max(1, len(text) // 4))
        return text, tok_out, elapsed

    def _meta_path(self, slug: str) -> Path:
        return self.registry_dir / f"{valid_slug(slug)}.json"

    def get_project(self, slug: str) -> dict[str, Any]:
        p = self._meta_path(slug)
        if not p.is_file():
            raise CreatorError(404, "unknown_project", f"Проект {slug!r} не найден")
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise CreatorError(404, "unknown_project", f"Проект {slug!r} повреждён") from None

    def list_projects(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in sorted(self.registry_dir.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return out

    def read_version_files(self, slug: str, version: int) -> dict[str, str]:
        s = valid_slug(slug)
        vdir = self.projects_dir / s / f"v{int(version)}"
        if not vdir.is_dir():
            raise CreatorError(404, "unknown_version", f"Версия v{version} проекта {s} не найдена")
        files: dict[str, str] = {}
        for f in sorted(vdir.iterdir()):
            if f.is_file():
                files[f.name] = f.read_text(encoding="utf-8")
        return files

    def _write_version_files(self, slug: str, version: int, files: dict[str, str]) -> Path:
        s = valid_slug(slug)
        vdir = self.projects_dir / s / f"v{int(version)}"
        vdir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            rel = safe_rel_path(name)
            target = (vdir / rel).resolve()
            if vdir.resolve() not in target.parents:
                raise UnsafePath(f"path escapes version directory: {name}")
            target.write_text(content, encoding="utf-8")
        return vdir

    async def create_project(self, body: dict[str, Any]) -> dict[str, Any]:
        why = self.stop_reason()
        if why:
            raise CreatorError(409, "stopped", f"Действие остановлено (STOP активен): {why}")

        raw_req = str(body.get("request") or "").strip()
        if not raw_req or len(raw_req) > MAX_REQUEST_CHARS:
            raise CreatorError(422, "invalid_request", f"Описание должно содержать от 1 до {MAX_REQUEST_CHARS} символов")

        explicit_kind = str(body.get("kind") or "").strip()
        if explicit_kind and explicit_kind != "auto" and explicit_kind not in VALID_KINDS:
            raise CreatorError(422, "invalid_kind", f"Неизвестный режим {explicit_kind!r}: используйте {', '.join(VALID_KINDS)}")

        spec = parse_creator_prompt(raw_req, explicit_kind if explicit_kind in VALID_KINDS else None)
        privacy = str(body.get("privacy") or "local_only")
        if privacy not in ("local_only", "default"):
            raise CreatorError(422, "invalid_privacy", "privacy должен быть 'local_only' или 'default'")

        slug_in = body.get("slug")
        if slug_in:
            try:
                slug = valid_slug(slug_in)
            except UnsafePath as exc:
                raise CreatorError(422, "invalid_slug", str(exc)) from None
        else:
            slug = slugify(str(body.get("title") or spec.clean_request[:40]))
            if self._meta_path(slug).exists():
                slug = f"{slug[:32]}-{uuid.uuid4().hex[:6]}"

        existing = None
        if self._meta_path(slug).exists():
            existing = self.get_project(slug)

        title = str(body.get("title") or (existing or {}).get("title") or spec.clean_request[:60]).strip()[:80]
        requested_model = str(body.get("model") or "instant").strip()

        candidate_files: dict[str, str] | None = None
        elapsed_s = 0.0
        tokens_out = 0
        model_used = "creator-blueprint-v1 (instant)"

        if requested_model and requested_model != "instant":
            target_model = requested_model
            if target_model == "auto":
                locals_list = await self.list_local_models()
                target_model = locals_list[0] if locals_list else ""
            if target_model:
                messages = build_creator_messages(spec, title, slug)
                t0 = time.monotonic()
                try:
                    raw_out, tokens_out, elapsed_s = await self._call_local_ollama(target_model, messages)
                    model_used = target_model
                    try:
                        parsed = parse_output(raw_out)
                        candidate_files = parsed.files
                    except ParseError:
                        candidate_files = None
                except Exception:  # noqa: BLE001 - graceful fallback to deterministic synthesis
                    elapsed_s = time.monotonic() - t0
                    model_used = f"{target_model} (fallback->blueprint)"

        t_synth = time.monotonic()
        telemetry = self.load_telemetry()
        files, used_llm = ensure_complete_build(
            spec.kind,
            candidate_files,
            title=title,
            slug=slug,
            request=spec.clean_request,
            telemetry=telemetry,
        )
        if elapsed_s <= 0:
            elapsed_s = max(0.01, time.monotonic() - t_synth)

        score = score_build(spec.kind, files, elapsed_s=elapsed_s, tokens_out=tokens_out, used_llm=used_llm)
        versions = list((existing or {}).get("versions") or [])
        next_v = (versions[-1]["n"] + 1) if versions else 1
        self._write_version_files(slug, next_v, files)

        digest = hashlib.sha256(
            "\n".join(f"{k}:{hashlib.sha256(v.encode('utf-8')).hexdigest()}" for k, v in sorted(files.items())).encode("utf-8")
        ).hexdigest()

        ver_entry = {
            "n": next_v,
            "kind": spec.kind,
            "kind_label": KIND_LABELS_RU.get(spec.kind, spec.kind),
            "model": model_used,
            "privacy": privacy,
            "request": spec.raw_request,
            "slash_command": spec.slash_command,
            "context_tags": spec.context_tags,
            "files": sorted(files.keys()),
            "bundle_sha256": digest,
            "score": score,
            "created_at": now_iso(),
        }
        versions.append(ver_entry)
        meta = {
            "slug": slug,
            "title": title,
            "kind": spec.kind,
            "kind_label": KIND_LABELS_RU.get(spec.kind, spec.kind),
            "current": next_v,
            "versions": versions,
            "skill_installed": bool((existing or {}).get("skill_installed")),
            "created_at": (existing or {}).get("created_at") or now_iso(),
            "updated_at": now_iso(),
        }
        _atomic_json(self._meta_path(slug), meta)
        if self.bus is not None:
            try:
                await self.bus.emit("creator.built", slug=slug, version=next_v, kind=spec.kind, score=score["total_score"])
            except Exception:  # noqa: BLE001
                pass
        return meta

    def preview_html(self, slug: str, version: int | None = None) -> tuple[str, str]:
        meta = self.get_project(slug)
        v = int(version or meta.get("current") or 1)
        files = self.read_version_files(slug, v)
        return inline_for_preview(files), preview_csp([])

    async def install_skill(self, slug: str, svc: Any) -> dict[str, Any]:
        """Install the project's SKILL.md into Bossman's SkillLibrary."""
        why = self.stop_reason()
        if why:
            raise CreatorError(409, "stopped", f"STOP активен: {why}")
        meta = self.get_project(slug)
        v = int(meta.get("current") or 1)
        files = self.read_version_files(slug, v)
        skill_md = files.get("SKILL.md") or make_skill_markdown(slug, meta["title"], meta["versions"][-1]["request"])
        from ..features.skills import _lib, _skill_dict

        lib = _lib(svc)
        skill_id = f"creator-{slug}"[:40].strip("-")
        sk = await asyncio.to_thread(lib.create, skill_id, skill_md, True)
        meta["skill_installed"] = True
        meta["skill_id"] = sk.id
        meta["updated_at"] = now_iso()
        _atomic_json(self._meta_path(slug), meta)
        return {"installed": True, "skill": _skill_dict(sk), "project": meta}

    async def send_to_pult(self, slug: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Format and optionally send a project summary to the owner's Telegram Pult."""
        from ..direct_gen import pult as _pult
        from ..features import pult_report

        meta = self.get_project(slug)
        ver = meta["versions"][-1]
        sc = ver.get("score") or {}
        html_msg = pult_report.format_telegram_html(
            title=f"Creator Mode · {meta['title']}",
            summary=f"Собран артефакт **{meta['kind_label']}** (`{meta['slug']}` v{ver['n']}).",
            findings=[
                f"Модель: `{ver['model']}`",
                f"Оценка гейтов: `{sc.get('total_score', 100)}/100` (`passed_all_gates={sc.get('passed_all_gates')}`)",
                f"Файлы: `{', '.join(ver.get('files') or [])}`",
                f"SHA-256: `{str(ver.get('bundle_sha256') or '')[:16]}…`",
            ],
            signature="Creator Mode · Agent 80",
        )
        if dry_run:
            return {"sent": False, "dry_run": True, "telegram_html": html_msg}
        try:
            token, chat_id, proxy = _pult.load_target()
            mid = await pult_report._send_text(token, chat_id, html_msg, proxy)
            return {"sent": True, "message_id": mid, "telegram_html": html_msg}
        except _pult.PultError as exc:
            return {"sent": False, "code": exc.code, "message": exc.message, "telegram_html": html_msg}

    async def run_arena(self, body: dict[str, Any]) -> dict[str, Any]:
        """Run a multi-agent Creator Mode benchmark across multiple local models."""
        why = self.stop_reason()
        if why:
            raise CreatorError(409, "stopped", f"STOP активен: {why}")

        prompt = str(body.get("request") or "/plugin Аудит безопасности и монетизации игры с отчётом в Пульт").strip()
        spec = parse_creator_prompt(prompt, body.get("kind"))
        requested_models = body.get("models")
        if not isinstance(requested_models, list) or not requested_models:
            avail = await self.list_local_models()
            preferred = [
                "ace-decider-08b:latest",
                "ace-qwen3-17b:latest",
                "qwen35-3d-coder:latest",
                "qwen3-coder:30b",
                "bossman-fast-qwen36-35b-a3b-q5:latest",
            ]
            requested_models = [m for m in preferred if m in avail][:3]
            if not requested_models:
                requested_models = avail[:2] if avail else ["creator-blueprint-v1"]

        arena_id = uuid.uuid4().hex[:12]
        results: list[dict[str, Any]] = []
        telemetry = self.load_telemetry()

        for idx, model_name in enumerate(requested_models[:5]):
            slug = slugify(f"arena-{arena_id[:6]}-{idx + 1}")
            title = f"Arena #{idx + 1} ({model_name})"
            cand_files: dict[str, str] | None = None
            elapsed_s = 0.0
            tok_out = 0
            raw_excerpt = ""
            error_note = ""

            if model_name != "creator-blueprint-v1":
                messages = build_creator_messages(spec, title, slug)
                t0 = time.monotonic()
                try:
                    raw_out, tok_out, elapsed_s = await self._call_local_ollama(model_name, messages)
                    raw_excerpt = raw_out[:300]
                    try:
                        cand_files = parse_output(raw_out).files
                    except ParseError as pe:
                        error_note = f"parse_fallback: {pe}"
                except Exception as exc:  # noqa: BLE001
                    elapsed_s = time.monotonic() - t0
                    error_note = f"{type(exc).__name__}: {str(exc)[:120]}"

            t_b = time.monotonic()
            files, used_llm = ensure_complete_build(
                spec.kind,
                cand_files,
                title=title,
                slug=slug,
                request=spec.clean_request,
                telemetry=telemetry,
            )
            if elapsed_s <= 0:
                elapsed_s = max(0.01, time.monotonic() - t_b)
            sc = score_build(spec.kind, files, elapsed_s=elapsed_s, tokens_out=tok_out, used_llm=used_llm)
            results.append({
                "model": model_name,
                "kind": spec.kind,
                "used_llm_directly": used_llm,
                "score": sc,
                "files": sorted(files.keys()),
                "raw_excerpt": raw_excerpt,
                "note": error_note,
            })

        results.sort(key=lambda r: (r["score"]["total_score"], r["score"]["tokens_per_sec"]), reverse=True)
        winner = results[0]["model"] if results else None
        report = {
            "arena_id": arena_id,
            "created_at": now_iso(),
            "request": spec.raw_request,
            "kind": spec.kind,
            "kind_label": KIND_LABELS_RU.get(spec.kind, spec.kind),
            "winner": winner,
            "results": results,
        }
        _atomic_json(self.arena_dir / f"{arena_id}.json", report)
        return report

    def list_arena_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in sorted(self.arena_dir.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        out.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return out[:limit]
