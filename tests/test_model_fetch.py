"""tools/model_fetch.py + tools/model_profiles.json.

Real downloads against a local http.server (Range and no-Range variants, redirects, dropped
connections). Every strictness rule has a pair: the legitimate case passes and the bad one is rejected.
"""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("model_fetch", ROOT / "tools/model_fetch.py")
mf = importlib.util.module_from_spec(spec)
sys.modules["model_fetch"] = mf
spec.loader.exec_module(mf)

REPO_MANIFEST = ROOT / "tools/model_profiles.json"
STUDIO = ROOT / "tools/studio_models.json"
PAYLOAD = bytes(range(256)) * 400  # 102400 bytes
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
OTHER = bytes(reversed(PAYLOAD))  # same size, different hash
DEAD_URL = "http://127.0.0.1:9/never"  # nothing listens: any network attempt fails


# ----------------------------------------------------------------------------- fixture server

class Server:
    """Serves self.files[path] = bytes.  Knobs: range_support, stop_after (path -> (n, stop_file)),
    drop_after (path -> n: close the socket after n bytes), redirects, json routes."""

    def __init__(self, range_support=True):
        self.files: dict[str, bytes] = {}
        self.json: dict[str, object] = {}
        self.headers_json: dict[str, dict] = {}
        self.redirects: dict[str, str] = {}
        self.range_support = range_support
        self.stop_after: dict[str, tuple[int, Path]] = {}
        self.drop_after: dict[str, int] = {}
        self.requests: list[tuple[str, str | None]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path
                outer.requests.append((path, self.headers.get("Range")))
                if path in outer.redirects:
                    self.send_response(302)
                    self.send_header("Location", outer.redirects[path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path in outer.json:
                    body = json.dumps(outer.json[path]).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    for k, v in outer.headers_json.get(path, {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(body)
                    return
                data = outer.files.get(path)
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                rng = self.headers.get("Range")
                if rng and outer.range_support:
                    start = int(rng.split("=")[1].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if path in outer.drop_after:
                    self.wfile.write(body[:outer.drop_after[path]])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                if path in outer.stop_after:
                    n, stop_file = outer.stop_after[path]
                    self.wfile.write(body[:n])
                    self.wfile.flush()
                    stop_file.write_text("stop")
                    time.sleep(0.4)
                    try:
                        self.wfile.write(body[n:])
                    except OSError:
                        pass
                    return
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def file_requests(self):
        return [r for r in self.requests if r[0].startswith("/f/")]


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


@pytest.fixture
def server_norange():
    s = Server(range_support=False)
    yield s
    s.close()


# ----------------------------------------------------------------------------- manifest helpers

RUNTIME = {"engine": "llama.cpp", "version": "b10964", "version_match": "10964", "status": "UNPINNED",
           "status_reason": "test", "compatibility_notes": "test", "license": {"id": "MIT"},
           "version_regex": r"version:\s*b?(\d+)", "binary_candidates": []}


def pinned_file(fid, url, *, name=None, data=PAYLOAD, dest="m"):
    return {"id": fid, "dest_dir": dest, "name": name or f"{fid}.gguf", "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "status": "PINNED", "url": url}


def unpinned_file(fid, *, name=None, glob=None, prefix=None, dest="m", url=None):
    spec = {"id": fid, "dest_dir": dest, "name": name, "size_bytes": None, "sha256": None, "status": "UNPINNED",
            "unpinned_reason": "test: not pinned"}
    if glob:
        spec["name_glob"] = glob
    if prefix:
        spec["sha256_prefix"] = prefix
    if url:
        spec["url"] = url
    return spec


def profile(pid, files, *, optional=False, status=None, min_free=0, source=None):
    statuses = {f["status"] for f in files}
    status = status or ("PINNED" if statuses == {"PINNED"} else "UNPINNED")
    prof = {"id": pid, "kind": "model", "category": "llm_coding_agents", "role": "test", "runtime": {"ref": "rt"},
            "source": source or {"hf_repo": None, "revision": None}, "files": files,
            "license": {"id": "MIT", "url": "https://example.invalid", "acceptance_required": False},
            "min_free_disk": {"bytes": min_free if status == "PINNED" else None, "basis": "test"},
            "optional": optional, "status": status}
    if status != "PINNED":
        prof["status_reason"] = "test"
    return prof


def manifest(*profiles):
    return {"schema_version": 1, "kind": "bossman.model_profiles", "runtimes": {"rt": dict(RUNTIME)},
            "profiles": list(profiles)}


def run(tmp_path, data, action, *extra, models=None):
    mpath = tmp_path / f"manifest-{time.monotonic_ns()}.json"
    mpath.write_text(json.dumps(data))
    out = tmp_path / f"report-{time.monotonic_ns()}.json"
    models = models or tmp_path / "models"
    argv = [action, "--manifest", str(mpath), "--json", "--report", str(out), "--studio-models", str(STUDIO)]
    if action not in ("validate", "pin", "runtime-check"):
        argv += ["--models-dir", str(models), "--margin-bytes", "0", "--chunk-bytes", "1024"]
    code = mf.main(argv + list(extra))
    report = json.loads(out.read_text()) if out.exists() else None
    return code, report


def states(report, pid):
    prof = next(p for p in report["profiles"] if p["id"] == pid)
    return [f["state"] for f in prof["files"]]


def place(models: Path, rel: str, data: bytes) -> Path:
    p = models / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ----------------------------------------------------------------------------- the repository manifest

def test_repo_manifest_is_valid_with_studio_contract():
    catalog = json.loads(STUDIO.read_text())
    data = mf.load_manifest(REPO_MANIFEST, catalog)
    for prof in data["profiles"]:
        for field in mf.REQUIRED_PROFILE_FIELDS:
            assert field in prof, (prof["id"], field)
        assert prof["category"] in mf.CATEGORIES
        assert {"id", "url", "acceptance_required"} <= set(prof["license"])
        assert prof["runtime"]["ref"] in data["runtimes"]
    ids = {p["id"] for p in data["profiles"]}
    assert {"baseline-installed", "snowllm-0.3.2", "qwen3.6-35b-a3b-fp8", "qwen3.8-27b-q4_k_xl",
            "qwen3.8-flash-next-q3_k_xl", "glm-5.3-flash-strix-balanced", "tools-structured-outputs",
            "studio-photo", "studio-video", "qwen3.8-27b-ud-q4_k_m-installed"} <= ids
    assert {p["category"] for p in data["profiles"]} == set(mf.CATEGORIES)


def test_repo_manifest_never_carries_an_unsourced_hash():
    """Nothing could be read from HF in the cloud session: every file must be UNPINNED with sha256 null."""
    data = json.loads(REPO_MANIFEST.read_text())
    for prof in data["profiles"]:
        for f in prof["files"]:
            if f["status"] == "UNPINNED":
                assert f["sha256"] is None and f["unpinned_reason"], f["id"]
            else:
                assert mf.HEX64.match(f["sha256"]) and f.get("pinned_via"), f["id"]


def test_repo_manifest_baseline_semantics():
    data = json.loads(REPO_MANIFEST.read_text())
    by_id = {p["id"]: p for p in data["profiles"]}
    base = by_id["baseline-installed"]
    assert base["reuse_only"] is True and base["optional"] is False
    names = {f["name"] for f in base["files"]}
    assert {"Qwen3.8-27B-UD-Q5_K_M.gguf", "Qwen3.6-35B-A3B-UD-Q5_K_M.gguf",
            "openai_gpt-oss-120b-MXFP4_MOE-00001-of-00002.gguf",
            "openai_gpt-oss-120b-MXFP4_MOE-00002-of-00002.gguf"} <= names
    assert base["lab_status"]["gpt-oss-120b"] == "PRELIMINARY_LAB_PRIMARY"
    assert data["runtimes"][base["runtime"]["ref"]]["version"] == "b10964"
    q4m = by_id["qwen3.8-27b-ud-q4_k_m-installed"]
    assert q4m["files"][0]["sha256_prefix"] == "322e194f" and q4m["reuse_only"] is True
    assert by_id["qwen3.8-flash-next-q3_k_xl"]["runtime"]["ref"] == "llama.cpp-qwen4exp-fork"
    assert by_id["qwen3.8-flash-next-q3_k_xl"]["runtime"]["requires_fork"] is True
    for pid in ("snowllm-0.3.2", "glm-5.3-flash-strix-balanced", "qwen3.6-35b-a3b-fp8", "qwen3.8-27b-q4_k_xl"):
        assert by_id[pid]["optional"] is True, pid
    assert data["runtimes"]["snowllm-0.3.2"]["kernel_conditions"]


def test_installed_q4_k_m_and_q4_k_xl_are_distinct_entries_with_distinct_files():
    data = json.loads(REPO_MANIFEST.read_text())
    by_id = {p["id"]: p for p in data["profiles"]}
    m = by_id["qwen3.8-27b-ud-q4_k_m-installed"]["files"]
    xl = by_id["qwen3.8-27b-q4_k_xl"]["files"]
    assert {f["id"] for f in m}.isdisjoint({f["id"] for f in xl})
    m_glob, xl_glob = m[0]["name_glob"], xl[0]["name_glob"]
    assert "Q4_K_M" in m_glob and "Q4_K_XL" in xl_glob
    # a real file of one quant never matches the other's pattern
    assert not fnmatch.fnmatchcase("Qwen3.8-27B-UD-Q4_K_M.gguf", xl_glob)
    assert not fnmatch.fnmatchcase("Qwen3.8-27B-UD-Q4_K_XL.gguf", m_glob)
    assert fnmatch.fnmatchcase("Qwen3.8-27B-UD-Q4_K_M.gguf", m_glob)
    assert fnmatch.fnmatchcase("Qwen3.8-27B-UD-Q4_K_XL.gguf", xl_glob)


def test_mixing_q4_entries_is_rejected():
    """Negative control: if XL's pattern also matched the M file, the validator refuses the manifest."""
    data = json.loads(REPO_MANIFEST.read_text())
    bad = copy.deepcopy(data)
    xl = next(p for p in bad["profiles"] if p["id"] == "qwen3.8-27b-q4_k_xl")
    xl["files"][0]["name_glob"] = "Qwen3.8-27B-UD-Q4_K_*.gguf"
    with pytest.raises(ValueError, match="overlaps"):
        mf.validate_manifest(bad)
    bad2 = copy.deepcopy(data)
    xl = next(p for p in bad2["profiles"] if p["id"] == "qwen3.8-27b-q4_k_xl")
    xl["files"][0]["id"] = "qwen3.8-27b-ud-q4_k_m"
    with pytest.raises(ValueError, match="duplicate file id"):
        mf.validate_manifest(bad2)
    mf.validate_manifest(data)  # legit passes


@pytest.mark.parametrize("field", mf.REQUIRED_PROFILE_FIELDS)
def test_every_required_profile_field_is_enforced(field):
    data = json.loads(REPO_MANIFEST.read_text())
    mf.validate_manifest(copy.deepcopy(data))
    del data["profiles"][0][field]
    with pytest.raises(ValueError, match=field):
        mf.validate_manifest(data)


@pytest.mark.parametrize("mutate, message", [
    (lambda f: f.update(sha256="a" * 64), "sha256 null"),                     # UNPINNED carrying a hash
    (lambda f: f.update(status="PINNED"), "PINNED needs"),                     # PINNED without hash/size
    (lambda f: f.update(sha256_prefix="XYZ"), "sha256_prefix"),
    (lambda f: f.update(dest_dir="../escape"), "escapes"),
    (lambda f: f.update(dest_dir="C:/models"), "relative|escapes"),
])
def test_file_rules_negative(mutate, message):
    data = json.loads(REPO_MANIFEST.read_text())
    bad = copy.deepcopy(data)
    mutate(bad["profiles"][0]["files"][0])
    with pytest.raises(ValueError, match=message):
        mf.validate_manifest(bad)


def test_pinned_hash_must_agree_with_observed_prefix():
    ok = pinned_file("a", DEAD_URL)
    ok["sha256_prefix"] = PAYLOAD_SHA[:8]
    mf.validate_manifest(manifest(profile("p", [ok])))
    bad = dict(ok, sha256_prefix="00000000" if not PAYLOAD_SHA.startswith("00000000") else "11111111")
    with pytest.raises(ValueError, match="contradicts"):
        mf.validate_manifest(manifest(profile("p", [bad])))


def test_studio_references_are_validated_against_the_catalog():
    catalog = json.loads(STUDIO.read_text())
    data = json.loads(REPO_MANIFEST.read_text())
    mf.validate_manifest(copy.deepcopy(data), catalog)
    unknown = copy.deepcopy(data)
    next(p for p in unknown["profiles"] if p["id"] == "studio-photo")["studio_refs"].append("sdcpp:nope")
    with pytest.raises(ValueError, match="not in studio catalog"):
        mf.validate_manifest(unknown, catalog)
    wrong_surface = copy.deepcopy(data)
    next(p for p in wrong_surface["profiles"] if p["id"] == "studio-photo")["studio_refs"] = ["sdcpp:wan2.2-ti2v-5b"]
    with pytest.raises(ValueError, match="surface"):
        mf.validate_manifest(wrong_surface, catalog)
    dangling = copy.deepcopy(data)
    next(p for p in dangling["profiles"] if p["id"] == "tools-structured-outputs")["model_refs"].append("ghost")
    with pytest.raises(ValueError, match="model_refs"):
        mf.validate_manifest(dangling)


def test_repo_manifest_fetch_refuses_unpinned_baseline_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "http://127.0.0.1:9")
    code, report = run(tmp_path, json.loads(REPO_MANIFEST.read_text()), "fetch", "--profile", "baseline-installed")
    assert code == mf.EXIT_REFUSED
    assert set(states(report, "baseline-installed")) == {mf.UNPINNED_REFUSED}


# ----------------------------------------------------------------------------- reuse + hash cache

def test_reuse_present_file_without_network(tmp_path):
    data = manifest(profile("base", [pinned_file("a", DEAD_URL)]))
    place(tmp_path / "models", "m/a.gguf", PAYLOAD)
    code, report = run(tmp_path, data, "fetch")
    assert code == 0 and states(report, "base") == [mf.REUSED]


def test_reuse_dir_hardlinks_matching_file_and_rejects_impostor(tmp_path):
    data = manifest(profile("base", [pinned_file("a", DEAD_URL)]))
    other = tmp_path / "old-models"
    place(other, "somewhere/a.gguf", PAYLOAD)
    code, report = run(tmp_path, data, "fetch", "--reuse-dir", str(other))
    assert code == 0 and states(report, "base") == [mf.REUSED]
    row = report["profiles"][0]["files"][0]
    assert row["mode"] in ("hardlink", "reference")
    assert (tmp_path / "models/m/a.gguf").read_bytes() == PAYLOAD
    # negative control: same name and size, different content -> not reused, download attempted (fails: no network)
    models2 = tmp_path / "models2"
    impostor = tmp_path / "impostor"
    place(impostor, "a.gguf", OTHER)
    code, report = run(tmp_path, data, "fetch", "--reuse-dir", str(impostor), models=models2)
    assert code == mf.EXIT_FAILED
    row = report["profiles"][0]["files"][0]
    assert row["state"] == mf.FAILED_NETWORK and row["reuse_rejected"]["state"] == mf.FAILED_HASH
    assert not (models2 / "m/a.gguf").exists()


def test_hash_cache_skips_rehash_but_notices_changes(tmp_path):
    data = manifest(profile("base", [pinned_file("a", DEAD_URL)]))
    path = place(tmp_path / "models", "m/a.gguf", PAYLOAD)
    code, first = run(tmp_path, data, "verify")
    assert code == 0 and first["hashed_now"] == [str(path.resolve())]
    code, second = run(tmp_path, data, "verify")
    assert code == 0 and second["hashed_now"] == [] and states(second, "base") == [mf.VERIFIED]
    path.write_bytes(OTHER)  # same size, new content, new mtime
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    code, third = run(tmp_path, data, "verify")
    assert code == mf.EXIT_FAILED and states(third, "base") == [mf.FAILED_HASH] and third["hashed_now"]


# ----------------------------------------------------------------------------- download / resume / cancel

def test_download_verified_then_hash_mismatch_is_not_promoted(tmp_path, server):
    server.files["/f/good"] = PAYLOAD
    server.files["/f/bad"] = OTHER
    good = manifest(profile("base", [pinned_file("a", server.url + "/f/good")]))
    code, report = run(tmp_path, good, "fetch")
    assert code == 0 and states(report, "base") == [mf.DOWNLOADED]
    assert (tmp_path / "models/m/a.gguf").read_bytes() == PAYLOAD
    bad = manifest(profile("base", [pinned_file("b", server.url + "/f/bad", data=PAYLOAD)]))
    code, report = run(tmp_path, bad, "fetch")
    assert code == mf.EXIT_FAILED and states(report, "base") == [mf.FAILED_HASH]
    assert not (tmp_path / "models/m/b.gguf").exists()
    assert not (tmp_path / "models/m/b.gguf.part").exists()  # a bad part is never resumed from


def test_cancel_leaves_resumable_part_then_resume_uses_range(tmp_path, server):
    server.files["/f/a"] = PAYLOAD
    stop = tmp_path / "models" / mf.STOP_NAME
    (tmp_path / "models").mkdir()
    server.stop_after["/f/a"] = (30_000, stop)
    data = manifest(profile("base", [pinned_file("a", server.url + "/f/a")]))
    code, report = run(tmp_path, data, "fetch")
    assert code == mf.EXIT_CANCELLED and report["verdict"] == mf.CANCELLED
    part = tmp_path / "models/m/a.gguf.part"
    assert part.is_file() and 0 < part.stat().st_size < len(PAYLOAD)
    assert not (tmp_path / "models/m/a.gguf").exists()
    # a STOP file still present refuses to start
    code, report = run(tmp_path, data, "fetch")
    assert code == mf.EXIT_REFUSED and "STOP" in report["error"]
    stop.unlink()
    del server.stop_after["/f/a"]
    have = part.stat().st_size
    code, report = run(tmp_path, data, "fetch")
    assert code == 0 and states(report, "base") == [mf.RESUMED]
    assert server.file_requests()[-1] == ("/f/a", f"bytes={have}-")
    assert (tmp_path / "models/m/a.gguf").read_bytes() == PAYLOAD
    assert not part.exists()


def test_dropped_connection_keeps_part_and_resumes_through_redirect(tmp_path, server):
    server.files["/f/a"] = PAYLOAD
    server.redirects["/r/a"] = "/f/a"
    server.drop_after["/f/a"] = 40_000
    data = manifest(profile("base", [pinned_file("a", server.url + "/r/a")]))
    code, report = run(tmp_path, data, "fetch")
    assert code == mf.EXIT_FAILED and states(report, "base") == [mf.FAILED_NETWORK]
    part = tmp_path / "models/m/a.gguf.part"
    assert part.stat().st_size == 40_000
    del server.drop_after["/f/a"]
    code, report = run(tmp_path, data, "fetch")
    assert code == 0 and states(report, "base") == [mf.RESUMED]
    assert server.file_requests()[-1] == ("/f/a", "bytes=40000-")  # Range survived the redirect


def test_server_without_range_restarts_from_zero(tmp_path, server_norange):
    server_norange.files["/f/a"] = PAYLOAD
    place(tmp_path / "models", "m/a.gguf.part", PAYLOAD[:50_000])
    data = manifest(profile("base", [pinned_file("a", server_norange.url + "/f/a")]))
    # the part sidecar must match, else the part is discarded before asking for a Range
    meta = {"url_file": "a.gguf", "sha256": PAYLOAD_SHA, "size_bytes": len(PAYLOAD)}
    (tmp_path / "models/m/a.gguf.part.json").write_text(json.dumps(meta))
    code, report = run(tmp_path, data, "fetch")
    row = report["profiles"][0]["files"][0]
    assert code == 0 and row["state"] == mf.DOWNLOADED and row["range_supported"] is False
    assert server_norange.file_requests()[-1] == ("/f/a", "bytes=50000-")
    assert (tmp_path / "models/m/a.gguf").read_bytes() == PAYLOAD


def test_part_of_another_version_is_not_resumed(tmp_path, server):
    server.files["/f/a"] = PAYLOAD
    place(tmp_path / "models", "m/a.gguf.part", OTHER[:50_000])
    (tmp_path / "models/m/a.gguf.part.json").write_text(json.dumps({"url_file": "a.gguf", "sha256": "0" * 64,
                                                                     "size_bytes": len(PAYLOAD)}))
    data = manifest(profile("base", [pinned_file("a", server.url + "/f/a")]))
    code, report = run(tmp_path, data, "fetch")
    assert code == 0 and states(report, "base") == [mf.DOWNLOADED]
    assert server.file_requests()[-1] == ("/f/a", None)


# ----------------------------------------------------------------------------- disk check

def test_disk_insufficient_refuses_before_any_request(tmp_path, server, monkeypatch):
    server.files["/f/a"] = PAYLOAD
    data = manifest(profile("base", [pinned_file("a", server.url + "/f/a")]))
    monkeypatch.setattr(mf, "free_bytes", lambda p: len(PAYLOAD) - 1)
    code, report = run(tmp_path, data, "fetch")
    assert code == mf.EXIT_REFUSED and report["error"] == mf.DISK_INSUFFICIENT
    assert server.file_requests() == []
    code, plan = run(tmp_path, data, "plan")
    assert plan["fits"] is False and plan["bytes_to_download"] == len(PAYLOAD)
    monkeypatch.setattr(mf, "free_bytes", lambda p: len(PAYLOAD))  # legit: exactly enough (margin 0)
    code, report = run(tmp_path, data, "fetch")
    assert code == 0 and states(report, "base") == [mf.DOWNLOADED]


def test_disk_counts_partial_bytes_and_min_free_floor(tmp_path, server, monkeypatch):
    server.files["/f/a"] = PAYLOAD
    prof = profile("base", [pinned_file("a", server.url + "/f/a")], min_free=len(PAYLOAD) * 10)
    data = manifest(prof)
    monkeypatch.setattr(mf, "free_bytes", lambda p: len(PAYLOAD) * 2)
    code, report = run(tmp_path, data, "fetch")
    assert code == mf.EXIT_REFUSED  # profile floor (min_free_disk) is above the free space
    prof["min_free_disk"]["bytes"] = 0
    place(tmp_path / "models", "m/a.gguf.part", PAYLOAD[:60_000])
    (tmp_path / "models/m/a.gguf.part.json").write_text(json.dumps(
        {"url_file": "a.gguf", "sha256": PAYLOAD_SHA, "size_bytes": len(PAYLOAD)}))
    code, plan = run(tmp_path, data, "plan")
    assert plan["bytes_to_download"] == len(PAYLOAD) - 60_000
    assert states(plan, "base") == ["PARTIAL"]


def test_optional_that_does_not_fit_is_skipped_required_continues(tmp_path, server, monkeypatch):
    server.files["/f/a"] = PAYLOAD
    server.files["/f/big"] = PAYLOAD * 3
    data = manifest(profile("base", [pinned_file("a", server.url + "/f/a")]),
                    profile("heavy", [pinned_file("big", server.url + "/f/big", data=PAYLOAD * 3)], optional=True))
    monkeypatch.setattr(mf, "free_bytes", lambda p: len(PAYLOAD) * 2)
    code, report = run(tmp_path, data, "fetch", "--profile", "base", "--profile", "heavy")
    assert code == 0
    assert states(report, "base") == [mf.DOWNLOADED]
    heavy = next(p for p in report["profiles"] if p["id"] == "heavy")
    assert heavy["files"][0]["state"] == mf.SKIPPED_OPTIONAL_FAILURE
    assert heavy["files"][0]["cause"] == mf.DISK_INSUFFICIENT


# ----------------------------------------------------------------------------- unpinned

def test_unpinned_refused_without_network_then_tofu_with_explicit_flag(tmp_path, server):
    server.files["/f/u"] = PAYLOAD
    data = manifest(profile("base", [unpinned_file("u", name="u.gguf", url=server.url + "/f/u")]))
    code, report = run(tmp_path, data, "fetch")
    assert code == mf.EXIT_REFUSED and states(report, "base") == [mf.UNPINNED_REFUSED]
    assert server.file_requests() == []
    code, report = run(tmp_path, data, "fetch", "--allow-unpinned")
    assert code == mf.EXIT_UNVERIFIED  # never reported as verified
    row = report["profiles"][0]["files"][0]
    assert row["state"] == mf.UNVERIFIED_TOFU and row["sha256_observed"] == PAYLOAD_SHA
    code, report = run(tmp_path, data, "verify")
    assert code == mf.EXIT_UNVERIFIED and states(report, "base") == [mf.UNVERIFIED_TOFU]


def test_unpinned_glob_without_name_is_refused_even_with_flag(tmp_path):
    data = manifest(profile("base", [unpinned_file("u", glob="X-Q4_K_XL*.gguf")]))
    code, report = run(tmp_path, data, "fetch", "--allow-unpinned")
    assert code == mf.EXIT_REFUSED
    assert "pin" in report["profiles"][0]["files"][0]["detail"]


def test_installed_unpinned_file_prefix_and_tofu_lock(tmp_path):
    good_prefix = PAYLOAD_SHA[:8]
    data = manifest(profile("base", [unpinned_file("q4m", glob="Q-UD-Q4_K_M*.gguf", prefix=good_prefix)]))
    path = place(tmp_path / "models", "m/Q-UD-Q4_K_M.gguf", PAYLOAD)
    code, report = run(tmp_path, data, "verify")
    row = report["profiles"][0]["files"][0]
    assert code == mf.EXIT_UNVERIFIED and row["state"] == mf.UNVERIFIED_TOFU and row["sha256_prefix_match"] is True
    # TOFU lock: the file changes afterwards -> FAILED_HASH (prefix check disabled to isolate the lock)
    data2 = manifest(profile("base", [unpinned_file("q4m", glob="Q-UD-Q4_K_M*.gguf")]))
    code, _ = run(tmp_path, data2, "verify")
    assert code == mf.EXIT_UNVERIFIED
    path.write_bytes(OTHER)
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    code, report = run(tmp_path, data2, "verify")
    assert code == mf.EXIT_FAILED and states(report, "base") == [mf.FAILED_HASH]
    # observed prefix contradicting the recorded one -> FAILED_HASH
    bad = manifest(profile("base", [unpinned_file("q4m", glob="Q-UD-Q4_K_M*.gguf", prefix="deadbeef")]))
    place(tmp_path / "fresh", "m/Q-UD-Q4_K_M.gguf", PAYLOAD)
    code, report = run(tmp_path, bad, "verify", models=tmp_path / "fresh")
    assert code == mf.EXIT_FAILED and states(report, "base") == [mf.FAILED_HASH]


def test_q4_k_m_file_is_never_taken_for_q4_k_xl(tmp_path):
    data = json.loads(REPO_MANIFEST.read_text())
    models = tmp_path / "models"
    place(models, "qwen38-27b/Qwen3.8-27B-UD-Q4_K_M.gguf", PAYLOAD)
    code, report = run(tmp_path, data, "verify", "--profile", "qwen3.8-27b-q4_k_xl",
                       "--profile", "qwen3.8-27b-ud-q4_k_m-installed")
    xl = next(p for p in report["profiles"] if p["id"] == "qwen3.8-27b-q4_k_xl")["files"][0]
    m = next(p for p in report["profiles"] if p["id"] == "qwen3.8-27b-ud-q4_k_m-installed")["files"][0]
    assert xl["state"] == mf.SKIPPED_OPTIONAL_FAILURE and xl["cause"] == mf.MISSING
    # the fixture bytes do not start with 322e194f -> the installed entry flags it (optional -> skipped, not fatal)
    assert m["state"] == mf.SKIPPED_OPTIONAL_FAILURE and m["cause"] == mf.FAILED_HASH
    assert code == 0  # both are optional; no required profile was selected


# ----------------------------------------------------------------------------- optional vs required

def test_optional_failure_does_not_fail_baseline(tmp_path, server):
    server.files["/f/bad"] = OTHER
    data = manifest(profile("base", [pinned_file("a", DEAD_URL)]),
                    profile("extra", [pinned_file("x", server.url + "/f/bad")], optional=True))
    place(tmp_path / "models", "m/a.gguf", PAYLOAD)
    code, report = run(tmp_path, data, "fetch", "--profile", "base", "--profile", "extra")
    assert code == 0 and report["verdict"] == "OK"
    extra = next(p for p in report["profiles"] if p["id"] == "extra")
    assert extra["files"][0] == {**extra["files"][0], "state": mf.SKIPPED_OPTIONAL_FAILURE, "cause": mf.FAILED_HASH}
    # negative control: the same failure in a required profile fails the run
    data["profiles"][1]["optional"] = False
    code, report = run(tmp_path, data, "fetch", "--profile", "base", "--profile", "extra")
    assert code == mf.EXIT_FAILED and states(report, "extra") == [mf.FAILED_HASH]


def test_default_selection_is_required_profiles_only(tmp_path):
    data = manifest(profile("base", [pinned_file("a", DEAD_URL)]),
                    profile("extra", [pinned_file("x", DEAD_URL)], optional=True))
    place(tmp_path / "models", "m/a.gguf", PAYLOAD)
    code, report = run(tmp_path, data, "verify")
    assert code == 0 and [p["id"] for p in report["profiles"]] == ["base"]
    code, report = run(tmp_path, data, "verify", "--profile", "nope")
    assert code == mf.EXIT_INVALID


# ----------------------------------------------------------------------------- pin via a fake HF API

COMMIT = "a" * 40


def hf_routes(server, repo, files):
    server.json[f"/api/models/{repo}/revision/main"] = {"sha": COMMIT, "cardData": {"license": "apache-2.0"}}
    tree = [{"type": "file", "path": p, "size": len(b), "lfs": {"oid": hashlib.sha256(b).hexdigest(), "size": len(b)}}
            for p, b in files.items()]
    half = len(tree) // 2 or 1
    server.json[f"/api/models/{repo}/tree/{COMMIT}?recursive=1"] = tree[:half]
    server.headers_json[f"/api/models/{repo}/tree/{COMMIT}?recursive=1"] = {
        "Link": f'</api/models/{repo}/tree/{COMMIT}?recursive=1&cursor=2>; rel="next"'}
    server.json[f"/api/models/{repo}/tree/{COMMIT}?recursive=1&cursor=2"] = tree[half:]
    for p, b in files.items():
        server.files[f"/f/{repo}/resolve/{COMMIT}/{p}"] = b


def test_pin_then_fetch_verified(tmp_path, server, monkeypatch):
    repo = "org/Model-GGUF"
    files = {"README.md.txt": b"x", "sub/Model-UD-Q4_K_XL-00001-of-00002.gguf": PAYLOAD,
             "sub/Model-UD-Q4_K_XL-00002-of-00002.gguf": OTHER, "Model-UD-Q4_K_M.gguf": b"m" * 10}
    hf_routes(server, repo, files)
    data = manifest(profile("xl", [unpinned_file("xl", glob="Model-UD-Q4_K_XL*.gguf")], optional=True,
                            source={"hf_repo": repo, "revision": None}))
    out = tmp_path / "pinned.json"
    code, _ = run(tmp_path, data, "pin", "--out", str(out), "--hf-endpoint", server.url, "--profile", "xl")
    assert code == 0
    pinned = mf.validate_manifest(json.loads(out.read_text()))
    prof = pinned["profiles"][0]
    assert prof["status"] == "PINNED" and prof["source"]["revision"] == COMMIT
    assert [f["name"] for f in prof["files"]] == ["Model-UD-Q4_K_XL-00001-of-00002.gguf",
                                                  "Model-UD-Q4_K_XL-00002-of-00002.gguf"]
    assert prof["files"][0]["sha256"] == PAYLOAD_SHA and prof["license"]["id_from_hf_card"] == "apache-2.0"
    monkeypatch.setenv("HF_ENDPOINT", server.url + "/f")
    code, report = run(tmp_path, pinned, "fetch", "--profile", "xl")
    assert code == 0 and states(report, "xl") == [mf.DOWNLOADED, mf.DOWNLOADED]
    assert (tmp_path / "models/m/Model-UD-Q4_K_XL-00002-of-00002.gguf").read_bytes() == OTHER


def test_pin_refuses_hash_contradicting_observed_prefix(tmp_path, server):
    repo = "org/Q"
    hf_routes(server, repo, {"Q-UD-Q4_K_M.gguf": PAYLOAD})
    wrong = "deadbeef" if not PAYLOAD_SHA.startswith("deadbeef") else "cafebabe"
    data = manifest(profile("m", [unpinned_file("m", glob="Q-UD-Q4_K_M*.gguf", prefix=wrong)],
                            source={"hf_repo": repo, "revision": None}))
    out = tmp_path / "pinned.json"
    code, report = run(tmp_path, data, "pin", "--out", str(out), "--hf-endpoint", server.url)
    assert code == mf.EXIT_FAILED and report["report"][0]["state"] == "PIN_CONFLICT"
    assert json.loads(out.read_text())["profiles"][0]["files"][0]["status"] == "UNPINNED"
    # legit control: the right prefix pins
    data["profiles"][0]["files"][0]["sha256_prefix"] = PAYLOAD_SHA[:8]
    code, report = run(tmp_path, data, "pin", "--out", str(out), "--hf-endpoint", server.url)
    assert code == 0 and json.loads(out.read_text())["profiles"][0]["files"][0]["sha256"] == PAYLOAD_SHA


def test_pin_unreachable_endpoint_reports_failure(tmp_path):
    data = manifest(profile("m", [unpinned_file("m", glob="Q*.gguf")], source={"hf_repo": "o/r", "revision": None}))
    code, report = run(tmp_path, data, "pin", "--out", str(tmp_path / "p.json"), "--hf-endpoint", "http://127.0.0.1:9")
    assert code == mf.EXIT_FAILED and report["report"][0]["state"] == "PIN_FAILED"


# ----------------------------------------------------------------------------- runtime-check

def fake_binary(tmp_path, text):
    script = tmp_path / f"llama-server-{time.monotonic_ns()}"
    script.write_text(f"#!{sys.executable}\nimport sys\nprint({text!r}, file=sys.stderr)\n")
    script.chmod(0o755)
    return script


@pytest.mark.skipif(os.name == "nt", reason="shebang fake binary")
def test_runtime_check_version_match_and_mismatch(tmp_path):
    data = manifest(profile("base", [pinned_file("a", DEAD_URL)]))
    ok = fake_binary(tmp_path, "version: 10964 (0123abcd)\nbuilt with clang")
    code, report = run(tmp_path, data, "runtime-check", "--runtime-bin", str(ok))
    assert code == 0 and report["runtimes"][0]["state"] == "RUNTIME_OK"
    old = fake_binary(tmp_path, "version: 10500 (0123abcd)")
    code, report = run(tmp_path, data, "runtime-check", "--runtime-bin", str(old))
    assert code == mf.EXIT_FAILED and report["runtimes"][0]["state"] == "RUNTIME_VERSION_MISMATCH"
    code, report = run(tmp_path, data, "runtime-check", "--runtime-bin", str(tmp_path / "absent"))
    assert code == mf.EXIT_FAILED and report["runtimes"][0]["state"] == "RUNTIME_MISSING"


def test_runtime_check_unpinned_runtime_is_reported_not_installed(tmp_path):
    data = json.loads(REPO_MANIFEST.read_text())
    code, report = run(tmp_path, data, "runtime-check", "--profile", "snowllm-0.3.2")
    assert code == 0 and report["runtimes"][0]["state"] == "RUNTIME_UNPINNED"


# ----------------------------------------------------------------------------- standalone (bundle layout)

def test_runs_standalone_from_app_support_under_isolated_python(tmp_path):
    support = tmp_path / "app-support"
    support.mkdir()
    shutil.copy(ROOT / "tools/model_fetch.py", support / "model_fetch.py")
    shutil.copy(REPO_MANIFEST, support / "model_profiles.json")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "BOSSMAN_MODELS_DIR")}
    env["LOCALAPPDATA"] = str(tmp_path / "lad")
    proc = subprocess.run([sys.executable, "-I", str(support / "model_fetch.py"), "plan", "--json"],
                          capture_output=True, text=True, cwd=tmp_path, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    plan = json.loads(proc.stdout)
    assert plan["models_dir"] == str(tmp_path / "lad" / "Bossman" / "models")
    assert {p["id"] for p in plan["profiles"]} == {"baseline-installed", "tools-structured-outputs"}
    proc = subprocess.run([sys.executable, "-I", str(support / "model_fetch.py"), "validate", "--json"],
                          capture_output=True, text=True, cwd=tmp_path, env=env, timeout=60)
    assert proc.returncode == 0 and json.loads(proc.stdout)["studio_catalog_checked"] is False


def test_ctrl_c_mid_download_leaves_resumable_part(tmp_path, server, monkeypatch):
    server.files["/f/a"] = PAYLOAD
    data = manifest(profile("base", [pinned_file("a", server.url + "/f/a")]))
    calls = {"n": 0}

    def interrupt_on_fifth(self):
        calls["n"] += 1
        if calls["n"] == 5:
            raise KeyboardInterrupt
        return False
    monkeypatch.setattr(mf.Runner, "stop_requested", interrupt_on_fifth)
    code, report = run(tmp_path, data, "fetch")
    assert code == mf.EXIT_CANCELLED and states(report, "base") == [mf.CANCELLED]
    part = tmp_path / "models/m/a.gguf.part"
    assert 0 < part.stat().st_size < len(PAYLOAD) and not (tmp_path / "models/m/a.gguf").exists()
    monkeypatch.undo()
    code, report = run(tmp_path, data, "fetch")
    assert code == 0 and states(report, "base") == [mf.RESUMED]
    assert (tmp_path / "models/m/a.gguf").read_bytes() == PAYLOAD
