"""Отсутствующая модель и недоступный сервис отвечают человеку, а не трейсбеком.

Продолжение test_invalid_input_is_human: там — неверный ввод, здесь — два
состояния среды, в которые владелец попадает регулярно: модель, которой нет, и
сервис, который не отвечает (ComfyUI настроен, но порт закрыт). Проверяется на
процессе сервера (на приёмке — установленный архив) и через задачу до конца:
принятый POST ничего не доказывает, важно, чем задача КОНЧИЛАСЬ и что написано
владельцу.
"""
from __future__ import annotations

import socket
import time

import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login  # noqa: F401
from .test_invalid_input_is_human import LEAKS

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]


@pytest.fixture
def dead_comfy(monkeypatch):
    """ComfyUI «настроен», но за адресом никого нет: порт занят и тут же закрыт."""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv('BOSSMAN_COMFYUI_URL', f'http://127.0.0.1:{port}')
    monkeypatch.setenv('BOSSMAN_COMFYUI_CHECKPOINT', 'stub.safetensors')
    return port


@pytest.fixture
def live(dead_comfy, editor_server):
    return editor_server


def _api(page, method, path, body=None):
    return page.evaluate("""async ([method, path, body]) => {
      const r = await fetch(path, {method, credentials: 'include',
        headers: {'Content-Type': 'application/json', 'X-BCC-CSRF': localStorage.getItem('bcc.csrf')},
        body: body === null ? undefined : JSON.stringify(body)});
      const text = await r.text(); let data = null;
      try { data = JSON.parse(text); } catch (e) { data = text; }
      return {status: r.status, data};
    }""", [method, path, body])


def _finished(page, job_id, timeout=40.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = _api(page, 'GET', f'/api/images/jobs/{job_id}')['data']
        if job['status'] in ('completed', 'failed', 'cancelled'):
            return job
        time.sleep(0.3)
    return _api(page, 'GET', f'/api/images/jobs/{job_id}')['data']


def _human(text: str) -> None:
    assert text and text.strip(), 'отказ без единого слова'
    leaked = [m for m in LEAKS if m in text]
    assert not leaked, f'наружу ушли внутренности {leaked}: {text[:300]}'
    assert sum(ch.isalpha() for ch in text) > 10, text[:300]


@pytest.mark.parametrize('alias,label', [('no-such-model', 'отсутствующая модель'),
                                         ('comfyui', 'недоступный сервис (ComfyUI не отвечает)')])
def test_the_job_ends_failed_with_a_human_sentence(live, alias, label):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            reply = _api(page, 'POST', '/api/images/jobs',
                         {'prompt': label, 'model_alias': alias, 'width': 256, 'height': 256})
            if reply['status'] >= 400:
                # Отказ на входе — тоже приемлемо, если он человеческий.
                _human(str(reply['data']))
                return
            job = _finished(page, reply['data']['id'])
            assert job['status'] == 'failed', f'{label}: задача не кончилась отказом: {job}'
            _human(str(job.get('error') or ''))
            print(f'\n  {label}: «{job.get("error")}»')
        finally:
            browser.close()


def test_a_real_provider_still_completes(live):
    """Негативный контроль: иначе «всё failed» означало бы сломанную очередь."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page()
            _login(page, live)
            reply = _api(page, 'POST', '/api/images/jobs', {'prompt': 'контроль', 'model_alias': 'mock-image'})
            assert reply['status'] < 300, reply
            assert _finished(page, reply['data']['id'])['status'] == 'completed'
        finally:
            browser.close()
