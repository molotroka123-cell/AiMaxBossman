"""Owner 30.10: /grechka window stores owner-sent or owner-forwarded media DPAPI-encrypted, first in the folder,
never shown or stored in the inbox; strangers and closed windows are ignored."""
import ctypes
import os
import sys

import pytest

from bcc.telegram_companion import grechka_intake as gi

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


def _unprotect(blob: bytes) -> bytes:
    class B(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(blob, len(blob))
    src, out = B(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), B()
    assert ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
    return ctypes.string_at(out.pbData, out.cbData)


def test_store_is_dpapi_encrypted_and_sorts_first(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, "FOLDER", tmp_path)
    (tmp_path / "20261013_014936_4040.enc").write_bytes(b"old")
    g = gi.GrechkaIntake(); g.arm("owner")
    assert g.store("owner", b"photo-bytes") == 1
    first = sorted(p.name for p in tmp_path.iterdir())[0]
    assert first.startswith("00_") and first.endswith(".enc")
    blob = (tmp_path / first).read_bytes()
    assert blob != b"photo-bytes" and _unprotect(blob) == b"photo-bytes"


def test_commands_window_and_media_pick():
    assert gi.COMMAND.match("/grechka") and gi.COMMAND.match("/гречка").group(1) is None
    assert gi.COMMAND.match("/grechka stop").group(1) == "stop" and not gi.COMMAND.match("/grechka now please")
    g = gi.GrechkaIntake(); assert not g.active("o"); g.arm("o"); assert g.active("o"); g.disarm("o"); assert not g.active("o")
    big = {"photo": [{"file_id": "s", "width": 90, "height": 90}, {"file_id": "L", "width": 1280, "height": 960}]}
    assert gi.media_file_id(big) == "L" and gi.media_file_id({"video": {"file_id": "v"}}) == "v"
    assert gi.media_file_id({"text": "hi"}) is None


def test_forward_markers_are_dropped_only_to_identify_the_forwarder():
    m = {"forward_origin": {"type": "user"}, "forward_from": {"id": 9}, "sender_chat": {"id": 1}, "from": {"id": 5}, "photo": []}
    out = gi.without_forward(m)
    assert set(out) == {"from", "photo"} and "forward_origin" in m          # the original is untouched
