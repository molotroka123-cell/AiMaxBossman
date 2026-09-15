"""P0-2: SATISFIED требует РАЗРЕШЁННОЙ привязанной улики, а не непустой строки.

Дефект, который здесь закрывается, был ровно один и был решающим:

    if condition == "SATISFIED" and not (type(evidence_ref) is str and evidence_ref.strip()):
        raise ObjectiveStoreError("SATISFIED requires a verified evidence reference")

`"x"` — непустая строка. Значит зелёный статус цели выставлялся ПРОЗОЙ МОДЕЛИ:
любое слово в поле улики объявляло цель достигнутой, и ни один слой выше не мог
это исправить, потому что хранилище — последняя линия обороны.

Тесты ниже — в первую очередь ОТРИЦАТЕЛЬНЫЕ. Каждый берёт улику, отличающуюся
от годной ровно одной привязкой, и требует ОТКАЗА:

  * произвольная строка;
  * неизвестный идентификатор;
  * улика другой цели;
  * улика прежней редакции;
  * отозванная улика;
  * протухшая улика;
  * подделанная улика (подпись и поле по отдельности);
  * улика другого условия;
  * улика, снятая при другой применимости (жизненный цикл);
  * улика чужого прогона;
  * одноразовая улика, использованная второй раз.

Положительный контроль тоже настоящий: годная улика РАЗРЕШАЕТ SATISFIED и
переживает НАСТОЯЩИЙ рестарт (новый объект хранилища поверх того же файла),
пока не закрылось окно свежести — и перестаёт разрешать, когда закрылось.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from bossman_shared import evidence as _evidence
from bossman_shared import objective_evidence as oev
from bossman_shared.objective_spec import ObjectiveSpec
from bossman_shared.objective_store import ObjectiveStore, ObjectiveStoreError

NOW = 1_000_000.0


@pytest.fixture(autouse=True)
def evidence_key(tmp_path, monkeypatch):
    monkeypatch.setenv(_evidence.ENV_KEY_FILE, str(tmp_path / "evidence.key"))
    monkeypatch.setenv(_evidence.ENV_DATA_DIR, str(tmp_path / "data"))
    _evidence.reset_cache()
    yield
    _evidence.reset_cache()


def raw_spec(objective_id="build", revision=1, previous_digest=None, **changes):
    body = {
        "schema_version": 1, "owner_id": "owner", "scope_id": "project",
        "objective_id": objective_id, "revision": revision,
        "previous_digest": previous_digest,
        "sources": [{"source_ref": "local-build", "source_revision": "v1",
                     "max_age_seconds": 30}],
        "predicates": [{"predicate_id": "green", "source_ref": "local-build",
                        "field": "passed", "value_type": "boolean",
                        "operator": "eq", "expected": True}],
        "expires_at": NOW + 100_000.0, "priority": 2,
        "allowed_triggers": ["source_change"], "permission_refs": ["owner-grant"],
        "conflict_keys": ["project-build"], "cooldown_seconds": 60,
        "limits": {"max_observations": 8, "max_missions": 2,
                   "max_wall_seconds": 300, "max_cost_usd": 5},
        "stop_conditions": ["owner-revocation"],
    }
    body.update(changes)
    return body


@pytest.fixture
def db(tmp_path):
    return tmp_path / "objectives" / "v5.db"


def activated(store, objective_id="build"):
    state = store.get(objective_id)
    return store.transition(objective_id, "ACTIVE", now=NOW, owner_id="owner",
                            expected_version=state.version)


@pytest.fixture
def store(db):
    store = ObjectiveStore(db)
    store.create(ObjectiveSpec.from_dict(raw_spec()))
    activated(store)
    return store


def mint(store, objective_id="build", **kw):
    kw.setdefault("condition", "SATISFIED")
    kw.setdefault("run_id", "run-1")
    return store.record_condition_evidence(objective_id, **kw)


def refused(store, ref, objective_id="build", now=None):
    """Требует ОТКАЗА и НЕИЗМЕННОСТИ записи: отказ не имеет права писать половину."""
    before = store.get(objective_id)
    with pytest.raises(ObjectiveStoreError) as excinfo:
        store.set_condition(objective_id, "SATISFIED", evidence_ref=ref,
                            expected_version=before.version, now=now)
    after = store.get(objective_id)
    assert after == before, "отказ изменил долговременную запись"
    assert after.condition != "SATISFIED"
    return str(excinfo.value)


# ------------------------------------------------------------- отрицательные

@pytest.mark.parametrize("ref", ["x", "ev-1", "SATISFIED", "oev1:", "oev1:zz",
                                 "cev1:" + "a" * 32, "oev1:" + "a" * 31,
                                 "oev1:" + "A" * 32, " ", "\t\n"])
def test_an_arbitrary_non_empty_string_never_buys_green(store, ref):
    """Ядро дефекта: `"x"` непустая, и раньше этого хватало."""
    assert "evidence" in refused(store, ref)


@pytest.mark.parametrize("ref", [None, "", "   ", 1, b"oev1:" + b"a" * 32, 0.0, True, object()])
def test_a_non_reference_is_refused_before_anything_is_read(store, ref):
    refused(store, ref)


def test_an_unknown_evidence_id_is_refused(store):
    """Ссылка ПРАВИЛЬНОЙ формы, но ни на что не указывающая, — это не улика."""
    assert oev.UNRESOLVED in refused(store, oev.make_ref("b" * 32))


def test_evidence_bound_to_a_different_objective_is_refused(db, store):
    store.create(ObjectiveSpec.from_dict(raw_spec(objective_id="other")))
    activated(store, "other")
    foreign = mint(store, "other")
    assert oev.WRONG_OBJECTIVE in refused(store, foreign)
    # И наоборот: улика «build» не годится для «other».
    assert oev.WRONG_OBJECTIVE in refused(store, mint(store, "build"), objective_id="other")


def test_evidence_bound_to_an_older_revision_is_refused(store):
    """Ревизия сменилась под целью — улика о прежнем теле больше ни о чём."""
    ref = mint(store)
    previous = store.get_spec("build")
    state = store.get("build")
    revised = ObjectiveSpec.from_dict(
        raw_spec(revision=2, previous_digest=previous.digest, priority=9), previous=previous)
    store.revise("build", revised, owner_id="owner", expected_version=state.version)
    activated(store)
    assert oev.WRONG_REVISION in refused(store, ref)


def test_revoked_evidence_is_refused(store):
    ref = mint(store)
    store.revoke_condition_evidence(ref, reason="withdrawn by the verifier")
    assert oev.REVOKED in refused(store, ref)
    # Отзыв липкий: повторный отзыв не воскрешает и не меняет момент отзыва.
    store.revoke_condition_evidence(ref)
    assert oev.REVOKED in refused(store, ref)


def test_stale_evidence_is_refused(store):
    ref = mint(store, ttl_seconds=10.0, now=NOW)
    assert oev.STALE in refused(store, ref, now=NOW + 10.001)
    # И «улика из будущего» тоже не улика.
    assert oev.STALE in refused(store, ref, now=NOW - 1.0)


def test_a_tampered_signature_is_refused(db, store):
    ref = mint(store)
    evidence_id = oev.parse_ref(ref)
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT record FROM v5_condition_evidence WHERE evidence_id=?",
                          (evidence_id,)).fetchone()
        record = json.loads(row[0])
        record["sig"] = "0" * 64
        con.execute("UPDATE v5_condition_evidence SET record=? WHERE evidence_id=?",
                    (json.dumps(record), evidence_id))
    assert oev.TAMPERED in refused(store, ref)


def test_a_tampered_binding_field_is_refused(db, store):
    """Правка поля в подписанном теле ломает подпись — и это видно."""
    store.create(ObjectiveSpec.from_dict(raw_spec(objective_id="other")))
    activated(store, "other")
    ref = mint(store, "other")
    evidence_id = oev.parse_ref(ref)
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT record FROM v5_condition_evidence WHERE evidence_id=?",
                          (evidence_id,)).fetchone()
        record = json.loads(row[0])
        record["objective_id"] = "build"
        con.execute("UPDATE v5_condition_evidence SET record=?,objective_id='build' "
                    "WHERE evidence_id=?", (json.dumps(record), evidence_id))
    assert oev.TAMPERED in refused(store, ref)


def test_a_tampered_column_is_refused_even_when_the_body_is_intact(db, store):
    """Колонки — зеркало подписанного тела. Разошлись — порча, а не «почти то же»."""
    ref = mint(store)
    evidence_id = oev.parse_ref(ref)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE v5_condition_evidence SET fresh_until=fresh_until+100000 "
                    "WHERE evidence_id=?", (evidence_id,))
    assert oev.TAMPERED in refused(store, ref)


def test_evidence_signed_with_a_foreign_key_is_refused(store):
    """Подпись чужим ключом — не подпись: ключ процесса не проходит через модель."""
    ref = mint(store, evidence_key=b"n" * 32)
    assert oev.TAMPERED in refused(store, ref)


def test_evidence_bound_to_a_different_condition_is_refused(store):
    """Здоровье и удовлетворённость — РАЗНЫЕ утверждения."""
    for condition in ("DEVIATED", "UNKNOWN"):
        assert oev.WRONG_CONDITION in refused(store, mint(store, condition=condition))


def test_evidence_from_a_different_applicability_is_refused(store):
    """Улика, снятая на активной цели, не применима к приостановленной."""
    ref = mint(store)
    state = store.get("build")
    state = store.transition("build", "PAUSED", now=NOW, owner_id="owner",
                             expected_version=state.version)
    assert oev.WRONG_APPLICABILITY in refused(store, ref)


def test_evidence_naming_another_objectives_run_is_refused(store):
    """Прогон, породивший улику, обязан принадлежать этой цели."""
    store.create(ObjectiveSpec.from_dict(raw_spec(objective_id="other")))
    activated(store, "other")
    other = store.get("other")
    store.insert_proposal_once(proposal_id="p-other", objective_id="other",
                               objective_digest=other.spec_digest, objective_revision=1,
                               created_at=NOW, valid_until=NOW + 100.0, payload={})
    store.reserve_once(reservation_id="r-other", objective_id="other",
                       proposal_id="p-other", created_at=NOW, payload={})
    with pytest.raises(ObjectiveStoreError):
        mint(store, "build", run_id="r-other")


def test_a_run_that_changes_owner_after_the_mint_is_refused(db, store):
    """Прогон переехал на другую цель после чеканки — улика перестаёт разрешаться."""
    ref = mint(store, run_id="r-1")
    other = store.create(ObjectiveSpec.from_dict(raw_spec(objective_id="other")))
    store.insert_proposal_once(proposal_id="p-1", objective_id="other",
                               objective_digest=other.spec_digest, objective_revision=1,
                               created_at=NOW, valid_until=NOW + 100.0, payload={})
    store.reserve_once(reservation_id="r-1", objective_id="other", proposal_id="p-1",
                       created_at=NOW, payload={})
    assert oev.WRONG_RUN in refused(store, ref)


def test_single_use_evidence_cannot_be_replayed(store):
    ref = mint(store, single_use=True)
    state = store.set_condition("build", "SATISFIED", evidence_ref=ref,
                                expected_version=store.get("build").version)
    assert state.condition == "SATISFIED"
    store.set_condition("build", "UNKNOWN", evidence_ref=None,
                        expected_version=state.version)
    assert oev.CONSUMED in refused(store, ref)


def test_a_canary_attestation_never_satisfies_an_objective_condition(store):
    """Здоровье канареечного выпуска и удовлетворённость цели — РАЗНЫЕ утверждения.

    Канареечная улика настоящая, разрешаемая и привязанная к ЭТОЙ цели — и всё
    равно не является причиной писать SATISFIED. Схемы ссылок разные намеренно:
    `cev1.` не разрешается резолвером условия ни при каком ключе.
    """
    from bossman_shared.objective_canary import (CanaryEvidenceLedger, CanaryPolicy,
                                                 plan_canary)
    population = ("build",) + tuple(f"obj-{i:02d}" for i in range(11))
    plan = plan_canary(population, revision_digest=store.get("build").spec_digest,
                       now=NOW, policy=CanaryPolicy(fraction=0.5))
    member = plan.cohort[0]
    ledger = CanaryEvidenceLedger(key=b"k" * 32)
    canary_ref = ledger.issue(plan, member, issued_at=NOW)
    assert ledger.resolve(canary_ref) is not None          # улика НАСТОЯЩАЯ
    assert oev.MALFORMED_REF in refused(store, canary_ref)


# ------------------------------------------------------------- положительные

def test_valid_bound_evidence_permits_satisfied(store):
    ref = mint(store)
    state = store.set_condition("build", "SATISFIED", evidence_ref=ref,
                                expected_version=store.get("build").version)
    assert state.condition == "SATISFIED"
    assert state.last_verified_evidence_ref == ref
    assert store.get("build") == state
    row = store.condition_evidence(ref)
    assert row["uses"] == 1 and row["revoked_at"] is None
    assert row["record"]["objective_id"] == "build"
    assert row["record"]["condition"] == "SATISFIED"


def test_evidence_survives_a_genuine_restart_while_it_is_still_fresh(db, store):
    """Настоящий рестарт: НОВЫЙ объект хранилища поверх того же файла."""
    ref = mint(store, ttl_seconds=3600.0, now=NOW)
    restarted = ObjectiveStore(db)
    state = restarted.set_condition("build", "SATISFIED", evidence_ref=ref,
                                    expected_version=restarted.get("build").version,
                                    now=NOW + 1800.0)
    assert state.condition == "SATISFIED" and state.last_verified_evidence_ref == ref
    assert ObjectiveStore(db).get("build").condition == "SATISFIED"


def test_evidence_whose_freshness_window_closed_is_refused_after_restart(db, store):
    """Долговечность — не бессрочность: пережить рестарт мало, надо быть свежей."""
    ref = mint(store, ttl_seconds=60.0, now=NOW)
    restarted = ObjectiveStore(db)
    before = restarted.get("build")
    with pytest.raises(ObjectiveStoreError) as excinfo:
        restarted.set_condition("build", "SATISFIED", evidence_ref=ref,
                                expected_version=before.version, now=NOW + 61.0)
    assert oev.STALE in str(excinfo.value)
    assert ObjectiveStore(db).get("build") == before


def test_a_revocation_survives_a_restart(db, store):
    ref = mint(store)
    store.revoke_condition_evidence(ref)
    restarted = ObjectiveStore(db)
    before = restarted.get("build")
    with pytest.raises(ObjectiveStoreError) as excinfo:
        restarted.set_condition("build", "SATISFIED", evidence_ref=ref,
                                expected_version=before.version)
    assert oev.REVOKED in str(excinfo.value)


# ----------------------------------------------- сохранённое прежнее поведение

@pytest.mark.parametrize("condition", ["DEVIATED", "UNKNOWN"])
def test_non_satisfied_conditions_are_untouched(store, condition):
    state = store.set_condition("build", condition, evidence_ref=None,
                                expected_version=store.get("build").version)
    assert (state.condition, state.last_verified_evidence_ref) == (condition, None)
    carried = store.set_condition("build", condition, evidence_ref="carried-forward",
                                  expected_version=state.version)
    assert carried.last_verified_evidence_ref == "carried-forward"


def test_the_cas_contract_still_holds_for_satisfied(store):
    from bossman_shared.objective_store import CompareAndSwapError
    ref = mint(store)
    before = store.get("build")
    with pytest.raises(CompareAndSwapError):
        store.set_condition("build", "SATISFIED", evidence_ref=ref,
                            expected_version=before.version + 7)
    assert store.get("build") == before
    # Проигравший CAS не тратит улику.
    assert store.condition_evidence(ref)["uses"] == 0
    state = store.set_condition("build", "SATISFIED", evidence_ref=ref,
                                expected_version=before.version)
    assert state.version == before.version + 1


def test_an_unsupported_condition_is_still_refused_before_evidence(store):
    for condition in ("satisfied", "GREEN", "", None):
        with pytest.raises(ObjectiveStoreError):
            store.set_condition("build", condition, evidence_ref=mint(store),
                                expected_version=store.get("build").version)


def test_leaving_active_still_drops_a_resolved_evidence_reference(store):
    ref = mint(store)
    state = store.set_condition("build", "SATISFIED", evidence_ref=ref,
                                expected_version=store.get("build").version)
    paused = store.transition("build", "PAUSED", now=NOW, owner_id="owner",
                              expected_version=state.version)
    assert (paused.condition, paused.last_verified_evidence_ref) == ("UNKNOWN", None)


def test_minting_refuses_an_unknown_objective_and_a_bad_window(store):
    with pytest.raises(ObjectiveStoreError):
        store.record_condition_evidence("ghost", condition="SATISFIED", run_id="r")
    for ttl in (0, -1.0, float("nan"), float("inf"), True, "60"):
        with pytest.raises(ObjectiveStoreError):
            store.record_condition_evidence("build", condition="SATISFIED", run_id="r",
                                            ttl_seconds=ttl)
    for run_id in ("", "   ", None, 5):
        with pytest.raises(ObjectiveStoreError):
            store.record_condition_evidence("build", condition="SATISFIED", run_id=run_id)


def test_the_reference_format_is_versioned_and_strict():
    assert oev.parse_ref(oev.make_ref("a" * 32)) == "a" * 32
    for bad in ("x", "", None, "oev1:", "oev2:" + "a" * 32, "cev1:" + "a" * 32,
                "oev1:" + "g" * 32, "oev1:" + "a" * 33, b"oev1:" + b"a" * 32):
        assert oev.parse_ref(bad) is None


def test_a_legacy_green_written_before_the_gate_is_retired_on_open(db, store):
    """База, зелёная ПО СТРОКЕ, не остаётся зелёной после установки гейта.

    Старый билд писал в `last_verified_evidence_ref` что угодно. Если такую
    строку оставить, починка защитила бы только новые записи, а запись
    продолжала бы утверждать зелёный, который никто не может перепроверить.
    """
    with sqlite3.connect(db) as con:                    # то, что писал старый билд
        con.execute("UPDATE v5_objectives SET condition='SATISFIED',"
                    "last_verified_evidence_ref='x' WHERE objective_id='build'")
    reopened = ObjectiveStore(db)
    state = reopened.get("build")
    assert state.condition == "UNKNOWN"
    # Ссылка сохранена как история: «мы больше не знаем» — не «ничего не было».
    assert state.last_verified_evidence_ref == "x"
    assert any(row["detail"].startswith("UNKNOWN:legacy_evidence")
               for row in reopened.journal("build"))
    # Идемпотентно: второе открытие уже нечего снимать.
    assert ObjectiveStore(db).get("build") == state


def test_a_resolved_green_is_not_retired_on_open(db, store):
    ref = mint(store)
    store.set_condition("build", "SATISFIED", evidence_ref=ref,
                        expected_version=store.get("build").version)
    assert ObjectiveStore(db).get("build").condition == "SATISFIED"
