"""MEDIA-RESTART regression with REAL processes (no monkeypatching of the code under test).

A separate "backend" process submits through the public SdCppProvider.submit() with a fake
engine (launcher -> python, i.e. a real process tree) and is then hard-killed. A new provider
for the same storage root must leave no engine running, must not kill a foreign engine of
another storage root, must not kill an unrelated process that reuses a recorded PID, and must
never accept a late output written by the orphan.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest

from bcc.studio import catalog
from bcc.studio.provider import ProviderOutput
from bcc.studio.providers import sdcpp

ENGINE = textwrap.dedent("""
    import sys, time, pathlib
    out = sys.argv[sys.argv.index("-o") + 1]
    time.sleep(float(sys.argv[sys.argv.index("--steps") + 1]))
    pathlib.Path(out).write_bytes(b"late-orphan-output")
""")

BACKEND = textwrap.dedent("""
    import asyncio, json, sys
    from pathlib import Path
    from bcc.studio import catalog
    from bcc.studio.providers import sdcpp
    from bcc.studio.provider import GenerationPlane
    cfg = json.loads(sys.argv[1]); store = Path(sys.argv[2])
    sdcpp.USE_JOB_OBJECT = sys.argv[3] == "job"
    seconds = int(sys.argv[4])
    cfg = {"bin": Path(cfg["bin"]), "root": Path(cfg["root"]), "manifest": cfg["manifest"]}
    model = next(m for m in catalog.load()["models"] if m["id"] == "sdcpp:z-image-turbo")
    async def main():
        prov = sdcpp.SdCppProvider(cfg, store, model)
        sub = await prov.submit(GenerationPlane("sdcpp:z-image-turbo", "a red cube",
                                                {"steps": seconds}, ()))
        print("SUBMITTED", sub.request_id, flush=True)
        await asyncio.sleep(300)
    asyncio.run(main())
""")


def _model():
    return next(m for m in catalog.load()["models"] if m["id"] == "sdcpp:z-image-turbo")


def _setup(tmp: Path, tag: str):
    models = tmp / f"models-{tag}"
    models.mkdir()
    files = {}
    for role, name in (("diffusion", "d.gguf"), ("vae", "v.gguf"), ("text_encoder", "t.gguf")):
        p = models / name
        p.write_bytes(f"{role}-{tag}".encode() * 64)
        files[role] = {"path": name, "bytes": p.stat().st_size,
                       "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    (models / "MANIFEST.json").write_text(json.dumps({"engines": {"z-image-turbo": {"files": files}}}))
    engine = tmp / f"engine-{tag}.py"
    engine.write_text(ENGINE, encoding="utf-8")
    if os.name == "nt":                                # cmd.exe -> python: a real process tree
        exe = tmp / f"fake-sd-cli-{tag}.cmd"
        exe.write_text(f'@"{sys.executable}" "{engine}" %*\r\n', encoding="ascii")
    else:
        exe = tmp / f"fake-sd-cli-{tag}.sh"
        exe.write_text(f'#!/bin/sh\n"{sys.executable}" "{engine}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    cfg = {"bin": str(exe), "root": str(models),
           "manifest": json.loads((models / "MANIFEST.json").read_text())}
    return cfg, engine


def _engines(marker: Path) -> list[psutil.Process]:
    out = []
    for p in psutil.process_iter(["cmdline"]):
        try:
            if any(str(marker) in (a or "") for a in (p.info["cmdline"] or [])):
                out.append(p)
        except psutil.Error:
            pass
    return out


def _procs(record: Path) -> list:
    try:
        return json.loads(record.read_text(encoding="utf-8"))["procs"]
    except (OSError, ValueError):                      # being replaced right now
        return []


def _wait(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.2)
    return pred()


def _kill_all(procs):
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass


class Backend:
    def __init__(self, tmp: Path, cfg: dict, store: Path, *, job: bool, seconds: int = 20):
        script = tmp / "backend.py"
        script.write_text(BACKEND, encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(script), json.dumps(cfg), str(store), "job" if job else "nojob",
             str(seconds)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        line = self.proc.stdout.readline()
        assert line.startswith("SUBMITTED"), line + (self.proc.stdout.read() if self.proc.poll() is not None else "")
        self.rid = line.split()[1]

    def crash(self):
        self.proc.kill()                               # no graceful shutdown, no cancel()
        self.proc.wait(10)


def _provider(cfg, store):
    return sdcpp.SdCppProvider({"bin": Path(cfg["bin"]), "root": Path(cfg["root"]),
                                "manifest": cfg["manifest"]}, store, _model())


@pytest.fixture(autouse=True)
def _clear_hash_cache():
    sdcpp._VERIFIED.clear()


@pytest.mark.skipif(os.name != "nt", reason="Job Object layer is Windows-only")
def test_job_object_kills_engine_tree_when_backend_is_hard_killed(tmp_path):
    cfg, engine = _setup(tmp_path, "job")
    store = tmp_path / "store"
    b = Backend(tmp_path, cfg, store, job=True)
    try:
        assert _wait(lambda: _engines(engine), 60), "fake engine never started"
        record = store / "engine-work" / f"{b.rid}{sdcpp.RECORD_SUFFIX}"
        assert record.is_file(), "durable record must exist while the engine runs"
        # the launcher's child (python) is recorded too: a real tree
        assert _wait(lambda: len(_procs(record)) >= 2, 10)
        b.crash()
        # layer 1: the kernel closes the job handle -> the whole tree dies without any restart
        assert _wait(lambda: not _engines(engine), 10), "engine outlived its hard-killed backend"
        prov = _provider(cfg, store)
        assert [e["rid"] for e in prov.reaped] == [b.rid] and not record.exists()
    finally:
        b.proc.kill()
        _kill_all(_engines(engine))


def test_record_reap_stops_orphan_tree_but_never_a_foreign_root(tmp_path):
    cfg, engine = _setup(tmp_path, "mine")
    fcfg, fengine = _setup(tmp_path, "foreign")
    store, other = tmp_path / "store", tmp_path / "other-store"
    # layer 2 on its own: job objects off in both backends
    mine = Backend(tmp_path, cfg, store, job=False)
    foreign = Backend(tmp_path, fcfg, other, job=False)
    dead_foreign = Backend(tmp_path, fcfg, other, job=False)
    try:
        assert _wait(lambda: _engines(engine), 60)
        assert _wait(lambda: len(_engines(fengine)) >= 2, 60)
        # let the sampler record the engine's child process too (launcher -> python)
        rec = store / "engine-work" / f"{mine.rid}{sdcpp.RECORD_SUFFIX}"
        assert _wait(lambda: len(_procs(rec)) >= 2, 10)
        mine.crash()
        dead_foreign.crash()                           # other root's backend also dead
        time.sleep(1.5)
        assert len(_engines(engine)) >= 1, "precondition: without a job object the orphan survives"
        prov = _provider(cfg, store)
        assert _wait(lambda: not _engines(engine), 10), "orphan engine tree still running after restart"
        assert [e["rid"] for e in prov.reaped] == [mine.rid] and not rec.exists()
        # never touch the other storage root: neither its live nor its orphaned engines
        time.sleep(1.0)
        assert len(_engines(fengine)) >= 2, "a foreign storage root's engine was killed"
        assert foreign.proc.poll() is None
        assert (other / "engine-work" / f"{dead_foreign.rid}{sdcpp.RECORD_SUFFIX}").is_file()
    finally:
        for b in (mine, foreign, dead_foreign):
            b.proc.kill()
        _kill_all(_engines(engine) + _engines(fengine))


def _dead_identity():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
    ident = {"pid": p.pid, "ctime": psutil.Process(p.pid).create_time()}
    p.wait(10)
    return ident


def _write_record(store: Path, rid: str, owner: dict, procs: list[dict]):
    work = store / "engine-work"
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{rid}{sdcpp.RECORD_SUFFIX}").write_text(json.dumps(
        {"v": 1, "rid": rid, "root": str(store.resolve()), "owner": owner, "procs": procs}))
    return work / f"{rid}{sdcpp.RECORD_SUFFIX}"


def test_stale_record_never_kills_unrelated_process_or_live_backend_engine(tmp_path):
    cfg, _ = _setup(tmp_path, "stale")
    store = tmp_path / "store"
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        real_ctime = psutil.Process(bystander.pid).create_time()
        # dead owner, but the recorded PID now belongs to an unrelated process (create_time differs)
        reused = _write_record(store, "a" * 16, _dead_identity(),
                               [{"pid": bystander.pid, "ctime": real_ctime - 1000.0}])
        # exact pid+ctime match, but its owner backend (this process) is alive -> not an orphan;
        # dispatch builds a new SdCppProvider per job inside the same backend
        live = _write_record(store, "b" * 16, sdcpp._owner(),
                             [{"pid": bystander.pid, "ctime": real_ctime}])
        prov = _provider(cfg, store)
        time.sleep(0.5)
        assert bystander.poll() is None, "an unrelated process was killed"
        assert [e["rid"] for e in prov.reaped] == ["a" * 16] and prov.reaped[0]["killed"] == []
        assert not reused.exists() and live.is_file()
    finally:
        bystander.kill()
        bystander.wait(10)


def test_late_orphan_output_is_never_accepted(tmp_path):
    cfg, engine = _setup(tmp_path, "late")
    store = tmp_path / "store"
    b = Backend(tmp_path, cfg, store, job=False, seconds=4)
    try:
        assert _wait(lambda: _engines(engine), 60)
        b.crash()
        raw = store / "engine-work" / f"{b.rid}.png"
        assert _wait(raw.is_file, 60), "precondition: the orphan finished and wrote its output"
        assert _wait(lambda: not _engines(engine), 10)
        prov = _provider(cfg, store)
        assert not raw.exists(), "late orphan output must be removed, not kept as a result"
        assert prov.reaped[0]["removed_outputs"] == [raw.name]
        with pytest.raises(ValueError):
            asyncio.run(prov.status(b.rid))
        with pytest.raises(ValueError):
            asyncio.run(prov.fetch(ProviderOutput(f"{b.rid}:0"), store / "stolen.png"))
    finally:
        b.proc.kill()
        _kill_all(_engines(engine))
