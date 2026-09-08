"""A2-03 / A2-04: каноническое разрешение на управление компьютером ДОЛЖНО
проверяться на ГРАНИЦЕ ЭФФЕКТА, а не только при создании задачи.

Почему это важно. `access_check` (профильный тумблер `computer_control`)
вызывался РОВНО в одном месте — `ComputerOperatorManager.create_task`. Между
созданием строки задачи и отправкой ввода в рабочий стол проходит сколько
угодно времени: план у модели, ожидание подтверждения владельца, пауза,
перезапуск процесса и `recover_all`, «Продолжить» из UI. Разрешение,
истинное в момент создания, к моменту эффекта могло быть уже снято — владелец
выключил тумблер, выключил профиль, профиль удалён, gate вообще не поднят, —
а задача продолжала жать клавиши: проверка при создании НЕ является проверкой
на границе эффекта.

Отдельно проверяются НЕ-UI входы: `run()` по уже существующей строке
(«Продолжить»/восстановление после перезапуска) вообще не проходил ни одной
проверки доступа, и не-локальный `source`, для которого профильный gate
обязан быть fail-CLOSED, а не «как раньше локально».

Тесты гоняют ПРОДАКШН-обвязку (`subsystem.build_manager`) с фейковыми
планировщиком/наблюдателем/адаптером: проверяется наша авторизация, а не
чужой рабочий стол.
"""
from __future__ import annotations

import asyncio

import pytest

from bossman.computer_operator.adapters.router import ActionRouter
from bossman.computer_operator.models import (ActionKind, ComputerAction, ExpectedState,
                                              TaskMode, TaskState)
from bossman.computer_operator.policy import authorize_computer_control
from bossman.computer_operator.subsystem import build_manager
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner
from bossman.profiles import gate
from bossman.profiles.service import ProfileService, ProfilesUnavailable
from bossman.profiles.store import ProfileStore

SCREEN = "ok"


def click(target="Кнопка"):
    # Нейтральная подпись: лексикон последствий не должен поднимать approval,
    # иначе тест мерил бы совсем другую стену.
    return ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text=SCREEN),
                               target=target, args={"x": 10, "y": 10})


async def _approval_create(kind, preview, tool=None, payload=None):
    raise AssertionError("в этих тестах подтверждение не запрашивается")


async def _approval_wait(approval_id, timeout_s=None):
    raise AssertionError("в этих тестах подтверждение не запрашивается")


_UNSET = object()


def wire(tmp_path, *, actions=None, access_check=_UNSET):
    """Продакшн-менеджер (тот класс, который отдаёт build_manager) на фейковом вводе."""
    mgr = build_manager(store_path=tmp_path / "tasks.json")
    mgr.planner = FakePlanner(list(actions if actions is not None else [click()]))
    mgr.observer = FakeObserver(summary=SCREEN, foreground={"app": "Блокнот"})
    adapter = FakeAdapter()
    mgr.action_router = ActionRouter([adapter])
    mgr.approval_create = _approval_create
    mgr.approval_wait = _approval_wait
    mgr.event_emit = lambda *a, **k: None
    if access_check is not _UNSET:
        mgr.access_check = access_check
    return mgr, adapter


def profile_service(tmp_path, *, device_id="rcd_owner", computer_control=True):
    store = ProfileStore(tmp_path / "profiles")
    p = store.create("владелец", device_id=device_id)
    store.update_toggles(p.id, {"computer_control": computer_control})
    return ProfileService(store), store


# ------------------------------------------------------------------ A2-03
# Разрешение было истинным при создании и ложно к моменту эффекта.

def test_toggle_switched_off_after_creation_denies_before_the_adapter(tmp_path):
    """Тумблер выключен ПОСЛЕ создания задачи: ввод в рабочий стол не уходит.

    Именно этот сценарий владелец и называет «выключил управление компом»:
    строка задачи уже существует, а согласия больше нет."""
    svc, store = profile_service(tmp_path)
    mgr, adapter = wire(tmp_path, access_check=svc.computer_access_check)

    t = mgr.create_task("нажать кнопку", source="core:rcd_owner", owner_device_id="rcd_owner")
    # Владелец снимает разрешение уже после создания задачи.
    store.update_toggles(store.by_device("rcd_owner").id, {"computer_control": False})

    asyncio.run(mgr.run(t.id))

    assert adapter.executed == [], "действие дошло до адаптера при выключенном тумблере"
    row = mgr.store.get(t.id)
    assert row.state is TaskState.FAILED
    assert "denied" in (row.last_error or "").lower()


def test_profile_disabled_after_creation_denies_at_the_boundary(tmp_path):
    """Не только тумблер: выключенный (или удалённый) профиль — тоже отказ."""
    svc, store = profile_service(tmp_path)
    mgr, adapter = wire(tmp_path, access_check=svc.computer_access_check)
    t = mgr.create_task("нажать кнопку", source="core:rcd_owner", owner_device_id="rcd_owner")

    store.set_enabled(store.by_device("rcd_owner").id, False)

    asyncio.run(mgr.run(t.id))
    assert adapter.executed == []
    assert mgr.store.get(t.id).state is TaskState.FAILED


def test_resume_entry_path_is_checked_too(tmp_path):
    """Вход «Продолжить» (routes → MANAGER.resume + MANAGER.run) не создаёт
    задачу и потому вообще не касался access_check."""
    svc, store = profile_service(tmp_path)
    mgr, adapter = wire(tmp_path, access_check=svc.computer_access_check)
    t = mgr.create_task("нажать кнопку", source="core:rcd_owner", owner_device_id="rcd_owner")
    mgr.pause(t.id)
    store.update_toggles(store.by_device("rcd_owner").id, {"computer_control": False})

    mgr.resume(t.id)
    asyncio.run(mgr.run(t.id))

    assert adapter.executed == []
    assert mgr.store.get(t.id).state is TaskState.FAILED


def test_recovered_task_after_restart_is_rechecked(tmp_path):
    """Перезапуск процесса: recover_all поднимает задачу из журнала, и её
    исполнение — тоже вход, на котором согласие могло быть уже снято."""
    svc, store = profile_service(tmp_path)
    mgr, _ = wire(tmp_path, access_check=svc.computer_access_check)
    t = mgr.create_task("нажать кнопку", source="core:rcd_owner", owner_device_id="rcd_owner")

    store.update_toggles(store.by_device("rcd_owner").id, {"computer_control": False})
    fresh, adapter = wire(tmp_path, access_check=svc.computer_access_check)   # тот же tasks.json
    fresh.recover_all()

    asyncio.run(fresh.run(t.id))
    assert adapter.executed == []
    assert fresh.store.get(t.id).state is TaskState.FAILED


def test_http_route_resume_does_not_bypass_the_gate(tmp_path, monkeypatch):
    """Публичный HTTP-вход целиком: POST /computer/tasks/{id}/resume.

    Проверяется не форма ответа, а то, что фоновая задача, которую заводит
    маршрут, не доводит ввод до рабочего стола."""
    from bossman.computer_operator import routes
    from bossman.remote_client.auth import SCOPE_CHAT, Principal

    svc, store = profile_service(tmp_path)
    mgr, adapter = wire(tmp_path, access_check=svc.computer_access_check)
    monkeypatch.setattr(routes, "MANAGER", mgr)

    t = mgr.create_task("нажать кнопку", mode=TaskMode.CONTROL,
                        source="core:rcd_owner", owner_device_id="rcd_owner")
    mgr.pause(t.id)
    store.update_toggles(store.by_device("rcd_owner").id, {"computer_control": False})
    principal = Principal(device_id="rcd_owner", scopes=frozenset({SCOPE_CHAT}))

    async def drive():
        # Маршрут вправе отказать сразу (стена на входе) — важно лишь то, что
        # рабочего стола действие не касается ни на входе, ни на границе эффекта.
        try:
            await routes.resume(t.id, principal)
        except PermissionError:
            return
        pending = [x for x in asyncio.all_tasks() if x is not asyncio.current_task()]
        await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(drive())
    assert adapter.executed == []
    assert mgr.store.get(t.id).state is not TaskState.RUNNING


# ------------------------------------------------------------------ A2-04
# Источник и недоступность gate не должны превращаться в «разрешено».

def test_nonlocal_source_denied_at_boundary_when_gate_is_unavailable(tmp_path, monkeypatch):
    """Строка создана, когда gate был поднят; к моменту эффекта его нет.

    Для не-локального источника это fail-CLOSED: «gate недоступен» не равно
    «разрешено» (Security Hardening V1.1, H2/H7 — но теперь и на эффекте)."""
    from bossman.profiles import service as svcmod

    svc, _ = profile_service(tmp_path)
    monkeypatch.setattr(svcmod, "_SERVICE", svc)
    mgr, adapter = wire(tmp_path)         # access_check = продакшн _profile_access_check
    t = mgr.create_task("нажать кнопку", source="core:rcd_owner", owner_device_id="rcd_owner")

    monkeypatch.setattr(svcmod, "_SERVICE", None)   # сервис профилей упал/не поднят
    asyncio.run(mgr.run(t.id))

    assert adapter.executed == []
    assert mgr.store.get(t.id).state is TaskState.FAILED


def test_source_recorded_on_the_task_is_the_one_checked(tmp_path):
    """Понижения источника на границе нет: проверяется `source` САМОЙ строки.

    Раньше эффект не спрашивал источник вовсе, поэтому удалённо созданная
    задача исполнялась ровно как локальная."""
    seen = []

    def check(device_id, source="local"):
        seen.append((device_id, source))
        raise gate.CapabilityDenied(gate.decide(None, "computer.control"))

    mgr, adapter = wire(tmp_path, access_check=check)
    # Строку создаём в обход create_task (он бы отказал) — ровно так она и
    # появляется после перезапуска: из журнала. Важен эффект, а не создание.
    from bossman.computer_operator.models import ComputerTask
    row = ComputerTask.create("нажать кнопку", source="telegram:42", owner_device_id="rcd_guest")
    mgr.store.save(row)

    asyncio.run(mgr.run(row.id))
    assert adapter.executed == []
    assert ("rcd_guest", "telegram:42") in seen


def test_gate_error_denies_instead_of_allowing(tmp_path):
    """Fail-CLOSED: неизвестная ошибка источника авторизации — это ОТКАЗ.

    Иначе достаточно уронить gate, чтобы получить рабочий стол."""
    def broken(device_id, source="local"):
        raise RuntimeError("profiles store unreadable")

    mgr, adapter = wire(tmp_path, access_check=broken)
    from bossman.computer_operator.models import ComputerTask
    row = ComputerTask.create("нажать кнопку", source="core:rcd_owner", owner_device_id="rcd_owner")
    mgr.store.save(row)

    asyncio.run(mgr.run(row.id))
    assert adapter.executed == []
    assert mgr.store.get(row.id).state is TaskState.FAILED


# ------------------------------------------------------------ чистая функция

def test_authorize_helper_is_fail_closed():
    with pytest.raises(PermissionError):
        authorize_computer_control(lambda d, s="local": (_ for _ in ()).throw(RuntimeError("x")),
                                   "rcd", "core:rcd")
    with pytest.raises(PermissionError):
        authorize_computer_control(
            lambda d, s="local": (_ for _ in ()).throw(ProfilesUnavailable("gate down")),
            "rcd", "core:rcd")
    # Старый одно-аргументный колбэк по-прежнему поддержан.
    calls = []
    authorize_computer_control(lambda d: calls.append(d), "rcd", "core:rcd")
    assert calls == ["rcd"]
    # Гейт не сконфигурирован (access_check=None) — поведение как раньше.
    authorize_computer_control(None, "rcd", "core:rcd")


# ------------------------------------------------------------ положительные контроли

def test_authorized_owner_on_enabled_profile_still_acts(tmp_path):
    """Оператор остаётся работоспособным: включённый профиль — действие уходит."""
    svc, _ = profile_service(tmp_path)
    mgr, adapter = wire(tmp_path, access_check=svc.computer_access_check)
    t = mgr.create_task("нажать кнопку", source="core:rcd_owner", owner_device_id="rcd_owner")

    asyncio.run(mgr.run(t.id))
    assert [a.kind for a in adapter.executed] == [ActionKind.CLICK]
    assert mgr.store.get(t.id).last_error != "computer control denied"


def test_local_owner_without_profiles_still_acts(tmp_path, monkeypatch):
    """Локальный хозяин без поднятого профильного сервиса — как и раньше, no-op."""
    from bossman.profiles import service as svcmod
    monkeypatch.setattr(svcmod, "_SERVICE", None)

    mgr, adapter = wire(tmp_path)          # продакшн access_check
    t = mgr.create_task("нажать кнопку")   # source="local"
    asyncio.run(mgr.run(t.id))
    assert [a.kind for a in adapter.executed] == [ActionKind.CLICK]


def test_create_task_gate_is_still_enforced(tmp_path):
    """Старая стена (проверка при создании) не снята — она просто больше не единственная."""
    svc, store = profile_service(tmp_path, computer_control=False)
    mgr, _ = wire(tmp_path, access_check=svc.computer_access_check)
    with pytest.raises(gate.CapabilityDenied):
        mgr.create_task("нажать кнопку", source="core:rcd_owner", owner_device_id="rcd_owner")
