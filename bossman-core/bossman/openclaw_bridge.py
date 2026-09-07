"""Security-gated bridge between Bossman and an optional local OpenClaw runtime.

The bridge deliberately does *not* auto-install OpenClaw or community skills.
It provides a narrow owner-invoked path:

    probe -> plan -> OpenClaw verify -> explicit owner approval -> pinned install
          -> post-install fingerprint -> local receipt

OpenClaw remains an external, untrusted skill runtime.  Bossman's approval,
evidence, learning/promotion and rollback authority are not delegated to it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = _REPO_ROOT / "integrations" / "openclaw" / "curated-skills.json"
DEFAULT_SECURITY_REVIEW = _REPO_ROOT / "integrations" / "openclaw" / "security-review-20260907.json"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?$")
_INSTALL_REF_RE = re.compile(r"^@[A-Za-z0-9_.-]+/[a-z0-9][a-z0-9-]{0,63}$")
_ALLOWED_ADMISSION = {"eligible", "hold", "reject", "reference", "external"}
_ALLOWED_ENV = {
    "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
    "SYSTEMROOT", "COMSPEC", "PATHEXT", "TERM", "LANG", "LC_ALL", "LC_CTYPE",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "OPENCLAW_HOME",
}
_DENIED_FLAGS = {"--force", "--force-install", "--acknowledge-clawhub-risk", "--acknowledge-install-policy-warning", "--global"}


class OpenClawBridgeError(RuntimeError):
    """Fail-closed bridge refusal."""


@dataclass(frozen=True)
class SkillPlan:
    slug: str
    install_ref: str
    version: str
    admission: str
    risk: str
    verify_argv: tuple[str, ...]
    install_argv: tuple[str, ...]
    bossman_constraints: tuple[str, ...]


@dataclass(frozen=True)
class ProbeResult:
    available: bool
    executable: str | None
    version: str | None
    reason: str | None = None


@dataclass(frozen=True)
class InstallReceipt:
    schema_version: int
    slug: str
    install_ref: str
    version: str
    catalog_source_sha: str
    security_review_date: str
    openclaw_version: str
    verification_sha256: str
    bundle_sha256: str
    installed_at: str
    workspace: str
    owner_approved: bool


Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenClawBridgeError(f"cannot read {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise OpenClawBridgeError(f"{label} must be a JSON object")
    return data


def _catalog_index(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    data = _read_json(path, label="OpenClaw curated catalog")
    source = data.get("source") or {}
    source_sha = str(source.get("commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise OpenClawBridgeError("curated catalog source commit is not pinned")
    policy = data.get("policy") or {}
    if policy.get("auto_install") is not False:
        raise OpenClawBridgeError("curated catalog must keep auto_install=false")
    skills = data.get("skills")
    if not isinstance(skills, list):
        raise OpenClawBridgeError("curated catalog skills must be a list")
    index: dict[str, dict[str, Any]] = {}
    for raw in skills:
        if not isinstance(raw, dict):
            raise OpenClawBridgeError("invalid curated skill row")
        slug = str(raw.get("slug") or "")
        if not _SLUG_RE.fullmatch(slug) or slug in index:
            raise OpenClawBridgeError(f"invalid or duplicate curated slug: {slug!r}")
        index[slug] = raw
    return data, index


def _review_index(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    data = _read_json(path, label="OpenClaw security review")
    if data.get("schema_version") != 1:
        raise OpenClawBridgeError("security review schema_version must be 1")
    reviewed_at = str(data.get("reviewed_at") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reviewed_at):
        raise OpenClawBridgeError("security review must carry YYYY-MM-DD reviewed_at")
    rows = data.get("skills")
    if not isinstance(rows, list):
        raise OpenClawBridgeError("security review skills must be a list")
    index: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise OpenClawBridgeError("invalid security review row")
        slug = str(raw.get("slug") or "")
        admission = raw.get("admission")
        if not _SLUG_RE.fullmatch(slug) or slug in index:
            raise OpenClawBridgeError(f"invalid or duplicate reviewed slug: {slug!r}")
        if admission not in _ALLOWED_ADMISSION:
            raise OpenClawBridgeError(f"invalid admission for {slug}: {admission!r}")
        index[slug] = raw
    return data, index


def sanitized_openclaw_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Minimal subprocess environment; provider/Bossman secrets are not inherited."""
    source = os.environ if source is None else source
    return {k: str(v) for k, v in source.items() if k in _ALLOWED_ENV and isinstance(v, str)}


def fingerprint_skill_dir(root: Path) -> str:
    """Hash installed skill names + bytes; refuse symlinks and special files."""
    if not root.is_dir() or root.is_symlink():
        raise OpenClawBridgeError(f"installed skill directory is missing or unsafe: {root}")
    digest = hashlib.sha256()
    files = sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())
    count = 0
    for path in files:
        if path.is_symlink():
            raise OpenClawBridgeError(f"installed skill contains symlink: {path.relative_to(root)}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise OpenClawBridgeError(f"installed skill contains special file: {path.relative_to(root)}")
        rel = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        count += 1
    if count == 0:
        raise OpenClawBridgeError("installed skill directory is empty")
    return digest.hexdigest()


class OpenClawBridge:
    def __init__(
        self,
        *,
        catalog_path: Path = DEFAULT_CATALOG,
        security_review_path: Path = DEFAULT_SECURITY_REVIEW,
        executable: str | None = None,
        runner: Runner = subprocess.run,
        which: Which = shutil.which,
        env_source: Mapping[str, str] | None = None,
    ) -> None:
        self.catalog, self._catalog = _catalog_index(catalog_path)
        self.review, self._review = _review_index(security_review_path)
        self._explicit_executable = executable
        self._runner = runner
        self._which = which
        self._env_source = dict(os.environ if env_source is None else env_source)

    def _resolve_executable(self) -> str:
        exe = self._explicit_executable or self._which("openclaw")
        if not exe:
            raise OpenClawBridgeError(
                "OpenClaw CLI is not installed or not on PATH; install OpenClaw separately, then rerun doctor"
            )
        return str(exe)

    def _run(self, argv: Sequence[str], *, cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        if not argv:
            raise OpenClawBridgeError("empty OpenClaw argv")
        if any(flag in argv for flag in _DENIED_FLAGS):
            raise OpenClawBridgeError("dangerous OpenClaw install override is forbidden by Bossman")
        return self._runner(
            list(argv),
            cwd=str(cwd) if cwd is not None else None,
            env=sanitized_openclaw_env(self._env_source),
            timeout=timeout,
            text=True,
            capture_output=True,
            check=False,
        )

    def probe(self) -> ProbeResult:
        try:
            exe = self._resolve_executable()
        except OpenClawBridgeError as exc:
            return ProbeResult(False, None, None, str(exc))
        try:
            proc = self._run((exe, "--version"), timeout=8)
        except (OSError, subprocess.SubprocessError) as exc:
            return ProbeResult(False, exe, None, f"OpenClaw probe failed: {exc}")
        version = (proc.stdout or "").strip().splitlines()
        if proc.returncode != 0 or not version:
            reason = (proc.stderr or proc.stdout or "OpenClaw --version failed").strip()[:500]
            return ProbeResult(False, exe, None, reason)
        return ProbeResult(True, exe, version[0][:200])

    def inventory(self, *, workspace: Path | None = None) -> Any:
        exe = self._resolve_executable()
        proc = self._run((exe, "skills", "list", "--json"), cwd=workspace, timeout=30)
        if proc.returncode != 0:
            raise OpenClawBridgeError(f"openclaw skills list failed: {(proc.stderr or proc.stdout).strip()[:500]}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise OpenClawBridgeError("OpenClaw skills list did not return JSON") from exc

    def plan(self, slug: str) -> SkillPlan:
        if not _SLUG_RE.fullmatch(slug):
            raise OpenClawBridgeError("invalid skill slug")
        curated = self._catalog.get(slug)
        if curated is None:
            raise OpenClawBridgeError(f"skill is not in Bossman's curated catalog: {slug}")
        reviewed = self._review.get(slug)
        if reviewed is None:
            raise OpenClawBridgeError(f"skill has no Bossman security review: {slug}")
        admission = str(reviewed.get("admission") or "")
        if admission != "eligible":
            raise OpenClawBridgeError(f"skill is not install-eligible: {slug} ({admission})")
        if curated.get("mode") != "candidate":
            raise OpenClawBridgeError(f"reference-only skill cannot be installed through Bossman: {slug}")

        install_ref = str(reviewed.get("install_ref") or "")
        version = str(reviewed.get("version") or "")
        if not _INSTALL_REF_RE.fullmatch(install_ref):
            raise OpenClawBridgeError(f"reviewed install_ref is invalid for {slug}")
        if not _VERSION_RE.fullmatch(version):
            raise OpenClawBridgeError(f"reviewed version is not pinned for {slug}")
        signals = reviewed.get("security_signals") or {}
        if not isinstance(signals, dict) or not signals:
            raise OpenClawBridgeError(f"security signals missing for {slug}")
        for name, status in signals.items():
            if str(status).lower() != "benign":
                raise OpenClawBridgeError(f"security signal {name} is not benign for {slug}: {status}")

        exe = self._resolve_executable()
        verify = (exe, "skills", "verify", install_ref, "--version", version, "--json")
        install = (exe, "skills", "install", install_ref, "--version", version)
        return SkillPlan(
            slug=slug,
            install_ref=install_ref,
            version=version,
            admission=admission,
            risk=str(curated.get("risk") or "unknown"),
            verify_argv=verify,
            install_argv=install,
            bossman_constraints=tuple(str(x) for x in (reviewed.get("bossman_constraints") or ())),
        )

    def verify(self, slug: str, *, workspace: Path | None = None) -> tuple[SkillPlan, dict[str, Any]]:
        plan = self.plan(slug)
        proc = self._run(plan.verify_argv, cwd=workspace, timeout=60)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "verification failed").strip()[:1000]
            raise OpenClawBridgeError(f"OpenClaw verification refused {slug}: {detail}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise OpenClawBridgeError("OpenClaw verify did not return JSON") from exc
        if not isinstance(payload, dict):
            raise OpenClawBridgeError("OpenClaw verify JSON must be an object")
        ok = payload.get("ok")
        decision = str(payload.get("decision") or "").lower()
        if ok is not True:
            raise OpenClawBridgeError(f"OpenClaw verify did not return ok=true for {slug}")
        if decision in {"fail", "blocked", "malicious", "suspicious", "risky"}:
            raise OpenClawBridgeError(f"OpenClaw verify decision refused {slug}: {decision}")
        return plan, payload

    @staticmethod
    def _safe_workspace(workspace: Path) -> Path:
        if workspace.is_symlink():
            raise OpenClawBridgeError("workspace symlink is not accepted")
        resolved = workspace.resolve(strict=True)
        if not resolved.is_dir():
            raise OpenClawBridgeError("workspace must be an existing directory")
        return resolved

    def install(self, slug: str, *, workspace: Path, owner_approved: bool = False) -> InstallReceipt:
        if owner_approved is not True:
            raise OpenClawBridgeError("explicit owner approval is required for community skill installation")
        ws = self._safe_workspace(workspace)
        probe = self.probe()
        if not probe.available or not probe.version:
            raise OpenClawBridgeError(probe.reason or "OpenClaw is unavailable")

        plan, verification = self.verify(slug, workspace=ws)
        proc = self._run(plan.install_argv, cwd=ws, timeout=180)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "install failed").strip()[:1000]
            raise OpenClawBridgeError(f"OpenClaw installation failed for {slug}: {detail}")

        skill_dir = ws / "skills" / slug
        bundle_sha = fingerprint_skill_dir(skill_dir)
        verify_bytes = json.dumps(verification, sort_keys=True, separators=(",", ":")).encode("utf-8")
        receipt = InstallReceipt(
            schema_version=1,
            slug=slug,
            install_ref=plan.install_ref,
            version=plan.version,
            catalog_source_sha=str(self.catalog["source"]["commit"]),
            security_review_date=str(self.review["reviewed_at"]),
            openclaw_version=probe.version,
            verification_sha256=hashlib.sha256(verify_bytes).hexdigest(),
            bundle_sha256=bundle_sha,
            installed_at=datetime.now(timezone.utc).isoformat(),
            workspace=str(ws),
            owner_approved=True,
        )
        self._write_receipt(ws, receipt)
        return receipt

    @staticmethod
    def _write_receipt(workspace: Path, receipt: InstallReceipt) -> Path:
        root = workspace / ".bossman" / "openclaw-receipts"
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{receipt.slug}.json"
        fd, tmp_name = tempfile.mkstemp(prefix=f".{receipt.slug}.", suffix=".tmp", dir=str(root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(receipt), handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return target
