"""Персона не переписывается генерацией, а запрос не выносит лишнего.

Два разных страха, и оба реальные. Первый: к десятому ролику ведущий стал
другим человеком, и назвать сцену, где это произошло, нельзя. Второй: в текст
запроса к чужому сервису попало то, чего там быть не должно.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from social_farm.generation.higgsfield_browser_contracts import (ArtifactReceipt,
                                                                 MediaKind)
from social_farm.generation.persona import (ContinuityLedger, ContinuityObservation,
                                            ContinuityStatus, PersonaCanonViolation,
                                            PersonaFact, StreamerPersona)
from social_farm.generation.pipeline import (ForbiddenContent, Script, ScriptBeat,
                                             build_prompt, plan_segment, plan_shots)
from social_farm.generation.segment_qa import (IncompleteSegment, ShotVerdict,
                                               build_segment, review_shot)

NOW = 1_800_000_000.0


def persona(**overrides) -> StreamerPersona:
    base = dict(
        persona_id="ateljie-host", version=1, display_name="Вера",
        speaking_style="спокойно, короткими фразами",
        facts={
            "appearance": PersonaFact("appearance", "тёмные волосы, каре",
                                      locked=True,
                                      note="внешность ведущего не меняется между сценами"),
            "outfit": PersonaFact("outfit", "серое пальто", locked=True),
            "location": PersonaFact("location", "мастерская у окна"),
            "lighting": PersonaFact("lighting", "мягкий вечерний свет"),
        },
        forbidden_claims=("лечит болезни", "гарантированный доход"),
        forbidden_topics=("медицинские советы",))
    base.update(overrides)
    return StreamerPersona(**base)


def script() -> Script:
    return Script(script_id="s1", title="Вечерняя мастерская", beats=(
        ScriptBeat("b1", "Вера показывает новую ткань", 8.0),
        ScriptBeat("b2", "крупный план строчки", 14.0, visual_note="макро, игла и нить"),
    ))


# ------------------------------------------------------------------ канон

def test_a_locked_fact_does_not_change_even_with_a_new_version():
    with pytest.raises(PersonaCanonViolation, match="заперт"):
        persona().revise({"appearance": PersonaFact("appearance", "блондинка")},
                         version=2, reason="модель предложила")


def test_a_locked_fact_changes_only_when_it_is_unlocked_on_purpose():
    updated = persona().revise(
        {"appearance": PersonaFact("appearance", "тёмные волосы, длинные", locked=True)},
        version=2, reason="владелец обновил образ", unlock=["appearance"])
    assert updated.value_of("appearance") == "тёмные волосы, длинные"
    assert updated.version == 2
    assert updated.changed_reason == "владелец обновил образ"


def test_a_canon_is_never_rewritten_in_place():
    """Правка без версии не оставляет следа, с какой сцены ведущий стал другим."""
    with pytest.raises(PersonaCanonViolation, match="отдельной версией"):
        persona().revise({"location": PersonaFact("location", "студия")},
                         version=1, reason="так лучше")


def test_a_change_without_a_reason_is_refused():
    with pytest.raises(PersonaCanonViolation, match="без причины"):
        persona().revise({"location": PersonaFact("location", "студия")},
                         version=2, reason="   ")


def test_an_unlocked_fact_changes_with_a_version_bump():
    updated = persona().revise({"location": PersonaFact("location", "балкон")},
                               version=2, reason="новая серия")
    assert updated.value_of("location") == "балкон"
    assert persona().value_of("location") == "мастерская у окна", "старая версия цела"


# --------------------------------------------------------------- непрерывность

def test_an_observation_never_rewrites_the_canon():
    """Модель может «увидеть» что угодно. Каноном это не становится."""
    canon = persona()
    ledger = ContinuityLedger(canon)
    finding = ledger.observe(ContinuityObservation(
        "outfit", "красная куртка", "qa", NOW))
    assert finding.status is ContinuityStatus.CONTESTED
    assert finding.blocking, "запертый факт — это брак кадра"
    assert canon.value_of("outfit") == "серое пальто"
    assert ledger.persona.value_of("outfit") == "серое пальто"


def test_a_disagreement_on_an_unlocked_fact_is_a_conflict_not_a_rejection():
    ledger = ContinuityLedger(persona())
    finding = ledger.observe(ContinuityObservation("location", "кухня", "qa", NOW))
    assert finding.status is ContinuityStatus.CONTESTED
    assert not finding.blocking
    assert "канон не изменён" in finding.detail


def test_something_the_canon_says_nothing_about_is_recorded_not_invented():
    ledger = ContinuityLedger(persona())
    finding = ledger.observe(ContinuityObservation("weather", "дождь", "qa", NOW))
    assert finding.status is ContinuityStatus.UNKNOWN
    assert not finding.blocking


# ------------------------------------------------------------------ раскадровка

def test_a_long_beat_is_split_into_generatable_shots():
    shots = plan_shots(script(), persona(), max_shot_seconds=10.0)
    assert [shot.beat_id for shot in shots] == ["b1", "b2", "b2"]
    assert shots[0].seconds == 8.0
    assert shots[1].seconds == shots[2].seconds == 7.0
    assert [shot.ordinal for shot in shots] == [0, 1, 2]


def test_shot_planning_is_deterministic():
    first = plan_shots(script(), persona())
    second = plan_shots(script(), persona())
    assert [s.to_dict() for s in first] == [s.to_dict() for s in second]


def test_a_forbidden_claim_stops_the_shot_it_does_not_get_edited_out():
    """Тихая правка означала бы, что сцена снялась не той, а заметить это
    можно будет только в готовом ролике."""
    bad = Script(script_id="s2", title="плохой", beats=(
        ScriptBeat("b1", "Вера говорит, что ткань лечит болезни", 5.0),))
    with pytest.raises(ForbiddenContent) as refused:
        plan_shots(bad, persona())
    assert refused.value.matched == "лечит болезни"


# ------------------------------------------------------------------ запрос

def test_the_prompt_carries_the_canon_and_nothing_else():
    shots = plan_shots(script(), persona())
    prompt = build_prompt(shots[0], persona())
    assert "серое пальто" in prompt
    assert "мягкий вечерний свет" in prompt
    assert "9:16" in prompt


def test_a_field_nobody_listed_never_reaches_the_provider():
    """В запрос уходит перечисленное. Двери для «остального контекста» нет."""
    leaky = persona(facts={
        "appearance": PersonaFact("appearance", "тёмные волосы"),
        "internal_note": PersonaFact("internal_note",
                                     "vault://higgsfield/session-cookie"),
        # Собирается из кусков нарочно: сканер секретов в CI обязан ловить
        # ключ, написанный в файле буквами, и этот тест не повод его ослаблять.
        "api_key": PersonaFact("api_key", "sk-" + "not-a-real-key-000000"),
    })
    shots = plan_shots(script(), leaky)
    prompt = build_prompt(shots[0], leaky)
    assert "vault://" not in prompt
    assert "not-a-real-key" not in prompt
    assert "internal_note" not in prompt


def test_build_prompt_has_no_free_context_parameter():
    """Проверяется подпись, а не поведение: параметра, через который можно
    было бы передать «ещё немного контекста», не существует."""
    import inspect
    names = set(inspect.signature(build_prompt).parameters)
    assert names == {"shot", "persona"}


def test_a_plan_produces_one_job_per_shot_with_digests(tmp_path):
    plan = plan_segment(script(), persona(), mission_id="m-1",
                        output_workspace=tmp_path / "approved")
    assert len(plan.requests) == len(plan.shots) == 3
    assert len(set(request.job_id for request in plan.requests)) == 3
    assert all(len(digest) == 64 for digest in plan.prompt_digests.values())


def test_an_image_shot_carries_no_duration(tmp_path):
    still = Script(script_id="s3", title="обложка", beats=(
        ScriptBeat("b1", "витрина ателье", 3.0, media_kind=MediaKind.IMAGE),))
    plan = plan_segment(still, persona(), mission_id="m-2",
                        output_workspace=tmp_path / "approved")
    assert plan.requests[0].duration_seconds is None


# ------------------------------------------------------------------ приёмка

def artifact(shot_id: str, kind: MediaKind = MediaKind.VIDEO) -> ArtifactReceipt:
    return ArtifactReceipt(job_id=shot_id, path=Path(f"/tmp/{shot_id}.mp4"),
                           sha256="a" * 64, media_kind=kind, bytes_size=4096,
                           accepted_at_epoch_s=NOW,
                           qa_verdicts={"basic_file_validation": "pass"})


def test_a_shot_that_contradicts_a_locked_fact_is_not_accepted():
    shots = plan_shots(script(), persona())
    ledger = ContinuityLedger(persona())
    verdict = review_shot(shots[0], artifact(shots[0].shot_id), ledger=ledger,
                          observed={"outfit": "красная куртка"}, now=NOW)
    assert not verdict.accepted
    assert any("канон держит" in reason for reason in verdict.reasons)


def test_a_shot_that_matches_the_canon_is_accepted():
    shots = plan_shots(script(), persona())
    ledger = ContinuityLedger(persona())
    verdict = review_shot(shots[0], artifact(shots[0].shot_id), ledger=ledger,
                          observed={"outfit": "серое пальто",
                                    "location": "мастерская у окна"},
                          now=NOW, measured_duration_s=8.2)
    assert verdict.accepted, verdict.reasons


def test_a_wildly_wrong_duration_is_rejected():
    shots = plan_shots(script(), persona())
    ledger = ContinuityLedger(persona())
    verdict = review_shot(shots[0], artifact(shots[0].shot_id), ledger=ledger,
                          now=NOW, measured_duration_s=2.0)
    assert not verdict.accepted
    assert any("длительность" in reason for reason in verdict.reasons)


def test_a_still_delivered_for_a_video_shot_is_rejected():
    shots = plan_shots(script(), persona())
    ledger = ContinuityLedger(persona())
    verdict = review_shot(shots[0], artifact(shots[0].shot_id, MediaKind.IMAGE),
                          ledger=ledger, now=NOW)
    assert not verdict.accepted


# ------------------------------------------------------------------ сегмент

def test_a_segment_is_built_in_storyboard_order_with_evidence(tmp_path):
    who = persona()
    shots = plan_shots(script(), who)
    artifacts = {shot.shot_id: artifact(shot.shot_id) for shot in shots}
    verdicts = {shot.shot_id: ShotVerdict(shot.shot_id, True) for shot in shots}

    manifest = build_segment("seg-1", who, shots, artifacts, verdicts, now=NOW)
    body = manifest.to_dict()
    assert [entry["ordinal"] for entry in body["shots"]] == [0, 1, 2]
    assert body["persona"] == {"id": who.persona_id, "version": who.version}
    assert all(len(entry["sha256"]) == 64 for entry in body["shots"])
    assert body["total_seconds"] == pytest.approx(22.0)

    written = manifest.write(tmp_path / "segments" / "seg-1.json")
    assert written.is_file()


def test_a_segment_with_one_rejected_shot_is_not_quietly_shortened():
    """Подставить соседний кадр и промолчать — это ролик, который никто не
    согласовывал."""
    who = persona()
    shots = plan_shots(script(), who)
    artifacts = {shot.shot_id: artifact(shot.shot_id) for shot in shots}
    verdicts = {shot.shot_id: ShotVerdict(shot.shot_id, True) for shot in shots}
    verdicts[shots[1].shot_id] = ShotVerdict(shots[1].shot_id, False,
                                             ("непрерывность",))
    with pytest.raises(IncompleteSegment) as refused:
        build_segment("seg-1", who, shots, artifacts, verdicts, now=NOW)
    assert shots[1].shot_id in refused.value.missing


def test_a_segment_missing_a_file_is_refused_too():
    who = persona()
    shots = plan_shots(script(), who)
    artifacts = {shots[0].shot_id: artifact(shots[0].shot_id)}
    verdicts = {shot.shot_id: ShotVerdict(shot.shot_id, True) for shot in shots}
    with pytest.raises(IncompleteSegment):
        build_segment("seg-1", who, shots, artifacts, verdicts, now=NOW)
