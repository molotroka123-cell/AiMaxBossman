"""video.* tool family: real engine path -> real Video Studio store/service -> real ffmpeg/ffprobe on TINY synthetic clips.

Owner uploads are simulated by the product's own HTTP upload route (the agent tools can only use media the
owner attached). Every ASK tool is checked for "no revision change before approval"; effects are read back
from the project store, and the exported file is re-probed with ffprobe independently of the tool.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from .common import OUT_REL, PROVEN_EXECUTED, FAILED, NOT_RUN, REPO_ROOT, Row, add_repo_paths, short
from .harness import Lab, ScriptedModel, Trace, Wait, tool_row, SETTLED

add_repo_paths()
BASE = "/api/video-studio"
EVIDENCE = REPO_ROOT / OUT_REL / "evidence" / "video"
_EXT = "ниже — внешние данные"


def _op() -> str:
    return uuid.uuid4().hex


def _parse(text: str) -> dict:
    """Tool message -> JSON payload (strips the 'external data' header)."""
    body = text.split("---\n", 1)[1] if "---\n" in text[:300] else text
    try:
        return json.loads(body)
    except ValueError:
        return {}


def _rev(text: str) -> int | None:
    d = _parse(text)
    p = d.get("project", d)
    return int(p["revision"]) if isinstance(p, dict) and "revision" in p else None


def _ffprobe(path: Path) -> dict:
    out = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    return json.loads(out.stdout or "{}")


def _make_media(dst: Path) -> list[Path]:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg not on PATH")
    paths = []
    for i, color in enumerate(("red", "blue")):
        p = dst / f"proof-src-{i}.mp4"
        subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=25:d=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-y", str(p)],
                       check=True, timeout=60)
        paths.append(p)
    return paths


async def _run_meta(lab: Lab, agent: dict, steps: list[tuple], meta: dict, decide, timeout: float = 120.0) -> Trace:
    """Lab.run with task.meta (video_project_id binds the project to the task, as the product does)."""
    model = ScriptedModel(steps)
    lab.svc.registry.adapter_factory = lambda m, p: model
    r = await lab.client.post("/api/tasks", json={"title": "proof-video", "prompt": "proof", "agent_id": agent["id"],
                                                  "run_now": True, "max_retries": 0})
    task_id = r.json()["task"]["id"]
    from bcc.db import tasks as tasks_t
    async with lab.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(meta=meta))
        await s.commit()
    lab.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(lab.svc.engine.worker_loop())
    watcher = asyncio.create_task(lab.svc.engine.approval_watcher())
    trace = Trace(task_id=task_id, status="", model=model)
    try:
        while True:
            st = await lab._until(task_id, SETTLED, timeout)
            if st != "waiting_approval":
                break
            await asyncio.sleep(0.1)
            pend = await lab._pending_approvals(task_id)
            wait = Wait(approval=pend[0], tool_rows=await lab._rows(task_id))
            trace.waits.append(wait)
            verdict = await decide(wait)
            wait.decision = bool(verdict)
            await lab.client.post(f"/api/approvals/{pend[0]['id']}", json={"approve": bool(verdict), "by": "proof-owner"})
            await asyncio.sleep(0.05)
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)
    trace.status = await lab._status(task_id)
    trace.tool_rows = await lab._rows(task_id)
    trace.approvals = await lab._all_approvals(task_id)
    return trace


def _dump(name: str, trace: Trace, extra: dict | None = None) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    payload = {"status": trace.status,
               "tool_rows": [{k: str(v) for k, v in r.items() if k in ("tool", "status", "effect", "approval_id", "error")} for r in trace.tool_rows],
               "waits": [{"preview": w.approval.get("preview"), "decision": w.decision} for w in trace.waits],
               "tool_messages": [m[:1500] for m in trace.tool_messages()], "extra": extra or {}}
    (EVIDENCE / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _names() -> list[str]:
    from bcc.tools import REGISTRY
    return [n for n in REGISTRY.names() if n.startswith("video.")]


async def run(lab: Lab, root: Path) -> list[Row]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rows: dict[str, Row] = {}
    try:
        await _run(lab, root, rows)
    except Exception as exc:                                    # an unexpected harness error must not hide untested tools
        why = f"probe aborted: {type(exc).__name__}: {short(exc, 240)}"
        for n in _names():
            if n not in rows:
                rows[n] = tool_row(n, NOT_RUN, why)
    for n in _names():
        if n not in rows:
            rows[n] = tool_row(n, NOT_RUN, "no probe defined / not reached in this run")
    return [rows[n] for n in sorted(rows)]


async def _run(lab: Lab, root: Path, rows: dict[str, Row]) -> None:
    svc = lab.svc
    video = svc.video_studio
    srcs = _make_media(root)
    # --- owner side (not a tool): create project + attach two clips through the product upload route
    r = await lab.client.post(BASE + "/projects", json={"name": "proof", "operation_id": _op()})
    project = r.json()["project"]
    pid = project["id"]
    media = []
    for p in srcs:
        up = await lab.client.post(BASE + "/media", params={"project_id": pid, "filename": p.name, "expected_revision": project["revision"],
                                                             "operation_id": _op()}, content=p.read_bytes())
        assert up.status_code == 200, up.text
        project = up.json()["project"]
        media.append(up.json()["media"])
    vtrack = project["sequences"][0]["tracks"][0]["id"]
    atrack = project["sequences"][0]["tracks"][1]["id"]
    m0, m1 = media[0]["id"], media[1]["id"]
    meta = {"video_project_id": pid}
    ask_perm = {"filesystem.read": True}              # read tools AUTO; filesystem.write NOT granted -> mutations ASK
    names_auto = ["video.project.open", "video.project.inspect", "video.timeline.inspect", "video.media.probe",
                  "video.project.interchange", "video.export.status", "video.output.verify"]
    names_ask = ["video.timeline.apply", "video.clip.trim", "video.clip.split", "video.clip.move", "video.effect.apply",
                 "video.keyframe.set", "video.audio.process", "video.captions.edit", "video.history.undo",
                 "video.media.import", "video.media.relink", "video.export.start", "video.preview.render", "video.export.cancel",
                 "video.media.analyse", "video.captions.transcribe"]
    agent = await lab.agent(names_auto + names_ask, ask_perm, max_steps=40, name="proof-video")

    revs_at_wait: list[tuple[str, int]] = []
    approved_tools: list[str] = []

    async def decide(w: Wait) -> bool:
        pending = [r for r in w.tool_rows if r["status"] == "pending_approval"]
        tool = pending[-1]["tool"] if pending else "?"
        cur = await video.store.get(pid)
        revs_at_wait.append((tool, int(cur["revision"])))
        approved_tools.append(tool)
        return True

    def R(h: list[str]) -> int:
        for m in reversed(h):                      # latest message that carries a project revision
            v = _rev(m)
            if v is not None:
                return v
        return int(project["revision"])

    def A(**kw):                                             # mutation args factory using the latest revision from history
        def f(h: list[str]) -> dict:
            return {"project_id": pid, "expected_revision": R(h), "operation_id": _op(), **kw}
        return f

    steps: list[tuple] = [
        ("tool", "video.project.inspect", {"project_id": pid}),
        ("tool", "video.project.open", {"project_id": pid}),
        ("tool", "video.timeline.inspect", {"project_id": pid}),
        ("tool", "video.media.probe", {"project_id": pid, "media_id": m0}),
        ("tool", "video.timeline.apply", A(command={"type": "timeline.apply", "operations": [
            {"type": "clip.add", "track_id": vtrack, "clip": {"id": "take-0", "media_id": m0, "start": 0, "source_in": 0, "source_out": 2_000_000}},
            {"type": "clip.add", "track_id": vtrack, "clip": {"id": "take-1", "media_id": m1, "start": 2_000_000, "source_in": 0, "source_out": 2_000_000}}]})),
        ("tool", "video.clip.trim", A(command={"clip_id": "take-0", "source_out": 1_500_000})),
        ("tool", "video.clip.split", A(command={"clip_id": "take-1", "at": 3_000_000})),
        ("tool", "video.clip.move", A(command={"clip_id": "take-1", "start": 1_500_000})),
        ("tool", "video.effect.apply", A(command={"clip_id": "take-0", "effect": {"type": "eq", "params": {"brightness": 0.1}}})),
        ("tool", "video.keyframe.set", A(command={"clip_id": "take-0", "param": "x", "t": 0, "value": 0})),
        ("tool", "video.audio.process", A(command={"clip_id": "take-0", "effect": {"type": "denoise", "params": {"reduction": 12}}})),
        ("tool", "video.captions.edit", A(command={"captions": [{"id": "cap-1", "start": 0, "end": 1_000_000, "text": "Привет, proof!"}]})),
        ("tool", "video.history.undo", A(command={})),
        ("tool", "video.timeline.inspect", {"project_id": pid}),
        ("text", "готово"),
    ]
    t0 = time.monotonic()
    tr = await _run_meta(lab, agent, steps, meta, decide)
    msgs = tr.tool_messages()
    _dump("p1_edit_chain", tr, {"revs_at_wait": revs_at_wait, "approved": approved_tools, "seconds": round(time.monotonic() - t0, 1)})
    final = await video.store.get(pid)
    seq = final["sequences"][0]
    clips = {c["id"]: c for t in seq["tracks"] for c in t["clips"]}
    # observed effects (store state) -------------------------------------------------------------
    by_tool = {r["tool"]: r for r in tr.tool_rows}

    def ex(tool: str) -> bool:
        return tool in by_tool and by_tool[tool]["status"] == "executed"

    # revision discipline: every ASK mutation parked with revision == previous result, then +1
    seq_ok = True
    for i in range(1, len(revs_at_wait)):
        if revs_at_wait[i][1] != revs_at_wait[i - 1][1] + 1:
            seq_ok = False
    reads_auto = all(by_tool[t]["effect"] == "auto" for t in ("video.project.inspect", "video.project.open", "video.timeline.inspect", "video.media.probe") if t in by_tool)
    asks_ok = bool(revs_at_wait) and seq_ok and all(by_tool[t]["effect"] == "ask" for t in by_tool if t in names_ask)

    def put(name: str, ok: bool, evidence: str, caveat: str = "") -> None:
        rows[name] = tool_row(name, PROVEN_EXECUTED if ok else FAILED, evidence, caveat)

    insp = _parse(msgs[0]) if msgs else {}
    put("video.project.inspect", ex("video.project.inspect") and insp.get("id") == pid and set(insp.get("media", {})) == {m0, m1},
        f"AUTO executed; returned the owner-attached project id={pid[:12]}.. with exactly the 2 uploaded media ids", "media attached by the owner upload route, not by a tool")
    put("video.project.open", ex("video.project.open") and _parse(msgs[1]).get("id") == pid, "AUTO executed; returned the same project document (id matches)")
    put("video.timeline.inspect", ex("video.timeline.inspect") and bool(_parse(msgs[2]).get("sequences")) and bool(_parse(msgs[-1]).get("sequences")),
        "AUTO executed (twice: before and after the edit chain); returned sequences/tracks")
    pr = _parse(msgs[3]) if len(msgs) > 3 else {}
    put("video.media.probe", ex("video.media.probe") and pr.get("id") == m0 and bool(pr.get("sha256")),
        f"AUTO executed; returned media {m0[:12]}.. descriptor with sha256 {str(pr.get('sha256'))[:12]}..")
    put("video.timeline.apply", ex("video.timeline.apply") and {"take-0", "take-1", "take-1b"} & set(clips) and "take-0" in clips,
        f"ASK->approved; store now has clips {sorted(clips)}; revision parked at {revs_at_wait[0][1] if revs_at_wait else '?'} until approval", "")
    t0c = clips.get("take-0", {})
    put("video.clip.trim", ex("video.clip.trim") and t0c.get("source_out") == 1_500_000,
        f"ASK->approved; store: take-0 source_out={t0c.get('source_out')} (was 2000000, requested 1500000)")
    put("video.clip.split", ex("video.clip.split") and len(clips) >= 4 - 0 and sum(1 for c in clips.values() if c.get("media_id") == m1) == 2,
        f"ASK->approved; store: media {m1[:8]}.. now appears in {sum(1 for c in clips.values() if c.get('media_id') == m1)} clips (split at 3s)")
    moved = [c for c in clips.values() if c.get("media_id") == m1 and c.get("start") == 1_500_000]
    put("video.clip.move", ex("video.clip.move") and bool(moved), f"ASK->approved; store: a take-1 clip starts at {[c.get('start') for c in clips.values() if c.get('media_id') == m1]}")
    effs = t0c.get("effects", [])
    put("video.effect.apply", ex("video.effect.apply") and any(e.get("type") == "eq" for e in effs), f"ASK->approved; store: take-0 effects={[e.get('type') for e in effs]}")
    put("video.keyframe.set", ex("video.keyframe.set") and "x" in json.dumps(t0c.get("keyframes", t0c)) , "ASK->approved; keyframe on param x present in take-0 document")
    put("video.audio.process", ex("video.audio.process") and "denoise" in json.dumps(t0c),
        "ASK->approved; denoise effect present in take-0 document")
    caps = json.dumps(final.get("captions", final))
    put("video.captions.edit", ex("video.captions.edit") and "Привет, proof!" in caps, "ASK->approved; caption text 'Привет, proof!' stored in the project")
    put("video.history.undo", ex("video.history.undo") and int(final["revision"]) >= 1, f"ASK->approved; undo command executed, project revision now {final['revision']}", "undo effect not independently diffed beyond executed+revision")
    if not asks_ok:
        for n in names_ask:
            if n in rows and rows[n].status == PROVEN_EXECUTED:
                rows[n].status = FAILED
                rows[n].evidence += f" | revision/ASK discipline broken: waits={revs_at_wait}"

    # --- media.import / relink / interchange / export chain (second task, same bound project) --------------
    steps2: list[tuple] = [
        ("tool", "video.media.import", {"project_id": pid, "media_id": m0, "expected_revision": int(final["revision"]), "operation_id": _op()}),
        ("tool", "video.project.interchange", {"project_id": pid, "format": "shotcut"}),
        ("text", "готово"),
    ]
    revs_at_wait.clear(); approved_tools.clear()
    tr2 = await _run_meta(lab, agent, steps2, meta, decide)
    m2 = tr2.tool_messages()
    _dump("p2_import_interchange", tr2, {"waits": revs_at_wait})
    by2 = {r["tool"]: r for r in tr2.tool_rows}
    ic = _parse(m2[1]) if len(m2) > 1 else {}
    put("video.project.interchange", "video.project.interchange" in by2 and by2["video.project.interchange"]["status"] == "executed" and bool(ic.get("filename")) and ic.get("parity_claim") is False,
        f"AUTO executed; shotcut export descriptor filename={ic.get('filename')}, parity_claim={ic.get('parity_claim')}")
    imp = by2.get("video.media.import")
    put("video.media.import", bool(imp and imp["status"] == "executed" and imp["effect"] == "ask"), f"ASK->approved; row status={imp and imp['status']}; result: {short(m2[0] if m2 else '', 140)}",
        "media already attached by the owner upload route; re-import of an attached descriptor")

    # --- export: ASK start -> job runs -> independent ffprobe of the output ------------------------------
    cur = await video.store.get(pid)
    steps3 = [("tool", "video.export.start", {"project_id": pid, "expected_revision": int(cur["revision"]), "operation_id": _op(),
                                              "options": {"width": 160, "height": 90, "fps": {"num": 25, "den": 1}}}),
              ("text", "готово")]
    revs_at_wait.clear()
    tr3 = await _run_meta(lab, agent, steps3, meta, decide, timeout=180.0)
    m3 = tr3.tool_messages()
    job = _parse(m3[0]) if m3 else {}
    job_id = job.get("job_id")
    out_path: Path | None = None
    job_final: dict = {}
    deadline = time.monotonic() + 120
    while job_id and time.monotonic() < deadline:
        job_final = (await lab.client.get(BASE + f"/exports/{job_id}")).json()
        if job_final.get("status") in ("completed", "failed", "cancelled"):
            break
        await asyncio.sleep(0.5)
    _dump("p3_export_start", tr3, {"job": job, "job_final_status": job_final.get("status"), "verification": job_final.get("verification")})
    probe_info: dict = {}
    if job_final.get("status") == "completed":
        dl = await lab.client.get(job_final["output_url"])
        out_path = root / "export-out.mp4"
        out_path.write_bytes(dl.content)
        probe_info = _ffprobe(out_path)
    dur = float((probe_info.get("format") or {}).get("duration") or 0)
    streams = {s.get("codec_type") for s in probe_info.get("streams", [])}
    put("video.export.start", bool(job_id) and job_final.get("status") == "completed" and out_path is not None and out_path.stat().st_size > 1000 and "video" in streams and dur > 1.0,
        f"ASK->approved; job {str(job_id)[:10]}.. completed; output {out_path.stat().st_size if out_path else 0} B; independent ffprobe: duration={dur:.2f}s streams={sorted(streams)}",
        "job executed by the product's own engine worker; ffmpeg CPU encode of a 160x90 clip")

    # --- export.status / output.verify (AUTO reads) ---------------------------------------------------------
    steps4 = [("tool", "video.export.status", {"job_id": job_id}), ("tool", "video.output.verify", {"job_id": job_id}), ("text", "готово")]
    if job_id and job_final.get("status") == "completed":
        tr4 = await _run_meta(lab, agent, steps4, meta, decide)
        m4 = tr4.tool_messages()
        by4 = {r["tool"]: r for r in tr4.tool_rows}
        st = _parse(m4[0]) if m4 else {}
        put("video.export.status", by4.get("video.export.status", {}).get("status") == "executed" and st.get("status") == "completed",
            f"AUTO executed; reported status={st.get('status')} for the real job (matches HTTP job status {job_final.get('status')})")
        put("video.output.verify", by4.get("video.output.verify", {}).get("status") == "executed" and out_path is not None and dur > 1.0,
            f"AUTO executed (verified_output opened the file); independently ffprobe duration={dur:.2f}s", "tool only opens/verifies; content correctness judged by ffprobe here")
        _dump("p4_status_verify", tr4)
    else:
        for n in ("video.export.status", "video.output.verify"):
            rows[n] = tool_row(n, NOT_RUN, f"export job did not complete (status={job_final.get('status')}); nothing to query")

    # --- not exercised ---------------------------------------------------------------------------------------
    for n, why in (("video.preview.render", "not exercised in this run (same export pipeline with preview=True)"),
                   ("video.export.cancel", "not exercised: needs a long-running job to cancel before it finishes"),
                   ("video.media.relink", "not exercised in this run"),
                   ("video.media.analyse", "not exercised in this run (silence/scene analysis jobs)"),
                   ("video.captions.transcribe", "needs a local speech model (BOSSMAN_VIDEO_ASR_MODEL); none configured and nothing is downloaded")):
        if n not in rows:
            rows[n] = tool_row(n, NOT_RUN, why)
