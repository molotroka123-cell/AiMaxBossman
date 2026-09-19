"""Владельческие сценарии 41–45: контекст и память ВГЛУБЬ.

Первые двадцать закрыли базовое: факт доживает до следующего хода, проект
вспоминается, A не протекает в B. Здесь проверяется то, что за этим:

* OS-41 — контекст переживает смерть ПРОЦЕССА, а не оператор `del`. Новый
  процесс операционной системы поднимает ту же память с диска и собирает ТОТ ЖЕ
  контекст с тем же отпечатком продукта.
* OS-42 — у наблюдения есть отметка времени и предельный возраст, и
  просроченное ЗАКРЫВАЕТ путь: устаревшая улика не ставит «зелёное», а
  устаревший факт мира читается как UNKNOWN, а не как последнее известное.
* OS-43 — редакция: секрет и приватное поле не доходят до собранного контекста,
  и это доказано ПОИСКОМ, у которого есть отрицательный контроль — тот же
  поиск находит секрет в НЕредактированном срезе.
* OS-44 — вытеснение по размеру: последняя проверенная улика остаётся, старое
  выбрасывается ЦЕЛИКОМ (не пересказывается), а потеря НАЗЫВАЕТСЯ.
* OS-45 — в контексте проекта A нет ни одной записи проекта B, и это доказано
  поиском по собранному тексту, а не доверием к сборщику.

ПРИВЯЗКА К РЕЕСТРУ. Канонический реестр — `owner_scenarios.json`, и правка
чужого файла здесь запрещена. Строки для OS-41…OS-50 лежат отдельно, в
`registry_41_50.json`, и должны быть сведены в канонический реестр владельцем.
Пока они не сведены, реализация НЕ регистрируется: каркас считает реализацию
без строки реестра смертельной ошибкой, и молчаливая регистрация уронила бы
прогон всех двадцати уже зелёных сценариев. Как только строка появится в
`owner_scenarios.json`, привязка произойдёт сама — без правки этого файла.

Зависимости: стандартная библиотека и `bossman_shared` (ставится корневым
`pip install -e .`). `bossman.obs` в OS-43 импортируется ВНУТРИ сценария и
объявлен способностью `bossman_core`: без неё сценарий не исполняется вовсе.
Ни sqlalchemy, ни asyncpg — ни прямо, ни транзитивно (BL-085).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spine_fixtures as fx  # noqa: E402
from bossman_shared.mission_ir import MissionIR  # noqa: E402
from bossman_shared.objective_context import (ContextRefusal, MemoryRecord,  # noqa: E402
                                              MAX_MEMORY_RECORDS, MEMORY_AUTHORITY,
                                              MEMORY_LABEL, SECTION_ORDER, UNKNOWN,
                                              ToolEntry, WorldFact, as_memory_data,
                                              assemble_v5_context)
from bossman_shared.objective_store import ObjectiveStore, ObjectiveStoreError  # noqa: E402
from bossman_shared.objective_world_state import (WorldFact as StateFact,  # noqa: E402
                                                  WorldStateError, WorldStateProjection,
                                                  require_fresh)
from scenario_runner import PRODUCT_CONTRACTS, scenario  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
POLICY = {"cloud": "never", "privacy": "local_only"}
REGISTRY = [ToolEntry(tool_id="fs.write", capability=fx.CAPABILITY, required_grant=fx.PERMISSION)]


# ------------------------------------------------------------------ привязка
def _declared_ids() -> frozenset[str]:
    """Идентификаторы, которые КАНОНИЧЕСКИЙ реестр действительно объявляет."""
    try:
        data = json.loads((HERE / "owner_scenarios.json").read_text(encoding="utf-8"))
        return frozenset(row["id"] for row in data["scenarios"])
    except BaseException:  # noqa: BLE001 — нечитаемый реестр это «ничего не объявлено»
        return frozenset()


DECLARED = _declared_ids()


def bind(scenario_id: str, *, depth: str = PRODUCT_CONTRACTS):
    """Привязать реализацию, ЕСЛИ строка уже сведена в канонический реестр."""
    def wrap(func):
        if scenario_id in DECLARED:
            scenario(id=scenario_id, depth=depth)(func)
        return func
    return wrap


# ----------------------------------------------------------- общие строители
def assemble(spec, mission, *, now, memory=(), observations=(), world_facts=(),
             enrolled=(fx.SOURCE,), byte_budget=32_768, lifecycle="ACTIVE", extra=None):
    """Один и тот же вызов сборки контекста продукта во всех пяти сценариях.

    Миссия передаётся ГОТОВОЙ: каждый новый допуск порождает новое предложение и
    новую бронь, а цель владельца объявляет предел числа миссий. Сценарий,
    собирающий контекст сто раз, упёрся бы в квоту и «доказал» бы её, а не
    вытеснение.
    """
    return assemble_v5_context(spec=spec, mission=mission, lifecycle=lifecycle, policy=POLICY,
                               now=now, observations=list(observations),
                               enrolled_sources=enrolled, memory=list(memory),
                               world_facts=list(world_facts), registry=REGISTRY,
                               grants=(fx.PERMISSION,), byte_budget=byte_budget, extra=extra)


def slice_text(slice_) -> str:
    """Текст, по которому ищут. Обе формы одного и того же содержимого.

    Каноническая сборка пишет JSON с `ensure_ascii=True`: «пароль» уезжает в
    вызов как `\\u043f\\u0430\\u0440...`. Поиск ТОЛЬКО по этой форме отвечал бы
    «секрета нет» ровно тогда, когда секрет русский, — то есть давал бы ложное
    «чисто» на самом интересном случае. Поэтому в стог кладутся обе формы:
    ровно то, что уйдёт в модель, И то же самое с раскрытыми экранированиями.
    """
    parts: list[str] = []
    for name, payload in slice_.sections:
        parts.append(name)
        parts.append(payload)
        parts.append(json.dumps(json.loads(payload), ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


def ready(ctx, *, now=fx.NOW, folder="состояние"):
    """Хранилище на диске, цель владельца и ОДНА допущенная миссия."""
    path = ctx.path(folder, "objectives.sqlite3")
    store, spec, _ = fx.ready_store(path, spec=fx.make_spec(expires_at=now + 100_000.0))
    mission = fx.mission_of(store, spec, ctx.path(folder, "результат.txt"), now=now)
    return path, store, spec, mission


# --------------------------------------------- OS-41: перезапуск ПРОЦЕССА
#: Долговечная память проекта, которую обязан поднять НОВЫЙ процесс.
PROJECT_FACT = "сборка проекта чинится правкой lock-файла, а не переустановкой"
RAM_ONLY_FACT = "этот факт никто не записывал на диск"


def durable_context(store_path: Path, mission_path: Path, *, now: float) -> dict:
    """Собрать контекст проекта ТОЛЬКО из того, что лежит на диске.

    Вызывается и в родительском процессе, и в ребёнке — один и тот же код, и
    расхождение отпечатка может означать ровно одно: память не пережила смерть
    процесса. Память здесь — ПОДПИСАННАЯ улика состояния цели: её текст хранит
    сам продукт (`v5_condition_evidence.detail`), а не переменная в процессе.
    """
    store = ObjectiveStore(store_path)
    state = store.get(fx.OBJECTIVE_A)
    spec = store.get_spec(fx.OBJECTIVE_A)
    mission = MissionIR.from_dict(json.loads(mission_path.read_text(encoding="utf-8")))
    ref = state.last_verified_evidence_ref or ""
    row = store.condition_evidence(ref) if ref else None
    record = (row or {}).get("record") or {}
    memory = []
    if record.get("detail"):
        memory.append(MemoryRecord(memory_id=str(record["evidence_id"]),
                                   objective_digest=spec.digest,
                                   observed_at=float(record["minted_at"]),
                                   text=str(record["detail"]),
                                   provenance=f"подписанная улика цели {ref}"))
    slice_ = assemble(spec, mission, now=now, memory=memory, lifecycle=state.lifecycle)
    return {
        "pid": os.getpid(),
        "lifecycle": state.lifecycle,
        "condition": state.condition,
        "observations_used": state.observations_used,
        "evidence_ref": ref,
        "memory_texts": [entry["text"] for entry in slice_.section("memory")],
        "digest": slice_.digest,
        "sections": list(slice_.names()),
    }


CHILD = """import json, pathlib, sys
sys.path.insert(0, sys.argv[2])
import scn_11_context_depth as scn
from bossman_shared.objective_store import ObjectiveStoreError
try:
    out = scn.durable_context(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[3]),
                              now=float(sys.argv[4]))
except ObjectiveStoreError as exc:
    out = {"pid": -1, "error": "%s: %s" % (type(exc).__name__, exc)}
print(json.dumps(out, ensure_ascii=False))
"""


def run_child(script: Path, store_path: Path, mission_path: Path, *, now: float,
              key_file: Path) -> dict:
    """НАСТОЯЩИЙ новый процесс операционной системы, а не новый объект в этом."""
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tools"), str(HERE)]),
        "BOSSMAN_EVIDENCE_KEY_FILE": str(key_file),
        "PYTHONIOENCODING": "utf-8",
    }
    done = subprocess.run([sys.executable, str(script), str(store_path), str(HERE),
                           str(mission_path), repr(now)],
                          capture_output=True, text=True, timeout=180, env=env)
    if done.returncode != 0:
        raise AssertionError(f"новый процесс не поднял контекст: {done.stderr[-500:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


@bind("OS-41")
def os41_context_survives_a_process_restart(ctx) -> None:
    """Память проекта → смерть процесса → НОВЫЙ процесс → тот же контекст."""
    from bossman_shared import evidence as _evidence  # noqa: PLC0415

    now = fx.NOW
    path, store, spec, mission = ready(ctx, now=now)
    mission_path = ctx.path("состояние", "mission.json")
    mission_path.write_text(json.dumps(mission.to_dict(), ensure_ascii=False),
                            encoding="utf-8")
    key_file = ctx.path("ключи", "evidence.key")

    previous = os.environ.get("BOSSMAN_EVIDENCE_KEY_FILE")
    os.environ["BOSSMAN_EVIDENCE_KEY_FILE"] = str(key_file)
    _evidence.reset_cache()
    try:
        _evidence.load_or_create_key()
        # Проверенный факт проекта записывается ПОДПИСАННОЙ долговечной уликой:
        # это и есть память, которая обязана пережить смерть процесса.
        ref = store.record_condition_evidence(
            fx.OBJECTIVE_A, condition="SATISFIED", run_id="run-до-смерти",
            detail=PROJECT_FACT, now=now, ttl_seconds=100_000.0)
        store.set_condition(fx.OBJECTIVE_A, "SATISFIED", evidence_ref=ref, now=now + 1.0,
                            expected_version=store.get(fx.OBJECTIVE_A).version)
        store.record_observation(fx.OBJECTIVE_A, observed_at=now - 5.0, count=1,
                                 expected_version=store.get(fx.OBJECTIVE_A).version)

        before = durable_context(path, mission_path, now=now + 2.0)
        ctx.positive("до смерти процесса проект помнит ПРОВЕРЕННЫЙ факт",
                     PROJECT_FACT in before["memory_texts"]
                     and before["condition"] == "SATISFIED",
                     f"память={before['memory_texts']}")

        del store  # УБИЙСТВО: всё, что жило в памяти процесса, исчезло вместе с ним
        script = ctx.path("перезапуск", "child.py")
        script.write_text(CHILD, encoding="utf-8")
        after = run_child(script, path, mission_path, now=now + 2.0, key_file=key_file)

        ctx.positive("контекст поднял ДРУГОЙ процесс операционной системы",
                     after.get("pid", -1) > 0 and after["pid"] != os.getpid(),
                     f"pid родителя={os.getpid()} pid ребёнка={after.get('pid')}")
        ctx.positive("новый процесс вспомнил тот же факт проекта",
                     after["memory_texts"] == before["memory_texts"]
                     and PROJECT_FACT in after["memory_texts"])
        ctx.positive("собранный контекст тождественен по отпечатку продукта",
                     after["digest"] == before["digest"],
                     f"до={before['digest'][:16]} после={after['digest'][:16]}")
        ctx.positive("состояние, счётчик и ссылка на улику пережили перезапуск",
                     after["observations_used"] == before["observations_used"] == 1
                     and after["evidence_ref"] == before["evidence_ref"] == ref
                     and after["condition"] == "SATISFIED",
                     f"наблюдений={after['observations_used']}")

        # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ. Пустое хранилище в новом процессе не выдумывает
        # ни проекта, ни его памяти — иначе «вспоминание» было бы фабрикацией.
        blank = run_child(script, ctx.path("пусто", "objectives.sqlite3"), mission_path,
                          now=now + 2.0, key_file=key_file)
        ctx.negative("новый процесс на пустом хранилище НЕ выдумывает проект",
                     blank.get("pid") == -1 and "ObjectiveStoreError" in blank.get("error", ""),
                     f"ответ={str(blank)[:160]}")

        # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ. Факт, живший только в оперативной памяти
        # родителя, после смерти процесса не воскресает. Сначала показывается,
        # что при жизни процесса он в контексте ВИДЕН, — иначе «его там нет»
        # означало бы лишь, что искали то, чего и не клали.
        spec_now = ObjectiveStore(path).get_spec(fx.OBJECTIVE_A)
        ram_slice = assemble(spec_now, mission, now=now + 2.0,
                             memory=[MemoryRecord(memory_id="m-ram",
                                                  objective_digest=spec_now.digest,
                                                  observed_at=now, text=RAM_ONLY_FACT,
                                                  provenance="только оперативная память")])
        ctx.positive("при жизни процесса незаписанный факт в контексте ВИДЕН",
                     RAM_ONLY_FACT in slice_text(ram_slice))
        ctx.negative("после смерти процесса незаписанный факт исчез",
                     RAM_ONLY_FACT not in " ".join(after["memory_texts"])
                     and after["digest"] != ram_slice.digest,
                     f"память ребёнка={after['memory_texts']}")

        # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ. Долговечность не значит «навсегда и без отзыва».
        reopened = ObjectiveStore(path)
        reopened.revoke_condition_evidence(ref, reason="владелец отозвал")
        ctx.refused("отозванная улика после перезапуска больше не ставит зелёное",
                    lambda: reopened.set_condition(
                        fx.OBJECTIVE_A, "SATISFIED", evidence_ref=ref, now=now + 60.0,
                        expected_version=reopened.get(fx.OBJECTIVE_A).version),
                    ObjectiveStoreError)
    finally:
        if previous is None:
            os.environ.pop("BOSSMAN_EVIDENCE_KEY_FILE", None)
        else:
            os.environ["BOSSMAN_EVIDENCE_KEY_FILE"] = previous
        _evidence.reset_cache()


# ------------------------------------------------------ OS-42: устаревший факт
@bind("OS-42")
def os42_stale_is_never_served_as_fresh(ctx) -> None:
    """Просроченное ЗАКРЫВАЕТ путь: ни зелёного, ни последнего известного значения."""
    now = fx.NOW
    ttl = 600.0
    path, store, spec, mission = ready(ctx, now=now)

    with fx.evidence_key(ctx.path("ключи")):
        fresh_ref = store.record_condition_evidence(
            fx.OBJECTIVE_A, condition="SATISFIED", run_id="run-свежий",
            detail="сборка зелёная", now=now, ttl_seconds=ttl)
        record = store.condition_evidence(fresh_ref)["record"]
        ctx.positive("у улики ЕСТЬ отметка времени и предельный возраст",
                     record["minted_at"] == now and record["fresh_until"] == now + ttl,
                     f"minted_at={record['minted_at']} fresh_until={record['fresh_until']}")
        state = store.set_condition(fx.OBJECTIVE_A, "SATISFIED", evidence_ref=fresh_ref,
                                    now=now + 10.0,
                                    expected_version=store.get(fx.OBJECTIVE_A).version)
        ctx.positive("СВЕЖАЯ улика открывает путь: зелёное поставлено",
                     state.condition == "SATISFIED"
                     and state.last_verified_evidence_ref == fresh_ref)

        stale_ref = store.record_condition_evidence(
            fx.OBJECTIVE_A, condition="SATISFIED", run_id="run-протухший",
            detail="сборка зелёная", now=now, ttl_seconds=ttl)
        ctx.refused("ПРОСРОЧЕННАЯ улика закрывает путь, а не открывает",
                    lambda: store.set_condition(
                        fx.OBJECTIVE_A, "SATISFIED", evidence_ref=stale_ref,
                        now=now + ttl + 1.0,
                        expected_version=store.get(fx.OBJECTIVE_A).version),
                    ObjectiveStoreError)
        ctx.refused("улика «из будущего» тоже отвергается, а не принимается авансом",
                    lambda: store.set_condition(
                        fx.OBJECTIVE_A, "SATISFIED", evidence_ref=stale_ref, now=now - 1.0,
                        expected_version=store.get(fx.OBJECTIVE_A).version),
                    ObjectiveStoreError)
        ctx.refused("проза вместо ссылки на улику зелёного не ставит",
                    lambda: store.set_condition(
                        fx.OBJECTIVE_A, "SATISFIED", evidence_ref="всё хорошо, честное слово",
                        now=now + 20.0,
                        expected_version=store.get(fx.OBJECTIVE_A).version),
                    ObjectiveStoreError)
        ctx.positive("после отказов зелёное осталось на СВЕЖЕЙ улике, а не на просроченной",
                     store.get(fx.OBJECTIVE_A).last_verified_evidence_ref == fresh_ref)

    # Факт мира: устаревший читается как UNKNOWN, а не как старое значение.
    projection = WorldStateProjection()
    projection.ingest(StateFact(key="сборка", value="зелёная", source_ref=fx.SOURCE,
                                scope_id=fx.SCOPE_A, observed_at=now - 100.0,
                                max_age_seconds=300.0, provenance_ref="obs-1"), now=now)
    ctx.positive("свежий факт мира открывает границу эффекта",
                 require_fresh(projection, fx.SCOPE_A, "сборка", now=now) == "зелёная")
    later = now + 1_000.0
    read = projection.read(fx.SCOPE_A, "сборка", now=later)
    ctx.negative("устаревший факт читается STALE, а не как последнее известное",
                 read.status == "STALE" and read.fact is None, f"статус={read.status}")
    ctx.refused("устаревший факт ЗАКРЫВАЕТ границу эффекта",
                lambda: require_fresh(projection, fx.SCOPE_A, "сборка", now=later),
                WorldStateError)
    ctx.refused("«не знаем» нельзя молча прочитать как ложь через if",
                lambda: bool(read.value_or_unknown()), TypeError)
    ctx.refused("факт, наблюдённый в будущем, не принимается",
                lambda: projection.ingest(
                    StateFact(key="сборка", value="зелёная", source_ref=fx.SOURCE,
                              scope_id=fx.SCOPE_A, observed_at=now + 10.0,
                              max_age_seconds=300.0, provenance_ref="obs-из-будущего"),
                    now=now),
                WorldStateError)

    # Срез контекста: устаревший факт рендерится UNKNOWN, значения в тексте нет.
    stale_fact = WorldFact(fact_id="сборка", source_ref=fx.SOURCE, scope_ref=fx.SCOPE_A,
                           observed_at=now - 10_000.0, max_age_seconds=300.0,
                           provenance="obs-старый", value="ЗЕЛЁНАЯ-СТАРОЕ-ЗНАЧЕНИЕ")
    fresh_fact = WorldFact(fact_id="сборка", source_ref=fx.SOURCE, scope_ref=fx.SCOPE_A,
                           observed_at=now - 10.0, max_age_seconds=300.0,
                           provenance="obs-свежий", value="ЗЕЛЁНАЯ-СВЕЖЕЕ-ЗНАЧЕНИЕ")
    stale_slice = assemble(spec, mission, now=now, world_facts=[stale_fact])
    fresh_slice = assemble(spec, mission, now=now, world_facts=[fresh_fact])
    rendered = stale_slice.section("world_state")[0]
    ctx.positive("в срезе устаревший факт помечен UNKNOWN",
                 rendered["freshness"] == UNKNOWN and rendered["value"] == UNKNOWN,
                 f"freshness={rendered['freshness']}")
    ctx.positive("свежий факт в срезе ЕСТЬ — тот же поиск его находит",
                 "ЗЕЛЁНАЯ-СВЕЖЕЕ-ЗНАЧЕНИЕ" in slice_text(fresh_slice)
                 and fresh_slice.section("world_state")[0]["freshness"] == "fresh")
    ctx.negative("старое значение не утекло в собранный текст",
                 "ЗЕЛЁНАЯ-СТАРОЕ-ЗНАЧЕНИЕ" not in slice_text(stale_slice))


# ---------------------------------------------------------------- OS-43: редакция
SECRET = "sk-VLADELEC-SEKRET-0123456789abcdef"  # ci-secret-scan: allow (подставное значение)
PRIVATE_FIELD = "пароль-владельца-9f3c"
USEFUL = "падение чинится правкой lock-файла"


@bind("OS-43")
def os43_redaction_keeps_secrets_out_of_context(ctx) -> None:
    """Секрет и приватное поле не доходят до собранного контекста — доказано поиском."""
    from bossman.obs import REDACTED, redact, redact_obj  # noqa: PLC0415

    now = fx.NOW
    path, store, spec, mission = ready(ctx, now=now)
    ctx.positive("редактор взят у продукта, а не написан в сценарии",
                 callable(redact) and callable(redact_obj) and bool(REDACTED),
                 "bossman.obs.redact / redact_obj")

    # Секрет встречается ТРЕМЯ разными способами: под именем ключа, в заголовке
    # и ГОЛЫМ куском текста. Без голого вхождения проверка не трогала бы правило
    # «токен по виду» и молча зеленела бы, даже если его выключить.
    raw = (f"{USEFUL}; ключ провайдера api_key={SECRET}; "
           f"password={PRIVATE_FIELD}; Bearer {SECRET}; в логе всплыл {SECRET}")
    cleaned = redact(raw)
    ctx.positive("полезный текст переживает редакцию",
                 USEFUL in cleaned, f"после редакции={cleaned[:90]}")
    ctx.positive("редакция оставляет видимую метку, а не молча укорачивает",
                 REDACTED in cleaned)
    ctx.positive("редакция идемпотентна: второй проход ничего не раскрывает",
                 redact(cleaned) == cleaned)

    private_payload = redact_obj({"api_key": SECRET, "password": PRIVATE_FIELD,
                                  "заметка": raw, "путь": "/дом/проект"})
    ctx.positive("приватное поле вычищено ПО ИМЕНИ ключа, а не только по виду значения",
                 private_payload["api_key"] == REDACTED
                 and private_payload["password"] == REDACTED
                 and private_payload["путь"] == "/дом/проект")

    observation = fx.observation(spec, observed_at=now - 10.0)
    observation["values"] = {**observation["values"], "api_key": SECRET,
                             "password": PRIVATE_FIELD}
    safe = redact_obj(observation)
    memory = [MemoryRecord(memory_id="m-заметка", objective_digest=spec.digest,
                           observed_at=now - 5.0, text=cleaned,
                           provenance="журнал проекта (редактировано)")]
    slice_ = assemble(spec, mission, now=now, memory=memory, observations=[safe])
    body = slice_text(slice_)
    ctx.positive("полезная часть памяти доехала до собранного контекста",
                 USEFUL in body)
    ctx.negative("секрет НЕ найден поиском по собранному тексту",
                 SECRET not in body, f"длина собранного текста={len(body)}")
    ctx.negative("приватное поле НЕ найдено поиском по собранному тексту",
                 PRIVATE_FIELD not in body)

    # ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ САМОГО ПОИСКА. Без редакции тот же поиск находит
    # секрет — значит поиск настоящий, а не всегда отвечает «не нашёл».
    leaky = assemble(spec, mission, now=now, observations=[observation],
                     memory=[MemoryRecord(memory_id="m-сырое",
                                          objective_digest=spec.digest,
                                          observed_at=now - 5.0, text=raw,
                                          provenance="журнал (без редакции)")])
    leaky_body = slice_text(leaky)
    ctx.positive("тот же поиск НАХОДИТ секрет в нередактированном срезе",
                 SECRET in leaky_body and PRIVATE_FIELD in leaky_body,
                 "контроль исправности поиска")

    # Приватный раздел нельзя дописать сбоку: у сборки закрытый словарь секций.
    ctx.refused("приватный раздел нельзя дописать в срез сбоку",
                lambda: assemble(spec, mission, now=now,
                                 extra={"секреты_владельца": {"api_key": SECRET}}),
                ContextRefusal)
    ctx.refused("известный способ вывалить всю память отвергнут ПО ИМЕНИ",
                lambda: assemble(spec, mission, now=now, extra={"memory_dump": [raw]}),
                ContextRefusal)
    ctx.refused("нередактированная память чужой цели в срез не попадает вовсе",
                lambda: as_memory_data(
                    [MemoryRecord(memory_id="m-чужое", objective_digest="0" * 64,
                                  observed_at=now, text=raw, provenance="чужой журнал")],
                    objective_digest=spec.digest),
                ContextRefusal)


# ------------------------------------------------- OS-44: вытеснение по размеру
LATEST_TEXT = "ПОСЛЕДНЯЯ-ПРОВЕРЕННАЯ-УЛИКА: сборка зелёная после правки lock-файла"
OLDEST_TEXT = "САМАЯ-СТАРАЯ-ЗАПИСЬ: когда-то падал линтер"


@bind("OS-44")
def os44_eviction_keeps_the_last_verified_evidence(ctx) -> None:
    """Вытеснение по размеру: последняя проверенная улика остаётся, потеря НАЗВАНА."""
    now = fx.NOW
    path, store, spec, mission = ready(ctx, now=now)

    memory = [MemoryRecord(memory_id=f"m-{index:02d}", objective_digest=spec.digest,
                           observed_at=now - 1_000.0 + index * 10.0,
                           text=(OLDEST_TEXT if index == 0 else
                                 LATEST_TEXT if index == 19 else
                                 f"промежуточная запись №{index}: шум {'ш' * 40}"),
                           provenance=f"журнал-{index:02d}")
              for index in range(20)]
    observations = [fx.observation(spec, green=(index == 9), observed_at=now - 100.0 + index,
                                   observation_id=f"obs-{index:02d}") for index in range(10)]

    kept = as_memory_data(memory, objective_digest=spec.digest)
    ctx.positive("вытеснение по количеству оставляет САМУЮ СВЕЖУЮ запись",
                 len(kept) == MAX_MEMORY_RECORDS and kept[0]["text"] == LATEST_TEXT,
                 f"оставлено={len(kept)} из {len(memory)}")
    ctx.negative("самая старая запись выброшена ЦЕЛИКОМ, а не пересказана",
                 all(OLDEST_TEXT not in entry["text"] for entry in kept)
                 and all("САМАЯ-СТАРАЯ" not in entry["text"] for entry in kept))
    ctx.negative("вытеснение не превращает уцелевшую память в полномочие",
                 all(e["authority"] == MEMORY_AUTHORITY and e["label"] == MEMORY_LABEL
                     for e in kept))

    full = assemble(spec, mission, now=now, memory=memory, observations=observations)
    seen = full.section("observations")
    ctx.positive("из наблюдений источника оставлено ровно ПОСЛЕДНЕЕ, и оно проверенное",
                 len(seen) == 1 and seen[0]["observation_id"] == "obs-09"
                 and seen[0]["values"]["green"] is True,
                 f"наблюдений в срезе={len(seen)}")
    ctx.negative("прежние наблюдения того же источника не пересказаны в срез",
                 "obs-00" not in slice_text(full) and "obs-08" not in slice_text(full))

    # СВОЙСТВО НА ДЛИННОЙ ОСИ. Бюджет байтов гоняется от «еле хватает головы» до
    # полного среза: хвост обязан падать целиком и строго с конца, а последняя
    # проверенная улика обязана оставаться на месте при КАЖДОМ бюджете, где её
    # раздел вообще уцелел. Один удачный бюджет доказал бы только сам себя.
    order = [name for name in SECTION_ORDER if name in tuple(full.names()) + full.dropped]
    refused, violations, with_observations, with_memory = 0, [], 0, 0
    for budget in range(48, full.byte_size + 400, 29):
        try:
            probe = assemble(spec, mission, now=now, memory=memory,
                             observations=observations, byte_budget=budget)
        except ContextRefusal:
            refused += 1
            continue
        names, dropped = list(probe.names()), list(probe.dropped)
        if names != order[:len(names)] or dropped != order[len(names):]:
            violations.append((budget, names, dropped))
            continue
        if "observations" in names:
            with_observations += 1
            if probe.section("observations")[0]["observation_id"] != "obs-09":
                violations.append((budget, "не последнее наблюдение", names))
        if "memory" in names:
            with_memory += 1
            if probe.section("memory")[0]["text"] != LATEST_TEXT:
                violations.append((budget, "не последняя запись памяти", names))
    ctx.positive("на ВСЕЙ оси бюджета вытеснение шло строго с хвоста",
                 not violations,
                 f"нарушений={len(violations)}: {violations[:2]}")
    ctx.positive("последняя проверенная улика уцелела при каждом бюджете, где раздел жив",
                 with_observations > 0 and with_memory > 0,
                 f"бюджетов с наблюдениями={with_observations}, с памятью={with_memory}")
    ctx.positive("слишком малый бюджет ОТКАЗЫВАЕТ, а не режет голову политики",
                 refused > 0, f"отказов={refused}")

    head_bytes = sum(len(body.encode("utf-8")) + len(name)
                     for name, body in full.sections[:3])
    tight = assemble(spec, mission, now=now, memory=memory, observations=observations,
                     byte_budget=head_bytes + 1)
    ctx.negative("потеря при вытеснении НАЗВАНА, а не молчалива",
                 bool(tight.dropped) and set(tight.dropped) <= set(SECTION_ORDER)
                 and "memory" in tight.dropped,
                 f"выброшено={tight.dropped}")
    ctx.negative("вытеснение в срезе не стирает улику из долговечной записи",
                 store.get_spec(fx.OBJECTIVE_A).digest == spec.digest
                 and as_memory_data(memory, objective_digest=spec.digest)[0]["text"]
                 == LATEST_TEXT)
    ctx.refused("бюджет, не вмещающий голову политики, отвергается ЦЕЛИКОМ",
                lambda: assemble(spec, mission, now=now, memory=memory, byte_budget=8),
                ContextRefusal)


# ------------------------------------------ OS-45: в A нет ни одной записи B
#: Источник проекта B назван так, чтобы не быть ПОДСТРОКОЙ источника проекта A.
#: С «src:build» и «src:build-b» поиск нашёл бы метку A внутри метки B и объявил
#: протечку там, где её нет: подстрока — не запись.
SOURCE_B = "src:проект-b"


@bind("OS-45")
def os45_no_single_record_of_b_in_a(ctx) -> None:
    """Поиск по собранному тексту: ни одной метки проекта B в контексте проекта A."""
    now = fx.NOW
    path, store_a, spec_a, mission_a = ready(ctx, now=now)
    spec_b = fx.make_spec(objective_id=fx.OBJECTIVE_B, scope_id=fx.SCOPE_B,
                          source_ref=SOURCE_B, expires_at=now + 100_000.0)
    store_b = ObjectiveStore(path)
    fx.activate(store_b, store_b.create(spec_b), objective_id=fx.OBJECTIVE_B,
                source_ref=SOURCE_B, now=now)
    mission_b = fx.mission_of(store_b, spec_b, ctx.path("work-b", "результат.txt"), now=now,
                              objective_id=fx.OBJECTIVE_B, scope_id=fx.SCOPE_B,
                              source_ref=SOURCE_B)

    memory_a = [MemoryRecord(memory_id="m-a-01", objective_digest=spec_a.digest,
                             observed_at=now - 20.0, text="МЕТКА-A: lock-файл проекта A",
                             provenance="журнал-A")]
    memory_b = [MemoryRecord(memory_id="m-b-01", objective_digest=spec_b.digest,
                             observed_at=now - 20.0,
                             text="МЕТКА-B: внутренняя переписка проекта B",
                             provenance="журнал-B")]
    obs_a = fx.observation(spec_a, observed_at=now - 10.0, observation_id="obs-a-01")
    obs_b = fx.observation(spec_b, observed_at=now - 10.0, observation_id="obs-b-01",
                           scope_id=fx.SCOPE_B, source_ref=SOURCE_B)

    slice_a = assemble(spec_a, mission_a, now=now, memory=memory_a, observations=[obs_a])
    slice_b = assemble(spec_b, mission_b, now=now, memory=memory_b, observations=[obs_b],
                       enrolled=(SOURCE_B,))
    text_a, text_b = slice_text(slice_a), slice_text(slice_b)

    marks_a = (spec_a.digest, fx.SCOPE_A, fx.SOURCE, "МЕТКА-A", "m-a-01", "журнал-A",
               "obs-a-01")
    marks_b = (spec_b.digest, fx.SCOPE_B, SOURCE_B, "МЕТКА-B", "m-b-01", "журнал-B",
               "obs-b-01")

    found_a = [mark for mark in marks_a if mark in text_a]
    ctx.positive("поиск исправен: КАЖДАЯ метка проекта A найдена в срезе A",
                 found_a == list(marks_a), f"найдено {len(found_a)} из {len(marks_a)}")
    found_b = [mark for mark in marks_b if mark in text_b]
    ctx.positive("поиск исправен: КАЖДАЯ метка проекта B найдена в срезе B",
                 found_b == list(marks_b), f"найдено {len(found_b)} из {len(marks_b)}")

    leaked = [mark for mark in marks_b if mark in text_a]
    ctx.negative("в срезе A не найдено НИ ОДНОЙ метки проекта B",
                 not leaked, f"протекло={leaked}")
    back = [mark for mark in marks_a if mark in text_b and mark not in marks_b]
    ctx.negative("и в обратную сторону: в срезе B нет меток проекта A",
                 not back, f"протекло={back}")
    ctx.negative("это два РАЗНЫХ контекста, а не один под двумя именами",
                 slice_a.digest != slice_b.digest
                 and slice_a.objective_digest != slice_b.objective_digest)

    ctx.refused("память проекта B отвергнута при сборке контекста A",
                lambda: as_memory_data(memory_b, objective_digest=spec_a.digest),
                ContextRefusal)
    ctx.refused("наблюдение проекта B отвергнуто, а не «учтено на всякий случай»",
                lambda: assemble(spec_a, mission_a, now=now, observations=[obs_b, obs_a]),
                ContextRefusal)
    ctx.refused("источник проекта B нельзя записать в контекст проекта A",
                lambda: assemble(spec_a, mission_a, now=now, enrolled=(SOURCE_B,)),
                ContextRefusal)
    ctx.refused("список соседних целей нельзя подмешать в контекст A",
                lambda: assemble(spec_a, mission_a, now=now,
                                 extra={"other_objectives": [fx.OBJECTIVE_B]}),
                ContextRefusal)
