"""Пульт владельца: один путь исполнения, привязанные подтверждения, СТОП.

Всё офлайн: Bossman и Telegram — httpx.MockTransport, локальных моделей нет.
Каждое требование раздела 5 ТЗ владельца проверяется отдельным тестом.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from bcc.telegram_companion.adapters import Core, Telegram, approval_digest
from bcc.telegram_companion.config import CompanionError, Person, Settings
from bcc.telegram_companion.console import APPROVE_TTL_S, CONSOLE_OFF
from bcc.telegram_companion.service import Companion, failure_text
from bcc.telegram_companion.store import Store

OWNER = Person(11111, 11111, 'owner', None)
GUEST = Person(22222, 22222, 'guest', None)
STRANGER = 99999
CORE = 'http://127.0.0.1:8800'
ACTOR = f'tg:user:{OWNER.user_id}@chat:{OWNER.chat_id}'


def approval(aid=7, kind='computer.act', preview='Удалить файл C:/отчёт.docx', task_id=3):
    return {'id': aid, 'kind': kind, 'preview': preview, 'task_id': task_id, 'run_id': None,
            'status': 'pending', 'decided_by': None, 'decided_at': None,
            'created_at': '2026-09-22T10:00:00'}


class Bossman:
    """Минимальный Bossman на mock-транспорте: очередь approvals + Computer Use."""

    def __init__(self, approvals=None, *, available=True, stopped=False, outcome_unknown=None,
                 observe_age=0.0, frame=None, offline=False):
        self.rows = {r['id']: dict(r) for r in (approvals or [])}
        self.available, self.stopped, self.outcome_unknown = available, stopped, outcome_unknown
        self.observe_age, self.frame, self.offline = observe_age, frame, offline
        self.calls = []
        self.generation = 1000
        self.telegram = []

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if 'api.telegram.org' in url:
            self.telegram.append((url.rsplit('/', 1)[-1], body))
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': len(self.telegram)}})
        self.calls.append((request.method, path))
        if self.offline:
            raise httpx.ConnectError('bossman unreachable')
        if path == '/health/live':
            return httpx.Response(200, json={'app': 'bossman-command-center', 'alive': True})
        if path == '/api/learning':
            return httpx.Response(200, json=[{'agent_id': 1, 'capability': 'browser', 'lesson': 'не кликать вслепую'}])
        if path == '/api/approvals' and request.method == 'GET':
            want = request.url.params.get('status', 'pending')
            rows = list(self.rows.values())
            if want not in ('all', ''):
                rows = [r for r in rows if r['status'] in want.split(',')]
            return httpx.Response(200, json=rows)
        if path.startswith('/api/approvals/') and request.method == 'POST':
            aid = int(path.rsplit('/', 1)[-1])
            row = self.rows.get(aid)
            if row is None:
                return httpx.Response(404, json={'message': 'нет'})
            if row['status'] == 'pending':
                row['status'] = 'approved' if body.get('approve') else 'rejected'
                row['decided_by'] = body.get('by')
            return httpx.Response(200, json=row)
        if path == '/api/computer/status':
            return httpx.Response(200, json={'available': self.available, 'detail': 'фикстура',
                                             'stopped': self.stopped, 'generation': self.generation,
                                             'session': 'abc', 'outcome_unknown': self.outcome_unknown,
                                             'tools': ['computer.observe']})
        if path == '/api/computer/observe':
            if self.stopped:
                return httpx.Response(200, json={'generation': self.generation, 'observed_at': time.time(),
                                                 'window': {}, 'elements': [], 'stopped': True})
            self.generation += 1
            return httpx.Response(200, json={'generation': self.generation, 'session': 'abc',
                                             'observed_at': time.time() - self.observe_age,
                                             'window': {'title': 'Блокнот'}, 'elements': [{'name': 'Файл'}],
                                             'screenshot': self.frame, 'stopped': False})
        if path == '/api/computer/stop':
            self.stopped = True
            return httpx.Response(200, json={'stopped': True, 'persisted': True})
        if path == '/api/computer/resume':
            self.stopped = False
            self.generation += 1
            return httpx.Response(200, json={'stopped': False, 'generation': self.generation})
        return httpx.Response(404, json={'message': 'нет такого'})


def cfg(**kw):
    return Settings((OWNER, GUEST), local_url='http://127.0.0.1:8083/v1', local_model='best',
                    core_url=CORE, bot_token='bot-fixture', pc_control=True, **kw)


def build(tmp_path, bossman: Bossman, **kw):
    settings = cfg(**kw)
    transport = bossman.transport()
    store = Store(tmp_path)
    app = Companion(settings, store, Telegram(settings, transport=transport),
                    Core(settings, transport=transport), None)
    return app


def msg(person=OWNER, text='/menu', update_id=1, **extra):
    return {'from': {'id': person.user_id, 'is_bot': False},
            'chat': {'id': person.chat_id, 'type': 'private'},
            'text': text, '_update_id': update_id, **extra}


def run(app, coro_factory):
    async def go():
        try:
            return await coro_factory()
        finally:
            await app.telegram.close()
            await app.core.close()
            app.store.close()
    return asyncio.run(go())


def commands_of(reply):
    """(label, команда) для каждой кнопки: opaque-токены разворачиваем локально."""
    return reply.keyboard


def press(app, reply, label_part):
    for row in reply.keyboard or []:
        for label, data in row:
            if label_part in label:
                return app.buttons[data[2:]][1]
    raise AssertionError(f'кнопки {label_part!r} нет: {[l for r in (reply.keyboard or []) for l, _ in r]}')


# ------------------------------------------------------------------ 1. меню
def test_main_menu_is_russian_console_for_owner_only(tmp_path):
    bossman = Bossman()
    app = build(tmp_path, bossman)
    labels = [label for row in app.main_menu(OWNER) for label, _ in row]
    for expected in ('Статус', 'Очередь', 'Новая задача', 'Экран', 'Открыть приложение/папку',
                     'Файлы проекта', 'Фото', 'Видео', 'Подтвердить', 'Пауза', 'СТОП', 'Продолжить',
                     'Диагностика', 'уроки', 'исправление'):
        assert any(expected in label for label in labels), expected
    assert len(app.main_menu(OWNER)) <= 8         # Telegram-разметка компаньона режет сверх 8 рядов
    tokens = {data for row in app.main_menu(OWNER) for _, data in row}
    assert all(data.startswith('b:') for data in tokens)            # opaque, без текста команды
    assert all(app.buttons[d[2:]][0] == OWNER.key for d in tokens)  # привязано к человеку
    guest_labels = [label for row in app.main_menu(GUEST) for label, _ in row]
    assert not any('СТОП' in label or 'Подтвердить' in label for label in guest_labels)
    run(app, lambda: asyncio.sleep(0))


# --------------------------------------------- 2. привязка подтверждения
def test_approval_binds_owner_chat_action_digest_ttl_and_nonce(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        listing = await app.handle(OWNER, msg(text='/approvals'))
        assert 'Удалить файл' in listing and '#7' in listing
        command = press(app, listing, 'Разрешить #7')
        nonce = command.split()[1]
        gate = app.store.open_gate(OWNER.key, nonce)      # подглядываем содержимое ворот
        assert gate == {'approval_id': 7, 'kind': 'computer.act', 'chat_id': OWNER.chat_id,
                        'user_id': OWNER.user_id, 'decision': True,
                        'digest': approval_digest(approval())}
        # Второй раз тот же nonce не потребляется: одноразовый.
        with pytest.raises(CompanionError, match='APPROVAL_GATE_EXPIRED_OR_USED'):
            app.store.open_gate(OWNER.key, nonce)
        # И чужой ключ его не откроет даже свежим.
        other = press(app, await app.handle(OWNER, msg(text='/approvals', update_id=2)), 'Разрешить #7')
        with pytest.raises(CompanionError, match='APPROVAL_GATE_EXPIRED_OR_USED'):
            app.store.open_gate(GUEST.key, other.split()[1])
    run(app, scenario)


def test_approval_applies_once_with_owner_actor(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        listing = await app.handle(OWNER, msg(text='/approvals'))
        command = press(app, listing, 'Разрешить #7')
        reply = await app.handle(OWNER, msg(text=command, update_id=3))
        assert 'Разрешено' in reply and ACTOR in reply
        assert bossman.rows[7]['status'] == 'approved' and bossman.rows[7]['decided_by'] == ACTOR
        # Повтор той же кнопки не повторяет эффект.
        with pytest.raises(CompanionError, match='APPROVAL_GATE_EXPIRED_OR_USED'):
            await app.handle(OWNER, msg(text=command, update_id=4))
        assert [c for c in bossman.calls if c[0] == 'POST' and c[1].startswith('/api/approvals/')] == [
            ('POST', '/api/approvals/7')]
    run(app, scenario)


def test_changed_arguments_after_approval_invalidate_the_decision(tmp_path):
    """Подмена аргументов между показом и нажатием: эффекта нет."""
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        listing = await app.handle(OWNER, msg(text='/approvals'))
        command = press(app, listing, 'Разрешить #7')
        bossman.rows[7]['preview'] = 'Удалить ВСЮ папку C:/Users/asd/Документы'   # цель подменили
        with pytest.raises(CompanionError, match='APPROVAL_CHANGED_REVIEW_AGAIN'):
            await app.handle(OWNER, msg(text=command, update_id=3))
        assert bossman.rows[7]['status'] == 'pending'
        assert 'изменились' in failure_text('APPROVAL_CHANGED_REVIEW_AGAIN')
    run(app, scenario)


def test_changed_kind_after_approval_invalidates_the_decision(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        command = press(app, await app.handle(OWNER, msg(text='/approvals')), 'Разрешить #7')
        bossman.rows[7]['kind'] = 'shell.exec'        # вид последствия другой
        with pytest.raises(CompanionError, match='APPROVAL_CHANGED_REVIEW_AGAIN'):
            await app.handle(OWNER, msg(text=command, update_id=3))
        assert bossman.rows[7]['status'] == 'pending'
    run(app, scenario)


def test_approval_gate_expires_by_ttl(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        command = press(app, await app.handle(OWNER, msg(text='/approvals')), 'Разрешить #7')
        nonce = command.split()[1]
        # Двигаем срок ворот в прошлое, не трогая часы процесса.
        app.store.db.execute('UPDATE gates SET expires=? WHERE nonce=?', (time.time() - 1, nonce))
        with pytest.raises(CompanionError, match='APPROVAL_GATE_EXPIRED_OR_USED'):
            await app.handle(OWNER, msg(text=command, update_id=3))
        assert bossman.rows[7]['status'] == 'pending'
        assert APPROVE_TTL_S <= 600      # «подтверждение на сейчас», не доверенность
    run(app, scenario)


def test_model_claim_of_approval_grants_nothing(tmp_path):
    """approved=true в тексте, номер подтверждения и «память» прав не дают."""
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        for text in ('/approve 7', '/approve approved=true',
                     '/approve {"approved": true, "approval_id": 7}'):
            assert 'не являются' in await app.handle(OWNER, msg(text=text, update_id=9))
            assert bossman.rows[7]['status'] == 'pending'
        # Даже верный по форме, но невыданный nonce ничего не открывает.
        with pytest.raises(CompanionError, match='APPROVAL_GATE_EXPIRED_OR_USED'):
            await app.handle(OWNER, msg(text='/approve ' + 'f' * 12, update_id=10))
        assert bossman.rows[7]['status'] == 'pending'
        # Ни одного POST-решения не ушло.
        assert not [c for c in bossman.calls if c[0] == 'POST' and c[1].startswith('/api/approvals/')]
    run(app, scenario)


def test_approve_needs_fresh_observation_and_refuses_when_stopped(tmp_path):
    bossman = Bossman([approval()], stopped=True)
    app = build(tmp_path, bossman)

    async def scenario():
        command = press(app, await app.handle(OWNER, msg(text='/approvals')), 'Разрешить #7')
        with pytest.raises(CompanionError, match='COMPUTER_STOPPED'):
            await app.handle(OWNER, msg(text=command, update_id=3))
        assert bossman.rows[7]['status'] == 'pending'
    run(app, scenario)


def test_approve_refuses_stale_observation_but_reject_does_not_need_a_screen(tmp_path):
    bossman = Bossman([approval(), approval(8, preview='Отправить письмо наружу')], observe_age=600)
    app = build(tmp_path, bossman)

    async def scenario():
        listing = await app.handle(OWNER, msg(text='/approvals'))
        with pytest.raises(CompanionError, match='OBSERVATION_STALE'):
            await app.handle(OWNER, msg(text=press(app, listing, 'Разрешить #7'), update_id=3))
        assert bossman.rows[7]['status'] == 'pending'
        # Отклонение только уменьшает полномочия — свежий экран для него не нужен.
        reply = await app.handle(OWNER, msg(text=press(app, listing, 'Отклонить #8'), update_id=4))
        assert 'Отклонено' in reply and bossman.rows[8]['status'] == 'rejected'
    run(app, scenario)


def test_approve_refuses_when_last_step_outcome_is_unknown(tmp_path):
    bossman = Bossman([approval()], outcome_unknown='адаптер не ответил')
    app = build(tmp_path, bossman)

    async def scenario():
        command = press(app, await app.handle(OWNER, msg(text='/approvals')), 'Разрешить #7')
        with pytest.raises(CompanionError, match='LAST_STEP_OUTCOME_UNKNOWN'):
            await app.handle(OWNER, msg(text=command, update_id=3))
        assert bossman.rows[7]['status'] == 'pending'
    run(app, scenario)


def test_already_decided_approval_is_not_decided_again(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        command = press(app, await app.handle(OWNER, msg(text='/approvals')), 'Разрешить #7')
        bossman.rows[7]['status'] = 'approved'          # решили в приложении Bossman
        bossman.rows[7]['decided_by'] = 'owner'
        with pytest.raises(CompanionError, match='APPROVAL_ALREADY_DECIDED'):
            await app.handle(OWNER, msg(text=command, update_id=3))
        assert bossman.rows[7]['decided_by'] == 'owner'
    run(app, scenario)


# ---------------------------------------------- 3. низкорисковые действия
def test_low_risk_reads_need_no_dialog_and_never_decide(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        queue = await app.handle(OWNER, msg(text='/queue'))
        assert 'Ожидают подтверждения: 1' in queue and '#7' in queue
        diag = await app.handle(OWNER, msg(text='/diag', update_id=2))
        assert 'Bossman: отвечает' in diag and 'СТОП: выключен' in diag
        lessons = await app.handle(OWNER, msg(text='/lessons', update_id=3))
        assert 'не кликать вслепую' in lessons
        assert bossman.rows[7]['status'] == 'pending'
        assert not [c for c in bossman.calls if c[0] == 'POST' and c[1].startswith('/api/approvals/')]
    run(app, scenario)


def test_allowed_screenshot_comes_from_bossman_observation(tmp_path):
    png = tmp_path / 'screen-17.png'
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
    bossman = Bossman(frame=str(png))
    app = build(tmp_path / 'home', bossman)
    sent = []

    async def capture(person, data, caption, kb=None):
        sent.append((person, data, caption))
    app.telegram.send_photo = capture

    async def scenario():
        assert await app.handle(OWNER, msg(text='/screen')) is None
        assert ('POST', '/api/computer/observe') in bossman.calls
        assert sent and sent[0][1].startswith(b'\x89PNG') and 'Блокнот' in sent[0][2]
        # Подсунутый путь мимо формата кадра не читается.
        assert app.read_frame(str(tmp_path / 'secret.txt')) is None
        assert app.read_frame(r'C:\Windows\System32\config\SAM') is None
    run(app, scenario)


# -------------------------------------------------- 4. СТОП / пауза / продолжить
def test_stop_works_under_load_without_llm_or_generator(tmp_path):
    """СТОП идёт по своей полосе и не ждёт ни модель, ни генерацию."""
    from bcc.telegram_companion.store import Store as _Store
    bossman = Bossman()
    app = build(tmp_path, bossman)
    app.image_job = {'id': 5, 'cancel': False, 'who': OWNER.key}
    app.models = None          # ни одной модели: СТОП обязан работать и так

    async def scenario():
        assert _Store.lane({'text': '/stop'}) == 'control'      # отдельная полоса от занятого чата
        reply = await app.handle(OWNER, msg(text='/stop'))
        assert 'СТОП' in reply
        assert app.image_job['cancel'] is True                  # отменяемое отменено
        assert bossman.stopped is True                          # /api/computer/stop применён
        assert app.store.get('delegation_locked') is True       # новые поручения заблокированы
        assert 'Уже совершённые внешние действия этим не отменяются' in reply
        # Пока СТОП включён, новое поручение не принимается.
        assert 'заблокированы' in await app.handle(OWNER, msg(text='/task сделай что-нибудь', update_id=2))
    run(app, scenario)


def test_pause_blocks_new_work_without_cancelling_running_generation(tmp_path):
    bossman = Bossman()
    app = build(tmp_path, bossman)
    app.image_job = {'id': 5, 'cancel': False, 'who': OWNER.key}

    async def scenario():
        reply = await app.handle(OWNER, msg(text='/pause'))
        assert 'Пауза' in reply and app.image_job['cancel'] is False
        assert app.store.get('delegation_locked') is True and bossman.stopped is True
    run(app, scenario)


def test_stop_reports_honestly_when_bossman_is_unreachable(tmp_path):
    bossman = Bossman(offline=True)
    app = build(tmp_path, bossman)

    async def scenario():
        reply = await app.handle(OWNER, msg(text='/stop'))
        assert 'НЕ подтвердил' in reply
        assert app.store.get('delegation_locked') is True   # локальный запрет всё равно действует
    run(app, scenario)


def test_resume_is_owner_only_and_requires_a_fresh_observation(tmp_path):
    bossman = Bossman(stopped=True)
    app = build(tmp_path, bossman)

    async def scenario():
        assert await app.handle(GUEST, msg(GUEST, '/resume')) == CONSOLE_OFF
        app.store.put('delegation_locked', True)
        bossman.available = False                     # наблюдать нечем
        refused = await app.handle(OWNER, msg(text='/resume', update_id=2))
        assert 'наблюдение' in refused and app.store.get('delegation_locked') is True
        bossman.available = True
        ok = await app.handle(OWNER, msg(text='/resume', update_id=3))
        assert 'Продолжено' in ok and 'Блокнот' in ok
        assert app.store.get('delegation_locked') is False
        assert ('POST', '/api/computer/observe') in bossman.calls
    run(app, scenario)


# ------------------------------------------------------- 5. идемпотентность
def test_replayed_update_and_callback_do_not_repeat_the_effect(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        update = {'update_id': 40, 'message': msg(text='/queue')}
        await app.ingest(update)
        await app.ingest(dict(update))                 # тот же update_id ещё раз
        pending = app.store.db.execute("SELECT count(*) FROM inbox WHERE id=40").fetchone()[0]
        assert pending == 1
        listing = await app.handle(OWNER, msg(text='/approvals', update_id=41))
        data = None
        for row in listing.keyboard:
            for label, value in row:
                if 'Разрешить #7' in label:
                    data = value
        cb = {'update_id': 42, 'callback_query': {'id': 'cb1', 'data': data,
              'from': {'id': OWNER.user_id, 'is_bot': False},
              'message': {'message_id': 5, 'chat': {'id': OWNER.chat_id, 'type': 'private'}}}}
        await app.ingest_callback(cb)
        await app.ingest_callback(dict(cb))            # повтор callback
        rows = app.store.db.execute("SELECT count(*) FROM inbox WHERE id=42").fetchone()[0]
        assert rows == 1
    run(app, scenario)


def test_edited_message_does_not_run_the_operation_again(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        await app.ingest({'update_id': 50, 'edited_message': msg(text='/stop')})
        queued = app.store.db.execute("SELECT count(*) FROM inbox").fetchone()[0]
        assert queued == 0
        assert app.store.get('offset') == 51            # приняли, но ничего не запустили
        assert bossman.stopped is False and app.store.get('delegation_locked') is None
    run(app, scenario)


def test_restart_between_approval_and_effect_leaves_it_unknown_not_replayed(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        command = press(app, await app.handle(OWNER, msg(text='/approvals')), 'Разрешить #7')
        nonce = command.split()[1]
        app.store.open_gate(OWNER.key, nonce)           # «упали» ровно между решением и эффектом
        app.store.recover()                             # перезапуск компаньона
        assert app.store.gate_phase(nonce) == 'decide_unknown'
        # Кнопки из прошлой жизни нет вовсе (buttons живут в памяти), а nonce сгорел.
        with pytest.raises(CompanionError, match='APPROVAL_GATE_EXPIRED_OR_USED'):
            await app.handle(OWNER, msg(text=command, update_id=5))
        assert bossman.rows[7]['status'] == 'pending'   # слепого повтора не было
    run(app, scenario)


def test_network_break_with_unknown_result_is_verified_not_repeated(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        command = press(app, await app.handle(OWNER, msg(text='/approvals')), 'Разрешить #7')
        nonce = command.split()[1]
        bossman.offline = True                          # связь рвётся до/во время решения
        with pytest.raises(CompanionError, match='NETWORK_UNAVAILABLE'):
            await app.handle(OWNER, msg(text=command, update_id=3))
        assert app.store.gate_phase(nonce) == 'decide_unknown'
        bossman.offline = False
        # Сверка, а не слепой повтор: старая кнопка мертва, состояние читается заново.
        with pytest.raises(CompanionError, match='APPROVAL_GATE_EXPIRED_OR_USED'):
            await app.handle(OWNER, msg(text=command, update_id=4))
        assert 'Ожидают подтверждения: 1' in await app.handle(OWNER, msg(text='/queue', update_id=5))
    run(app, scenario)


# --------------------------------------------------------- чужие и посторонние
def test_foreign_id_chat_group_and_forward_get_no_rights(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        listing = await app.handle(OWNER, msg(text='/approvals'))
        data = None
        for row in listing.keyboard:
            for label, value in row:
                if 'Разрешить #7' in label:
                    data = value
        stranger = {'from': {'id': STRANGER, 'is_bot': False}, 'chat': {'id': STRANGER, 'type': 'private'},
                    'text': '/stop', '_update_id': 60}
        group = {'from': {'id': OWNER.user_id, 'is_bot': False}, 'chat': {'id': -100, 'type': 'supergroup'},
                 'text': '/stop', '_update_id': 61}
        forwarded = {**msg(text='/stop', update_id=62), 'forward_origin': {'type': 'user'}}
        for body in (stranger, group, forwarded):
            assert app.authorized(body) is None
            await app.ingest({'update_id': body['_update_id'], 'message': body})
        assert app.store.db.execute("SELECT count(*) FROM inbox").fetchone()[0] == 0
        assert bossman.stopped is False
        # Кнопка владельца, нажатая чужим человеком: ничего не потребляется.
        await app.ingest_callback({'update_id': 63, 'callback_query': {
            'id': 'cbX', 'data': data, 'from': {'id': STRANGER, 'is_bot': False},
            'message': {'message_id': 5, 'chat': {'id': STRANGER, 'type': 'private'}}}})
        assert app.store.db.execute("SELECT count(*) FROM inbox").fetchone()[0] == 0
        assert bossman.rows[7]['status'] == 'pending'
    run(app, scenario)


def test_guest_cannot_reach_the_console_even_with_a_valid_looking_nonce(tmp_path):
    bossman = Bossman([approval()])
    app = build(tmp_path, bossman)

    async def scenario():
        command = press(app, await app.handle(OWNER, msg(text='/approvals')), 'Разрешить #7')
        assert await app.handle(GUEST, msg(GUEST, command, update_id=3)) == CONSOLE_OFF
        assert bossman.rows[7]['status'] == 'pending'
    run(app, scenario)


# ---------------------------------------------------------------- секреты
def test_console_replies_never_leak_the_bot_token(tmp_path):
    bossman = Bossman([approval(preview='токен bot-fixture в описании')])
    app = build(tmp_path, bossman)

    async def scenario():
        await app.handle(OWNER, msg(text='/approvals'))
        await app.telegram.send(OWNER, 'проверка bot-fixture')
        payloads = [json.dumps(body, ensure_ascii=False) for _, body in bossman.telegram]
        assert payloads and all('bot-fixture' not in p for p in payloads)
    run(app, scenario)
