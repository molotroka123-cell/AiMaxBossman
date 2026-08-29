"""Stage 11 — AI Lab candidates: raw trajectory → производный датасет-кандидат.

Инварианты (non-negotiable Stage 11):
- raw trajectory НИКОГДА не становится training-данными напрямую;
- raw иммутабелен: candidat — производная запись, raw не мутируется;
- poisoned/empty/failed траектории отвергаются по умолчанию;
- provenance на каждом сэмпле (источник, sanitizer, validator, approval);
- дубликат кандидата по (source_sha256, sanitizer_version) — конфликт.
Качество/валидация — переиспользует Stage 8 DatasetGate (второго гейта нет).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path, PureWindowsPath
from typing import Any

from .. import errors
from ..sandbox.dataset import DatasetGate
from .sanitizer import SANITIZER_VERSION, content_sha256, sanitize_obj

# id песочницы — короткий безопасный идентификатор, а НЕ путь: только ASCII
# буквы/цифры/«-»/«_», первый символ — буква или цифра. Этим отсекаются ../,
# абсолютные пути, буквы дисков (C:), UNC (\\server), NUL, %-кодирование
# (%2e%2e, %00) и сверхдлинные имена — все они не проходят полный матч.
_SANDBOX_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SANDBOX_ID_MAX = 64


def sandbox_id_rejected(sandbox_id: str) -> bool:
    """Полная батарея отказов для sandbox_id ДО любых операций с путями."""
    if not isinstance(sandbox_id, str) or not sandbox_id:
        return True
    if "\x00" in sandbox_id:
        return True                        # NUL (и в любом виде)
    if "%" in sandbox_id:
        return True                        # URL-кодирование: %2e%2e, %2f, %00
    if len(sandbox_id) > _SANDBOX_ID_MAX:
        return True                        # сверхдлинное имя
    if "/" in sandbox_id or "\\" in sandbox_id:
        return True                        # сегменты пути, смешанные слеши, UNC
    if ".." in sandbox_id:
        return True                        # «..» где угодно в id — отказ
    pw = PureWindowsPath(sandbox_id)
    if pw.is_absolute() or pw.drive or pw.root:
        return True                        # абсолютный путь, диск (C:\), UNC, корень
    return _SANDBOX_ID_RE.fullmatch(sandbox_id) is None


def sandbox_trajectory_path(sandbox_id: str, workspace_root: str | Path) -> Path:
    """sandbox_id → путь к trajectory.jsonl СТРОГО внутри workspace песочницы.

    Путь вычисляет СЕРВЕР: <workspace_root>/<sandbox_id>/trajectory.jsonl.
    Сдерживание проверяется ПОСЛЕ resolve() — ловит symlink/junction-побег.
    Сравнение — normcase-префикс: детерминировано на всех платформах
    (case-insensitive на Windows). Отказ единообразный (NotFound с одним
    только sandbox_id): не различаем «плохой id» и «нет файла», чтобы ручка
    не стала зондом файловой системы, и никогда не показываем путь хоста.
    """
    denied = errors.NotFound(f"trajectory not found: sandbox_id={sandbox_id[:80]!r}")
    if sandbox_id_rejected(sandbox_id):
        raise denied
    root = Path(workspace_root).resolve()
    path = (root / sandbox_id / "trajectory.jsonl").resolve()
    inside = os.path.normcase(str(path)).startswith(
        os.path.normcase(str(root) + os.sep))
    if not inside or not path.is_file():
        raise denied
    return path

# маркеры отравленных/непригодных траекторий (defense in depth поверх DatasetGate)
_POISON_MARKERS = (
    "ignore previous instructions", "disregard all", "system prompt:",
    "you are now", "developer mode", "../", "<script",
)
_MAX_TEXT_LEN = 20_000


class AiLabCandidate:
    __slots__ = ("id", "source_sha256", "sandbox_id", "trajectory_path", "samples",
                 "state", "reasons", "decided_by", "decided_at", "created_at",
                 "sanitizer_version")

    def __init__(self, *, id: str, source_sha256: str, sandbox_id: str,
                 trajectory_path: str, samples: list[dict], reasons: tuple[str, ...],
                 created_at: float, sanitizer_version: str = SANITIZER_VERSION,
                 state: str = "CANDIDATE", decided_by: str | None = None,
                 decided_at: float | None = None) -> None:
        self.id = id
        self.source_sha256 = source_sha256
        self.sandbox_id = sandbox_id
        self.trajectory_path = trajectory_path
        self.samples = samples
        self.reasons = reasons
        self.created_at = created_at
        self.sanitizer_version = sanitizer_version
        self.state = state
        self.decided_by = decided_by
        self.decided_at = decided_at

    @property
    def approved(self) -> bool:
        return self.state == "APPROVED"

    def provenance(self, sample: dict) -> dict:
        """Provenance сэмпла: источник, санитайзер, валидатор, approval."""
        return {
            "candidate_id": self.id,
            "source": {"sandbox_id": self.sandbox_id,
                       "trajectory_path": self.trajectory_path,
                       "sha256": self.source_sha256},
            "sanitizer_version": self.sanitizer_version,
            "validator": {"passed": not self.reasons, "reasons": list(self.reasons)},
            "approval": ({"by": self.decided_by, "at": self.decided_at}
                         if self.state == "APPROVED" else None),
        }


def load_trajectory(path: str | Path) -> tuple[list[dict], str, dict]:
    """Читает append-only JSONL траектории ТОЛЬКО на чтение.
    Возвращает (события, sha256 содержимого, meta). Raw не мутируется.
    Ошибки НЕ раскрывают путь хоста (единое сообщение без p)."""
    p = Path(path)
    if not p.is_file():
        raise errors.BossmanError("trajectory not found",
                                  code=errors.ErrorCode.NOT_FOUND)
    try:
        raw = p.read_bytes()
    except OSError:
        raise errors.BossmanError("trajectory not readable",
                                  code=errors.ErrorCode.NOT_FOUND) from None
    sha = __import__("hashlib").sha256(raw).hexdigest()
    events: list[dict] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue                      # битая строка не валит весь источник
        if isinstance(ev, dict):
            events.append(ev)
    failed = any(ev.get("kind") == "failure" for ev in events)
    return events, sha, {"events": len(events), "has_failures": failed}


def _poisoned(events: list[dict]) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    for ev in events:
        blob = json.dumps(ev, ensure_ascii=False, default=str)
        low = blob.lower()
        for marker in _POISON_MARKERS:
            if marker in low:
                reasons.append(f"poison marker: {marker!r}")
                break
        for v in ev.values():
            if isinstance(v, str) and len(v) > _MAX_TEXT_LEN:
                reasons.append("oversized field")
                break
    return bool(reasons), tuple(reasons)


class CandidateStore:
    """Хранилище кандидатов: JSON-файл, append-only по сути записи."""

    def __init__(self, root: str | Path, *, min_samples: int = 1) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / "candidates.json"
        self.gate = DatasetGate(min_samples=min_samples)

    # --- persist ---

    def _load(self) -> list[dict]:
        if not self._path.is_file():
            return []
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, rows: list[dict]) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        os_replace(tmp, self._path)

    # --- API ---

    def create_from_sandbox(self, sandbox_id: str, *, workspace_root: str | Path,
                            sandbox_id_verified: bool = False) -> AiLabCandidate:
        """Кандидат из траектории песочницы БЕЗ произвольных путей хоста.

        Путь к raw вычисляет сам store: <workspace_root>/<sandbox_id>/
        trajectory.jsonl с полным сдерживанием (sandbox_trajectory_path:
        отказ абсолютным путям, «..», дискам, UNC, NUL, %-кодированию,
        сверхдлинным именам; сдерживание после resolve() ловит symlink-побег).
        `sandbox_id_verified` — интерлок: вызывающий (роут) подтверждает, что
        id пришёл из доверенного источника и прошёл свою проверку; store в
        ЛЮБОМ случае перепроверяет всё сам (defense in depth). Ошибки — только
        с sandbox_id, без путей хоста.
        """
        if not sandbox_id_verified:
            raise errors.PolicyDenied(
                "sandbox_id must be verified by the caller before store-level creation",
                code=errors.ErrorCode.POLICY_DENIED)
        path = sandbox_trajectory_path(sandbox_id, workspace_root)
        return self.create(str(path), sandbox_id=sandbox_id)

    def create(self, trajectory_path: str, *, sandbox_id: str,
               candidate_id: str | None = None) -> AiLabCandidate:
        """raw trajectory → sanitize → validate → кандидат. Raw только читается."""
        events, sha, meta = load_trajectory(trajectory_path)
        if meta["has_failures"]:
            raise errors.BossmanError(
                "trajectory contains failures — rejected by default",
                code=errors.ErrorCode.POLICY_DENIED)
        poisoned, pre_reasons = _poisoned(events)
        if poisoned:
            raise errors.BossmanError(
                f"trajectory rejected: {', '.join(pre_reasons)}",
                code=errors.ErrorCode.POLICY_DENIED)

        rows = self._load()
        for r in rows:
            if r["source_sha256"] == sha and r["sanitizer_version"] == SANITIZER_VERSION:
                raise errors.BossmanError(
                    f"duplicate candidate for source {sha[:12]} "
                    f"(existing {r['id']})", code=errors.ErrorCode.CONFLICT)

        samples = self.gate.sanitize(events)          # Stage 8 sanitize (obs-redact)
        samples = sanitize_obj(samples)               # + PII-подобное
        kept, reasons = self.gate.validate(samples)   # Stage 8 validate
        if len(kept) < self.gate.min_samples:
            raise errors.BossmanError(
                f"validation failed: {', '.join(reasons) or 'no samples'}",
                code=errors.ErrorCode.POLICY_DENIED)

        cand = AiLabCandidate(
            id=candidate_id or f"cand_{int(time.time() * 1000)}_{sha[:8]}",
            source_sha256=sha, sandbox_id=sandbox_id,
            trajectory_path=str(trajectory_path), samples=kept, reasons=reasons,
            created_at=time.time())
        rows.append({
            "id": cand.id, "source_sha256": cand.source_sha256,
            "sandbox_id": sandbox_id, "trajectory_path": cand.trajectory_path,
            "sanitizer_version": SANITIZER_VERSION, "state": cand.state,
            "reasons": list(reasons), "created_at": cand.created_at,
            "decided_by": None, "decided_at": None, "samples": kept,
        })
        self._save(rows)
        return cand

    def get(self, candidate_id: str) -> AiLabCandidate:
        for r in self._load():
            if r["id"] == candidate_id:
                return AiLabCandidate(
                    id=r["id"], source_sha256=r["source_sha256"],
                    sandbox_id=r["sandbox_id"], trajectory_path=r["trajectory_path"],
                    samples=r["samples"], reasons=tuple(r["reasons"]),
                    created_at=r["created_at"], sanitizer_version=r["sanitizer_version"],
                    state=r["state"], decided_by=r["decided_by"],
                    decided_at=r["decided_at"])
        raise errors.BossmanError(f"candidate not found: {candidate_id}",
                                  code=errors.ErrorCode.NOT_FOUND)

    def list(self) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "samples"} for r in self._load()]

    def decide(self, candidate_id: str, *, approve: bool, by: str) -> AiLabCandidate:
        """Human gate. REJECTED-кандидат уже не поднять обратно без нового sanitize."""
        rows = self._load()
        for r in rows:
            if r["id"] == candidate_id:
                if r["state"] == "REJECTED":
                    raise errors.BossmanError("candidate already rejected",
                                              code=errors.ErrorCode.CONFLICT)
                r["state"] = "APPROVED" if approve else "REJECTED"
                r["decided_by"] = by
                r["decided_at"] = time.time()
                if not approve:
                    r["reasons"] = list({*r["reasons"], "rejected_by_human"})
                self._save(rows)
                return self.get(candidate_id)
        raise errors.BossmanError(f"candidate not found: {candidate_id}",
                                  code=errors.ErrorCode.NOT_FOUND)


def os_replace(src: Path, dst: Path) -> None:
    import os
    os.replace(src, dst)
