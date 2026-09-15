"""Real free-only installed-app UI/model smoke. No credentials or server logs in evidence.

Run with the release's Python -I. Exit 2 means OWNER_REQUIRED, never a live pass.
Six tasks maximum; UI default permits two retries (18 model attempts maximum).
Trajectories are regression examples, not evidence of training model weights.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile
import time

BASE_URL = 'https://openrouter.ai/api/v1'
TASKS = (
    ('arithmetic', 'Посчитай 17*23. Ответь только числом.'),
    ('structured_data', 'Return only valid JSON: an object with key city set to Prague and key count set to integer 3.'),
    ('instruction_following', 'Напиши ровно три строки без нумерации: сначала Прага, затем Брно, затем Острава.'),
)
TERMINAL = {'completed', 'failed', 'stopped', 'blocked', 'paused', 'waiting_approval'}


def free_models(catalog: list[dict]) -> list[dict]:
    """Require explicit zero for every advertised price and a :free identity.

    Unknown, negative, NaN, inf or additional nonzero charges fail closed.
    :free identities cannot silently turn into their paid sibling via fallback.
    """
    selected = {}
    for model in catalog:
        if not isinstance(model, dict):
            continue
        name, pricing = model.get('id'), model.get('pricing')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,200}:free', name):
            continue
        if not isinstance(pricing, dict) or not {'prompt', 'completion'} <= pricing.keys():
            continue
        try:
            prices = [Decimal(str(value)) for value in pricing.values()]
            if not all(value.is_finite() and value == 0 for value in prices):
                continue
        except (InvalidOperation, ValueError, TypeError):
            continue
        architecture = model.get('architecture', {})
        if not isinstance(architecture, dict):
            continue
        modalities = architecture.get('output_modalities', ['text'])
        if not isinstance(modalities, list):
            continue
        if 'text' not in modalities:
            continue
        selected[name] = model
    return [selected[name] for name in sorted(selected)[:2]]


def answer_ok(case: str, answer: str) -> bool:
    if case == 'arithmetic':
        return answer.strip() == '391'
    if case == 'structured_data':
        try:
            value = json.loads(answer)
            return value == {'city': 'Prague', 'count': 3} and type(value['count']) is int
        except (ValueError, TypeError):
            return False
    return case == 'instruction_following' and answer.strip().splitlines() == ['Прага', 'Брно', 'Острава']


def redact(value, secret: str):
    if isinstance(value, str):
        value = value.replace(secret, '[REDACTED]') if secret else value
        return re.sub(r'sk-or-v1-[A-Za-z0-9_-]+', '[REDACTED]', value)
    if isinstance(value, dict):
        return {str(key): redact(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    return value


def exercise(model, secret, args, report, trajectories):
    from bcc import owner_acceptance as owner
    from bcc.browser_runtime import chromium_executable
    from playwright.sync_api import sync_playwright

    report['stage'] = 'bundled_browser_discovery'
    executable = chromium_executable()
    if not executable:
        raise RuntimeError('Bundled Chromium unavailable')
    selected = {'secret': secret, 'provider': {'kind': 'openai_compat', 'base_url': BASE_URL},
                'model': {'name': model['id'], 'alias': model['id'], 'kind': 'cloud',
                          'context_window': model.get('context_length') or 8192,
                          'caps': '{}', 'price_in': 0, 'price_out': 0, 'pricing_known': True}}
    with tempfile.TemporaryDirectory(prefix='bossman-free-live-') as folder:
        data = Path(folder)
        os.chmod(data, 0o700)
        owner._restrict_to_owner(data)
        asyncio.run(owner.seed_private_registry(data, selected))
        selected['secret'] = None
        port = owner.free_port()
        with (data / 'private-server.log').open('wb') as log:
            report['stage'] = 'isolated_app_start'
            process = owner.launch(data, port, log)
            client = None
            saved = []
            try:
                client, initial_identity = owner.connect(process, data, port)
                response = client.patch('/api/agents/1', json={
                    'max_steps': 1, 'max_tokens': 512, 'fallback_model_id': None,
                    'tools': [], 'permissions': {},
                    'system_prompt': 'Follow the user format exactly. Answer directly. Do not use tools.'})
                response.raise_for_status()
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(executable_path=executable, headless=True)
                    try:
                        page = browser.new_page()
                        page.set_default_timeout(30000)
                        report['stage'] = 'ui_login'
                        page.goto(f'http://127.0.0.1:{port}/')
                        page.locator('#login-token').fill((data / owner.TOKEN_FILE).read_text().strip())
                        page.locator('#login-submit').click()
                        page.locator('#shell:not([hidden])').wait_for()
                        for case, prompt in TASKS:
                            report['stage'] = 'free_tariff_revalidation:' + case
                            # Revalidate tariff immediately before every new task.
                            import httpx
                            with httpx.Client(timeout=30, follow_redirects=False) as remote:
                                catalog = remote.get(BASE_URL + '/models')
                                catalog.raise_for_status()
                                if not free_models([item for item in catalog.json()['data'] if item.get('id') == model['id']]):
                                    raise RuntimeError('Selected free tariff is no longer available')
                            report['stage'] = 'ui_task_submission:' + case
                            page.goto(f'http://127.0.0.1:{port}/#/tasks')
                            page.get_by_placeholder('Что должен сделать BOSSMAN?').fill(prompt)
                            with page.expect_response(lambda r: r.url.endswith('/api/tasks') and r.request.method == 'POST') as creation:
                                page.locator('.composer').get_by_role('button', name='Запустить', exact=True).click()
                            if not creation.value.ok:
                                raise RuntimeError('UI task creation failed')
                            task_id = creation.value.json()['task']['id']
                            report['stage'] = 'real_model_result:' + case
                            deadline = time.monotonic() + args.timeout
                            result = {}
                            while time.monotonic() < deadline:
                                response = client.get(f'/api/tasks/{task_id}')
                                response.raise_for_status()
                                result = response.json()
                                if result['task']['status'] in TERMINAL:
                                    break
                                time.sleep(0.5)
                            runs = result.get('runs') or []
                            answer = str(result.get('result') or '')
                            passed = (result.get('task', {}).get('status') == 'completed'
                                      and bool(runs) and len(runs) <= 3
                                      and runs[-1].get('status') == 'completed'
                                      and all(run.get('model_alias') == model['id'] for run in runs)
                                      and all(run.get('cost_usd') in (0, 0.0) for run in runs)
                                      and answer_ok(case, answer))
                            record = {'source_sha': report['identity']['source_sha'], 'model': model['id'],
                                      'case': case, 'prompt': prompt, 'answer': answer[:8192],
                                      'task_id': task_id, 'task_status': result.get('task', {}).get('status'),
                                      'status': 'PASS' if passed else 'FAIL', 'restart_persistence': 'NOT_RUN',
                                      'usage': [{key: run.get(key) for key in ('tokens_in', 'tokens_out', 'cost_usd', 'model_alias')} for run in runs],
                                      'training_weights_updated': False}
                            trajectories.append(record)
                            saved.append((task_id, result, record))
                            if result.get('task', {}).get('status') not in TERMINAL:
                                raise RuntimeError('Task timed out; server stopped to prevent further work')
                    finally:
                        browser.close()
                client.close()
                client = None
                report['stage'] = 'restart_persistence'
                owner.stop(process)
                process = owner.launch(data, port, log)
                client, restarted_identity = owner.connect(process, data, port)
                for task_id, before, record in saved:
                    response = client.get(f'/api/tasks/{task_id}')
                    response.raise_for_status()
                    after = response.json()
                    persistent = (initial_identity.get('started_at') != restarted_identity.get('started_at')
                                  and before.get('result') == after.get('result')
                                  and before['task']['status'] == after['task']['status']
                                  and [run['id'] for run in before.get('runs', [])] == [run['id'] for run in after.get('runs', [])])
                    record['restart_persistence'] = 'PASS' if persistent else 'FAIL'
                    if not persistent:
                        record['status'] = 'FAIL'
            finally:
                if client is not None:
                    client.close()
                owner.stop(process)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trajectories', type=Path)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 180:
        parser.error('--timeout must be in 1..180 seconds')
    if re.fullmatch(r'[0-9a-f]{40}', args.expected_sha) is None:
        parser.error('--expected-sha must be a full lowercase commit SHA')
    report = {'status': 'FAIL', 'source_sha': args.expected_sha,
              'scope': 'installed UI to real free-model task and restart smoke',
              'max_tasks': 6, 'max_model_attempts': 18, 'max_output_tokens_per_attempt': 512,
              'paid_fallback': False, 'training_weights_updated': False,
              'target_hardware_verified': False, 'tasks': []}
    secret = os.environ.pop('BOSSMAN_OPENROUTER_API_KEY', '').strip()
    # Prevent automatic provider bootstrap and child-process key inheritance.
    os.environ.pop('OPENROUTER_API_KEY', None)
    trajectories = []
    try:
        if not secret:
            report.update(status='OWNER_REQUIRED', reason='Set BOSSMAN_OPENROUTER_API_KEY as an Actions secret.')
        else:
            report['stage'] = 'installed_identity'
            from bcc.owner_acceptance import installed_identity
            report['identity'] = installed_identity()
            if report['identity']['source_sha'] != args.expected_sha:
                raise RuntimeError('Installed source SHA mismatch')
            import httpx
            report['stage'] = 'free_model_catalog'
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                response = client.get(BASE_URL + '/models')
                response.raise_for_status()
                models = free_models(response.json()['data'])
            report['models'] = [model['id'] for model in models]
            if len(models) != 2:
                report.update(status='OWNER_REQUIRED', reason='Two explicitly zero-priced :free text models are required.')
            else:
                for model in models:
                    exercise(model, secret, args, report, trajectories)
                report['status'] = 'PASS' if len(trajectories) == 6 and all(
                    row['status'] == 'PASS' and row['restart_persistence'] == 'PASS'
                    for row in trajectories) else 'FAIL'
                report['stage'] = 'complete'
    except Exception as exc:
        # Provider/browser errors may embed credentials or private request data.
        report.update(status='FAIL', error_type=type(exc).__name__)
    finally:
        report['tasks'] = trajectories
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(redact(report, secret), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if args.trajectories:
            args.trajectories.parent.mkdir(parents=True, exist_ok=True)
            args.trajectories.write_text(''.join(json.dumps(redact(row, secret), ensure_ascii=False) + '\n' for row in trajectories), encoding='utf-8')
    print('BOSSMAN_FREE_LIVE=' + report['status'])
    return {'PASS': 0, 'FAIL': 1, 'OWNER_REQUIRED': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
