"""RT-04: FFmpeg понимает ПРОТОКОЛЫ, а containment по путям — нет.

`http://127.0.0.1/x.mp4` pathlib разрешает в `<workdir>/http:/127.0.0.1/x.mp4`,
то есть ВНУТРИ рабочей папки, — и проверка содержания говорила «можно». FFmpeg
читает ту же строку как сетевой URL. Точно так же проезжали `file:`, `concat:`,
`tcp:`, `data:` и `pipe:`.

Вторая половина: признаком пути было расширение, поэтому операнд `input` без
расширения не проверялся вовсе, хотя мог быть symlink наружу.

Ни один тест не запускает ffmpeg и не ходит в сеть: подменён `_run`, поэтому
проверяется ровно граница — что дошло бы до бинарника.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bossman.toolkit import ToolContext
import bossman.toolkit.media as media

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def stand(tmp_path: Path, monkeypatch):
    reached: list[list[str]] = []

    async def _never_executes(argv, timeout=900, cwd=None):
        reached.append(list(argv))
        return 0, ""

    monkeypatch.setattr(media, "_run", _never_executes)
    workdir = tmp_path / "workspace" / "coder"
    workdir.mkdir(parents=True)
    secret = tmp_path / "canary_secret.txt"
    secret.write_text("CANARY-DO-NOT-READ\n", encoding="utf-8")
    (workdir / "in.mp4").write_bytes(b"\x00" * 16)
    return workdir, secret, reached, ToolContext(agent="coder", workdir=workdir, run_id=1)


@pytest.mark.parametrize("operand", [
    "http://127.0.0.1:9/x.mp4",
    "https://127.0.0.1:9/x.mp4",
    "tcp://127.0.0.1:9",
    "udp://127.0.0.1:9",
    "rtsp://127.0.0.1:9/s",
    "ftp://127.0.0.1:9/x",
    "data:audio/wav;base64,AAAA",
    "pipe:0",
    "subfile,,start,0,end,10,:/etc/hostname",
])
async def test_no_protocol_operand_reaches_ffmpeg(stand, operand):
    """Ни один сетевой/устройственный/псевдо-протокол не доходит до бинарника."""
    _, _, reached, ctx = stand
    result = await media.ffmpeg({"args": ["-i", operand, "out.mp4"]}, ctx)
    assert result.error, f"{operand} дошёл до ffmpeg"
    assert reached == [], f"{operand} запустил процесс"


async def test_file_protocol_cannot_name_a_target_outside(stand):
    _, secret, reached, ctx = stand
    result = await media.ffmpeg({"args": ["-i", f"file:{secret}", "out.mp4"]}, ctx)
    assert result.error and reached == []


async def test_concat_protocol_cannot_name_targets_outside(stand):
    _, secret, reached, ctx = stand
    result = await media.ffmpeg(
        {"args": ["-i", f"concat:{secret}|{secret}", "out.mp4"]}, ctx)
    assert result.error and reached == []


async def test_protocol_output_is_refused(stand):
    workdir, secret, reached, ctx = stand
    result = await media.ffmpeg(
        {"args": ["-i", "in.mp4", f"file:{secret.parent}/out.mp4"]}, ctx)
    assert result.error and reached == []


async def test_extensionless_input_symlink_is_refused(stand):
    """Раньше признаком пути было расширение, поэтому `input` не проверялся."""
    workdir, secret, reached, ctx = stand
    (workdir / "input").symlink_to(secret)
    result = await media.ffmpeg({"args": ["-i", "input", "out.mp4"]}, ctx)
    assert result.error and reached == []


async def test_extensionless_output_symlink_is_refused(stand):
    workdir, secret, reached, ctx = stand
    (workdir / "output").symlink_to(secret.parent / "escaped")
    result = await media.ffmpeg({"args": ["-i", "in.mp4", "output"]}, ctx)
    assert result.error and reached == []


async def test_output_symlink_leaving_the_workdir_is_refused(stand):
    workdir, secret, reached, ctx = stand
    (workdir / "out.mp4").symlink_to(secret.parent / "escaped.mp4")
    result = await media.ffmpeg({"args": ["-i", "in.mp4", "out.mp4"]}, ctx)
    assert result.error and reached == []


async def test_probe_and_vision_refuse_protocols_too(stand):
    _, _, _, ctx = stand
    assert (await media.probe({"path": "http://127.0.0.1:9/x.mp4"}, ctx)).error
    assert (await media.vision_describe({"path": "file:/etc/hostname"}, ctx)).error


async def test_an_ordinary_transcode_inside_the_workdir_still_runs(stand):
    """Положительный контроль: строгость не должна убить сам инструмент."""
    _, _, reached, ctx = stand
    result = await media.ffmpeg(
        {"args": ["-i", "in.mp4", "-c", "copy", "-preset", "veryfast", "out.mp4"]}, ctx)
    assert not result.error, result.content
    assert len(reached) == 1
    assert "in.mp4" in reached[0] and "out.mp4" in reached[0]
    # Не-путевые операнды (кодек, пресет) проходят как есть и путями не считаются.
    assert "copy" in reached[0] and "veryfast" in reached[0]
