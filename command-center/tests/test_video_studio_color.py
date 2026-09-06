"""Pixel-level SDR regression: matrix tags must describe the encoded samples."""
import json
import shutil

import pytest

from bcc.video_studio.media import MediaLibrary, binary, process
from bcc.video_studio.render import render_project
from bcc.video_studio.model import new_project, new_clip

pytestmark = pytest.mark.skipif(
    not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
    reason='real FFmpeg binaries required',
)


async def decoded_rgb(path):
    data, _ = await process([
        binary('ffmpeg'), '-v', 'error', '-i', str(path), '-frames:v', '1',
        '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1',
    ], binary_output=True)
    return data


def center_pixel(data, width, height, column):
    offset = ((height // 2) * width + (column * width // 6 + width // 12)) * 3
    return data[offset:offset + 3]


@pytest.mark.asyncio
@pytest.mark.parametrize('matrix', ['bt709', 'smpte170m'])
async def test_native_pixels_preserve_tagged_source_across_preview_sizes(tmp_path, matrix):
    # Six interior samples include saturated colors AND midtones; clipping a
    # wrong conversion to primary extremes cannot satisfy all these oracles.
    colors = [(210, 25, 35), (25, 210, 40), (35, 30, 210),
              (180, 100, 45), (65, 130, 185), (128, 128, 128)]
    ppm = tmp_path / 'colors.ppm'
    row = b''.join(bytes(colors[x * 6 // 360]) for x in range(360))
    ppm.write_bytes(b'P6\n360 180\n255\n' + row * 180)
    source = tmp_path / 'source.mp4'
    await process([
        binary('ffmpeg'), '-v', 'error', '-y', '-loop', '1', '-i', str(ppm),
        '-t', '0.4', '-r', '25', '-vf',
        f'scale=out_color_matrix={matrix}:out_range=tv,format=yuv420p',
        '-c:v', 'libx264', '-crf', '0', '-colorspace', matrix,
        '-color_range', 'tv', str(source),
    ])
    reference = await decoded_rgb(source)
    media = await MediaLibrary(tmp_path).import_file(source)
    project = new_project("colors", "SDR colors")
    project["media"] = {media["id"]: media}
    project["sequences"][0]["tracks"][0]["clips"] = [new_clip({"media_id": media["id"], "source_out": 400_000})]
    project['sequences'][0].update(width=360, height=180)
    project['sequences'][0]['tracks'][0]['clips'][0]['source_out'] = 400_000
    for width, height in [(360, 180), (180, 90)]:
        output = tmp_path / f'out-{width}.mp4'
        await render_project(project, tmp_path, output,
                             {'width': width, 'height': height, 'crf': 0})
        actual = await decoded_rgb(output)
        assert len(actual) == width * height * 3
        for column in range(6):
            expected = center_pixel(reference, 360, 180, column)
            observed = center_pixel(actual, width, height, column)
            assert max(abs(a - b) for a, b in zip(expected, observed)) <= 4, (
                matrix, width, column, tuple(expected), tuple(observed))
        raw, _ = await process([
            binary('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=color_space,color_range', '-of', 'json', str(output),
        ])
        stream = json.loads(raw)['streams'][0]
        assert stream['color_space'] == 'bt709'
        assert stream['color_range'] == 'tv'
