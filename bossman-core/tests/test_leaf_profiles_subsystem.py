"""authored_by_lane (opsplug): bossman.profiles.subsystem - the profile gate is a critical, fail-closed boot step."""
import types

import pytest

from bossman.profiles import subsystem
from bossman.profiles.gate import CapabilityDenied
from bossman.profiles.service import ProfileService, get_service, set_service


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    before = get_service()
    set_service(None)
    monkeypatch.setattr(subsystem, "settings", types.SimpleNamespace(workspace_dir=tmp_path))
    yield tmp_path
    set_service(before)


def test_contract_profiles_are_critical_so_a_failed_gate_aborts_boot():
    sub = subsystem.build_subsystem()
    assert sub.name == "profiles" and sub.critical is True


async def test_validate_installs_a_working_service_on_a_durable_store_under_the_workspace(fresh):
    assert get_service() is None
    await subsystem.build_subsystem().validate()
    svc = get_service()
    assert isinstance(svc, ProfileService)
    assert (fresh / "_profiles").is_dir()                      # the store root was created under the workspace


async def test_installed_service_is_fail_closed_for_remote_sources_without_a_profile(fresh):
    await subsystem.build_subsystem().validate()
    svc = get_service()
    assert svc.decide_device("unknown-device", "computer.control", source="local").allow is True
    remote = svc.decide_device("unknown-device", "computer.control", source="remote")
    assert remote.allow is False
    with pytest.raises(CapabilityDenied):
        svc.computer_access_check("unknown-device", source="telegram")


async def test_start_and_stop_are_harmless_no_ops(fresh):
    sub = subsystem.build_subsystem()
    await sub.validate()
    await sub.start()
    await sub.stop()
    await sub.stop()
    assert get_service() is not None
