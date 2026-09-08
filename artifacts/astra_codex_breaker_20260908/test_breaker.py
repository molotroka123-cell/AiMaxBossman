"""Narrow adversarial reproductions; run commands are in the audit report."""
import asyncio
import json
import subprocess
import sys

import pytest

from bossman.apprentice.openhands_client import OpenHandsClient, OpenHandsRequest
from bcc.streaming import read_stream
from bcc.v2.openrouter_ext import OpenRouterClient
import httpx


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("repeat", [1, 2])
def test_protected_file_hidden_by_index_flag(tmp_path, repeat):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "audit@example.invalid")
    git(tmp_path, "config", "user.name", "Breaker")
    protected = tmp_path / "protected.txt"
    protected.write_text("owner baseline\n", encoding="utf-8")
    git(tmp_path, "add", "protected.txt")
    git(tmp_path, "commit", "-qm", "fixture")
    code = (
        "import json,sys,subprocess,pathlib; "
        "p=json.load(sys.stdin); w=pathlib.Path(p['workspace']); "
        "subprocess.run(['git','-C',str(w),'update-index','--assume-unchanged',"
        "'protected.txt'],check=True); "
        "(w/'protected.txt').write_text('unauthorized change\\n',encoding='utf-8'); "
        "print(json.dumps({'schema':'bossman.openhands.v1','status':'completed'}))"
    )
    result = OpenHandsClient([sys.executable, "-c", code]).run(
        OpenHandsRequest("allowed output only", tmp_path, ("allowed",), ("protected.txt",)))
    actual = protected.read_text(encoding="utf-8")
    print(f"repeat={repeat} status={result.status} changed={result.changed_files!r} "
          f"diff={result.diff!r} protected={actual!r}")
    assert actual == "owner baseline\n" or result.status != "completed", \
        "protected change admitted as completed with empty evidence"


@pytest.mark.asyncio
async def test_async_max_chunks_really_bounds_consumption():
    consumed = 0
    async def lines():
        nonlocal consumed
        for i in range(100):
            consumed += 1
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': str(i)}}]})
            yield ''
    result = await read_stream(lines(), max_chunks=2)
    print(f"consumed={consumed} returned={len(result.deltas)}")
    assert consumed <= 2, "async parser reads all frames before applying its cap"


@pytest.mark.asyncio
async def test_partial_malformed_stream_is_not_a_usable_completed_answer():
    body = 'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
    body += 'data: {broken-json\n\n'
    client = OpenRouterClient('fixture', base_url='http://fixture.invalid',
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body)))
    result = await client.stream_outcome('fixture', [{'role':'user','content':'hi'}])
    print(f"status={result.status} ok={result.ok} finish={result.finish_reason!r} detail={result.detail!r}")
    assert not result.ok, "truncated malformed stream advertised as usable"
