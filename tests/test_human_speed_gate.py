"""Пересмотренный контракт приёмки по времени: `tools.human_speed_gate`.

Здесь проверяется САМО ПРАВИЛО, детерминированно, без обращения к хранилищу:
живые замеры настоящего CAS живут в `tests/test_v5_human_speed.py`. Векторы
ниже воспроизводят ФОРМЫ, которые были реально измерены (числа и их
происхождение — в шапке `test_v5_human_speed.py`), а величины срывов взяты
буквально из красных прогонов CI: 25.16834 мс на `329d58a` и 10.012683 мс на
`e25920f7`.

Правка меняет строгость проверки, поэтому у каждого послабления здесь есть
пара: законный случай проходит И по-настоящему плохой случай по-прежнему
отвергается. Обе стороны — ниже, в одном файле, рядом.
"""
from __future__ import annotations

import pytest

from tools.human_speed_gate import (FAIL, INSUFFICIENT, PASS, StorageFloor,
                                    latency_contract)

LIMIT = 10.0
# Форма здорового прогона на тихом хосте: p50 около 1.15 мс, тело около 1.5 мс.
# Пол того же хоста, снятый чередуясь: около 0.26 мс. Отношение p50/floor_p50
# на этой машине измерено в 4.13–5.15 (31 прогон, включая экстремальную
# нагрузку), поэтому 4.4 ниже — не выдумка, а середина измеренного диапазона.
HEALTHY = [1.10 + (i % 9) * 0.05 for i in range(100)]
HEALTHY_FLOOR = [0.25 + (i % 5) * 0.01 for i in range(100)]
# Обе величины — настоящие p100 из красных прогонов CI, не придуманные.
CI_STALL_329D58A = 25.16834
CI_STALL_E25920F = 10.012683


def contract(samples, floor, **kw):
    return latency_contract(samples, limit_ms=LIMIT, floor_samples_ms=floor, **kw)


def with_stalls(*values):
    """Здоровое распределение, у которого испорчены только худшие замеры."""
    samples = list(HEALTHY)
    for i, value in enumerate(values):
        samples[i] = value
    return samples


# --------------------------------------------------------------- приёмка хоста

def test_the_absolute_limit_is_still_the_first_basis_and_is_not_raised():
    """Порог владельца не поднят и не превращён в предупреждение.

    `p100 < 10 мс` по-прежнему первое основание и проверяется раньше любого
    послабления; ровно-на-пороге — это уже нарушение, сравнение строгое.
    """
    result = contract(HEALTHY, HEALTHY_FLOOR)
    assert result["status"] == PASS and result["basis"] == "absolute_p100"
    assert result["limit_ms"] == 10.0 and result["comparison"] == "strictly_less_than"
    assert result["outliers_removed"] == 0

    # Ровно 10 мс — не «меньше десяти».
    at_the_limit = with_stalls(*([LIMIT] * 2))
    assert contract(at_the_limit, HEALTHY_FLOOR)["status"] == FAIL


def test_one_isolated_stall_is_classified_as_noise_and_the_raw_value_survives():
    """Подпись обоих красных прогонов CI: один замер из ста мимо, тело чистое.

    Пол хоста в этих прогонах был БЫСТРЫМ (на `e25920f7` относительное
    основание тоже отказало, значит floor_p100 <= 10.012683/8 = 1.2516 мс),
    то есть чередующийся пол этот срыв не видел — и не мог увидеть.
    """
    for stall in (CI_STALL_329D58A, CI_STALL_E25920F):
        result = contract(with_stalls(stall), HEALTHY_FLOOR)
        assert result["status"] == PASS, result
        assert result["basis"] == "isolated_stall"
        # Сырой результат сохранён и НЕ переименован в PASS-величину.
        assert result["max_ms"] == stall and result["value_ms"] == stall
        assert result["stalls_ms"] == [stall] and result["over_limit"] == 1
        assert result["percentile"] == 100
        # Ровно то, что делает срыв срывом: тело не сдвинулось.
        assert result["body_ms"] < LIMIT and result["p50_ms"] < LIMIT


def test_two_stalls_are_a_distribution_and_are_still_rejected():
    """Два замера мимо — это уже доля, а не единичное событие.

    Один из ста — это 1%. Два из ста контракт обязан назвать распределением:
    иначе «шумом» можно объявить что угодно, добавляя по замеру за раз.
    """
    result = contract(with_stalls(CI_STALL_329D58A, 11.0), HEALTHY_FLOOR)
    assert result["status"] == FAIL
    assert result["reason"] == "excess_spread_across_the_distribution"
    assert result["over_limit"] == 2 and result["max_ms"] == CI_STALL_329D58A


def test_the_isolated_stall_route_needs_the_whole_rest_of_the_body_inside():
    """Граница послабления, обе её стороны.

    Даже распределение, прижатое к самому порогу, может быть прощено за ОДИН
    вышедший замер — но ровно за один. Стоит второму коснуться порога, и это
    уже доля, а не событие: PASS исчезает, хотя «худший замер» тот же самый.
    """
    inside = [9.99] * 99 + [40.0]
    granted = contract(inside, [1.5] * 100)
    assert granted["status"] == PASS and granted["basis"] == "isolated_stall"
    assert granted["body_ms"] == 9.99 and granted["over_limit"] == 1

    outside = [9.99] * 98 + [LIMIT, 40.0]
    refused = contract(outside, [1.5] * 100)
    assert refused["status"] == FAIL and refused["basis"] is None
    assert refused["reason"] == "excess_spread_across_the_distribution"
    assert refused["over_limit"] == 2 and refused["max_ms"] == 40.0


# ------------------------------------------- отделение шума от дефекта хранения

def test_a_regression_below_the_absolute_limit_is_still_rejected():
    """Главное усиление: гейт видит сдвиг РАСПРЕДЕЛЕНИЯ под порогом.

    Каждая операция подорожала на несколько миллисекунд, p100 остался внутри
    10 мс — прежний абсолютный контракт сказал бы PASS. Отношение к полу
    СВОЕГО ЖЕ хоста выросло с 4.4 до 20 и говорит правду: подорожала работа,
    а не железо. Измерено вживую (+4 мс на каждый CAS): p50 5.24–5.44 мс,
    p100 5.75–6.02 мс, отношение 18.1–20.2 при допуске 8.
    """
    regressed = [5.2 + (i % 9) * 0.05 for i in range(100)]
    result = contract(regressed, HEALTHY_FLOOR)
    assert result["status"] == FAIL and result["basis"] is None
    assert result["reason"] == "operation_disproportionate_to_its_own_host_floor"
    # Именно то, чего абсолютный порог не видит: всё внутри 10 мс.
    assert result["max_ms"] < LIMIT and result["over_limit"] == 0
    assert result["p50_ratio"] > result["floor_multiple"]


def test_a_uniformly_slow_host_still_passes_on_its_own_floor():
    """Равномерно медленный хост — про железо, и пол это показывает.

    Форма измерена под нагрузкой (12 счётных процессов и 4 параллельных
    fsync-потока на 4 ядрах): CAS p50 20.1 мс, пол p50 3.9 мс, отношение 5.15
    — то есть операция осталась пропорциональной, изменился хост.
    """
    slow_host = [20.0 + (i % 7) * 0.6 for i in range(100)]
    slow_floor = [3.9 + (i % 7) * 0.12 for i in range(100)]
    result = contract(slow_host, slow_floor)
    assert result["status"] == PASS and result["basis"] == "host_floor"
    assert result["p50_ratio"] < result["floor_multiple"]
    # Абсолютное число сохранено как есть, а не заменено «нормированным».
    assert result["max_ms"] >= LIMIT and result["limit_ms"] == LIMIT


def test_a_slow_operation_on_a_fast_host_is_never_excused():
    """Медленный CAS на быстром диске — отказ по любому основанию."""
    result = contract([50.0] * 100, HEALTHY_FLOOR)
    assert result["status"] == FAIL
    assert result["reason"] == "operation_disproportionate_to_its_own_host_floor"


def test_the_allowance_over_a_slow_host_floor_is_finite():
    """И на медленном хосте кратность конечна: 8x — это не «сколько угодно»."""
    slow_floor = [12.0] * 100
    assert contract([60.0] * 100, slow_floor)["status"] == PASS
    assert contract([200.0] * 100, slow_floor)["status"] == FAIL


def test_a_stall_on_a_host_whose_floor_also_stalled_is_host_not_code():
    """Измерено вживую (40 тихих прогонов): бывает, что срыв ловит и пол.

    Тогда основание — `host_floor`, а не `isolated_stall`, и это видно в
    отчёте. Числа взяты из настоящего прогона: CAS тело 12.591 мс, максимум
    19.249 мс, а у пола при медиане 0.215 мс поднялся ВЕСЬ хвост —
    2.152/2.537/2.600/2.996/3.615 мс. Поднялся хвост у обоих, значит
    запинался хост, и допуск по телу (2.996 * 8 = 23.97 мс) это подтверждает.
    """
    samples = with_stalls(19.249, 12.591)
    floor = ([0.215 + (i % 5) * 0.01 for i in range(95)]
             + [2.152, 2.537, 2.600, 2.996, 3.615])
    result = contract(samples, floor)
    assert result["status"] == PASS and result["basis"] == "host_floor"
    assert result["over_limit"] == 2 and result["allowed_body_ms"] == 2.996 * 8


# ------------------------------------------------- вырожденные наборы и отчёт

@pytest.mark.parametrize("samples,floor", [
    ([], []),
    ([1.2] * 99, [0.26] * 99),
    ([0.0] * 100, [0.26] * 100),
    ([1.2] * 100, [0.0] * 100),
])
def test_an_empty_or_degenerate_sample_set_can_never_pass(samples, floor):
    """Пустой или нулевой набор — это ОТСУТСТВИЕ evidence, а не зелёный свет.

    Нулевая медиана означает, что мерили не то или не тем: долговечная запись
    не занимает ноль. Такой набор не должен уметь выдать PASS никаким путём.
    """
    result = latency_contract(samples, limit_ms=LIMIT, floor_samples_ms=floor)
    assert result["status"] == INSUFFICIENT and result["basis"] is None
    assert result["reason"] in ("sample_count", "degenerate_measurement")


def test_the_host_floor_is_required_and_must_be_interleaved_one_to_one():
    """Оправдание задержки — утверждение о хосте, и без хоста не принимается.

    Пол, снятый другим числом тиков, не чередовался с измерением, а значит
    не может свидетельствовать о том, что происходило во время замеров.
    """
    missing = latency_contract(HEALTHY, limit_ms=LIMIT, floor_samples_ms=None)
    assert missing["status"] == INSUFFICIENT
    assert missing["reason"] == "host_floor_not_measured" and missing["basis"] is None

    unpaired = contract(HEALTHY, HEALTHY_FLOOR[:50])
    assert unpaired["status"] == FAIL and unpaired["reason"] == "floor_not_interleaved"
    assert unpaired["n"] == 100 and unpaired["n_floor"] == 50


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, 10 ** 400])
def test_invalid_timings_cannot_pass_on_either_side_of_the_ratio(bad):
    assert contract([bad] * 100, HEALTHY_FLOOR)["status"] == FAIL
    assert contract(HEALTHY, [bad] * 100)["status"] == FAIL


def test_every_verdict_reports_the_basis_and_the_numbers_behind_it():
    """PASS обязан быть перепроверяемым по числам, а не по слову «PASS».

    В каждом ответе есть `basis`, а у зелёного — все величины, из которых он
    выведен: перцентили, ранг тела, число вышедших за порог замеров, их сырые
    значения, пол хоста и посчитанные из него допуски.
    """
    verdicts = [
        contract(HEALTHY, HEALTHY_FLOOR),
        contract(with_stalls(CI_STALL_329D58A), HEALTHY_FLOOR),
        contract([20.0] * 100, [3.9] * 100),
        contract([50.0] * 100, HEALTHY_FLOOR),
        latency_contract(HEALTHY, limit_ms=LIMIT, floor_samples_ms=None),
    ]
    for result in verdicts:
        assert "basis" in result and "status" in result
        assert (result["basis"] is None) == (result["status"] != PASS)

    for result in verdicts[:4]:
        for key in ("n", "n_floor", "p50_ms", "p95_ms", "body_ms", "body_rank",
                    "max_ms", "value_ms", "over_limit", "floor_over_limit",
                    "stalls_ms", "floor_p50_ms",
                    "floor_body_ms", "floor_max_ms", "p50_ratio", "allowed_p50_ms",
                    "allowed_body_ms", "allowed_max_ms", "limit_ms", "floor_multiple",
                    "max_isolated_stalls", "outliers_removed"):
            assert key in result, (key, result)
    # Ранг тела — это позиция в отсортированной выборке, её можно пересчитать.
    green = verdicts[1]
    assert green["body_rank"] == green["n"] - green["max_isolated_stalls"]
    assert green["allowed_p50_ms"] == green["floor_p50_ms"] * green["floor_multiple"]


def test_the_isolated_stall_allowance_cannot_be_widened_without_more_samples():
    """Послабление нельзя расширить подписью — только новыми замерами.

    Доля вышедших за порог замеров ограничена одним процентом ПО КОНСТРУКЦИИ:
    хочешь разрешить два срыва — собери двести замеров.
    """
    with pytest.raises(ValueError, match="1%"):
        latency_contract(HEALTHY, limit_ms=LIMIT, floor_samples_ms=HEALTHY_FLOOR,
                         max_isolated_stalls=2)
    widened = latency_contract(HEALTHY * 2, limit_ms=LIMIT,
                               floor_samples_ms=HEALTHY_FLOOR * 2,
                               minimum=200, max_isolated_stalls=2)
    assert widened["status"] == PASS


@pytest.mark.parametrize("kwargs", [
    {"limit_ms": 0}, {"limit_ms": float("nan")}, {"limit_ms": -1},
    {"minimum": 0}, {"minimum": 1.0}, {"max_isolated_stalls": -1},
    {"max_isolated_stalls": 1.0}, {"floor_multiple": 0}, {"floor_multiple": -2.0},
])
def test_an_unusable_configuration_is_refused_rather_than_coerced(kwargs):
    call = {"limit_ms": LIMIT, "floor_samples_ms": HEALTHY_FLOOR, **kwargs}
    with pytest.raises(ValueError):
        latency_contract(HEALTHY, **call)


def test_the_storage_floor_measures_the_same_class_of_work(tmp_path):
    """Пол — это стоимость самой дешёвой ДОЛГОВЕЧНОЙ записи, а не пустой цикл."""
    floor = StorageFloor(tmp_path)
    for _ in range(20):
        assert floor.tick() > 0
    floor.close()
    assert len(floor.samples) == 20
    assert floor.at(100) == max(floor.samples)
    assert floor.at(50) <= floor.at(100)
    fresh = tmp_path / "unsampled"
    fresh.mkdir()
    with pytest.raises(ValueError, match="never sampled"):
        StorageFloor(fresh).at(100)


def test_the_floor_s_own_stalls_are_counted_and_change_no_verdict():
    """«Хост срывался, или это код?» — вопрос, который в красном CI решается
    спором, потому что число, которое на него отвечает, не записано.

    Пол — самая дешёвая долговечная запись этого хоста; ей не за что быть
    медленной. Пол над порогом означает, что срывался планировщик. Число
    публикуется рядом с вердиктом и НЕ участвует ни в одной ветке решения:
    вердикты ниже совпадают до последнего поля с теми, что были до него.
    """
    quiet_floor = [0.4] * 100
    stalling_floor = [0.4] * 97 + [15.3, 17.1, 11.2]
    healthy = [1.5] * 100

    assert latency_contract(healthy, limit_ms=LIMIT, floor_samples_ms=quiet_floor
                            )["floor_over_limit"] == 0
    stalled = latency_contract(healthy, limit_ms=LIMIT, floor_samples_ms=stalling_floor)
    assert stalled["floor_over_limit"] == 3

    # Тот же вердикт, то же основание, те же числа — новая колонка ничего не
    # решает. Иначе это было бы послаблением, а не свидетельством.
    quiet = latency_contract(healthy, limit_ms=LIMIT, floor_samples_ms=quiet_floor)
    assert stalled["status"] == quiet["status"] == PASS
    assert stalled["basis"] == quiet["basis"] == "absolute_p100"
    assert {k: v for k, v in stalled.items() if k not in
            ("floor_over_limit", "floor_p50_ms", "floor_body_ms", "floor_max_ms",
             "p50_ratio", "allowed_p50_ms", "allowed_body_ms", "allowed_max_ms")} == \
           {k: v for k, v in quiet.items() if k not in
            ("floor_over_limit", "floor_p50_ms", "floor_body_ms", "floor_max_ms",
             "p50_ratio", "allowed_p50_ms", "allowed_body_ms", "allowed_max_ms")}


def test_a_stalling_floor_does_not_rescue_a_distribution_that_spread():
    """Прямая проверка, что новая колонка не превратилась в четвёртое
    основание для PASS: наблюдение CI (два срыва при поле, который сам вышел
    за порог) как было FAIL, так и осталось."""
    samples = [2.16] * 98 + [11.97, 23.35]
    floor = [0.73] * 99 + [17.09]
    result = latency_contract(samples, limit_ms=LIMIT, floor_samples_ms=floor)
    assert result["floor_over_limit"] == 1 and result["over_limit"] == 2
    assert result["status"] == FAIL and result["basis"] is None
    assert result["reason"] == "excess_spread_across_the_distribution"
