"""Learning trace: схема, валидация, редактирование секретов, стор, retrieval.

Канонические пути (repo root):
  schemas/learning_fix_case.schema.json   — схема записи
  data/learning/fix_cases.jsonl           — ТОЛЬКО learning_status=VERIFIED
  data/learning/failed_experiments.jsonl  — FAILED_EXPERIMENT / PARTIAL / UNVERIFIED / REJECTED
  docs/learning/fix_logs/<TASK_ID>.md     — человекочитаемая карточка

Инварианты:
  * VERIFIED требует evidence, external_verification и verified_by, причём
    верификатор ≠ агент/модель записи (нет самосертификации);
  * скрытые поля рассуждений запрещены (FORBIDDEN_FIELDS);
  * секреты (Bearer/api_key/token-like/канарейка BOSSMAN_TEST_SECRET_*) не
    сохраняются — редактируются до записи, а валидация отвергает остатки;
  * retrieval по умолчанию отдаёт только VERIFIED; прочие статусы доступны
    только явным include_failed=True и помечены — их нельзя принять за
    предпочтительное поведение.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "learning_fix_case.schema.json"
DATA_DIR = ROOT / "data" / "learning"
DOCS_DIR = ROOT / "docs" / "learning" / "fix_logs"

STATUSES = ("VERIFIED", "FAILED_EXPERIMENT", "PARTIAL", "UNVERIFIED", "REJECTED")
FORBIDDEN_FIELDS = frozenset({"chain_of_thought", "hidden_reasoning", "thoughts", "scratchpad",
                              "raw_reasoning", "private_reasoning"})
# Что считается «внешней» верификацией: не сам агент записи.
SELF_VERIFIER_MARKERS = ("self", "same-agent", "self-report")

_RE_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")
_RE_KV = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b(\s*[:=]\s*)([^\s,;\"']{6,})")
_RE_TOKENLIKE = re.compile(
    r"\b(sk-[A-Za-z0-9\-_]{8,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|ghs_[A-Za-z0-9]{20,}|"
    r"xox[abpr]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z\-_]{30,}|hf_[A-Za-z0-9]{20,}|"
    r"BOSSMAN_TEST_SECRET_[A-Za-z0-9]{4,})")
REDACTED = "***REDACTED***"


class ValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def redact_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    out = _RE_BEARER.sub("Bearer " + REDACTED, text)
    out = _RE_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
    return _RE_TOKENLIKE.sub(REDACTED, out)


def has_secret(text: str) -> bool:
    """True, если остался НЕотредактированный секрет. Плейсхолдер REDACTED
    вырезается перед проверкой: «Authorization: ***REDACTED***» — не секрет."""
    if not isinstance(text, str):
        return False
    probe = text.replace(REDACTED, "")
    return bool(_RE_BEARER.search(probe) or _RE_KV.search(probe) or _RE_TOKENLIKE.search(probe))


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj


def _walk_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for x in obj:
            yield from _walk_strings(x)
    elif isinstance(obj, dict):
        for x in obj.values():
            yield from _walk_strings(x)


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def case_id(case: dict) -> str:
    """Детерминированный id: task_id + end_sha (+ start_sha) — одинаковая запись
    даёт одинаковый id, дубликаты видны."""
    raw = f"{case.get('task_id', '')}|{case.get('start_sha', '')}|{case.get('end_sha', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _type_ok(value: Any, spec: dict) -> bool:
    t = spec.get("type")
    types = t if isinstance(t, list) else [t]
    for tt in types:
        if tt == "string" and isinstance(value, str):
            return True
        if tt == "array" and isinstance(value, list):
            items = spec.get("items") or {}
            return all(_type_ok(v, items) for v in value) if items else True
        if tt == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if tt == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if tt == "boolean" and isinstance(value, bool):
            return True
        if tt == "object" and isinstance(value, dict):
            return True
        if tt is None:
            return True
    return False


def validate(case: dict, *, schema: dict | None = None) -> list[str]:
    """Список ошибок (пустой = валидно). Проверяет схему (required/типы/enum/
    additionalProperties) и инварианты слоя обучения."""
    schema = schema or load_schema()
    errors: list[str] = []
    if not isinstance(case, dict):
        return ["case must be an object"]
    props = schema.get("properties", {})
    for f in schema.get("required", []):
        if f not in case:
            errors.append(f"missing required field: {f}")
    for k, v in case.items():
        if k in FORBIDDEN_FIELDS or k in schema.get("x-forbidden-fields", []):
            errors.append(f"forbidden field (hidden reasoning is not stored): {k}")
            continue
        if k not in props:
            errors.append(f"unknown field: {k}")
            continue
        spec = props[k]
        if not _type_ok(v, spec):
            errors.append(f"bad type for {k}")
        if "enum" in spec and v not in spec["enum"]:
            errors.append(f"{k} not in {spec['enum']}")
        if "minLength" in spec and isinstance(v, str) and len(v) < spec["minLength"]:
            errors.append(f"{k} too short")
        if k == "confidence" and isinstance(v, (int, float)) and not 0 <= v <= 1:
            errors.append("confidence must be within [0,1]")
        if k == "tags" and isinstance(v, dict):
            tspec = spec.get("properties", {})
            for tk, tv in v.items():
                if tk not in tspec:
                    errors.append(f"unknown tag: {tk}")
                elif "enum" in tspec[tk] and tv not in tspec[tk]["enum"]:
                    errors.append(f"tag {tk} not in {tspec[tk]['enum']}")
    status = case.get("learning_status")
    if status == "VERIFIED":
        if not case.get("evidence"):
            errors.append("VERIFIED requires non-empty evidence")
        if not str(case.get("external_verification") or "").strip():
            errors.append("VERIFIED requires external_verification")
        if not (case.get("verified_by") or []):
            errors.append("VERIFIED requires verified_by")
        errors += _identity_errors(case)
        errors += _evidence_record_errors(case)
        if "case_id" in case and case["case_id"] != case_id(case):
            errors.append("case_id does not match deterministic hash")
    for s in _walk_strings(case):
        if has_secret(s):
            errors.append("secret-like value present (redact before storing)")
            break
    return errors


# P0-02: независимость — по типизированной identity, не по display-строкам.
INDEPENDENT_CLASSES = frozenset({"cross_model", "external_tool", "human"})
EVIDENCE_TTL_S = 30 * 24 * 3600     # запись учит долго; наблюдение должно быть датировано


def _identity_errors(case: dict) -> list[str]:
    verifiers = case.get("verifiers")
    if not verifiers:
        return ["VERIFIED requires typed verifiers[] (principal_id + independence_class); "
                "legacy string-only verified_by is UNVERIFIED until migrated"]
    me_principal = str(case.get("principal_id") or case.get("agent") or "")
    me_model = str(case.get("model") or "")
    me_run = str(case.get("run_id") or "")
    for v in verifiers:
        if not isinstance(v, dict):
            return ["verifier entries must be objects"]
        cls = str(v.get("independence_class") or "")
        pid = str(v.get("principal_id") or "")
        if cls not in INDEPENDENT_CLASSES:
            continue
        if pid and pid == me_principal:
            continue
        if me_run and str(v.get("run_id") or "") == me_run:
            continue
        if cls == "cross_model" and str(v.get("model_id") or "") == me_model:
            continue
        return []          # хотя бы один независимый верификатор
    return ["VERIFIED requires an independent verifier (different principal, run and model/tool/human)"]


def _evidence_record_errors(case: dict) -> list[str]:
    recs = case.get("evidence_records")
    if not recs:
        return ["VERIFIED requires evidence_records[] with observed_at/source/task binding"]
    for r in recs:
        if not isinstance(r, dict):
            return ["evidence_records entries must be objects"]
        if not float(r.get("observed_at") or 0) > 0:
            return ["evidence_record without observed_at"]
        if float(r.get("collected_at") or r.get("observed_at")) < float(r.get("observed_at")):
            return ["evidence_record collected_at before observed_at"]
        if not str(r.get("source") or "").strip():
            return ["evidence_record without source"]
        if str(r.get("task_id") or "") != str(case.get("task_id")):
            return ["evidence_record bound to another task"]
        if case.get("run_id") and str(r.get("run_id") or "") != str(case.get("run_id")):
            return ["evidence_record bound to another run"]
        if case.get("end_sha") and r.get("head_sha") and r["head_sha"] != case["end_sha"]:
            return ["evidence_record bound to another head_sha"]
        if not str(r.get("expected") or "") or not str(r.get("actual") or ""):
            return ["evidence_record without expected/actual"]
    return []


_MD_SECTIONS = [
    ("task", "Task"), ("symptom", "Symptom"), ("reproduction", "Reproduction"),
    ("evidence", "Evidence"), ("root_cause_hypotheses", "Hypotheses considered"),
    ("rejected_hypotheses", "Rejected hypotheses + why"), ("root_cause", "Root cause"),
    ("relevant_code_paths", "Relevant code paths"), ("fix_strategy", "Fix strategy"),
    ("alternatives_considered", "Alternatives considered"), ("why_this_fix", "Why this fix was chosen"),
    ("files_changed", "Files changed"), ("tests_added", "Tests added"),
    ("original_repro_result", "Original reproduction after fix"),
    ("adversarial_variants", "Adversarial variants"), ("regression_result", "Regression"),
    ("external_verification", "Fresh external verification"),
    ("failure_recovery_lessons", "Failed approaches / recovery lessons"),
    ("generalizable_lessons", "Generalizable lessons"), ("teach_local_model", "Teach local model"),
    ("limitations", "Limitations / follow-up"),
]


def render_markdown(case: dict) -> str:
    lines = [f"# Learning Case: {case.get('task_id')}", "", "## Metadata",
             f"MODEL: {case.get('model')}", f"AGENT: {case.get('agent')}",
             f"START_SHA: {case.get('start_sha')}", f"END_SHA: {case.get('end_sha')}",
             f"LEARNING_STATUS: {case.get('learning_status')}",
             f"OUTCOME: {case.get('outcome', '')}",
             f"VERIFIED_BY: {', '.join(case.get('verified_by') or [])}",
             f"CONFIDENCE: {case.get('confidence')}",
             f"TAGS: {json.dumps(case.get('tags') or {}, ensure_ascii=False)}",
             f"FINDINGS: {', '.join(case.get('finding_ids') or [])}", ""]
    for key, title in _MD_SECTIONS:
        val = case.get(key)
        if val in (None, "", []):
            continue
        lines.append(f"## {title}")
        if isinstance(val, list):
            lines += [f"- {v}" for v in val]
        else:
            lines.append(str(val))
        lines.append("")
    return "\n".join(lines)


class ConflictError(RuntimeError):
    """CAS: ожидаемая версия записи не совпала с текущей."""


class LearningStore:
    """JSONL-хранилище с ЕДИНЫМ authoritative состоянием на case_id.

    Инварианты (P0-03):
      * case_id живёт ровно в одном корпусе (VERIFIED → fix_cases.jsonl, иначе →
        failed_experiments.jsonl); новая запись с тем же case_id — это версия
        (version+1, supersedes_version), старая версия уходит в history.jsonl с
        tombstone=True и superseded_by_version — retrieval её больше не видит;
      * запись атомарна: temp-файл в том же каталоге → fsync → os.replace, для
        обоих корпусов и истории; параллельные писатели сериализуются файловой
        блокировкой (fcntl.flock) на data_dir/.lock;
      * CAS: add(case, expected_version=N) отвергает запись, если текущая
        версия ≠ N (ConflictError) — детерминированное разрешение конфликта;
      * повреждённый/оборванный хвост файла не становится authoritative: строка,
        которая не парсится, игнорируется (и считается в .corrupt_lines)."""

    def __init__(self, data_dir: Path | None = None, docs_dir: Path | None = None,
                 schema: dict | None = None):
        self.data_dir = Path(data_dir or DATA_DIR)
        self.docs_dir = Path(docs_dir or DOCS_DIR)
        self.schema = schema or load_schema()
        self.verified_path = self.data_dir / "fix_cases.jsonl"
        self.failed_path = self.data_dir / "failed_experiments.jsonl"
        self.history_path = self.data_dir / "history.jsonl"
        self.corrupt_lines = 0

    # ------------------------------------------------------------ locking
    @contextlib.contextmanager
    def _locked(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.data_dir / ".lock"
        fh = open(lock_path, "a+")
        try:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except ImportError:            # Windows: msvcrt
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except ImportError:
                pass
            fh.close()

    # ------------------------------------------------------------ write
    def current(self, cid: str) -> dict | None:
        """Текущая (authoritative) запись по case_id из любого корпуса."""
        for c in self._read(self.verified_path) + self._read(self.failed_path):
            if c.get("case_id") == cid and not c.get("tombstone"):
                return c
        return None

    def add(self, case: dict, *, write_markdown: bool = True,
            expected_version: int | None = None) -> dict:
        case = redact_obj(dict(case))
        case.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        case["case_id"] = case_id(case)
        case.pop("tombstone", None); case.pop("superseded_by_version", None)
        with self._locked():
            verified = self._read(self.verified_path)
            failed = self._read(self.failed_path)
            prev = next((c for c in verified + failed if c.get("case_id") == case["case_id"]), None)
            cur_version = int(prev.get("version") or 1) if prev else 0
            if expected_version is not None and expected_version != cur_version:
                raise ConflictError(f"case {case['case_id']}: expected version {expected_version}, "
                                    f"current is {cur_version}")
            case["version"] = cur_version + 1
            if prev is not None:
                case["supersedes_version"] = cur_version
            errors = validate(case, schema=self.schema)
            if errors:
                raise ValidationError(errors)
            # единственное authoritative место: убрать старую версию отовсюду
            verified = [c for c in verified if c.get("case_id") != case["case_id"]]
            failed = [c for c in failed if c.get("case_id") != case["case_id"]]
            if case["learning_status"] == "VERIFIED":
                verified.append(case)
            else:
                failed.append(case)
            self._rewrite(self.verified_path, verified)
            self._rewrite(self.failed_path, failed)
            if prev is not None:
                tomb = dict(prev); tomb["tombstone"] = True
                tomb["superseded_by_version"] = case["version"]
                self._append_atomic(self.history_path, tomb)
        if write_markdown:
            self.docs_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", str(case["task_id"]))
            (self.docs_dir / f"{safe}.md").write_text(render_markdown(case), encoding="utf-8")
        return case

    # ------------------------------------------------------------ read
    def _read(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                self.corrupt_lines += 1          # оборванный хвост — не authoritative
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            except OSError:
                pass
            finally:
                os.close(dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _rewrite(self, path: Path, cases: list[dict]) -> None:
        self._atomic_write(path, "".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n"
                                         for c in cases))

    def _append_atomic(self, path: Path, rec: dict) -> None:
        existing = self._read(path)
        self._rewrite(path, existing + [rec])

    def verified(self) -> list[dict]:
        return [c for c in self._read(self.verified_path)
                if c.get("learning_status") == "VERIFIED" and not c.get("tombstone")]

    def failed(self) -> list[dict]:
        return [c for c in self._read(self.failed_path) if not c.get("tombstone")]

    def history(self) -> list[dict]:
        return self._read(self.history_path)

    def retrieve(self, *, domain: str | None = None, bug_class: str | None = None,
                 component: str | None = None, severity: str | None = None,
                 outcome: str | None = None, finding_id: str | None = None,
                 text: str | None = None, include_failed: bool = False,
                 limit: int = 8) -> list[dict]:
        """Фильтр по тегам/исходу/находке/тексту. По умолчанию — только VERIFIED.
        include_failed=True добавляет прочие статусы, каждый с пометкой
        `retrieval_warning` — их нельзя использовать как предпочтительное поведение.
        Superseded (tombstone) версии не возвращаются никогда."""
        pool = list(self.verified())
        if include_failed:
            for c in self.failed():
                c = dict(c)
                c["retrieval_warning"] = (f"{c.get('learning_status')}: negative/unverified knowledge — "
                                          "do NOT treat as preferred production behaviour")
                pool.append(c)

        def ok(c: dict) -> bool:
            tags = c.get("tags") or {}
            if domain and tags.get("domain") != domain:
                return False
            if bug_class and tags.get("bug_class") != bug_class:
                return False
            if component and component not in (tags.get("component") or ""):
                return False
            if severity and tags.get("severity") != severity:
                return False
            if outcome and c.get("outcome") != outcome:
                return False
            if finding_id and finding_id not in (c.get("finding_ids") or []):
                return False
            if text:
                hay = " ".join(_walk_strings({k: v for k, v in c.items()})).lower()
                if text.lower() not in hay:
                    return False
            return True
        return [c for c in pool if ok(c)][:max(1, limit)]

    def compact(self, case: dict) -> dict:
        """Компактная форма для инъекции в контекст локальной модели: уроки,
        доказательства, provenance — без длинных полей."""
        return {"case_id": case.get("case_id"), "task_id": case.get("task_id"),
                "version": case.get("version"),
                "learning_status": case.get("learning_status"), "outcome": case.get("outcome"),
                "symptom": case.get("symptom"), "root_cause": case.get("root_cause"),
                "evidence": (case.get("evidence") or [])[:4],
                "generalizable_lessons": case.get("generalizable_lessons"),
                "teach_local_model": case.get("teach_local_model"),
                "verified_by": case.get("verified_by"), "end_sha": case.get("end_sha"),
                "retrieval_warning": case.get("retrieval_warning")}

    def export_sanitized(self) -> list[dict]:
        """Экспорт для будущего fine-tuning: только VERIFIED, только явные поля,
        повторно отредактированные."""
        return [redact_obj(c) for c in self.verified()]


def _cli(argv: list[str]) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="learning.trace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("file")
    v = sub.add_parser("validate"); v.add_argument("file")
    r = sub.add_parser("retrieve")
    for f in ("domain", "bug_class", "component", "severity", "outcome", "finding_id", "text"):
        r.add_argument(f"--{f}")
    r.add_argument("--include-failed", action="store_true"); r.add_argument("--limit", type=int, default=8)
    ns = ap.parse_args(argv)
    store = LearningStore()
    if ns.cmd == "validate":
        errs = validate(json.loads(Path(ns.file).read_text(encoding="utf-8")))
        print("\n".join(errs) if errs else "OK"); return 1 if errs else 0
    if ns.cmd == "add":
        case = store.add(json.loads(Path(ns.file).read_text(encoding="utf-8")))
        print(case["case_id"]); return 0
    kw = {k: getattr(ns, k) for k in ("domain", "bug_class", "component", "severity", "outcome",
                                       "finding_id", "text")}
    for c in store.retrieve(include_failed=ns.include_failed, limit=ns.limit, **kw):
        print(json.dumps(store.compact(c), ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
