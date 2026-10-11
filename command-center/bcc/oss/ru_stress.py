"""Russian stress (udarenie) pre-processor for the local voice clone.

Pipeline, in this order (stress is placed on the FINAL spoken words, so every
number and abbreviation has to be words first):

1. ``normalize_for_speech`` - NFC, abbreviations, numbers+units -> words.
2. ``stress_plus`` - an open-source accentizer (RUAccent, Apache-2.0 code,
   run in a separate Python environment by ``ru_stress_worker.py``) puts ``+``
   before the stressed vowel (``зам+ок``).
3. ``to_acute`` / ``to_respelled`` - converts the ``+`` form into the form
   the TTS model sees: U+0301 combining acute after the vowel, or an
   orthoepic respelling (unstressed ``о`` -> ``а``).

English/Latin words are never touched. Nothing here talks to a network; the
accentizer worker runs offline against already provisioned model files.
Text never goes to logs.
"""
from __future__ import annotations

import hashlib
import re
import tempfile
import threading
import unicodedata
from collections import OrderedDict
from pathlib import Path
from typing import Callable

ACUTE = "́"
PLUS = "+"
STRESS_MODES = ("off", "plus", "acute", "respell")
# Bump when the pipeline output changes so persistent cache entries are not reused.
PIPELINE_VERSION = "ru-stress-1"
VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_CYR = "А-Яа-яЁё"
_VOWEL_SET = set(VOWELS)

# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------
_UNITS_M = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_UNITS_F = ["ноль", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_UNITS_N = ["ноль", "одно", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
          "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят",
         "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот",
             "восемьсот", "девятьсот"]
# (singular, 2-4, 5+) and grammatical gender of the scale noun.
_SCALES = [("тысяча", "тысячи", "тысяч", "f"),
           ("миллион", "миллиона", "миллионов", "m"),
           ("миллиард", "миллиарда", "миллиардов", "m")]
_ORD_UNITS = ["нулевой", "первый", "второй", "третий", "четвёртый", "пятый", "шестой",
              "седьмой", "восьмой", "девятый"]
_ORD_TEENS = ["десятый", "одиннадцатый", "двенадцатый", "тринадцатый", "четырнадцатый",
              "пятнадцатый", "шестнадцатый", "семнадцатый", "восемнадцатый", "девятнадцатый"]
_ORD_TENS = ["", "", "двадцатый", "тридцатый", "сороковой", "пятидесятый", "шестидесятый",
             "семидесятый", "восьмидесятый", "девяностый"]
_ORD_HUNDREDS = ["", "сотый", "двухсотый", "трёхсотый", "четырёхсотый", "пятисотый",
                 "шестисотый", "семисотый", "восьмисотый", "девятисотый"]


def _plural_index(n: int) -> int:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return 2
    n %= 10
    return 0 if n == 1 else 1 if 2 <= n <= 4 else 2


def _triplet(n: int, gender: str) -> list[str]:
    words: list[str] = []
    h, rest = divmod(n, 100)
    if h:
        words.append(_HUNDREDS[h])
    if 10 <= rest <= 19:
        words.append(_TEENS[rest - 10])
    else:
        t, u = divmod(rest, 10)
        if t:
            words.append(_TENS[t])
        if u:
            table = _UNITS_F if gender == "f" else _UNITS_N if gender == "n" else _UNITS_M
            words.append(table[u])
    return words


def cardinal(n: int, gender: str = "m") -> str:
    """Nominative cardinal in words (0 <= n < 10**12)."""
    if n < 0 or n >= 10 ** 12:
        raise ValueError("number out of range")
    if n == 0:
        return "ноль"
    groups: list[int] = []
    x = n
    while x:
        x, r = divmod(x, 1000)
        groups.append(r)
    words: list[str] = []
    for index in range(len(groups) - 1, -1, -1):
        value = groups[index]
        if not value:
            continue
        if index == 0:
            words.extend(_triplet(value, gender))
        else:
            scale = _SCALES[index - 1]
            words.extend(_triplet(value, scale[3]))
            words.append(scale[_plural_index(value)])
    return " ".join(words)


def _ordinal_nominative(n: int) -> str:
    """Masculine nominative ordinal; only the last component is ordinal."""
    if n == 0:
        return "нулевой"
    if n % 1000 == 0:
        prefix = {1000: "", 2000: "двух", 3000: "трёх", 4000: "четырёх"}.get(n)
        if prefix is not None:
            return prefix + "тысячный"
        return cardinal(n)  # rare: keep the cardinal rather than guess
    tail = n % 100
    if tail == 0:
        h = n // 100 % 10
        rest = n - h * 100
        return (cardinal(rest) + " " if rest else "") + _ORD_HUNDREDS[h]
    rest = n - tail
    lead = (cardinal(rest) + " ") if rest else ""
    if tail < 10:
        last = _ORD_UNITS[tail]
    elif tail < 20:
        last = _ORD_TEENS[tail - 10]
    elif tail % 10 == 0:
        last = _ORD_TENS[tail // 10]
    else:
        last = cardinal(tail - tail % 10) + " " + _ORD_UNITS[tail % 10]
    return lead + last


def ordinal(n: int, case: str = "nom") -> str:
    """Masculine ordinal in nominative (nom), genitive (gen) or prepositional (prep)."""
    base = _ordinal_nominative(n)
    words = base.split(" ")
    last = words[-1]
    if case != "nom":
        if last.endswith("ий"):  # третий
            stem, suf = last[:-2], {"gen": "ьего", "prep": "ьем"}[case]
        elif last.endswith("ой") or last.endswith("ый"):
            stem, suf = last[:-2], {"gen": "ого", "prep": "ом"}[case]
        else:
            stem, suf = last, ""
        last = stem + suf
    words[-1] = last
    return " ".join(words)


_FRACTION_WORDS = {1: ("десятая", "десятых"), 2: ("сотая", "сотых"), 3: ("тысячная", "тысячных")}

# --------------------------------------------------------------------------
# Units and abbreviations
# --------------------------------------------------------------------------
# key -> (1, 2-4, 5+, gender)
_UNIT_FORMS: dict[str, tuple[str, str, str, str]] = {
    "%": ("процент", "процента", "процентов", "m"),
    "$": ("доллар", "доллара", "долларов", "m"),
    "€": ("евро", "евро", "евро", "m"),
    "₽": ("рубль", "рубля", "рублей", "m"),
    "руб": ("рубль", "рубля", "рублей", "m"),
    "руб.": ("рубль", "рубля", "рублей", "m"),
    "р.": ("рубль", "рубля", "рублей", "m"),
    "кг": ("килограмм", "килограмма", "килограммов", "m"),
    "км": ("километр", "километра", "километров", "m"),
    "мм": ("миллиметр", "миллиметра", "миллиметров", "m"),
    "см": ("сантиметр", "сантиметра", "сантиметров", "m"),
    "м": ("метр", "метра", "метров", "m"),
    "мин": ("минута", "минуты", "минут", "f"),
    "сек": ("секунда", "секунды", "секунд", "f"),
    "с": ("секунда", "секунды", "секунд", "f"),
    "ч": ("час", "часа", "часов", "m"),
    "мс": ("миллисекунда", "миллисекунды", "миллисекунд", "f"),
    "кб": ("килобайт", "килобайта", "килобайт", "m"),
    "мб": ("мегабайт", "мегабайта", "мегабайт", "m"),
    "гб": ("гигабайт", "гигабайта", "гигабайт", "m"),
    "тб": ("терабайт", "терабайта", "терабайт", "m"),
    "гц": ("герц", "герца", "герц", "m"),
    "квт": ("киловатт", "киловатта", "киловатт", "m"),
    "шт": ("штука", "штуки", "штук", "f"),
    "шт.": ("штука", "штуки", "штук", "f"),
}
_ABBREVIATIONS: tuple[tuple[str, str], ...] = (
    (r"\bи т\.\s?д\.", "и так далее"),
    (r"\bи т\.\s?п\.", "и тому подобное"),
    (r"\bт\.\s?е\.", "то есть"),
    (r"\bт\.\s?к\.", "так как"),
    (r"\bт\.\s?н\.", "так называемый"),
    (r"\bи др\.", "и другие"),
    (r"\bи пр\.", "и прочее"),
    (r"№\s?(?=\d)", "номер "),
    (r"\bнапр\.", "например"),
)
_NUM_RE = re.compile(
    r"(?:(?P<cur>[$€₽])\s?)?(?P<int>\d+(?:[  ]\d{3})*)(?P<frac>[.,]\d{1,3})?(?!\d)")
_UNIT_AFTER_RE = re.compile(r"\s?(%|[$€₽]|[А-Яа-яЁё]{1,4})(?![А-Яа-яЁё])")
_ORD_SUFFIX_RE = re.compile(r"\b(\d+)-(й|го|м|ого|ом)(?![А-Яа-яЁё])")
_YEAR_RE = re.compile(r"\b(\d{4})\s+(год[а-яё]*|г\.)(?![А-Яа-яЁё])")


def _int(digits: str) -> int:
    return int(re.sub(r"[  ]", "", digits))


_MASCULINE_A = frozenset("""раза года часа рубля метра дня мая июня июля января февраля марта апреля
августа сентября октября ноября декабря папа дядя""".split())
_MASCULINE_U = frozenset("году дню часу раз".split())


def _next_word_gender(rest: str) -> str:
    """Gender of the counted noun from its ending (heuristic, good for common words)."""
    match = re.match(r"\s*([А-Яа-яЁё]+)", rest)
    if not match:
        return "m"
    word = match.group(1).lower()
    if word in _MASCULINE_A or word in _MASCULINE_U:
        return "m"
    if word.endswith(("а", "я", "ы", "и", "у", "ю")) and not word.endswith(("ов", "ей")):
        return "f"
    if word.endswith(("о", "е")):
        return "n"
    return "m"


def _lookup_unit(token: str) -> tuple[str, str, str, str] | None:
    key = token.lower()
    return _UNIT_FORMS.get(key) or _UNIT_FORMS.get(key.rstrip("."))


def _expand_numbers(text: str) -> str:
    def years(match: re.Match[str]) -> str:
        n, noun = int(match.group(1)), match.group(2)
        if noun == "г.":
            return f"{ordinal(n, 'nom')} год"
        case = "prep" if noun.startswith("году") else "gen" if noun.startswith("года") else "nom"
        return f"{ordinal(n, case)} {noun}"

    def ords(match: re.Match[str]) -> str:
        suffix = match.group(2)
        case = "gen" if suffix in {"го", "ого"} else "prep" if suffix in {"м", "ом"} else "nom"
        return ordinal(int(match.group(1)), case)

    text = _YEAR_RE.sub(years, text)
    text = _ORD_SUFFIX_RE.sub(ords, text)
    out: list[str] = []
    pos = 0
    for match in _NUM_RE.finditer(text):
        if match.start() < pos:
            continue
        before = text[match.start() - 1] if match.start() else " "
        if before.isalpha() and not match.group("cur"):
            continue  # part of a token such as "mp3" or "F5"
        out.append(text[pos:match.start()])
        end = match.end()
        unit = None
        cur = match.group("cur")
        if cur:
            unit = _UNIT_FORMS[cur]
        else:
            tail = _UNIT_AFTER_RE.match(text, end)
            if tail and _lookup_unit(tail.group(1)):
                unit = _lookup_unit(tail.group(1))
                end = tail.end()
        try:
            value = _int(match.group("int"))
            frac = match.group("frac")
            if frac and re.search(r"[A-Za-z][\w-]*\s$", text[:match.start()]):
                # A version after a Latin name ("Bossman 2.1"): "два точка один".
                words = f"{cardinal(value)} точка {cardinal(int(frac[1:]))}"
            elif frac:
                digits = frac[1:]
                part = int(digits)
                whole = cardinal(value, "f")
                celyh = "целая" if _plural_index(value) == 0 else "целых"
                small = _FRACTION_WORDS[len(digits)]
                words = (f"{whole} {celyh} {cardinal(part, 'f')} "
                         f"{small[0] if _plural_index(part) == 0 else small[1]}")
                if unit:
                    words += " " + unit[1]
            else:
                gender = unit[3] if unit else _next_word_gender(text[end:])
                words = cardinal(value, gender)
                if unit:
                    words += " " + unit[_plural_index(value)]
        except (ValueError, KeyError):
            out.append(text[match.start():end])
            pos = end
            continue
        out.append(words)
        pos = end
    out.append(text[pos:])
    return "".join(out)


def normalize_for_speech(text: str) -> str:
    """Make text speakable: NFC, abbreviations, numbers/units to words.

    Latin words are left exactly as they are. Idempotent for already
    normalized text.
    """
    value = unicodedata.normalize("NFC", text)
    value = value.replace(ACUTE, "")  # never trust marks that came in from outside
    for pattern, replacement in _ABBREVIATIONS:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    value = _expand_numbers(value)
    value = re.sub(r"[ \t ]+", " ", value).strip()
    return value


# --------------------------------------------------------------------------
# Stress marks: '+' form <-> acute <-> respelling
# --------------------------------------------------------------------------
_WORD_RE = re.compile(rf"[{_CYR}+]+(?:-[{_CYR}+]+)*|[A-Za-z0-9_]+")


def clean_plus(text: str) -> str:
    """Keep ``+`` only directly before a Cyrillic vowel inside a Cyrillic word.

    ``+`` is dropped before ``ё`` (always stressed) and anywhere else (stray
    plus signs, Latin words), so downstream code can trust the markup.
    """
    chars = list(text)
    out: list[str] = []
    for i, ch in enumerate(chars):
        if ch != PLUS:
            out.append(ch)
            continue
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        prev = chars[i - 1] if i else ""
        if nxt in _VOWEL_SET and nxt not in "ёЁ" and not re.match(r"[A-Za-z0-9]", prev or " "):
            out.append(PLUS)
    return "".join(out)


def strip_marks(text: str) -> str:
    return text.replace(PLUS, "").replace(ACUTE, "")


def to_acute(text_plus: str) -> str:
    """``зам+ок`` -> ``замо́к`` (combining acute after the vowel)."""
    return re.sub(rf"\+([{VOWELS}])", lambda m: m.group(1) + ACUTE, clean_plus(text_plus))


_PROCLITICS = frozenset("""в во на по от до из изо за над под при про без для об обо о к ко с со у
не ни но и а да то же ли бы ль""".split())


def to_respelled(text_plus: str) -> str:
    """Orthoepic respelling: unstressed ``о`` -> ``а`` (akanye), nothing else.

    Words with several vowels and no stress mark are left as written, because
    guessing would damage them. Latin words and ``ё`` are never changed.
    """
    marked = clean_plus(text_plus)

    def part(token: str) -> str:
        vowels = [i for i, ch in enumerate(token.replace(PLUS, "")) if ch in _VOWEL_SET]
        if PLUS in token:
            stressed = token.index(PLUS)
            plain = token.replace(PLUS, "")
            # index of the stressed vowel in the plain word
            stressed_plain = stressed
            chars = [("а" if ch == "о" else "А" if ch == "О" else ch) if i != stressed_plain else ch
                     for i, ch in enumerate(plain)]
            return "".join(chars)
        if len(vowels) == 1 and token.lower() in _PROCLITICS:
            return token.replace("о", "а").replace("О", "А")
        return token

    def word(match: re.Match[str]) -> str:
        token = match.group(0)
        if re.search(r"[A-Za-z0-9_]", token):
            return token
        return "-".join(part(p) for p in token.split("-"))

    return _WORD_RE.sub(word, marked)


# --------------------------------------------------------------------------
# Accentizer runner + cache
# --------------------------------------------------------------------------
class StressUnavailable(RuntimeError):
    """The accentizer environment is missing or failed; callers fall back."""


_cache: "OrderedDict[str, str]" = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 256


def _key(text: str) -> str:
    return hashlib.sha256((PIPELINE_VERSION + "\0" + text).encode("utf-8")).hexdigest()


def _default_runner(argv: list[str], *, stdin: bytes | None, stopped: Callable[[], bool],
                    timeout: float) -> None:
    from .piper import _run  # local import keeps this module importable standalone
    _run(argv, stdin=stdin, stopped=stopped, timeout=timeout)


def stress_plus(
    text: str, *, python_executable: str | Path | None, model_dir: str | Path | None,
    cache_dir: str | Path | None = None,
    runner: Callable[..., None] | None = None,
    stopped: Callable[[], bool] = lambda: False,
    timeout: float = 120.0,
) -> str:
    """Return ``text`` with ``+`` before stressed vowels (already normalized input).

    Raises :class:`StressUnavailable` when the accentizer cannot run.
    Cached in memory and, when ``cache_dir`` is given, on disk.
    """
    if not text.strip() or not re.search(rf"[{_CYR}]", text):
        return text
    key = _key(text)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    disk = Path(cache_dir) / f"{key}.txt" if cache_dir else None
    if disk is not None and disk.is_file():
        try:
            value = clean_plus(disk.read_text(encoding="utf-8"))
            _remember(key, value)
            return value
        except OSError:
            pass
    if not python_executable or not model_dir:
        raise StressUnavailable("STRESS_ENGINE_UNCONFIGURED")
    python = Path(python_executable)
    directory = Path(model_dir)
    if not python.is_absolute() or not python.is_file() or not directory.is_dir():
        raise StressUnavailable("STRESS_ENGINE_UNAVAILABLE")
    worker = Path(__file__).with_name("ru_stress_worker.py")
    run = runner or _default_runner
    try:
        with tempfile.TemporaryDirectory(prefix="bossman-stress-") as temp:
            out = Path(temp) / "stressed.txt"
            run([str(python), str(worker), str(directory.resolve()), str(out)],
                stdin=text.encode("utf-8"), stopped=stopped, timeout=timeout)
            if not out.is_file():
                raise StressUnavailable("STRESS_OUTPUT_MISSING")
            value = clean_plus(out.read_text(encoding="utf-8").strip())
    except StressUnavailable:
        raise
    except Exception as exc:  # PiperError and friends: STOP must propagate
        if str(exc) == "VOICE_STOPPED":
            raise
        raise StressUnavailable("STRESS_ENGINE_FAILED") from exc
    # The accentizer must not change words, only add marks.
    if strip_marks(value).split() != text.split():
        raise StressUnavailable("STRESS_TEXT_CHANGED")
    _remember(key, value)
    if disk is not None:
        try:
            disk.parent.mkdir(parents=True, exist_ok=True)
            disk.write_text(value, encoding="utf-8")
        except OSError:
            pass
    return value


def _remember(key: str, value: str) -> None:
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def render_mode(text_plus: str, mode: str) -> str:
    """Convert a ``+`` form to what the TTS sees for ``mode``."""
    if mode == "plus":
        return clean_plus(text_plus)
    if mode == "acute":
        return to_acute(text_plus)
    if mode == "respell":
        return to_respelled(text_plus)
    return strip_marks(text_plus)


def prepare_for_tts(
    text: str, mode: str, *, python_executable: str | Path | None = None,
    model_dir: str | Path | None = None, cache_dir: str | Path | None = None,
    runner: Callable[..., None] | None = None,
    stopped: Callable[[], bool] = lambda: False,
) -> str:
    """Full pipeline. Never raises for an unavailable accentizer.

    ``mode`` ``off`` returns the text byte-for-byte (the historical path).
    Any other mode normalizes numbers/abbreviations; if the accentizer is
    missing or fails, the normalized text without marks is returned.
    """
    if mode not in STRESS_MODES:
        raise ValueError("unknown stress mode")
    if mode == "off":
        return text
    normalized = normalize_for_speech(text)
    try:
        stressed = stress_plus(normalized, python_executable=python_executable,
                               model_dir=model_dir, cache_dir=cache_dir,
                               runner=runner, stopped=stopped)
    except StressUnavailable:
        return normalized
    return render_mode(stressed, mode)
