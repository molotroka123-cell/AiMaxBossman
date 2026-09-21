"""Владельческие сценарии 1–4: вопрос ИИ и контекст.

Цепочки владельца, а не вызовы функций: запрос владельца доходит до модели
через собранный контекст, контекст переживает следующий ход, контекст проекта
вспоминается после перезапуска и НЕ протекает в соседний проект.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spine_fixtures as fx  # noqa: E402
from bossman_shared.objective_context import (ContextRefusal, MemoryRecord,  # noqa: E402
                                              MEMORY_AUTHORITY, MEMORY_LABEL,
                                              ToolEntry, as_memory_data,
                                              assemble_v5_context)
from bossman_shared.objective_store import ObjectiveStore  # noqa: E402
from scenario_runner import PRODUCT_CONTRACTS, scenario  # noqa: E402

POLICY = {"cloud": "never", "privacy": "local_only"}
REGISTRY = [ToolEntry(tool_id="fs.write", capability=fx.CAPABILITY, required_grant=fx.PERMISSION)]


def _slice_for(ctx, store, spec, *, memory=(), observations=(), enrolled=(fx.SOURCE,)):
    mission = fx.mission_of(store, spec, ctx.path("work", "result.txt"))
    return assemble_v5_context(spec=spec, mission=mission, lifecycle="ACTIVE", policy=POLICY,
                               now=fx.NOW, observations=list(observations),
                               enrolled_sources=enrolled, memory=list(memory),
                               registry=REGISTRY, grants=(fx.PERMISSION,))


@scenario(id="OS-01", depth=PRODUCT_CONTRACTS)
def os01_owner_asks_a_plain_question(ctx) -> None:
    """Запрос владельца → цель → срез контекста → модель → видимый ответ."""
    store, spec, _ = fx.ready_store(ctx.path("state", "objectives.sqlite3"))
    slice_ = _slice_for(ctx, store, spec, observations=[fx.observation(spec)])
    goal = slice_.section("mission")["goal"]

    answer = ctx.require_model(ctx.ai.chat([
        {"role": "system", "content": "Ты помощник Bossman. Отвечай коротко, по-русски."},
        {"role": "user", "content": f"Задача владельца: {goal}. Ответь одной фразой, что сделаешь."},
    ], max_tokens=120))

    ctx.positive("модель ответила владельцу свободным текстом",
                 len(answer.text.strip()) >= 3, f"символов={len(answer.text)}")
    ctx.positive("ответ пришёл в форме, которую читает ядро (bossman.llm.chat)",
                 answer.as_core_message().get("role") == "assistant"
                 and "_usage" in answer.as_core_message())
    ctx.positive("вопрос собран из среза контекста, а не из воздуха",
                 goal and slice_.objective_digest == spec.digest, f"goal={goal[:60]}")
    ctx.negative("срез не тащил в модель ничего вне цели владельца",
                 set(slice_.names()) <= {"policy", "mission", "objective_predicates",
                                         "world_state", "observations", "capabilities",
                                         "skill", "memory"},
                 f"секции={slice_.names()}")


@scenario(id="OS-02", depth=PRODUCT_CONTRACTS)
def os02_context_survives_next_turn(ctx) -> None:
    """Ход 1 сообщает факт, ход 2 обязан им воспользоваться."""
    store, spec, _ = fx.ready_store(ctx.path("state", "objectives.sqlite3"))
    slice_ = _slice_for(ctx, store, spec)
    secret = "ГРАНАТ"

    first = ctx.require_model(ctx.ai.chat([
        {"role": "system", "content": "Отвечай одним словом."},
        {"role": "user", "content": f"Запомни кодовое слово проекта: {secret}. "
                                    "Ответь одним словом: ЗАПОМНИЛ"},
    ], max_tokens=16))
    ctx.positive("первый ход принят моделью", bool(first.text.strip()))

    # Ход 2 несёт историю хода 1 — ровно так ядро продолжает разговор.
    second = ctx.require_model(ctx.ai.chat([
        {"role": "system", "content": "Отвечай одним словом."},
        {"role": "user", "content": f"Запомни кодовое слово проекта: {secret}. "
                                    "Ответь одним словом: ЗАПОМНИЛ"},
        {"role": "assistant", "content": first.text},
        {"role": "user", "content": "Назови кодовое слово проекта одним словом."},
    ], max_tokens=16))
    ctx.positive("на следующем ходе модель использует факт предыдущего хода",
                 secret.lower() in second.text.lower(), f"ответ={second.text[:40]}")

    # Отрицательный контроль: без истории тот же вопрос не может быть отвечен верно.
    blind = ctx.ai.chat([
        {"role": "system", "content": "Отвечай одним словом."},
        {"role": "user", "content": "Назови кодовое слово проекта одним словом."},
    ], max_tokens=16)
    ctx.negative("без переданного контекста факт не воспроизводится",
                 not (blind.ok and secret.lower() in blind.text.lower()),
                 f"outcome={blind.outcome} ответ={blind.text[:40]}")
    ctx.negative("срез контекста не расширился между ходами",
                 slice_.digest == _slice_for(ctx, store, spec).digest)


@scenario(id="OS-03", depth=PRODUCT_CONTRACTS)
def os03_project_context_is_recalled(ctx) -> None:
    """Факт проекта переживает перезапуск и возвращается в срез с происхождением."""
    path = ctx.path("state", "objectives.sqlite3")
    store, spec, _ = fx.ready_store(path)
    store.record_observation(fx.OBJECTIVE_A, observed_at=fx.NOW - 5.0, count=1,
                             expected_version=store.get(fx.OBJECTIVE_A).version)

    del store  # перезапуск: всё, что было в памяти процесса, исчезло
    reopened = ObjectiveStore(path)
    recalled = reopened.get(fx.OBJECTIVE_A)
    ctx.positive("после перезапуска проект вспоминается из долговечной записи",
                 recalled.objective_id == fx.OBJECTIVE_A and recalled.lifecycle == "ACTIVE"
                 and recalled.observations_used == 1,
                 f"наблюдений={recalled.observations_used}")

    fact = MemoryRecord(memory_id="m-проект", objective_digest=spec.digest,
                        observed_at=fx.NOW - 5.0,
                        text="сборка проекта чинится правкой lock-файла",
                        provenance="журнал проекта A")
    slice_ = _slice_for(ctx, reopened, spec, memory=[fact],
                        observations=[fx.observation(spec)])
    memory_section = slice_.section("memory")
    ctx.positive("вспомненный факт проекта попал в срез с происхождением",
                 memory_section and memory_section[0]["provenance"] == "журнал проекта A"
                 and memory_section[0]["label"] == MEMORY_LABEL,
                 f"записей={len(memory_section)}")
    ctx.positive("предикаты цели проекта вспомнились вместе с фактом",
                 slice_.section("objective_predicates")[0]["predicate_id"] == "p-green")

    ctx.negative("вспомненный факт остаётся данными и не становится полномочием",
                 memory_section[0]["authority"] == MEMORY_AUTHORITY)
    ctx.refused("факт, не принадлежащий этой цели, не вспоминается",
                lambda: as_memory_data(
                    [MemoryRecord(memory_id="m-чужой", objective_digest="0" * 64,
                                  observed_at=fx.NOW, text="чужое", provenance="чужой журнал")],
                    objective_digest=spec.digest),
                ContextRefusal)


@scenario(id="OS-04", depth=PRODUCT_CONTRACTS)
def os04_project_a_does_not_leak_into_b(ctx) -> None:
    """Отрицательный контроль всей памяти: A не имеет права появиться в B."""
    path = ctx.path("state", "objectives.sqlite3")
    store_a, spec_a, _ = fx.ready_store(path)
    spec_b = fx.make_spec(objective_id=fx.OBJECTIVE_B, scope_id=fx.SCOPE_B,
                          source_ref="src:build-b")
    store_b = ObjectiveStore(path)
    state_b = store_b.create(spec_b)
    fx.activate(store_b, state_b, objective_id=fx.OBJECTIVE_B, source_ref="src:build-b")

    mission_b = fx.mission_of(store_b, spec_b, ctx.path("work-b", "result.txt"),
                              objective_id=fx.OBJECTIVE_B, scope_id=fx.SCOPE_B,
                              source_ref="src:build-b")
    own = fx.observation(spec_b, source_ref="src:build-b", scope_id=fx.SCOPE_B,
                         observation_id="obs-b")
    alien = fx.observation(spec_a, source_ref=fx.SOURCE, scope_id=fx.SCOPE_A)
    secret_a = MemoryRecord(memory_id="m-a", objective_digest=spec_a.digest,
                            observed_at=fx.NOW - 50.0,
                            text="внутренняя переписка проекта A",
                            provenance="project-a/journal")

    slice_b = assemble_v5_context(spec=spec_b, mission=mission_b, lifecycle="ACTIVE",
                                  policy=POLICY, now=fx.NOW, observations=[own],
                                  enrolled_sources=("src:build-b",), registry=REGISTRY,
                                  grants=(fx.PERMISSION,))
    ctx.positive("срез проекта B построен и несёт именно проект B",
                 slice_b.objective_digest == spec_b.digest
                 and slice_b.section("policy")["scope_id"] == fx.SCOPE_B)
    ctx.positive("в срезе B видны только источники самого B",
                 all(o["source_ref"] == "src:build-b" for o in slice_b.section("observations")))

    body = "\n".join(payload for _, payload in slice_b.sections)
    ctx.negative("в срезе B нет отпечатка, области и имени проекта A",
                 spec_a.digest not in body and fx.SCOPE_A not in body
                 and fx.OBJECTIVE_A not in body)
    ctx.refused("наблюдение проекта A отвергнуто, а не «учтено на всякий случай»",
                lambda: assemble_v5_context(spec=spec_b, mission=mission_b, lifecycle="ACTIVE",
                                            policy=POLICY, now=fx.NOW,
                                            observations=[alien, own],
                                            enrolled_sources=("src:build-b",)),
                ContextRefusal)
    ctx.refused("память проекта A отвергнута при сборке контекста B",
                lambda: as_memory_data([secret_a], objective_digest=spec_b.digest),
                ContextRefusal)
    ctx.refused("источник проекта A нельзя записать в контекст проекта B",
                lambda: assemble_v5_context(spec=spec_b, mission=mission_b, lifecycle="ACTIVE",
                                            policy=POLICY, now=fx.NOW,
                                            enrolled_sources=(fx.SOURCE,)),
                ContextRefusal)
    _ = store_a
