"""V5 N6: рабочее место целей — мастер создания, «Улики», «Ревизии», управление.

До этого страница была инспектором только для чтения: цель нельзя было создать
из UI, а на чём держится её состояние и что в ней меняли — не показывалось
нигде. В карточке V5 так и стояло: «The creation wizard and the Evidence/
Revisions detail tabs are NOT_RUN».

Проверяется владельческий путь целиком: предпросмотр согласия -> создание ->
подписка на источники -> включение -> пауза -> возобновление -> отзыв, и обе
вкладки на живом хранилище.
"""
from __future__ import annotations

import pytest

NOW = 4_000_000_000.0


def spec(objective_id="nightly-build", owner="owner", revision=1, previous_digest=None):
    return {
        "schema_version": 1, "owner_id": owner, "scope_id": "project",
        "objective_id": objective_id, "revision": revision,
        "previous_digest": previous_digest,
        "predicates": [{"predicate_id": "green", "source_ref": "local-build",
                        "field": "passed", "value_type": "boolean",
                        "operator": "eq", "expected": True}],
        "sources": [{"source_ref": "local-build", "source_revision": "v1",
                     "max_age_seconds": 900}],
        "expires_at": NOW + 86_400.0, "priority": 3,
        "allowed_triggers": ["source_change"], "permission_refs": ["repo.read"],
        "conflict_keys": ["project-build"], "cooldown_seconds": 300,
        "limits": {"max_observations": 50, "max_missions": 5,
                   "max_wall_seconds": 600.0, "max_cost_usd": 2.0},
        "stop_conditions": ["owner-revocation"],
    }


@pytest.fixture
def owner_id(env):
    return getattr(env.svc, "owner_id", "owner")


async def create(env, owner_id, **kw):
    return await env.client.post("/api/objectives", json={"spec": spec(owner=owner_id, **kw)})


# ------------------------------------------------------ мастер создания

async def test_the_preview_shows_the_authority_without_creating_anything(env, owner_id):
    """Согласие даётся на конкретные полномочия, бюджет и ритм — и до создания."""
    r = await env.client.post("/api/objectives/preview", json={"spec": spec(owner=owner_id)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is False
    preview = body["preview"]
    assert preview["capabilities"] == ["repo.read"]
    assert preview["budget"] == {"max_missions": 5, "max_observations": 50,
                                 "max_wall_seconds": 600.0, "max_cost_usd": 2.0}
    assert preview["cadence"]["allowed_triggers"] == ["source_change"]
    assert preview["cadence"]["cooldown_seconds"] == 300
    assert preview["success_condition"][0]["field"] == "passed"
    assert preview["scope_id"] == "project"
    assert len(preview["spec_digest"]) == 64

    # Предпросмотр ничего не записал.
    listing = await env.client.get("/api/objectives")
    assert listing.json() == []


async def test_a_malformed_spec_names_the_field_it_refuses(env, owner_id):
    """Мастер обязан показать, КАКОЕ поле не годится, а не «не удалось создать»."""
    broken = spec(owner=owner_id)
    broken["limits"] = {"max_observations": 50}
    r = await env.client.post("/api/objectives/preview", json={"spec": broken})
    assert r.status_code == 400
    assert r.json()["error"]["message"]


async def test_a_spec_without_a_spec_object_is_refused(env):
    r = await env.client.post("/api/objectives", json={"objective_id": "x"})
    assert r.status_code == 400 and "spec" in r.json()["error"]["message"]


async def test_creating_an_objective_does_not_start_it(env, owner_id):
    """Создание не является включением: цель попадает в DRAFT и ни за чем не следит."""
    r = await create(env, owner_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lifecycle"] == "DRAFT"
    assert body["condition"] == "UNKNOWN"
    assert body["enrolled_sources"] == []
    assert body["preview"]["capabilities"] == ["repo.read"]

    status = (await env.client.get("/api/objectives/status")).json()
    assert status["standing_autonomy_enabled"] is False
    assert status["observers_running"] is False
    assert status["counts"]["total"] == 1 and status["counts"]["active"] == 0


async def test_a_spec_claiming_another_owner_is_refused(env, owner_id):
    """Владелец в теле запроса — заявление клиента, а не личность."""
    r = await env.client.post("/api/objectives",
                              json={"spec": spec(owner=f"not-{owner_id}")})
    assert r.status_code == 403


async def test_a_duplicate_objective_id_is_refused(env, owner_id):
    assert (await create(env, owner_id)).status_code == 200
    assert (await create(env, owner_id)).status_code == 409


# ------------------------------------------------------ владельческое управление

async def test_the_owner_runs_the_whole_lifecycle(env, owner_id):
    """Подписка -> включить -> пауза -> возобновить -> отозвать, и отзыв липкий."""
    created = (await create(env, owner_id)).json()
    version = created["version"]

    enrolled = await env.client.post(f"/api/objectives/{created['objective_id']}/enrollment",
                                     json={"sources": ["local-build"], "version": version})
    assert enrolled.status_code == 200, enrolled.text
    version = enrolled.json()["version"]
    assert enrolled.json()["enrolled_sources"] == ["local-build"]

    for requested, expected in (("ACTIVE", "ACTIVE"), ("PAUSED", "PAUSED"),
                                ("ACTIVE", "ACTIVE"), ("REVOKED", "REVOKED")):
        r = await env.client.post(f"/api/objectives/{created['objective_id']}/lifecycle",
                                  json={"lifecycle": requested, "version": version})
        assert r.status_code == 200, r.text
        assert r.json()["lifecycle"] == expected
        version = r.json()["version"]

    # Отзыв необратим: воскресить цель нельзя.
    resurrect = await env.client.post(f"/api/objectives/{created['objective_id']}/lifecycle",
                                      json={"lifecycle": "ACTIVE", "version": version})
    assert resurrect.status_code == 400


async def test_a_stale_version_is_a_conflict_not_a_silent_overwrite(env, owner_id):
    created = (await create(env, owner_id)).json()
    first = await env.client.post(f"/api/objectives/{created['objective_id']}/lifecycle",
                                  json={"lifecycle": "ACTIVE", "version": created["version"]})
    assert first.status_code == 200
    second = await env.client.post(f"/api/objectives/{created['objective_id']}/lifecycle",
                                   json={"lifecycle": "PAUSED", "version": created["version"]})
    assert second.status_code == 409


# ------------------------------------------------------ вкладка «Улики»

async def test_the_evidence_tab_never_calls_an_unverified_state_verified(env, owner_id):
    created = (await create(env, owner_id)).json()
    r = await env.client.get(f"/api/objectives/{created['objective_id']}/evidence")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["condition"] == "UNKNOWN"
    assert body["verified"] is False
    assert body["last_verified_evidence_ref"] is None
    assert "не выдаётся за подтверждённое" in body["note"]
    assert body["usage"] == {"observations": 0, "missions": 0,
                             "wall_seconds": 0.0, "cost_usd": 0.0}
    assert body["open_reservations"] == []
    assert [row["event"] for row in body["journal"]] == ["imported"]   # только создание


async def test_the_evidence_tab_reports_a_verified_condition_with_its_reference(env, owner_id):
    """Положительный контроль: подтверждённое состояние показывается вместе со
    ссылкой на улику, по которой его можно перепроверить."""
    from bcc.features.objectives import _store_path
    from bossman_shared.objective_store import ObjectiveStore

    created = (await create(env, owner_id)).json()
    store = ObjectiveStore(_store_path(env.svc))
    store.set_condition(created["objective_id"], "SATISFIED", evidence_ref="ev-1",
                        expected_version=created["version"])

    body = (await env.client.get(f"/api/objectives/{created['objective_id']}/evidence")).json()
    assert body["condition"] == "SATISFIED"
    assert body["verified"] is True and body["last_verified_evidence_ref"] == "ev-1"
    assert "подтверждённым только при свежем" in body["note"]


async def test_the_evidence_tab_shows_an_open_reservation_as_uncertainty(env, owner_id):
    """Незакрытый резерв — неопределённость после падения, и он виден владельцу."""
    from bcc.features.objectives import _store_path
    from bossman_shared.objective_store import ObjectiveStore

    created = (await create(env, owner_id)).json()
    store = ObjectiveStore(_store_path(env.svc))
    state = store.transition(created["objective_id"], "ACTIVE", now=NOW, owner_id=owner_id,
                             expected_version=created["version"])
    with store._connect() as con:
        con.execute(
            "INSERT INTO v5_reservations(reservation_id,objective_id,proposal_id,created_at,"
            "state,payload) VALUES('r-1',?,'p-1',?,'RESERVED','{}')",
            (created["objective_id"], NOW))
    body = (await env.client.get(f"/api/objectives/{created['objective_id']}/evidence")).json()
    assert [r["reservation_id"] for r in body["open_reservations"]] == ["r-1"]
    assert state.lifecycle == "ACTIVE"


async def test_the_evidence_tab_404s_for_an_unknown_objective(env):
    assert (await env.client.get("/api/objectives/nope/evidence")).status_code == 404


# ------------------------------------------------------ вкладка «Ревизии»

async def test_the_revisions_tab_shows_what_changed_not_only_that_it_changed(env, owner_id):
    from bcc.features.objectives import _store_path
    from bossman_shared.objective_spec import ObjectiveSpec
    from bossman_shared.objective_store import ObjectiveStore

    created = (await create(env, owner_id)).json()
    store = ObjectiveStore(_store_path(env.svc))
    first = store.get_spec(created["objective_id"])
    second = ObjectiveSpec.from_dict(
        {**spec(owner=owner_id, revision=2, previous_digest=first.digest),
         "limits": {"max_observations": 50, "max_missions": 9,
                    "max_wall_seconds": 600.0, "max_cost_usd": 2.0}},
        previous=first)
    store.revise(created["objective_id"], second, owner_id=owner_id,
                 expected_version=created["version"], now=NOW)

    body = (await env.client.get(f"/api/objectives/{created['objective_id']}/revisions")).json()
    assert body["current"]["revision"] == 2
    assert body["current"]["summary"]["budget"]["max_missions"] == 9
    assert [row["revision"] for row in body["superseded"]] == [1]
    assert body["superseded"][0]["spec"]["limits"]["max_missions"] == 5    # прежнее тело
    assert body["superseded"][0]["superseded_at"] == NOW
    assert [e["event"] for e in body["revision_events"]] == ["revised"]
    assert body["history_complete"] is True and body["note"] == ""


async def test_an_objective_older_than_the_history_says_so(env, owner_id):
    """Отсутствие истории не выдаётся за отсутствие изменений."""
    from bcc.features.objectives import _store_path
    from bossman_shared.objective_store import ObjectiveStore

    created = (await create(env, owner_id)).json()
    store = ObjectiveStore(_store_path(env.svc))
    with store._connect() as con:            # цель, пережившая апгрейд до истории
        con.execute("INSERT INTO v5_journal(objective_id,at,event,detail) "
                    "VALUES(?,?,'revised','0->1')", (created["objective_id"], NOW))

    body = (await env.client.get(f"/api/objectives/{created['objective_id']}/revisions")).json()
    assert body["superseded"] == []
    assert [e["event"] for e in body["revision_events"]] == ["revised"]
    assert body["history_complete"] is False
    assert "не сохранена" in body["note"]


async def test_the_revisions_tab_404s_for_an_unknown_objective(env):
    assert (await env.client.get("/api/objectives/nope/revisions")).status_code == 404
