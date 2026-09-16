"""Кнопка «Отмена» экспорта в Video Studio нажимается по-настоящему. Закрывает BL-057 для видео.

Управляемость — в тестовом окружении: перед настоящим ffmpeg в PATH сервера стоит
затвор. Он пропускает всё, кроме рендера (аргумент -frames:v), а рендер держит,
пока существует файл-барьер, и затем запускает НАСТОЯЩИЙ ffmpeg с теми же
аргументами. Боевой код не изменён: binary() продукта резолвит ffmpeg через PATH
в момент вызова, EditorServer копирует окружение в процесс сервера.

Доказывается через браузер: экспорт действительно начал рендер (ffmpeg запущен и
удерживается), «Отмена» останавливает именно его, соседний экспорт доходит до
конца, интерфейс показывает фактическое состояние, частичный результат не
объявлен готовым (файл отменённого — 409), повторный экспорт работает.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import editor_server, login as _login, change, snapshot  # noqa: F401

pytestmark = [pytest.mark.timeout(360),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]

POSIX_GATE = """#!/bin/sh
# Затвор рендера: держит только вызовы с -frames:v, пока существует барьер.
case " $* " in *" -frames:v "*)
  while [ -e "$BOSSMAN_TEST_FFMPEG_HOLD" ]; do sleep 0.2; done ;;
esac
exec "$BOSSMAN_TEST_REAL_FFMPEG" "$@"
"""
WINDOWS_GATE = """@echo off
echo %* | find "-frames:v" >nul
if errorlevel 1 goto run
:wait
if exist "%BOSSMAN_TEST_FFMPEG_HOLD%" (ping -n 2 127.0.0.1 >nul & goto wait)
:run
"%BOSSMAN_TEST_REAL_FFMPEG%" %*
"""


@pytest.fixture
def ffmpeg_gate(tmp_path, monkeypatch):
    real = shutil.which('ffmpeg')
    if not real:
        pytest.skip('ffmpeg is not installed on this host')
    gate = tmp_path / 'ffmpeg-gate'
    gate.mkdir()
    hold = tmp_path / 'hold-render'
    (gate / 'ffmpeg').write_text(POSIX_GATE, encoding='utf-8')
    (gate / 'ffmpeg').chmod(0o755)
    (gate / 'ffmpeg.cmd').write_text(WINDOWS_GATE, encoding='utf-8')
    monkeypatch.setenv('BOSSMAN_TEST_REAL_FFMPEG', real)
    monkeypatch.setenv('BOSSMAN_TEST_FFMPEG_HOLD', str(hold))
    monkeypatch.setenv('PATH', str(gate) + os.pathsep + os.environ.get('PATH', ''))
    yield hold


@pytest.fixture
def live(ffmpeg_gate, editor_server):        # порядок: затвор в PATH до старта сервера
    return editor_server


def _fixture_video(tmp_path):
    path = tmp_path / 'red-audio.mp4'
    subprocess.run([os.environ['BOSSMAN_TEST_REAL_FFMPEG'], '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                    'color=red:size=320x180:rate=30:duration=2', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=2', '-c:v', 'libx264',
                    '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)],
                   check=True, timeout=30)
    return path


def _export(page):
    page.get_by_role('button', name='Экспорт', exact=True).first.click()
    return change(page, '/exports', lambda: page.locator('dialog').get_by_role('button', name='Применить', exact=True).click())


def _wait(predicate, timeout=60.0, step=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def test_cancel_stops_the_rendering_export_only_and_partial_output_is_not_ready(live, ffmpeg_gate, tmp_path):
    from playwright.sync_api import sync_playwright, expect

    server, hold = live, ffmpeg_gate
    fixture = _fixture_video(tmp_path)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            context = browser.new_context(viewport={'width': 1720, 'height': 1100})
            page = context.new_page()
            _login(page, server)
            page.goto(server.url + '/#/video-studio')
            page.get_by_role('button', name='＋ Новый проект', exact=True).click()
            page.get_by_label('Название', exact=True).fill('Cancel proof')
            page.get_by_role('button', name='Применить', exact=True).click()
            page.locator('.vs-library input[type=file]').set_input_files(fixture)      # импорт — без затвора
            page.locator('.vs-media-card').first.wait_for(timeout=30000)
            page.locator('.vs-media-card').first.dblclick()
            page.locator('.vs-clip').first.wait_for()
            # Импорт ставит в очередь задачу «Подготовка медиа» (thumbnail →
            # proxy → waveform, два вызова с -frames:v), а видеозадачи движок
            # допускает по одной. На Windows-раннере (прогон 105) затвор был
            # взведён, пока она ещё шла: удержал ЕЁ thumbnail, и экспорт A так и
            # не был допущен — «running» не наступил за 60 с, в хвосте задания
            # остались два cmd + два PING. На Linux она успевала раньше. Барьер
            # взводится только после того, как подготовка завершилась.
            expect(page.locator('.vs-job', has_text='Подготовка медиа')).to_contain_text('completed', timeout=90000)

            hold.write_text('hold')                     # с этого момента рендер удерживается
            a = _export(page)['job_id']
            b = _export(page)['job_id']
            job = lambda jid: snapshot(context, f'{server.url}/api/video-studio/exports/{jid}')

            # Нужный экспорт действительно начал рендер и удерживается затвором.
            assert _wait(lambda: job(a)['status'] == 'running'), job(a)
            time.sleep(2.0)
            assert job(a)['status'] == 'running', 'затвор не удержал рендер — барьер пустой'

            # Импорт даёт свою карточку задачи; карточки экспорта берём по подписи.
            cards = page.locator('.vs-job', has_text='Экспорт')
            expect(cards).to_have_count(2)
            expect(cards.nth(0)).to_contain_text('running')
            with page.expect_response(lambda r: r.url.endswith(f'/exports/{a}/cancel') and r.request.method == 'POST') as seen:
                cards.nth(0).get_by_role('button', name='Отмена', exact=True).click()
            assert seen.value.ok

            # Отменён именно A; B не тронут и доходит до конца после отпускания затвора.
            assert _wait(lambda: job(a)['status'] in ('stopped', 'cancelled'), timeout=30), job(a)
            assert job(b)['status'] not in ('stopped', 'cancelled', 'failed'), job(b)
            hold.unlink()
            assert _wait(lambda: job(b)['status'] == 'completed', timeout=120), job(b)
            assert job(b)['verification']['decoded'] is True

            # Частичный результат не объявлен готовым: у A нет ссылки, файл — 409.
            assert job(a)['status'] in ('stopped', 'cancelled') and not job(a).get('output_url')
            refused = context.request.get(f'{server.url}/api/video-studio/exports/{a}/file')
            assert refused.status == 409, refused.status
            expect(page.locator('.vs-job a[download]')).to_have_count(1, timeout=30000)
            expect(cards.nth(0)).to_contain_text(re.compile('stopped|cancelled'))

            # Повторный запуск работает.
            c = _export(page)['job_id']
            assert _wait(lambda: job(c)['status'] == 'completed', timeout=120), job(c)
            expect(page.locator('.vs-job a[download]')).to_have_count(2, timeout=30000)
        finally:
            browser.close()
