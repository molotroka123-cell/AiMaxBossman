"""fs.write and fs.edit put exactly the agent's bytes on disk.

On Windows ``Path.write_text`` turns every LF into CRLF: the completion gate then
re-read a file that was not what the agent wrote and marked the write
unverified (owner scenario OS-08), and one fs.edit rewrote the line endings of
a whole LF file.
"""
from bossman.toolkit import ToolContext
from bossman.toolkit.files import fs_edit, fs_write


async def test_fs_write_puts_the_exact_utf8_bytes_on_disk(tmp_path):
    ctx = ToolContext(agent="coder", workdir=tmp_path)
    content = "зелёная сборка\nвторая строка\n"
    await fs_write({"path": "out.txt", "content": content}, ctx)
    assert (tmp_path / "out.txt").read_bytes() == content.encode("utf-8")


async def test_fs_edit_keeps_the_files_own_line_endings(tmp_path):
    ctx = ToolContext(agent="coder", workdir=tmp_path)
    (tmp_path / "lf.py").write_bytes(b"a = 1\nb = 2\nc = 3\n")
    (tmp_path / "crlf.py").write_bytes(b"a = 1\r\nb = 2\r\nc = 3\r\n")
    await fs_edit({"path": "lf.py", "old": "b = 2", "new": "b = 20"}, ctx)
    await fs_edit({"path": "crlf.py", "old": "b = 2", "new": "b = 20"}, ctx)
    assert (tmp_path / "lf.py").read_bytes() == b"a = 1\nb = 20\nc = 3\n"
    assert (tmp_path / "crlf.py").read_bytes() == b"a = 1\r\nb = 20\r\nc = 3\r\n"


async def test_a_multiline_fragment_still_matches_a_crlf_file(tmp_path):
    ctx = ToolContext(agent="coder", workdir=tmp_path)
    (tmp_path / "crlf.py").write_bytes(b"a = 1\r\nb = 2\r\nc = 3\r\n")
    await fs_edit({"path": "crlf.py", "old": "a = 1\nb = 2", "new": "a = 10\nb = 20"}, ctx)
    assert (tmp_path / "crlf.py").read_bytes() == b"a = 10\r\nb = 20\r\nc = 3\r\n"
