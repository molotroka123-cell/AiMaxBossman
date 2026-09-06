"""Forbidden cwd must fail before ASK; preflight is not effect authorization."""
from dataclasses import replace
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import settings_kv
from bcc.features.tools_terminal import SPECS, ROOTS_KEY
from bcc.tools import ToolContext, ToolResult, context_denial, decide_effect, execute_tool
from bcc.v2 import scratch

SPEC = next(s for s in SPECS if s.name == 'terminal.run')


def ctx(env, **task):
    return ToolContext(svc=env.svc, task={'id': 901, **task}, run_id=1,
                       agent={'id': 902, 'permissions': ['terminal.run']})


async def roots(env, root: Path):
    import json
    async with env.svc.db.session() as session:
        await session.execute(sa.delete(settings_kv).where(settings_kv.c.key == ROOTS_KEY))
        await session.execute(sa.insert(settings_kv).values(
            key=ROOTS_KEY, value_enc=env.svc.vault.encrypt(json.dumps([str(root)]))))
        await session.commit()


@pytest.mark.parametrize('mode', ['sandbox', 'project_host', 'system_admin'])
async def test_outside_root_is_denied_without_launch(env, tmp_path, mode):
    await roots(env, tmp_path / 'allowed')
    context = ctx(env)
    args = {'command': 'python mutate.py', 'cwd': str(tmp_path / 'forbidden'), 'mode': mode}
    assert await context_denial(SPEC, args, context)
    called = []
    async def handler(args, context):
        called.append(args)
        return ToolResult(content='incorrectly dispatched')
    result = await execute_tool(replace(SPEC, handler=handler), args, context)
    assert result.error and called == []


async def test_allowed_path_cannot_become_authority_after_revoke(env, tmp_path):
    allowed = tmp_path / 'allowed'
    await roots(env, allowed)
    context = ctx(env)
    args = {'command': 'python mutate.py', 'cwd': str(allowed)}
    assert await context_denial(SPEC, args, context) is None
    assert decide_effect(SPEC, args, context.agent)[0] == 'ask'
    await roots(env, tmp_path / 'other')
    called = []
    async def handler(args, context):
        called.append(True)
        return ToolResult()
    assert (await execute_tool(replace(SPEC, handler=handler), args, context)).error
    assert not called


async def test_own_scratch_preflight_has_no_creation_side_effect(env):
    context = ctx(env)
    own = scratch.for_context(context)
    assert not own.exists()
    assert await context_denial(SPEC, {'command': 'git status', 'cwd': 'scratch'}, context) is None
    assert not own.exists()
    assert decide_effect(SPEC, {'command': 'git status', 'cwd': 'scratch'}, context.agent)[0] == 'auto'


async def test_other_scratch_denied_even_with_shared_root(env):
    context = ctx(env)
    await roots(env, env.settings.data_dir)
    other = scratch.owner_dir(env.settings, task_id=999, agent_id=777)
    assert await context_denial(SPEC, {'command': 'git status', 'cwd': str(other)}, context)
    assert not other.exists()


@pytest.mark.parametrize('kind', ['raise', 'invalid'])
async def test_context_check_failure_refuses_without_handler(env, kind):
    called = []
    async def check(args, context):
        if kind == 'raise':
            raise OSError('private diagnostic must not leak')
        return True
    async def handler(args, context):
        called.append(True)
        return ToolResult()
    spec = replace(SPEC, handler=handler, context_deny=check)
    result = await execute_tool(spec, {'command': 'git status'}, ctx(env))
    assert result.error and not called
    assert 'private diagnostic' not in result.content


async def test_context_cancellation_propagates(env):
    import asyncio
    async def check(args, context):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await context_denial(replace(SPEC, context_deny=check), {}, ctx(env))


def test_context_policy_identity_is_part_of_approval_identity():
    async def other_check(args, context):
        return None
    assert replace(SPEC, context_deny=other_check).impl_fingerprint != SPEC.impl_fingerprint
