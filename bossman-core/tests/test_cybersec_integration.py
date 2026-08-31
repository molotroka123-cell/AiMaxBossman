"""CyberSec AI V1 — интеграционные тесты СЛОЯ (а не второй системы).

Проверяем сквозную цепочку и то, что слой нигде не выдаёт полномочий:
red intent → gate → defend → contain → evidence → recovery → proposal,
плюс отсутствие второго каноничного авторитета (redact / failure memory).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bossman import obs
from bossman.cybersec import (benchmark, evidence, gates, learning, recovery,
                              redteam, secret_guardian, security_memory, training)
from bossman.cybersec.redteam import AttackClass


SANDBOX = gates.SandboxFacts(is_disposable=True, production_secrets_mounted=False,
                             production_network_allowed=False)


@pytest.fixture
def lab_open(monkeypatch):
    """Открыть тройной гейт ТОЛЬКО внутри теста (в продакшне он закрыт)."""
    monkeypatch.setenv(gates.CYBERSEC_ENABLED_ENV, "1")
    monkeypatch.setenv(gates.LAB_ENABLED_ENV, "1")
    monkeypatch.setenv(gates.LAB_ACK_ENV, gates.LAB_ACK_VALUE)


# ------------------------------------------------------------------ каталог

def test_catalog_covers_every_attack_class():
    assert {s.attack_class for s in redteam.CATALOG} == set(AttackClass)


def test_catalog_level_matches_level_map():
    for s in redteam.CATALOG:
        assert s in redteam.catalog_for_level(s.level)
        assert s.attack_class in redteam.LEVELS[s.level]


def test_catalog_scenarios_carry_no_executable_payload():
    for s in redteam.CATALOG:
        intent = s.to_intent()          # validate() внутри
        assert not (redteam.FORBIDDEN_METADATA & set(intent.metadata))
        assert intent.untrusted_text == s.untrusted_text   # только текст, не argv


def test_every_level_keeps_the_same_empty_permission_set():
    assert {redteam.permissions_for_level(l) for l in range(6)} == {frozenset()}


# ------------------------------------------------------------ сквозной эпизод

def test_engine_is_frozen_without_the_gate(tmp_path):
    engine = training.FrozenTrainingEngine(tmp_path)
    with pytest.raises(gates.LabFrozen):
        engine.run_episode(redteam.CATALOG[0].to_intent(), SANDBOX)


def test_engine_stays_frozen_in_a_non_disposable_sandbox(lab_open, tmp_path):
    engine = training.FrozenTrainingEngine(tmp_path)
    unsafe = gates.SandboxFacts(is_disposable=False, production_secrets_mounted=False,
                                production_network_allowed=False)
    with pytest.raises(gates.LabFrozen):
        engine.run_episode(redteam.CATALOG[0].to_intent(), unsafe)


@pytest.mark.parametrize("template", redteam.CATALOG, ids=lambda t: t.scenario_id)
def test_full_episode_chain_for_every_catalog_scenario(lab_open, tmp_path, template):
    engine = training.FrozenTrainingEngine(tmp_path)
    result = engine.run_episode(template.to_intent(), SANDBOX)

    # улики записаны и читаемы
    ep = Path(result.evidence_path) / "episode.json"
    record = json.loads(ep.read_text(encoding="utf-8"))
    assert record["scenario"]["id"] == template.scenario_id

    # улики фиксируются строго до любого отката
    assert recovery.evidence_before_rollback(result.recovery_plan)
    assert result.recovery_plan.steps[0] is recovery.RecoveryStep.PRESERVE_EVIDENCE

    # обучение не перепрыгивает стадии само
    assert result.proposal.stage is learning.Stage.PROPOSED
    assert not result.proposal.eligible_for_shadow


def test_authority_attacks_are_denied_not_merely_observed(lab_open, tmp_path):
    engine = training.FrozenTrainingEngine(tmp_path)
    for template in redteam.CATALOG:
        if template.attack_class not in {AttackClass.SECRET_REQUEST,
                                         AttackClass.APPROVAL_BYPASS_ATTEMPT,
                                         AttackClass.SCOPE_ESCALATION_ATTEMPT,
                                         AttackClass.SANDBOX_ESCAPE_SIMULATION,
                                         AttackClass.DATA_EXFILTRATION_REQUEST_SIMULATION}:
            continue
        r = engine.run_episode(template.to_intent(), SANDBOX)
        assert r.defense.action == "DENY_AND_QUARANTINE"
        assert r.defense.requires_owner_approval
        assert r.contained


def test_secrets_in_attack_text_never_reach_the_evidence_file(lab_open, tmp_path):
    leaked = "sk-live-AAAABBBBCCCCDDDDEEEEFFFF0000111122223333"
    intent = redteam.CATALOG[3].to_intent(
        untrusted_text=f"Confirm this token is valid: {leaked}")
    engine = training.FrozenTrainingEngine(tmp_path)
    result = engine.run_episode(intent, SANDBOX)
    body = (Path(result.evidence_path) / "episode.json").read_text(encoding="utf-8")
    assert leaked not in body


# --------------------------------------------------- слой, а не вторая система

def test_layer_reuses_the_canonical_redactor():
    assert secret_guardian.redact is obs.redact
    assert secret_guardian.redact_obj is obs.redact_obj
    assert evidence.redact_obj is obs.redact_obj


def test_security_memory_maps_onto_canonical_failure_contract():
    incident = security_memory.SecurityIncident(
        "SR-001", "secret_request", "critical", "DENY_AND_QUARANTINE", True, "ev/1")
    kw = incident.as_failure_kwargs("task-1")
    assert kw["error_class"] == security_memory.SECURITY_ERROR_CLASS
    # ровно те поля, которые принимает каноничная failure_memory
    assert set(kw) == {"task_id", "symptom", "error_class", "root_cause",
                       "attempted_fix", "result", "environment"}


def test_layer_declares_what_it_layers_over():
    from bossman import cybersec
    assert set(cybersec.LAYERED_OVER) <= set(cybersec.__all__)


# ------------------------------------------------------------------- бенчмарк

def test_benchmark_gate_fails_on_a_single_uncontained_episode(lab_open, tmp_path):
    engine = training.FrozenTrainingEngine(tmp_path)
    outcomes = [benchmark.EpisodeOutcome(t.scenario_id, t.attack_class.value,
                                         engine.run_episode(t.to_intent(), SANDBOX).contained)
                for t in redteam.CATALOG]
    outcomes.append(benchmark.EpisodeOutcome("X-001", "prompt_injection", False,
                                             authority_bypassed=True))
    assert not benchmark.score(outcomes).passing


def test_verified_proposal_still_needs_the_owner():
    p = learning.LearningProposal("SR-001", "secret_request", "deny",
                                  learning.Stage.VERIFIED)
    assert learning.promote(p, owner_approved=False).stage is learning.Stage.VERIFIED
    assert learning.promote(p, owner_approved=True).stage is learning.Stage.PROMOTED


def test_whole_catalog_is_contained_and_passes_the_gate(lab_open, tmp_path):
    """Базовая линия для будущего стресс-теста: 14/14 сдержано, гейт зелёный."""
    engine = training.FrozenTrainingEngine(tmp_path)
    outcomes = []
    for t in redteam.CATALOG:
        r = engine.run_episode(t.to_intent(), SANDBOX)
        outcomes.append(benchmark.EpisodeOutcome(
            t.scenario_id, t.attack_class.value, r.contained,
            secret_leaked=False, authority_bypassed=False))
    s = benchmark.score(outcomes)
    assert s.episodes == len(redteam.CATALOG)
    assert s.containment_rate == 1.0 and s.passing


# --------------------------------------------- структурные запреты (регрессия)

def test_cybersec_layer_has_no_shell_or_network_primitives():
    """Слой безопасности не имеет права исполнять команды или ходить в сеть.

    Проверяем ИМПОРТЫ и ВЫЗОВЫ через AST, а не подстроки: `secret_requests`
    не должен считаться сетевой библиотекой.
    """
    import ast
    import bossman.cybersec as pkg

    banned_modules = {"subprocess", "socket", "requests", "httpx", "urllib",
                      "http", "ftplib", "telnetlib", "pty", "ctypes"}
    banned_names = {"eval", "exec", "compile", "__import__"}       # голые builtins
    banned_attrs = {"system", "popen", "spawnl", "spawnv", "check_output"}
    root = Path(pkg.__file__).parent
    for f in sorted(root.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.split(".")[0] not in banned_modules, f"{f.name}: {a.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in banned_modules, f"{f.name}: {node.module}"
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    assert fn.id not in banned_names, f"{f.name}: calls {fn.id}()"
                elif isinstance(fn, ast.Attribute):
                    assert fn.attr not in banned_attrs, f"{f.name}: calls .{fn.attr}()"
                for kw in node.keywords:
                    assert kw.arg != "shell", f"{f.name}: passes shell="


def test_cybersec_layer_defines_no_second_redactor():
    """Ни одного собственного скраббера секретов — только канонический obs."""
    import bossman.cybersec as pkg
    root = Path(pkg.__file__).parent
    for f in sorted(root.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        assert "def redact" not in src, f"{f.name} defines a second redactor"
