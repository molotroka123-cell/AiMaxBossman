"""Hidden holdout for `bossman_v3/self_improvement/runner.py::atomic_json`: a replace racing a reader or another writer must not fail.

    python tools/tree_holdout/atomic_json_replace.py --repo <checkout> [--out result.json]

Found on the owner PC 07.10 (audit): on Windows `os.replace(tmp, path)` raises PermissionError while another handle
(a reader, the dashboard, another writer's replace) has the destination open, so a campaign state/evidence write
died on a race that is normal in a running system. Contract of an atomic JSON write: it either completes with the
new content or (only after bounded effort) reports failure; a short-lived reader or a concurrent writer is NOT a
failure. Valid behaviour must stay as it was (redaction, allow_nan=False, no temp leftovers, parent creation).
The defect cases only fail where the OS refuses to replace an open file (Windows); on POSIX they pass on the base too.
Exit 0 = all passed, 1 = some failed, 2 = the repo could not be loaded.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path


def cases(r, redact_obj) -> list[dict]:
    out: list[dict] = []

    def record(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 - a crash is a failed case, reported as such
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        out.append({"case": name, "ok": bool(ok), "detail": str(detail)[:300]})

    def fresh() -> Path:
        return Path(tempfile.mkdtemp(prefix="holdout-atomic-"))

    def leftovers(d: Path) -> list[str]:
        return sorted(p.name for p in d.iterdir() if p.name.endswith(".tmp"))

    def held_open(seconds: float):
        d = fresh()
        p = d / "state.json"
        r.atomic_json(p, {"v": 0})
        opened, release = threading.Event(), threading.Event()

        def hold():
            with p.open("rb") as stream:
                stream.read()
                opened.set()
                release.wait(seconds)
        t = threading.Thread(target=hold)
        t.start()
        opened.wait(5)
        started = time.monotonic()
        try:
            r.atomic_json(p, {"v": 1, "text": "Привет"})
            err = ""
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        finally:
            release.set()
            t.join(10)
        took = round(time.monotonic() - started, 2)
        got = json.loads(p.read_text(encoding="utf-8"))
        return err, got, took, leftovers(d)

    record("reader holds the destination open 0.4 s -> write completes with the new content",
           lambda: (lambda e, g, t, l: (not e and g.get("v") == 1 and not l, f"err={e!r} content={g} after={t}s tmp={l}"))(*held_open(0.4)))
    record("reader holds the destination open 1.0 s -> write completes with the new content",
           lambda: (lambda e, g, t, l: (not e and g.get("v") == 1 and not l, f"err={e!r} content={g} after={t}s tmp={l}"))(*held_open(1.0)))

    def writers(threads: int, rounds: int, readers: int):
        d = fresh()
        p = d / "results.json"
        errors: list[str] = []

        def write(i: int):
            for n in range(rounds):
                try:
                    r.atomic_json(p, {"writer": i, "n": n})
                except Exception as exc:  # noqa: BLE001
                    errors.append(type(exc).__name__)

        def read():
            for _ in range(rounds * 4):
                try:
                    json.loads(p.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 - the reader's own errors are not the subject
                    pass
        pool = [threading.Thread(target=write, args=(i,)) for i in range(threads)]
        pool += [threading.Thread(target=read) for _ in range(readers)]
        for t in pool:
            t.start()
        for t in pool:
            t.join(120)
        final = json.loads(p.read_text(encoding="utf-8"))
        return errors, final, leftovers(d)

    record("8 concurrent writers x 25 to one path -> no writer fails, file is valid JSON, no tmp left",
           lambda: (lambda e, f, l: (not e and isinstance(f, dict) and "writer" in f and not l,
                                     f"writer_errors={len(e)} {sorted(set(e))} final={f} tmp={l}"))(*writers(8, 25, 0)))
    record("4 writers x 25 with 4 readers on the same path -> no writer fails",
           lambda: (lambda e, f, l: (not e and not l, f"writer_errors={len(e)} {sorted(set(e))} tmp={l}"))(*writers(4, 25, 4)))

    def bounded():
        err, got, took, left = held_open(2.5)
        return (took < 15 and not left and (not err or err.startswith("PermissionError")),
                f"err={err!r} after={took}s tmp={left}")
    record("a reader holding 2.5 s: returns within 15 s (success or a PermissionError), no tmp left", bounded)

    # valid behaviour preserved
    def roundtrip():
        d = fresh()
        p = d / "a" / "b" / "x.json"
        value = {"k": [1, 2.5, None, True], "ru": "Привет"}
        r.atomic_json(p, value)
        text = p.read_text(encoding="utf-8")
        return (json.loads(text) == value and "Привет" in text and "\n  " in text and not leftovers(p.parent),
                f"text={text[:80]!r}")
    record("valid/creates parent dirs, roundtrips unicode, indented, no tmp", roundtrip)

    def overwrite():
        d = fresh()
        p = d / "x.json"
        r.atomic_json(p, {"v": 1})
        r.atomic_json(p, {"v": 2})
        return json.loads(p.read_text(encoding="utf-8")) == {"v": 2} and not leftovers(d), p.read_text(encoding="utf-8")
    record("valid/overwrite replaces the content", overwrite)

    def nan_rejected():
        d = fresh()
        p = d / "x.json"
        r.atomic_json(p, {"v": 1})
        try:
            r.atomic_json(p, {"v": float("nan")})
            return False, "NaN was written"
        except ValueError:
            pass
        return json.loads(p.read_text(encoding="utf-8")) == {"v": 1} and not leftovers(d), "kept the old file"
    record("valid/NaN is refused (ValueError), old file intact, no tmp", nan_rejected)

    def redacted():
        d = fresh()
        p = d / "x.json"
        value = {"note": "ok", "key": "sk-or-v1-" + "a1b2c3d4" * 8, "auth": "Bearer " + "abcdef0123456789" * 3}
        r.atomic_json(p, value)
        return json.loads(p.read_text(encoding="utf-8")) == redact_obj(value), p.read_text(encoding="utf-8")[:200]
    record("valid/content equals redact_obj(value)", redacted)

    def unserialisable_cleans_up():
        d = fresh()
        p = d / "x.json"
        try:
            r.atomic_json(p, {"v": object()})
        except Exception:  # noqa: BLE001
            return not p.exists() and not leftovers(d), f"files={sorted(x.name for x in d.iterdir())}"
        return False, "object() was written"
    record("valid/unserialisable value fails cleanly, no tmp, no file", unserialisable_cleans_up)
    return out


def run(repo: Path) -> dict:
    for sub in ("bossman-core", "."):
        sys.path.insert(0, str((repo / sub).resolve()))
    import importlib
    mod = importlib.import_module("bossman_v3.self_improvement.runner")
    if not str(Path(mod.__file__).resolve()).startswith(str(repo.resolve())):
        raise RuntimeError(f"imported {mod.__file__}, not the checkout {repo}")
    rows = cases(mod, mod.redact_obj)
    failed = [x for x in rows if not x["ok"]]
    return {"holdout": "atomic_json_replace/1", "module": str(mod.__file__), "total": len(rows),
            "failed": len(failed), "passed": not failed, "failures": failed, "cases": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    try:
        result = run(args.repo)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"holdout": "atomic_json_replace/1", "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"HOLDOUT atomic_json_replace: {result['total'] - result['failed']}/{result['total']} passed")
    for x in result["failures"][:12]:
        print(f"  FAIL {x['case']}: {x['detail']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
