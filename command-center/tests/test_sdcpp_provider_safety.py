"""sd.cpp provider safety regressions (owner audit): MEDIA-HASH, MEDIA-CANCEL.

Uses a fake manifest + files; no 25 GB models or real engine binary required.
"""
import asyncio
import time
import pytest

import bcc.studio.providers.sdcpp as sdcpp


def _cfg(root, sha):
    return {"root": root, "bin": root / "bin",
            "manifest": {"engines": {"z-image-turbo": {"files": {"diffusion":
                {"path": "diffusion.gguf", "bytes": (root / "diffusion.gguf").stat().st_size,
                 "sha256": sha}}}}}}


def test_engine_files_verifies_bytes_not_just_size(tmp_path):
    sdcpp._VERIFIED.clear()
    f = tmp_path / "diffusion.gguf"
    f.write_bytes(b"correct-model-bytes" * 100)
    good = sdcpp._sha256(f)

    # correct file: passes
    assert sdcpp.engine_files(_cfg(tmp_path, good), "sdcpp:z-image-turbo")["diffusion"] == f.resolve()

    # MEDIA-HASH regression: same byte count, sha differs from the manifest ->
    # a same-size corruption / wrong revision is rejected, not silently accepted.
    sdcpp._VERIFIED.clear()
    with pytest.raises(ValueError, match="sha256"):
        sdcpp.engine_files(_cfg(tmp_path, "0" * 64), "sdcpp:z-image-turbo")


def test_run_does_not_spawn_after_cancel(tmp_path):
    # MEDIA-CANCEL regression: a cancel that arrives before the engine starts
    # must not launch inference. The argv points at a non-existent binary, so if
    # _run tried to spawn it we would get FileNotFoundError; instead it returns.
    model = {"id": "sdcpp:z-image-turbo", "surface": "image", "deadline_seconds": 900}
    prov = sdcpp.SdCppProvider({"root": tmp_path, "bin": tmp_path / "bin", "manifest": {}},
                               tmp_path, model)
    job = {"canceled": True, "argv": ["/nonexistent/engine/binary", "x"],
           "raw": tmp_path / "x.png", "init": None, "started": time.time(), "log": []}
    asyncio.run(prov._run("rid", job))
    assert "proc" not in job
