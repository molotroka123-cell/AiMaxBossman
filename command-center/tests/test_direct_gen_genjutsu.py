"""Genjutsu live constructor: mask engines, the masks endpoint, honest LoRA status, worker helpers.

No GPU, no network, no real FaceFusion/SegFormer: the mask worker is a fake runner with the same call
signature as DirectGenService._run_masks (like test_direct_gen_faceswap fakes FaceFusion). The real worker's
pure image helpers are tested directly when numpy + opencv are installed.
"""
from __future__ import annotations

import base64
import json
import struct
import subprocess
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.direct_gen import genjutsu
from bcc.direct_gen.service import DirectGenError, DirectGenService

from .test_direct_gen_faceswap import make_faceswap_home


def png(width: int, height: int, value: int = 0) -> bytes:
    """A real greyscale PNG (zlib + CRC), so the header size parser and decoders both accept it."""
    raw = b"".join(b"\x00" + bytes([value]) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


FRAME = png(40, 30, 120)
FAKE_MODEL = b"not really onnx, but sha-pinned in the test"


class FakeWorker:
    """Writes what parsing_worker.py writes: <region>.png + masks.json; records argv/cwd."""

    def __init__(self, *, returncode=0, regions=("hair", "top", "bottom", "clothes", "person", "skin", "dress"),
                 info=None, stderr=""):
        self.calls: list[dict] = []
        self.returncode, self.regions, self.info, self.stderr = returncode, regions, info, stderr

    def __call__(self, argv, cwd, timeout):
        out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path(argv[-1])
        image = Path(argv[argv.index("--image") + 1]) if "--image" in argv else Path(argv[-2])
        self.calls.append({"argv": list(argv), "cwd": Path(cwd), "timeout": timeout, "image_bytes": image.read_bytes()})
        if self.returncode == 0:
            out.mkdir(parents=True, exist_ok=True)
            for name in self.regions:
                (out / f"{name}.png").write_bytes(png(40, 30, 255 if name in ("hair", "top") else 0))
            info = self.info or {"size": [40, 30], "soft": True, "seconds": {"load": 1.2, "parse": 0.7},
                                 "providers": ["DmlExecutionProvider"], "passes": 2,
                                 "area": {r: (0.25 if r in ("hair", "top") else 0.0) for r in self.regions}}
            (out / "masks.json").write_text(json.dumps(info), encoding="utf-8")
        return SimpleNamespace(returncode=self.returncode, stdout="", stderr=self.stderr)


@pytest.fixture
def model(tmp_path, monkeypatch):
    p = tmp_path / "segformer" / "model.onnx"
    p.parent.mkdir()
    p.write_bytes(FAKE_MODEL)
    import hashlib
    monkeypatch.setattr(genjutsu, "SEGFORMER_SHA256", hashlib.sha256(FAKE_MODEL).hexdigest())
    return p


def install(env, tmp_path, model, worker, *, facefusion=True):
    svc = DirectGenService(env.svc, models_dir=tmp_path / "models", workflows_dir=tmp_path / "workflows",
                           media_dir=tmp_path / "media", sd_bin=tmp_path / "sd-cli.exe",
                           free_memory=lambda: 64 * 1024 ** 3, foreign_engines=lambda: [])
    svc.faceswap_home = make_faceswap_home(tmp_path) if facefusion else tmp_path / "no-facefusion"
    svc.genjutsu_model = model
    svc.masks_runner = worker
    env.svc.direct_gen = svc
    return svc


def body(**over):
    b = {"image_b64": base64.b64encode(FRAME).decode(), "consent": True}
    b.update(over)
    return b


# ---------------------------------------------------------------- describe / status

def test_describe_reports_both_engines_with_licences_and_honest_training(tmp_path, model):
    info = genjutsu.describe(make_faceswap_home(tmp_path), model)
    engines = {e["id"]: e for e in info["engines"]}
    assert info["default_engine"] == "segformer"
    assert engines["segformer"]["available"] and engines["facefusion"]["available"]
    assert engines["segformer"]["license"]["commercial"] is False           # NVIDIA SegFormer licence §3.3
    assert "некоммерческ" in engines["segformer"]["label"]
    assert engines["facefusion"]["approximate"] == ["top", "bottom"]
    assert info["lora_training"]["available"] is False
    assert info["lora_training"]["status"] == "не подключено"
    assert info["regions"] == ["hair", "top", "bottom", "clothes"]


def test_describe_refuses_missing_or_unverified_weights(tmp_path, model):
    home = make_faceswap_home(tmp_path)
    missing = genjutsu.describe(home, tmp_path / "nope.onnx")
    assert missing["engines"][0]["available"] is False and "нет весов" in missing["engines"][0]["reason"]
    assert missing["default_engine"] == "facefusion"
    model.write_bytes(b"tampered weights")
    bad = genjutsu.describe(home, model)
    assert bad["engines"][0]["available"] is False and "sha256" in bad["engines"][0]["reason"]


def test_describe_without_facefusion_venv(tmp_path, model):
    info = genjutsu.describe(tmp_path / "missing", model)
    assert info["default_engine"] is None
    assert all(not e["available"] for e in info["engines"])


def test_image_size_parses_png_and_jpeg_headers():
    assert genjutsu.image_size(png(17, 9)) == (17, 9)
    sof = b"\xff\xd8" + b"\xff\xe0\x00\x10" + b"J" * 14 + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", 300, 400) + b"\x03" + b"\x00" * 9
    assert genjutsu.image_size(sof) == (400, 300)
    assert genjutsu.image_size(b"GIF89a....") is None


def test_worker_argv_uses_facefusion_interpreter_and_our_workers(tmp_path, model):
    home = make_faceswap_home(tmp_path)
    seg = genjutsu.worker_argv("segformer", home, model, tmp_path / "f.png", tmp_path / "o", "cpu")
    assert seg[0].endswith("python.exe") and seg[1].endswith("parsing_worker.py")
    assert seg[seg.index("--provider") + 1] == "cpu" and seg[seg.index("--model") + 1] == str(model)
    ff = genjutsu.worker_argv("facefusion", home, model, tmp_path / "f.png", tmp_path / "o")
    assert ff[1].endswith("masks_worker.py") and ff[-2:] == [str(tmp_path / "f.png"), str(tmp_path / "o")]
    with pytest.raises(ValueError):
        genjutsu.worker_argv("magic", home, model, tmp_path / "f.png", tmp_path / "o")


# ---------------------------------------------------------------- the masks endpoint

async def test_masks_end_to_end_over_http(env, tmp_path, model):
    worker = FakeWorker()
    svc = install(env, tmp_path, model, worker)
    info = (await env.client.get("/api/direct-gen/genjutsu")).json()
    assert info["default_engine"] == "segformer" and info["lora_training"]["available"] is False
    r = await env.client.post("/api/direct-gen/genjutsu/masks", json=body())
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out["regions"]) >= {"hair", "top", "bottom", "clothes"}
    assert out["regions"]["hair"]["area"] == 0.25 and out["regions"]["bottom"]["area"] == 0.0
    assert base64.b64decode(out["regions"]["top"]["png_b64"]).startswith(b"\x89PNG")
    assert out["engine"] == "segformer" and out["provider"] == "directml" and out["license"]["commercial"] is False
    assert out["consent_confirmed"] is True and out["stored"] is False
    assert out["image"] == {"width": 40, "height": 30, "bytes": len(FRAME)}
    call = worker.calls[0]
    assert call["image_bytes"] == FRAME and call["cwd"] == svc.faceswap_home
    # nothing about the person is kept on disk
    tmp = svc.data_dir / "genjutsu-tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


async def test_masks_require_consent(env, tmp_path, model):
    worker = FakeWorker()
    install(env, tmp_path, model, worker)
    r = await env.client.post("/api/direct-gen/genjutsu/masks", json=body(consent=False))
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    assert worker.calls == []


@pytest.mark.parametrize("image, code", [
    (None, "image_required"),
    (base64.b64encode(b"GIF89a not ours").decode(), "image_invalid"),
    ("%%%not-base64%%%", "image_invalid"),
    (base64.b64encode(png(8, 8)).decode(), "image_invalid"),           # below 16 px
    (base64.b64encode(png(5000, 20)).decode(), "image_invalid"),       # above MAX_SIDE
])
async def test_masks_refuse_bad_pictures(env, tmp_path, model, image, code):
    worker = FakeWorker()
    svc = install(env, tmp_path, model, worker)
    with pytest.raises(DirectGenError) as exc:
        await svc.create_masks("owner", body(image_b64=image))
    assert exc.value.code == code
    assert worker.calls == []


async def test_masks_unknown_and_unavailable_engines(env, tmp_path, model):
    worker = FakeWorker()
    svc = install(env, tmp_path, model, worker)
    r = await env.client.post("/api/direct-gen/genjutsu/masks", json=body(engine="photoshop"))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_engine"
    svc.genjutsu_model = tmp_path / "gone.onnx"
    r = await env.client.post("/api/direct-gen/genjutsu/masks", json=body(engine="segformer"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "model_unavailable"
    svc.faceswap_home = tmp_path / "no-facefusion"
    r = await env.client.post("/api/direct-gen/genjutsu/masks", json=body())
    assert r.status_code == 409 and r.json()["error"]["code"] == "model_unavailable"
    assert worker.calls == []


async def test_masks_fall_back_to_cpu_while_a_gpu_job_runs(env, tmp_path, model):
    worker = FakeWorker()
    svc = install(env, tmp_path, model, worker)
    svc._running = "some-swap-job"
    out = await svc.create_masks("owner", body())
    assert out["provider"] == "cpu"
    assert worker.calls[0]["argv"][-1] == "cpu"
    with pytest.raises(DirectGenError) as exc:
        await svc.create_masks("owner", body(engine="facefusion"))
    assert exc.value.code == "gpu_busy"
    assert svc.genjutsu_info()["gpu_busy"] is True


async def test_facefusion_engine_marks_top_bottom_approximate(env, tmp_path, model):
    worker = FakeWorker(regions=("person", "clothes", "top", "bottom", "hair"),
                        info={"seconds": 8.05, "size": [40, 30], "approximate": ["top", "bottom"],
                              "area": {"hair": 0.05, "top": 0.0, "bottom": 0.16, "clothes": 0.16, "person": 0.55},
                              "models": {"person": "u2net_human"}})
    svc = install(env, tmp_path, model, worker)
    out = await svc.create_masks("owner", body(engine="facefusion"))
    assert out["approximate"] == ["top", "bottom"] and out["license"]["commercial"] is True
    assert out["soft"] is False and out["worker_seconds"] == 8.05
    assert worker.calls[0]["argv"][1].endswith("masks_worker.py")


async def test_worker_failure_is_reported_verbatim_and_cleaned_up(env, tmp_path, model):
    svc = install(env, tmp_path, model, FakeWorker(returncode=3, stderr="DmlExecutionProvider: device removed"))
    with pytest.raises(DirectGenError) as exc:
        await svc.create_masks("owner", body())
    assert exc.value.code == "engine_error" and "device removed" in exc.value.message
    svc.masks_runner = FakeWorker(regions=("hair", "person"))            # no top/bottom/clothes written
    with pytest.raises(DirectGenError) as exc:
        await svc.create_masks("owner", body())
    assert exc.value.code == "bad_output"
    tmp = svc.data_dir / "genjutsu-tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


# ---------------------------------------------------------------- the real worker's image helpers

def _worker():
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from bcc.direct_gen import parsing_worker
    return parsing_worker


def test_worker_regions_cover_constructor_zones_and_licence_is_noncommercial():
    w = _worker()
    assert set(genjutsu.REGIONS) <= set(w.REGIONS)
    assert 2 in w.REGIONS["hair"] and 4 in w.REGIONS["top"] and 6 in w.REGIONS["bottom"]
    assert set(w.REGIONS["top"]) | set(w.REGIONS["bottom"]) <= set(w.REGIONS["clothes"])
    assert w.LICENSE["commercial"] is False
    assert w.SOURCE["sha256"] == "a93a8dac171b5c1fcc53632a8bfc180bfd9759ea69a3e207451bb07f76add54f"


def test_worker_picture_box_fine_size_and_smoothstep():
    import numpy as np
    w = _worker()
    g = np.zeros((100, 120), np.uint8)
    g[10:90, 20:100] = 200
    assert w.picture_box(g) == (10, 90, 20, 100)
    assert w.picture_box(np.zeros((50, 50), np.uint8)) == (0, 50, 0, 50)
    assert w.fine_size(1019, 1043, 768) == (736, 768)                    # aspect kept, /32
    assert w.fine_size(300, 200, 768) == (512, 352)                      # small crops go up to 512
    s = w.smoothstep(0.2, 0.8, np.array([0.0, 0.2, 0.5, 0.8, 1.0], np.float32))
    assert s[0] == 0 and s[1] == 0 and abs(s[2] - 0.5) < 1e-6 and s[3] == 1 and s[4] == 1


def test_worker_keeps_worn_clothes_and_drops_the_coat_rack():
    import numpy as np
    w = _worker()
    prob = np.zeros((200, 200), np.float32)
    prob[60:150, 40:120] = 0.9          # worn shirt
    prob[60:150, 150:190] = 0.9         # a coat on a rack: 50% of the shirt, not touching skin
    prob[100:102, 120:150] = 0.9        # joined to the shirt by a thin bridge (a strand over it)
    prob[155:195, 30:75] = 0.9          # a second worn piece (25% of the shirt) next to an arm
    skin = np.zeros_like(prob)
    skin[40:60, 60:100] = 0.9           # neck above the shirt
    skin[160:190, 75:85] = 0.9          # arm next to that piece
    gate = w.soft_keep(prob, skin)
    assert gate[100, 80] > 0.99, "the shirt must stay"
    assert gate[100, 175] < 0.05, "the coat on the rack must go"
    assert gate[175, 50] > 0.99, "a big piece touching skin stays"
    assert np.allclose(w.soft_keep(prob[:150, :120], skin[:150, :120]), 1.0)    # one blob: nothing to gate
    person = np.zeros((200, 200), np.float32)
    person[30:190, 30:130] = 0.9
    person[5:12, 180:190] = 0.9
    keep = w.main_person(person)
    assert keep[100, 80] == 1 and keep[8, 185] == 0


def test_worker_guided_filter_snaps_to_image_edges():
    import numpy as np
    w = _worker()
    guide = np.zeros((64, 64), np.float32)
    guide[:, 32:] = 1.0
    blurry = np.clip((np.arange(64, dtype=np.float32) - 24) / 16, 0, 1)[None].repeat(64, 0)
    sharp = w.guided_filter(guide, blurry, 4, 1e-4)
    assert sharp[32, 28] < blurry[32, 28] and sharp[32, 36] > blurry[32, 36]


# ---------------------------------------------------------------- browser logic (node) + UI invariants

UI = Path(__file__).resolve().parents[1] / "ui"
JS = (UI / "pages" / "genjutsu.js").read_text(encoding="utf-8")
CORE = (UI / "pages" / "genjutsu_core.js").read_text(encoding="utf-8")


def _node() -> str:
    import shutil
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: the constructor's JS contracts were not executed")
    return node


def test_js_core_contracts_pass_under_node():
    run = subprocess.run([_node(), "--test", str(UI / "tests" / "genjutsu_core.test.mjs")],
                         capture_output=True, text=True, timeout=120, check=False)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-2000:]


def test_js_zip_opens_with_python_zipfile(tmp_path):
    import zipfile
    script = tmp_path / "zip.mjs"
    core = (UI / "pages" / "genjutsu_core.js").as_uri()
    script.write_text(
        f"import {{ makeZip }} from '{core}';\n"
        "const z = makeZip([{ name: 'ohwx_001.png', data: new Uint8Array([137, 80, 78, 71, 1, 2]) },"
        " { name: 'ohwx_001.txt', data: 'ohwx, woman, кириллица' }]);\n"
        f"import {{ writeFileSync }} from 'node:fs'; writeFileSync({json.dumps(str(tmp_path / 'd.zip'))}, z);\n",
        encoding="utf-8")
    run = subprocess.run([_node(), str(script)], capture_output=True, text=True, timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    with zipfile.ZipFile(tmp_path / "d.zip") as z:
        assert z.testzip() is None
        assert z.namelist() == ["ohwx_001.png", "ohwx_001.txt"]
        assert z.read("ohwx_001.txt").decode("utf-8") == "ohwx, woman, кириллица"


def test_ui_is_offline_honest_and_has_the_owner_controls():
    import re
    for text in (JS, CORE):
        assert not re.search(r"https?://", text) and "url(" not in text
        assert "XMLHttpRequest" not in text and "WebSocket(" not in text
    assert "const base = '/api/direct-gen'" in JS
    assert "consent: true" in JS and "gj-consent" in JS                     # consent gate like create_swap
    assert "не подключено" in JS                                            # training is honestly not connected
    assert "Это не генерация" in JS                                         # recolour, not a new outfit
    for testid in ("gj-undo", "gj-redo", "gj-reset", "gj-split", "gj-zip", "gj-save", "gj-masks"):
        assert testid in JS, testid
    assert "debounce(" in JS and "LIVE_MS = 60" in JS                       # live sliders, no request per move
    assert "PRESETS" in JS and "presetSettings" in JS
    assert "некоммерческ" in JS                                             # licence is shown next to the engine
    direct = (UI / "pages" / "direct_gen.js").read_text(encoding="utf-8")
    assert "import { genjutsuPanel } from './genjutsu.js';" in direct and "genjutsuPanel()," in direct


def test_js_files_parse():
    for name in ("genjutsu.js", "genjutsu_core.js", "direct_gen.js"):
        run = subprocess.run([_node(), "--check", str(UI / "pages" / name)], capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, name + run.stderr
