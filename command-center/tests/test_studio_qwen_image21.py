"""Qwen-Image-2.1 uses the existing Studio/sd.cpp plane, never a parallel backend."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from bcc.studio import catalog
from bcc.studio.provider import GenerationPlane
from bcc.studio.providers import sdcpp


MODEL_ID = "sdcpp:qwen-image-2.1"


def _model() -> dict:
    return next(row for row in catalog.load()["models"] if row["id"] == MODEL_ID)


def _reference() -> dict:
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(out, format="PNG")
    return {"role": "reference", "data_uri": "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()}


def test_qwen_catalog_declares_local_unverified_generation_and_three_references():
    model = _model()
    assert model["provider"] == "sdcpp" and model["surface"] == "image"
    assert model["free"] is True and model["price"]["usd"] == 0
    assert model["enabled"] is False and model["answers"]["VERIFIED"] is False
    assert model["roles"] == {"reference": 3}
    assert "non-commercial" in model["license"]


def test_qwen_argv_uses_pinned_vision_encoder_and_no_cloud(tmp_path):
    files = {role: tmp_path / role for role in sdcpp.REQUIRED_ROLES[MODEL_ID]}
    settings = {"width": 256, "height": 256, "steps": 4, "seed": 7}
    argv = sdcpp._argv({"bin": tmp_path / "sd-cli"}, MODEL_ID,
                       GenerationPlane(MODEL_ID, "a blue cube", settings), settings,
                       files, tmp_path / "out.png", None)
    assert argv[argv.index("--llm_vision") + 1] == str(files["llm_vision"])
    assert argv[argv.index("--diffusion-model") + 1] == str(files["diffusion"])
    assert argv[argv.index("--llm") + 1] == str(files["llm"])
    assert "--offload-to-cpu" in argv and "-r" not in argv


@pytest.mark.asyncio
async def test_qwen_references_are_verified_ordered_and_cleaned(monkeypatch, tmp_path):
    model = _model()
    cfg = {"bin": tmp_path / "sd-cli", "root": tmp_path, "manifest": {}}
    files = {role: tmp_path / role for role in sdcpp.REQUIRED_ROLES[MODEL_ID]}
    monkeypatch.setattr(sdcpp, "verify_engine_files", lambda *_a, **_kw: {
        role: {"path": path} for role, path in files.items()})
    monkeypatch.setattr(sdcpp, "verify_engine_binary", lambda *_a, **_kw: {"match": True})

    async def no_engine(_self, _rid, _job):
        return None

    monkeypatch.setattr(sdcpp.SdCppProvider, "_run", no_engine)
    provider = sdcpp.SdCppProvider(cfg, tmp_path, model)
    plane = GenerationPlane(MODEL_ID, "change blue to green",
                            {"width": 256, "height": 256, "steps": 4, "seed": 7},
                            (_reference(), _reference(), _reference()))
    submitted = await provider.submit(plane)
    job = provider.job_record(submitted.request_id)
    refs = job["refs"]
    assert len(refs) == 3 and all(path.is_file() for path in refs)
    assert [job["argv"][i + 1] for i, arg in enumerate(job["argv"]) if arg == "-r"] == [str(p) for p in refs]
    assert all(hashlib.sha256(p.read_bytes()).digest() == hashlib.sha256(refs[0].read_bytes()).digest()
               for p in refs)
    assert set(refs) <= set(sdcpp._sidecar_files(job["record"], provider.work))
    await asyncio.sleep(0)
    provider._cleanup_work(job)
    assert all(not path.exists() for path in refs)
    with pytest.raises(ValueError, match="1-3 reference"):
        await provider.submit(GenerationPlane(MODEL_ID, "edit", plane.settings, plane.media + (_reference(),)))


def test_sidecar_cannot_delete_reference_outside_engine_work(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    inside = work / "ref.png"
    outside = tmp_path / "owner.png"
    paths = sdcpp._sidecar_files({"reference_files": [str(inside), str(outside)]}, work)
    assert inside in paths and outside not in paths
