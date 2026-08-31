"""Профили доступа: модель, gate, durable-стор, per-profile knowledge, enforcement."""
from __future__ import annotations

import pytest

from bossman.profiles import gate
from bossman.profiles.memory import knowledge_dir, memory_namespace, profile_root
from bossman.profiles.models import Profile, default_toggles, normalize_toggles
from bossman.profiles.service import ProfileService
from bossman.profiles.store import ProfileStore, safe_id


# ---------- store CRUD + durability ----------

def test_create_and_get_roundtrip(tmp_path):
    store = ProfileStore(tmp_path)
    p = store.create("Гость Вася", telegram_user_id="12345")
    assert p.id and p.enabled and p.memory_namespace == f"profile:{p.id}"
    again = store.get(p.id)
    assert again is not None and again.name == "Гость Вася" and again.telegram_user_id == "12345"


def test_defaults_are_deny(tmp_path):
    store = ProfileStore(tmp_path)
    p = store.create("guest")
    # всё чувствительное выключено по умолчанию
    assert p.toggles == default_toggles()
    assert all(v is False for v in p.toggles.values())


def test_durability_survives_new_instance(tmp_path):
    ProfileStore(tmp_path).create("persist-me", device_id="rcd_abc")
    p = ProfileStore(tmp_path).by_device("rcd_abc")
    assert p is not None and p.name == "persist-me"


def test_lookup_by_telegram_and_device(tmp_path):
    store = ProfileStore(tmp_path)
    store.create("a", device_id="rcd_1", telegram_user_id="tg1")
    assert store.by_device("rcd_1").telegram_user_id == "tg1"
    assert store.by_telegram("tg1").device_id == "rcd_1"
    assert store.by_device(None) is None and store.by_telegram(None) is None


def test_update_toggles_only_known_keys(tmp_path):
    store = ProfileStore(tmp_path)
    p = store.create("x")
    upd = store.update_toggles(p.id, {"computer_control": True, "nonsense": True})
    assert upd.toggles["computer_control"] is True
    assert "nonsense" not in upd.toggles


def test_normalize_toggles_coerces_bool_and_drops_unknown():
    t = normalize_toggles({"internet": 1, "bogus": "x"})
    assert t["internet"] is True and "bogus" not in t
    # отсутствующие берутся из безопасного дефолта
    assert t["computer_control"] is False


# ---------- capability gate ----------

def _prof(**toggles):
    p = Profile(id="pid", name="n")
    p.toggles = normalize_toggles(toggles)
    return p


def test_gate_denies_unknown_capability():
    d = gate.decide(_prof(computer_control=True), "totally.unknown")
    assert d.allow is False and "неизвестн" in d.reason


def test_gate_computer_control_toggle():
    off = gate.decide(_prof(computer_control=False), "computer.control")
    on = gate.decide(_prof(computer_control=True), "computer.control")
    assert off.allow is False and on.allow is True
    # терминал тоже под computer_control
    assert gate.decide(_prof(computer_control=False), "terminal.run").allow is False


def test_gate_personal_data_toggle():
    assert gate.decide(_prof(personal_data=False), "personal.read").allow is False
    assert gate.decide(_prof(personal_data=True), "personal.read").allow is True


def test_gate_disabled_profile_denies_everything():
    p = _prof(computer_control=True, internet=True)
    p.enabled = False
    assert gate.decide(p, "computer.control").allow is False
    assert gate.decide(p, "browser.read").allow is False


def test_gate_none_profile_denies():
    assert gate.decide(None, "computer.control").allow is False


def test_enforce_raises_on_deny():
    with pytest.raises(gate.CapabilityDenied):
        gate.enforce(_prof(computer_control=False), "computer.control")
    gate.enforce(_prof(computer_control=True), "computer.control")  # не бросает


# ---------- per-profile knowledge folder ----------

def test_knowledge_dir_created_and_confined(tmp_path):
    kd = knowledge_dir(tmp_path, "guest-abc", create=True)
    assert kd.exists() and kd.is_dir()
    assert str(kd).startswith(str((tmp_path / "_profiles").resolve()))
    assert kd.name == "knowledge"


def test_profile_root_rejects_escape(tmp_path):
    # id санитизируется → побег невозможен
    assert safe_id("../../etc") == "etc"
    root = profile_root(tmp_path, "../../etc")
    assert str(root).startswith(str((tmp_path / "_profiles").resolve()))


def test_memory_namespace_per_profile():
    p = Profile(id="joe-123", name="Joe", memory_namespace="profile:joe-123")
    assert memory_namespace(p) == "profile:joe-123"


# ---------- service + device access check ----------

def test_service_computer_access_check_blocks_when_off(tmp_path):
    store = ProfileStore(tmp_path)
    store.create("blocked", device_id="rcd_block")   # computer_control off по умолчанию
    svc = ProfileService(store)
    with pytest.raises(gate.CapabilityDenied):
        svc.computer_access_check("rcd_block")


def test_service_computer_access_check_allows_when_on(tmp_path):
    store = ProfileStore(tmp_path)
    p = store.create("ok", device_id="rcd_ok")
    store.update_toggles(p.id, {"computer_control": True})
    svc = ProfileService(store)
    svc.computer_access_check("rcd_ok")   # не бросает


def test_service_unknown_device_is_local_owner_by_default(tmp_path):
    svc = ProfileService(ProfileStore(tmp_path))
    svc.computer_access_check("rcd_nonexistent")     # no-op (не strict)
    svc.computer_access_check(None)


def test_service_strict_unknown_device_denies(tmp_path):
    svc = ProfileService(ProfileStore(tmp_path), strict_unknown_device=True)
    with pytest.raises(gate.CapabilityDenied):
        svc.computer_access_check("rcd_nonexistent")


# ---------- enforcement wired into computer operator manager ----------

def test_manager_create_task_blocked_by_profile(tmp_path):
    from bossman.computer_operator.manager import ComputerOperatorManager
    from bossman.computer_operator.store import JsonTaskStore

    store = ProfileStore(tmp_path / "prof")
    store.create("guest", device_id="rcd_guest")     # computer_control off
    svc = ProfileService(store)

    mgr = ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "tasks.json"),
        planner=object(), observer=object(), action_router=object(),
        approval_create=lambda *a, **k: None, approval_wait=lambda *a, **k: None,
        event_emit=lambda *a, **k: None,
        access_check=svc.computer_access_check)

    with pytest.raises(gate.CapabilityDenied):
        mgr.create_task("открой блокнот", owner_device_id="rcd_guest")

    # включаем тумблер → задача создаётся
    store.update_toggles(store.by_device("rcd_guest").id, {"computer_control": True})
    t = mgr.create_task("открой блокнот", owner_device_id="rcd_guest")
    assert t.id


def test_manager_no_access_check_keeps_old_behavior(tmp_path):
    from bossman.computer_operator.manager import ComputerOperatorManager
    from bossman.computer_operator.store import JsonTaskStore

    mgr = ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "tasks.json"),
        planner=object(), observer=object(), action_router=object(),
        approval_create=lambda *a, **k: None, approval_wait=lambda *a, **k: None,
        event_emit=lambda *a, **k: None)     # access_check=None
    t = mgr.create_task("local goal", owner_device_id="rcd_whatever")
    assert t.id


# ---------- Security Hardening V1.1: fail-closed для не-локальных источников ----------

def test_nonlocal_unknown_device_is_denied(tmp_path):
    """Удалённый/telegram источник без профиля → fail-CLOSED (не 'локальный хозяин')."""
    from bossman.profiles import gate
    svc = ProfileService(ProfileStore(tmp_path))
    # локальный источник без профиля — по-прежнему разрешён (хозяин)
    svc.computer_access_check("rcd_x", source="local")
    # не-локальный источник без профиля — запрещён
    with pytest.raises(gate.CapabilityDenied):
        svc.computer_access_check("rcd_x", source="remote")
    with pytest.raises(gate.CapabilityDenied):
        svc.computer_access_check("rcd_x", source="telegram")


def test_module_callback_failcloses_when_service_down_for_nonlocal(tmp_path, monkeypatch):
    """Сервис не поднят: локальный — no-op; не-локальный — fail-closed."""
    from bossman.profiles import service as svcmod
    monkeypatch.setattr(svcmod, "_SERVICE", None)
    svcmod.computer_access_check("rcd_x", source="local")   # no-op
    with pytest.raises(svcmod.ProfilesUnavailable):
        svcmod.computer_access_check("rcd_x", source="remote")


def test_manager_failcloses_for_nonlocal_source_when_profiles_down(tmp_path, monkeypatch):
    """Полный путь: computer_operator.create_task с не-локальным источником и
    неподнятым gate → PermissionError (задача не создаётся)."""
    from bossman.computer_operator.manager import ComputerOperatorManager
    from bossman.computer_operator.store import JsonTaskStore
    from bossman.computer_operator.subsystem import _profile_access_check
    from bossman.profiles import service as svcmod
    monkeypatch.setattr(svcmod, "_SERVICE", None)

    mgr = ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "tasks.json"),
        planner=object(), observer=object(), action_router=object(),
        approval_create=lambda *a, **k: None, approval_wait=lambda *a, **k: None,
        event_emit=lambda *a, **k: None, access_check=_profile_access_check)

    with pytest.raises(PermissionError):
        mgr.create_task("открой блокнот", source="remote", owner_device_id="rcd_guest")
    # локальный источник — не режем (хозяин)
    t = mgr.create_task("открой блокнот", source="local", owner_device_id=None)
    assert t.id
