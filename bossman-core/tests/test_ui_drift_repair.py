"""Починка адаптера — это работа с границами, а не просто задача кодеру.

Опасность здесь не в том, что работник напишет плохой селектор. Опасность в
том, что «починить» отказ проще всего, выключив то, что отказывает: убрать
распознавание капчи, обернуть всё в `except Exception`, пометить тест `skip`.
Поэтому проверяется не качество ремонта, а невозможность такого ремонта.
"""
from __future__ import annotations

import pytest

from bossman.apprentice.openhands_client import OpenHandsResult
from bossman.apprentice.selector_repair import (ALLOWED_PATHS, PROTECTED_PATHS,
                                                RepairRefused, admissible,
                                                build_repair_mission, scan_packet)

FARM = "apps/social-farm"


def packet(**overrides) -> dict:
    base = {
        "schema": "social_farm.ui_drift.v1",
        "provider": "higgsfield",
        "adapter_version": "higgsfield/0.1.0-unverified",
        "selector_pack_version": "0.1.0-unverified",
        "failing_action": "generation.submit",
        "expected_strategies": [{"kind": "role", "value": "button|Generate"}],
        "session_state": "READY",
        "url": "https://example.invalid/create",
        "surface": [{"tag": "button", "role": "button", "name": "Create",
                     "label": "", "disabled": False}],
        "observed_at_epoch_s": 1_800_000_000.0,
        "detail": "цель не найдена ни одной стратегией",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------ пакет

def test_a_packet_carrying_a_secret_never_leaves_the_machine():
    for poisoned in (
        {"detail": "Bearer abcdefghijklmnopqrstuvwxyz012345"},
        {"url": "https://example.invalid/create?session_token=abcdefgh12345678"},
        {"detail": "password=hunter2hunter2"},
        {"detail": "vault://higgsfield/session"},
    ):
        with pytest.raises(RepairRefused, match="пакет содержит"):
            build_repair_mission(packet(**poisoned), workspace="/tmp/w")


def test_a_clean_packet_scans_clean():
    assert scan_packet(packet()) == ""


def test_a_packet_of_the_wrong_shape_is_refused():
    with pytest.raises(RepairRefused, match="ui_drift"):
        build_repair_mission(packet(schema="something.else"), workspace="/tmp/w")


def test_login_and_secret_entry_are_not_repairable_by_a_coding_worker():
    """Там чинить нечего: вход в аккаунт делает человек."""
    for action in ("login.submit", "login.password.fill", "media.delete"):
        with pytest.raises(RepairRefused, match="не чинится"):
            build_repair_mission(packet(failing_action=action), workspace="/tmp/w")


# ------------------------------------------------------------------ границы

def test_the_mission_may_touch_selectors_and_tests_and_nothing_else():
    mission = build_repair_mission(packet(), workspace="/tmp/w")
    assert mission.allowed_paths == ALLOWED_PATHS
    assert f"{FARM}/src/social_farm/generation/higgsfield_selectors.py" \
        in mission.allowed_paths
    assert f"{FARM}/tests/unit/" in mission.allowed_paths


def test_everything_that_makes_the_browser_path_safe_is_protected():
    mission = build_repair_mission(packet(), workspace="/tmp/w")
    for guarded in (f"{FARM}/src/social_farm/browser/challenge.py",
                    f"{FARM}/src/social_farm/browser/isolation.py",
                    f"{FARM}/src/social_farm/browser/secrets.py",
                    f"{FARM}/src/social_farm/browser/capabilities.py",
                    f"{FARM}/src/social_farm/domain/safety.py",
                    f"{FARM}/tests/unit/test_independence.py"):
        assert guarded in mission.protected_paths, guarded


def test_the_control_plane_is_out_of_reach_of_a_selector_repair():
    mission = build_repair_mission(packet(), workspace="/tmp/w")
    assert "bossman-core/" in mission.protected_paths
    assert "command-center/" in mission.protected_paths
    assert ".github/" in mission.protected_paths


def test_the_instruction_orders_reproduction_before_repair():
    mission = build_repair_mission(packet(), workspace="/tmp/w")
    text = mission.instruction
    assert "СНАЧАЛА воспроизведите отказ" in text
    assert "не коммитить" in text
    assert "завершённой миссию объявляете не вы" in text.lower() \
        or "Завершённой миссию объявляете не вы" in text


def test_the_instruction_shows_the_surface_without_markup():
    mission = build_repair_mission(packet(), workspace="/tmp/w")
    assert "role='button'" in mission.instruction or 'role="button"' in mission.instruction
    assert "<html" not in mission.instruction
    assert "<div" not in mission.instruction


# ------------------------------------------------------------------ приёмка

def result(diff: str, files=(f"{FARM}/tests/unit/test_x.py",),
           status: str = "completed") -> OpenHandsResult:
    return OpenHandsResult(status=status, changed_files=tuple(files), diff=diff,
                           sidecar={})


def test_a_repair_that_disables_a_test_is_not_admissible():
    ok, why = admissible(result("+    @pytest.mark.skip(reason='flaky')\n"))
    assert not ok and "отключение теста" in why


def test_a_repair_that_deletes_an_assertion_is_not_admissible():
    ok, why = admissible(result("-    assert challenge.present\n"))
    assert not ok and "удаление проверки" in why


def test_a_repair_that_swallows_every_error_is_not_admissible():
    ok, why = admissible(result("+        except Exception:\n+            pass\n"))
    assert not ok and "перехват всех исключений" in why


def test_a_repair_that_clicks_by_coordinates_is_not_admissible():
    ok, why = admissible(result("+    await page.mouse.click(120, 480)\n"))
    assert not ok and "координатам" in why


def test_a_repair_without_a_test_is_not_proven():
    ok, why = admissible(result(
        "+    {'kind': 'role', 'value': 'button|Create'},\n",
        files=(f"{FARM}/src/social_farm/generation/higgsfield_selectors.py",)))
    assert not ok and "без теста" in why


def test_a_repair_that_did_nothing_is_not_a_repair():
    ok, why = admissible(result("", files=()))
    assert not ok


def test_a_failed_worker_result_is_not_admissible():
    ok, why = admissible(result("+ ok\n", status="failed"))
    assert not ok


def test_a_reproduced_and_scoped_repair_is_admitted_but_grants_nothing():
    ok, why = admissible(result(
        "+    {'kind': 'role', 'value': 'button|Create'},\n"
        "+def test_the_new_control_is_found():\n+    assert True\n",
        files=(f"{FARM}/src/social_farm/generation/higgsfield_selectors.py",
               f"{FARM}/tests/unit/test_higgsfield_drift.py")))
    assert ok
    assert "правами это не является" in why
