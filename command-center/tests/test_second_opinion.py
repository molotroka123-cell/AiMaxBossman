"""Гейт второго мнения: необратимое действие не проходит без независимого опровержения."""
from __future__ import annotations

from bcc.features import second_opinion as so

EXECUTOR = so.Actor(principal_id="coder:qwen-14b", model_id="qwen-14b", run_id="run-1")
INDEPENDENT = so.Actor(principal_id="human:owner", model_id="", run_id="run-2",
                       independence_class="human")


def _intent(kind: str = "file.delete", **kw) -> so.Intent:
    return so.Intent(kind=kind, executor=EXECUTOR,
                     summary=kw.get("summary", "убрать старые логи"),
                     target=kw.get("target", "/data/logs"))


def _body(intent: so.Intent, opinion: so.Opinion | None = None) -> dict:
    body = {"kind": intent.kind, "summary": intent.summary, "target": intent.target,
            "executor": intent.executor.as_dict()}
    if opinion is not None:
        body["opinion"] = {"verdict": opinion.verdict, "reason": opinion.reason,
                           "verifier": opinion.verifier.as_dict()}
    return body


# ---------- классификация ----------

def test_irreversible_catalog_is_data_and_unknown_kind_is_irreversible():
    """Обратимость берётся из явного каталога, а незнакомый вид необратим (fail-closed)."""
    assert so.classify("file.delete").reversible is False
    assert so.classify("email.send").category == "egress"
    assert so.classify("payment.charge").category == "spend"
    assert so.classify("file.read").reversible is True
    unknown = so.classify("frobnicate.everything")
    assert unknown.reversible is False and unknown.category == "unknown"


# ---------- гейт ----------

async def test_irreversible_without_second_opinion_does_not_pass(monkeypatch):
    """Необратимое действие без второго мнения не проходит: проверяющего нет —
    значит проверки не было, а не «всё в порядке»."""
    monkeypatch.setenv(so.FLAG, "1")
    d = await so.run_gate(_intent())
    assert d.applied is True and d.allowed is False
    assert d.opinion.verdict == so.COULD_NOT_CHECK
    assert "проверяющий не подключён" in d.opinion.reason


async def test_same_principal_verifier_is_rejected_and_action_blocked(monkeypatch):
    """Тот же principal (пусть и под другим ролевым префиксом) независимым не
    считается, и действие НЕ проходит, даже когда вердикт «не опровергнуто»."""
    monkeypatch.setenv(so.FLAG, "1")
    self_check = so.Opinion(so.NOT_REFUTED,
                            so.Actor(principal_id="verifier:Qwen-14B", model_id="other-model",
                                     run_id="run-9", independence_class="cross_model"),
                            "по-моему всё в порядке")
    d = await so.run_gate(_intent(), opinion=self_check)
    assert d.allowed is False
    assert any("один principal" in e for e in d.independence)


async def test_same_run_and_same_model_are_not_independent(monkeypatch):
    """Другой principal, но тот же запуск или та же модель — тоже самопроверка."""
    monkeypatch.setenv(so.FLAG, "1")
    same_run = so.Opinion(so.NOT_REFUTED,
                          so.Actor("other-agent", model_id="other", run_id="run-1",
                                   independence_class="cross_model"), "ок")
    same_model = so.Opinion(so.NOT_REFUTED,
                            so.Actor("other-agent", model_id="qwen-14b", run_id="run-7",
                                     independence_class="cross_model"), "ок")
    weak_class = so.Opinion(so.NOT_REFUTED,
                            so.Actor("other-agent", model_id="other", run_id="run-7",
                                     independence_class="self"), "ок")
    for opinion, marker in ((same_run, "того же запуска"), (same_model, "тот же экземпляр модели"),
                            (weak_class, "independence_class")):
        d = await so.run_gate(_intent(), opinion=opinion)
        assert d.allowed is False, marker
        assert any(marker in e for e in d.independence), (marker, d.independence)


async def test_could_not_check_blocks_exactly_like_refuted(monkeypatch):
    """Третье состояние — не согласие: «не смог проверить» блокирует так же,
    как «опровергнуто», а проходит только «не опровергнуто»."""
    monkeypatch.setenv(so.FLAG, "1")
    verdicts = {}
    for verdict in (so.REFUTED, so.COULD_NOT_CHECK, so.NOT_REFUTED):
        d = await so.run_gate(_intent(), opinion=so.Opinion(verdict, INDEPENDENT, "причина"))
        verdicts[verdict] = d.allowed
    assert verdicts == {so.REFUTED: False, so.COULD_NOT_CHECK: False, so.NOT_REFUTED: True}


async def test_broken_verifier_is_could_not_check_not_consent(monkeypatch):
    """Упавший проверяющий и мусор вместо Opinion не разрешают действие."""
    monkeypatch.setenv(so.FLAG, "1")

    def boom(intent, challenge):
        raise RuntimeError("нет доступа")

    async def garbage(intent, challenge):
        return "всё хорошо, разрешаю"

    for fn in (boom, garbage):
        d = await so.run_gate(_intent(), verifier=fn)
        assert d.allowed is False and d.opinion.verdict == so.COULD_NOT_CHECK


async def test_reversible_action_passes_without_second_opinion(monkeypatch):
    """Обратимое действие проходит без проверяющего: гейт не должен стоить
    второго мнения там, где ошибку можно откатить."""
    monkeypatch.setenv(so.FLAG, "1")
    d = await so.run_gate(_intent(kind="snapshot.create"))
    assert d.applied is True and d.allowed is True and d.opinion is None
    assert "обратимо" in " ".join(d.reasons)


async def test_independent_verifier_lets_action_through(monkeypatch):
    """Полный проход: независимый проверяющий искал причину и не нашёл."""
    monkeypatch.setenv(so.FLAG, "1")

    async def verifier(intent, challenge):
        assert so.challenge_is_adversarial(challenge)
        return so.Opinion(so.NOT_REFUTED, INDEPENDENT, "объект и объём совпадают с замыслом")

    d = await so.run_gate(_intent(), verifier=verifier)
    assert d.allowed is True and d.opinion.verifier.principal_id == "human:owner"


# ---------- запрос проверяющему ----------

def test_challenge_demands_refutation_not_confirmation():
    """Запрос поручает опровергнуть и не содержит просьбы подтвердить."""
    text = so.build_challenge(_intent(kind="email.send", target="client@example.com"))
    low = text.lower()
    assert "опроверг" in low and "почему это действие ошибочно" in low
    assert "худший случай" in low
    assert not any(m in low for m in so.CONFIRM_MARKERS)
    assert so.challenge_is_adversarial(text) is True
    # обратная сторона проверки: просьба согласиться распознаётся как негодная
    assert so.challenge_is_adversarial("Подтверди, что действие верно") is False
    # три вердикта названы прямо, включая блокирующий третий
    assert all(v in text for v in (so.REFUTED, so.NOT_REFUTED, so.COULD_NOT_CHECK))


# ---------- ручки ----------

async def test_flag_off_keeps_previous_behaviour(env, monkeypatch):
    """При выключенном флаге гейт не применяется: ничего не запрещает, ничего
    не пишет, а меняющая состояние ручка честно отказывает."""
    monkeypatch.delenv(so.FLAG, raising=False)
    d = await so.run_gate(_intent())
    assert d.applied is False and d.allowed is True

    r = await env.client.post("/api/second-opinion/check", json=_body(_intent()))
    assert r.status_code == 409
    rules = (await env.client.get("/api/second-opinion")).json()
    assert rules["enabled"] is False and rules["irreversible"]
    listed = (await env.client.get("/api/second-opinion/decisions")).json()
    assert listed["enabled"] is False and listed["decisions"] == []


async def test_check_endpoint_blocks_and_records_both_participants(env, monkeypatch):
    """Решение записано с обоими участниками, вердиктом и причиной — и в журнале
    ручки, и событием на шине."""
    monkeypatch.setenv(so.FLAG, "1")
    opinion = so.Opinion(so.REFUTED, INDEPENDENT, "в каталоге лежат ещё не выгруженные логи")
    r = await env.client.post("/api/second-opinion/check", json=_body(_intent(), opinion))
    assert r.status_code == 200
    payload = r.json()
    assert payload["allowed"] is False
    assert "опроверг" in payload["challenge"].lower()

    listed = (await env.client.get("/api/second-opinion/decisions")).json()
    assert listed["count"] == 1
    rec = listed["decisions"][0]
    assert rec["executor"]["principal_id"] == EXECUTOR.principal_id
    assert rec["verifier"]["principal_id"] == INDEPENDENT.principal_id
    assert rec["verdict"] == so.REFUTED
    assert "не выгруженные логи" in " ".join(rec["reasons"])

    events = await env.svc.bus.recent(20)
    ev = [e for e in events if e["kind"] == "second_opinion.decision"]
    assert len(ev) == 1
    data = ev[0]["data"]
    assert data["executor"] == EXECUTOR.principal_id
    assert data["verifier"] == INDEPENDENT.principal_id
    assert data["allowed"] is False


async def test_check_endpoint_uses_plugged_verifier_and_blocks_dependent_one(env, monkeypatch):
    """Проверяющий подключается снаружи функцией; неотличимый от исполнителя
    проверяющий не спасает действие даже через ручку."""
    monkeypatch.setenv(so.FLAG, "1")
    seen: list[str] = []

    async def verifier(intent, challenge):
        seen.append(challenge)
        # проверяющий выдаёт себя за другого, но это тот же principal
        return so.Opinion(so.NOT_REFUTED,
                          so.Actor("executor:Qwen-14B", model_id="another", run_id="run-42",
                                   independence_class="external_tool"), "возражений нет")

    so.set_verifier(env.app, verifier)
    try:
        r = await env.client.post("/api/second-opinion/check", json=_body(_intent()))
    finally:
        so.set_verifier(env.app, None)
    assert r.status_code == 200 and r.json()["allowed"] is False
    assert seen and so.challenge_is_adversarial(seen[0])
    rec = r.json()["decision"]
    assert rec["verdict"] == so.NOT_REFUTED and rec["independence"]
