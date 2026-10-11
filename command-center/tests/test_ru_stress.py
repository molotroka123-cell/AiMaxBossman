"""Russian stress pre-processor contracts (no model download, no network)."""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.oss import ru_stress as rs

ACUTE = rs.ACUTE
HOST_PYTHON = Path(r"C:\Users\asd\Bossman\voice-stress-venv\Scripts\python.exe")
HOST_MODELS = Path(r"C:\Users\asd\Bossman\voice-stress-work\ruaccent_models")


@pytest.fixture(autouse=True)
def _fresh_cache():
    rs.clear_cache()
    yield
    rs.clear_cache()


# --- numbers and abbreviations (done BEFORE stressing) ---------------------

@pytest.mark.parametrize("value, words", [
    (0, "ноль"), (1, "один"), (2, "два"), (11, "одиннадцать"), (21, "двадцать один"),
    (100, "сто"), (101, "сто один"), (1000, "одна тысяча"), (2000, "две тысячи"),
    (25000, "двадцать пять тысяч"), (1_000_000, "один миллион"),
    (2_000_001, "два миллиона один"),
])
def test_cardinals(value, words):
    assert rs.cardinal(value) == words


def test_cardinal_gender():
    assert rs.cardinal(1, "f") == "одна" and rs.cardinal(2, "f") == "две"
    assert rs.cardinal(21, "n") == "двадцать одно"


@pytest.mark.parametrize("text, expected", [
    ("25 проверок", "двадцать пять проверок"),
    ("3 минуты", "три минуты"),
    ("1 секунду", "одна секунду"),
    ("2 раза", "два раза"),
    ("2 недели", "две недели"),
    ("40% бюджета", "сорок процентов бюджета"),
    ("$25", "двадцать пять долларов"),
    ("3,5 кг", "три целых пять десятых килограмма"),
    ("5 мин. Дальше", "пять минут. Дальше"),
    ("в 2026 году", "в две тысячи двадцать шестом году"),
    ("с 2000 года", "с двухтысячного года"),
    ("№5", "номер пять"),
    ("Bossman 2.1 готов", "Bossman два точка один готов"),
    ("т.е. так", "то есть так"),
    ("и т.д.", "и так далее"),
])
def test_normalize_expands_numbers_and_abbreviations(text, expected):
    assert rs.normalize_for_speech(text) == expected


def test_normalize_leaves_english_and_digit_tokens_alone():
    text = "Запусти Bossman, mp3 и F5-TTS через GitHub и Wi-Fi."
    assert rs.normalize_for_speech(text) == text


def test_normalize_is_idempotent_and_drops_foreign_marks():
    once = rs.normalize_for_speech("Через 25 минут")
    assert rs.normalize_for_speech(once) == once
    assert rs.normalize_for_speech("замо" + ACUTE + "к") == "замок"


def test_ordinals_cases():
    assert rs.ordinal(3) == "третий"
    assert rs.ordinal(3, "gen") == "третьего"
    assert rs.ordinal(21, "gen") == "двадцать первого"
    assert rs.ordinal(2026, "prep") == "две тысячи двадцать шестом"


# --- stress mark conversions ------------------------------------------------

def test_to_acute_puts_mark_after_vowel_and_keeps_latin():
    assert rs.to_acute("на двер+и вис+ит зам+ок") == (
        "на двер" + "и" + ACUTE + " вис" + "и" + ACUTE + "т зам" + "о" + ACUTE + "к")
    assert rs.to_acute("Boss+man") == "Bossman"


def test_acute_skips_monosyllables_but_not_polysyllables():
    assert rs.to_acute("н+а двер+и м+ы") == "на дверѝ".replace("ѝ", "и" + ACUTE) + " мы"
    assert rs.to_acute("н+а", keep_monosyllables=True) == "на" .replace("а", "а" + ACUTE)


def test_minimal_pair_homographs_differ_after_conversion():
    # The accentizer decides; the converter must keep the two readings distinct.
    lock = rs.to_acute("зам+ок")
    castle = rs.to_acute("з+амок")
    assert lock != castle
    assert lock == "замо" + ACUTE + "к" and castle == "за" + ACUTE + "мок"
    assert rs.strip_marks(lock) == rs.strip_marks(castle) == "замок"


def test_yo_is_always_stressed_and_never_double_marked():
    assert rs.to_acute("+ёж") == "ёж"
    assert rs.clean_plus("в+ёл") == "вёл"


def test_clean_plus_drops_stray_plus_signs():
    assert rs.clean_plus("C++") == "C"
    assert rs.clean_plus("2+2") == "22"
    assert rs.clean_plus("зам+ок + ещё") == "зам+ок  ещё"


def test_respell_only_unstressed_o_becomes_a():
    assert rs.to_respelled("зам+ок") == "замок"      # stressed o stays
    assert rs.to_respelled("з+амок") == "замак"   # post-stress o is reduced
    assert rs.to_respelled("дорог+а") == "дарага"
    assert rs.to_respelled("к+оротко") == "коратка"
    assert rs.to_respelled("в мо+ём д+оме") == "в моём доме"  # ё untouched
    assert rs.to_respelled("вод+а") == "вада"
    assert rs.to_respelled("Bossman и Telegram") == "Bossman и Telegram"
    assert rs.to_respelled("по дор+оге") == "па дароге"


def test_respell_leaves_unmarked_polysyllables_alone():
    assert rs.to_respelled("колобок") == "колобок"


def test_respell_never_touches_other_vowels():
    out = rs.to_respelled("н+овые отч+ёты, в+ы пишет+е")
    assert "е" in out and "я" not in out
    assert rs.strip_marks("в+ы") == "вы"


# --- accentizer runner, cache, fallbacks ------------------------------------

def _fake_env(tmp_path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"stub")
    models = tmp_path / "models"
    models.mkdir()
    return python, models


def test_stress_plus_runs_worker_once_and_caches(tmp_path):
    python, models = _fake_env(tmp_path)
    calls = []

    def runner(argv, *, stdin, stopped, timeout):
        calls.append((argv, stdin, timeout))
        Path(argv[-1]).write_text("на двер+и вис+ит зам+ок", encoding="utf-8")

    first = rs.stress_plus("на двери висит замок", python_executable=python, model_dir=models,
                           cache_dir=tmp_path / "cache", runner=runner)
    second = rs.stress_plus("на двери висит замок", python_executable=python, model_dir=models,
                            cache_dir=tmp_path / "cache", runner=runner)
    assert first == second == "на двер+и вис+ит зам+ок"
    assert len(calls) == 1
    assert calls[0][1] == "на двери висит замок".encode()
    assert "двери" not in " ".join(calls[0][0])  # text never travels in argv
    rs.clear_cache()  # disk cache still serves it
    third = rs.stress_plus("на двери висит замок", python_executable=python, model_dir=models,
                           cache_dir=tmp_path / "cache", runner=runner)
    assert third == first and len(calls) == 1


def test_negative_control_no_cyrillic_never_calls_engine(tmp_path):
    def runner(*a, **k):
        raise AssertionError("engine must not run for text without Russian words")

    text = "Hello, this is Bossman and Wi-Fi."
    assert rs.stress_plus(text, python_executable=None, model_dir=None, runner=runner) == text
    assert rs.prepare_for_tts(text, "acute", runner=runner) == text


def test_engine_that_rewrites_words_is_rejected(tmp_path):
    python, models = _fake_env(tmp_path)

    def runner(argv, *, stdin, stopped, timeout):
        Path(argv[-1]).write_text("совсем другой текст", encoding="utf-8")

    with pytest.raises(rs.StressUnavailable):
        rs.stress_plus("на двери висит замок", python_executable=python, model_dir=models,
                       runner=runner)


def test_prepare_falls_back_to_normalized_text_when_engine_missing(tmp_path):
    spoken = rs.prepare_for_tts("Через 3 минуты", "acute",
                                python_executable=tmp_path / "nope.exe", model_dir=tmp_path)
    assert spoken == "Через три минуты"


def test_prepare_off_is_byte_for_byte_passthrough():
    text = "Через 3 минуты, т.е. скоро."
    assert rs.prepare_for_tts(text, "off") == text


def test_prepare_modes_render_from_one_stressed_form(tmp_path):
    python, models = _fake_env(tmp_path)

    def runner(argv, *, stdin, stopped, timeout):
        Path(argv[-1]).write_text("Зам+ок на двер+и", encoding="utf-8")

    kw = dict(python_executable=python, model_dir=models, runner=runner)
    assert rs.prepare_for_tts("Замок на двери", "plus", **kw) == "Зам+ок на двер+и"
    assert rs.prepare_for_tts("Замок на двери", "acute", **kw) == (
        "Замо" + ACUTE + "к на двер" + "и" + ACUTE)
    assert rs.prepare_for_tts("Замок на двери", "respell", **kw) == "Замок на двери"


def test_stop_propagates_from_engine(tmp_path):
    python, models = _fake_env(tmp_path)

    class Stop(Exception):
        pass

    def runner(argv, *, stdin, stopped, timeout):
        raise Stop("VOICE_STOPPED")

    with pytest.raises(Stop):
        rs.stress_plus("на двери", python_executable=python, model_dir=models, runner=runner)


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        rs.prepare_for_tts("текст", "loud")


# --- real engine minimal pairs (skipped where RUAccent is not provisioned) ---

real_engine = pytest.mark.skipif(
    not (HOST_PYTHON.is_file() and HOST_MODELS.is_dir()),
    reason="RUAccent environment not provisioned on this machine")


MINIMAL_PAIRS = [
    ("на двери висит замок", "зам+ок"),
    ("в старом замке жил король", "з+амке"),
    ("не забудьте купить муку", "мук+у"),
    ("торты закажем в пекарне", "т+орты"),
    ("подпишите договор", "догов+ор"),
    ("каталог товаров", "катал+ог"),
    ("а звонишь ты редко", "звон+ишь"),
    ("красивее всего", "крас+ивее"),
    ("средства на счёте", "ср+едства"),
]


@real_engine
def test_real_accentizer_minimal_pairs(tmp_path):
    # One worker launch for the whole table: the model load dominates the cost.
    text = " ".join(sentence.capitalize() + "." for sentence, _ in MINIMAL_PAIRS)
    out = rs.stress_plus(text, python_executable=HOST_PYTHON, model_dir=HOST_MODELS,
                         cache_dir=tmp_path, timeout=300)
    missing = [expected for _, expected in MINIMAL_PAIRS if expected not in out.lower()]
    assert not missing, (missing, out)
