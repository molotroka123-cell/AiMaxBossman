"""Регрессия BCC-V2-SESSION-20783913FA36-P1-FIX-001.

Наблюдённый дефект: владелец просил открыть YouTube в браузере; агент без
инструментов ответил текстовым отказом («сделайте вручную»); единственный на
тот момент gate_completion (review_gate) не был настроен на задачу и вернул
NOT_APPLICABLE; движок трактовал «ни одного FAIL» как «можно завершать» —
задача стала completed, хотя реального действия не произошло.

TEST1..TEST4 — из требований патча:
  TEST1 — текстовый отказ не завершает action-задачу.
  TEST2 — информационная задача с валидным текстовым ответом завершается как
          обычно (страховка от гиперкоррекции: не ловим ЛЮБОЙ ответ модели).
  TEST3 — подтверждённое действие (детерминированный харнесс: строка в
          tool_calls) завершается успешно.
  TEST4 — NOT_APPLICABLE другого гейта не означает «действие подтверждено»:
          evaluation_not_applicable != side_effect_verified.
TEST5 (стухшая браузерная сессия) — в tests/test_feat_browser.py, рядом с
остальными browser-тестами того же фичемодуля (см. P1-D).
"""
import sqlalchemy as sa

from bcc import db as dbm
from bcc.features.action_gate import looks_like_action_refusal

from .conftest import FakeAdapter
from .helpers import make_stack

# Тот самый двуязычный (RU + вплотную-CJK) ответ из реальной сессии
# 20783913fa36 — эталон, а не синтетика.
REAL_REFUSAL = (
    "Извините за путаницу. Я не могу直接操作您的计算机. 不过，我可以指导您如何"
    "手动完成这些步骤。请按照以下步骤在您选择的浏览器中打开YouTube并播放"
    "《Never Gonna Give You Up》这首歌："
)


async def _run_once(env):
    """Прогнать очередь до пустоты (worker в тестах выключен)."""
    for _ in range(10):
        run_id = await env.svc.engine.claim()
        if run_id is None:
            return
        await env.svc.engine.execute(run_id)


async def test_real_session_text_is_detected_as_refusal():
    # Явная защита от регрессии самого детектора отдельно от полного
    # прогона движка — если это перестанет матчиться, TEST1 объяснит меньше,
    # чем должен.
    assert looks_like_action_refusal(REAL_REFUSAL)


async def test1_textual_refusal_cannot_complete_action_task(env):
    """Точное воспроизведение сессии 20783913fa36: агент без инструментов,
    текстовый отказ вместо действия. Задача НЕ должна стать completed."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(REAL_REFUSAL)
    stack = await make_stack(env.client, prompt="Открой на моём компьютере в браузере YouTube")
    await _run_once(env)

    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] != "completed"
    # Честный терминал (см. action_gate._gate: agent без action-инструментов
    # блокируется сразу, без бесконечного requeue).
    assert task["status"] == "failed"

    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(dbm.tool_calls))).fetchall()
    assert rows == []          # ни одного подтверждённого вызова инструмента


async def test2_informational_answer_still_completes_normally(env):
    """Страховка от гиперкоррекции: обычный текстовый ответ на
    информационный запрос должен завершаться, как и раньше."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        "Вот краткое содержание статьи: главный вывод — рост на 12% за квартал.")
    stack = await make_stack(env.client, prompt="Сделай краткое содержание статьи")
    await _run_once(env)

    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "completed"


async def test3_verified_action_via_deterministic_harness_completes(env):
    """Детерминированный харнесс имитирует РЕАЛЬНЫЙ вызов инструмента (строка
    в tool_calls, как её оставляет настоящий tool loop) до того, как гейт
    читает `answer`. Даже если модель ЗАТЕМ подмешала отказную фразу в текст,
    наличие вызова инструмента — сильнее: гейт обязан вернуть NOT_APPLICABLE
    и не мешать завершению."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(REAL_REFUSAL)
    stack = await make_stack(env.client, prompt="Открой YouTube в браузере")

    run_id = await env.svc.engine.claim()
    assert run_id is not None
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(dbm.tool_calls).values(
            run_id=run_id, task_id=stack["task"]["id"], tool="browser.open",
            status="executed"))
        await s.commit()
    await env.svc.engine.execute(run_id)

    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] == "completed"


async def test4_not_applicable_verdict_does_not_certify_side_effect(env):
    """evaluation_not_applicable != side_effect_verified — ровно то, что в
    реальной сессии 20783913fa36 сломалось: единственный гейт (review_gate) не
    настроен на задачу и честно возвращает NOT_APPLICABLE, а движок раньше
    трактовал «ни одного FAIL» как «подтверждено». Ответ здесь — обычный
    текст без фразы отказа, так что САМ action_gate тоже говорит
    NOT_APPLICABLE (он не единственный источник блокировки, это важно для
    инварианта: проверяем НЕ action_gate, а общее правило движка). Второй,
    независимый гейт (имитирует свежую проверку доказательств — «вызовов
    инструмента не было») возвращает FAIL. NOT_APPLICABLE не должен погасить
    этот FAIL."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Сделано.")

    async def fresh_evidence_check(task, run_id, answer):
        return {"verdict": "FAIL", "reasons": "no_verified_action", "requeue": False,
               "status": "failed"}
    env.svc.engine.add_hook("gate_completion", fresh_evidence_check)

    stack = await make_stack(env.client, prompt="Открой на компьютере YouTube и включи видео")
    await _run_once(env)

    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()["task"]
    assert task["status"] != "completed"

    async with env.svc.db.session() as s:
        events = (await s.execute(sa.select(dbm.events)
                                  .where(dbm.events.c.kind == "evaluation.completed"))).fetchall()
    verdicts = [e._mapping["data"].get("verdict") for e in events]
    # Оба вердикта реально были эмитированы за этот прогон — NOT_APPLICABLE
    # (от action_gate — ответ без фразы отказа, ему сказать нечего) зафиксирован
    # как факт РЯДОМ с FAIL другого гейта, а не молча прочитан как «действие
    # подтверждено» и не подавил его.
    assert "NOT_APPLICABLE" in verdicts
    assert "FAIL" in verdicts
