"""Extended synthetic coding tasks for tools/llm_task_bench.py (16 tasks, each checked by a real unit test).

Owner 22.10: «4 задачи — не рекорд»: the router decision needs >=20 tasks. These are general Python problems with exact,
deterministic checks; nothing here touches Bossman data. Import: `from tools.llm_bench_tasks import TASKS_EXTENDED`.
"""
from __future__ import annotations

TASKS_EXTENDED: list[tuple[str, str, str]] = [
    ("roman",
     "Write Python `to_roman(n: int) -> str` and `from_roman(s: str) -> int` for 1..3999 using standard subtractive "
     "notation (IV, IX, XL, XC, CD, CM). `from_roman` must raise ValueError for an invalid or non-canonical numeral "
     "such as 'IIII' or 'VX', and `to_roman` for n outside 1..3999.",
     r"""
import pytest
from sol import to_roman, from_roman
assert to_roman(1994) == 'MCMXCIV' and to_roman(3999) == 'MMMCMXCIX' and to_roman(4) == 'IV'
assert from_roman('MCMXCIV') == 1994 and from_roman('XLII') == 42
for n in range(1, 4000):
    assert from_roman(to_roman(n)) == n
for bad in ['IIII', 'VX', '', 'ABC']:
    with pytest.raises(ValueError): from_roman(bad)
for bad in [0, 4000]:
    with pytest.raises(ValueError): to_roman(bad)
"""),
    ("dedupe_rows",
     "Write Python `dedupe(rows: list[dict], key: str) -> list[dict]` that removes rows whose `key` value is a duplicate "
     "ignoring case and surrounding whitespace, keeping the FIRST occurrence and the original order. Rows missing the key "
     "or with an empty value are all kept.",
     r"""
from sol import dedupe
rows = [{'e': 'A@x.io', 'i': 1}, {'e': ' a@X.IO ', 'i': 2}, {'e': 'b@x.io', 'i': 3}, {'i': 4}, {'e': '', 'i': 5}, {'e': '', 'i': 6}]
assert [r['i'] for r in dedupe(rows, 'e')] == [1, 3, 4, 5, 6]
assert dedupe([], 'e') == []
"""),
    ("merge_intervals",
     "Write Python `merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]` that merges overlapping or touching "
     "closed intervals and returns them sorted by start. Input may be unsorted and may contain (b, a) with b > a, "
     "which must be normalised to (a, b).",
     r"""
from sol import merge
assert merge([(1, 3), (2, 6), (8, 10), (15, 18)]) == [(1, 6), (8, 10), (15, 18)]
assert merge([(1, 4), (4, 5)]) == [(1, 5)]
assert merge([(5, 1), (2, 3)]) == [(1, 5)]
assert merge([]) == []
assert merge([(7, 7)]) == [(7, 7)]
"""),
    ("slugify",
     "Write Python `slugify(text: str, max_len: int = 50) -> str`: lower-case, keep Unicode letters and digits, replace any "
     "run of other characters with a single '-', strip leading/trailing '-', then cut to max_len without ending in '-'. "
     "Empty input returns ''.",
     r"""
from sol import slugify
assert slugify('Hello,  World! 2026') == 'hello-world-2026'
assert slugify('  Привет, мир — тест  ') == 'привет-мир-тест'
assert slugify('a--b__c') == 'a-b-c'
assert slugify('abc def', max_len=4) == 'abc'
assert slugify('!!!') == '' and slugify('') == ''
"""),
    ("semver",
     "Write Python `cmp(a: str, b: str) -> int` returning -1, 0 or 1 by Semantic Versioning 2.0.0 precedence for "
     "versions like '1.2.3', '1.2.3-rc.1', '1.2.3-rc.10', '1.2.3+build.5' (build metadata is ignored; a pre-release has "
     "lower precedence than the release; numeric identifiers compare numerically, alphanumeric lexically, and a longer "
     "pre-release list wins when all preceding identifiers are equal). Raise ValueError on malformed input.",
     r"""
import pytest
from sol import cmp
assert cmp('1.2.3', '1.2.3') == 0 and cmp('1.2.3+build.5', '1.2.3+build.9') == 0
assert cmp('1.2.3-rc.1', '1.2.3') == -1 and cmp('1.2.3', '1.2.3-rc.1') == 1
assert cmp('1.2.3-rc.2', '1.2.3-rc.10') == -1
assert cmp('1.0.0-alpha', '1.0.0-alpha.1') == -1
assert cmp('1.0.0-alpha.beta', '1.0.0-beta') == -1
assert cmp('1.10.0', '1.9.9') == 1 and cmp('0.9.0', '1.0.0') == -1
for bad in ['1.2', '1.2.3.4', 'v1.2.3', '01.2.3']:
    with pytest.raises(ValueError): cmp(bad, '1.0.0')
"""),
    ("token_bucket",
     "Write a Python class `TokenBucket(rate: float, capacity: int, clock=time.monotonic)` with `allow(n: int = 1) -> bool`: "
     "the bucket starts full, refills continuously at `rate` tokens per second up to `capacity`, and `allow` consumes n "
     "tokens if available (returning True) or returns False without consuming anything.",
     r"""
from sol import TokenBucket
t = [0.0]
b = TokenBucket(rate=2.0, capacity=4, clock=lambda: t[0])
assert all(b.allow() for _ in range(4)) and not b.allow()
t[0] = 0.5; assert b.allow() and not b.allow()
t[0] = 10.0; assert b.allow(4) and not b.allow(1)
t[0] = 10.25; assert not b.allow(1)
t[0] = 10.5; assert b.allow(1)
"""),
    ("json_path",
     "Write Python `get_path(data, path: str, default=None)` reading nested dicts/lists by a dotted path with list indexes, "
     "e.g. 'a.b[0].c' or 'items[-1].name'. Return `default` when any step is missing, the index is out of range, or the "
     "type does not match. Keys may contain digits; indexes are only inside [].",
     r"""
from sol import get_path
d = {'a': {'b': [{'c': 1}, {'c': 2}]}, 'items': [{'name': 'x'}, {'name': 'y'}], 'k9': 'v'}
assert get_path(d, 'a.b[0].c') == 1 and get_path(d, 'a.b[1].c') == 2 and get_path(d, 'items[-1].name') == 'y'
assert get_path(d, 'k9') == 'v'
assert get_path(d, 'a.b[5].c', 'dflt') == 'dflt' and get_path(d, 'a.x', 0) == 0 and get_path(d, 'a.b.c') is None
assert get_path([1, 2], '[1]') == 2
"""),
    ("iso_duration",
     "Write Python `seconds(iso: str) -> int` that parses an ISO-8601 duration of the forms 'PnDTnHnMnS' with any subset "
     "of components (e.g. 'PT1H30M15S', 'P2DT3H', 'PT45S', 'P1D', 'PT0S') and returns the total whole seconds. Weeks "
     "('P2W') are 7 days each. Raise ValueError for anything else, including months/years, negative values and empty 'P'.",
     r"""
import pytest
from sol import seconds
assert seconds('PT1H30M15S') == 5415 and seconds('P2DT3H') == 183600 and seconds('PT45S') == 45
assert seconds('P1D') == 86400 and seconds('PT0S') == 0 and seconds('P2W') == 1209600
for bad in ['P', 'PT', '1H', 'P1M', 'P1Y', 'PT-5S', 'PT1H1H', '']:
    with pytest.raises(ValueError): seconds(bad)
"""),
    ("levenshtein",
     "Write Python `distance(a: str, b: str) -> int` computing the Levenshtein edit distance (insert, delete, substitute, "
     "each cost 1) in O(len(a)*len(b)) time and O(min(len(a), len(b))) extra memory.",
     r"""
from sol import distance
assert distance('kitten', 'sitting') == 3 and distance('', 'abc') == 3 and distance('abc', '') == 3
assert distance('flaw', 'lawn') == 2 and distance('same', 'same') == 0 and distance('', '') == 0
assert distance('a' * 2000, 'a' * 1999 + 'b') == 1
"""),
    ("kv_config",
     "Write Python `parse(text: str, env: dict[str, str]) -> dict[str, str]` for a config format: one `key = value` per line; "
     "blank lines and lines starting with '#' or ';' are ignored; a trailing ' # comment' is stripped unless the value is "
     "double-quoted; double-quoted values keep their inner text verbatim (including '#'); `${NAME}` inside unquoted values is "
     "replaced from `env` (missing name -> empty string); keys are trimmed and case-sensitive; a duplicate key keeps the last "
     "value. Raise ValueError for a line without '='.",
     r"""
import pytest
from sol import parse
text = '''
# comment
host = example.org   # trailing
port=8080
path = "/a # not a comment"
home = ${HOME}/x
miss = ${NOPE}!
host = final
; another comment
'''
out = parse(text, {'HOME': '/root'})
assert out == {'host': 'final', 'port': '8080', 'path': '/a # not a comment', 'home': '/root/x', 'miss': '!'}
with pytest.raises(ValueError): parse('novalue', {})
"""),
    ("brackets",
     "Write Python `balanced(s: str) -> bool` for '()[]{}' with proper nesting; any other characters are ignored. Also "
     "`first_error(s: str) -> int | None` returning the index of the first unmatched or mismatched bracket (for an unclosed "
     "opener, its own index), or None when balanced.",
     r"""
from sol import balanced, first_error
assert balanced('a(b[c]{d})') and balanced('') and not balanced('(]') and not balanced('((') and not balanced(')')
assert first_error('a(b[c]{d})') is None and first_error('(]') == 1 and first_error('x)') == 1 and first_error('(()') == 0
"""),
    ("top_words",
     "Write Python `top_words(text: str, k: int) -> list[tuple[str, int]]`: the k most frequent words, case-insensitive, "
     "where a word is a maximal run of Unicode letters/digits/apostrophes; ties broken alphabetically; fewer than k words "
     "returns what exists; k <= 0 returns [].",
     r"""
from sol import top_words
assert top_words('b a b a c', 2) == [('a', 2), ('b', 2)]
assert top_words("It's it's IT'S the the a.", 1) == [("it's", 3)]
assert top_words('Ёж ёж еж', 1) == [('ёж', 2)]
assert top_words('x', 5) == [('x', 1)] and top_words('x', 0) == []
"""),
    ("human_bytes",
     "Write Python `human(n: int) -> str` formatting a byte count with binary units: 0..1023 -> 'N B'; otherwise one decimal "
     "and unit among KB, MB, GB, TB, PB (1 KB = 1024 B), e.g. 1536 -> '1.5 KB', 1048576 -> '1.0 MB'. Values that round to "
     "1024.0 of a unit must roll over to the next unit ('1.0 MB', not '1024.0 KB'). Negative input raises ValueError.",
     r"""
import pytest
from sol import human
assert human(0) == '0 B' and human(1023) == '1023 B' and human(1024) == '1.0 KB' and human(1536) == '1.5 KB'
assert human(1048576) == '1.0 MB' and human(1048575) == '1.0 MB' and human(5 * 1024 ** 4) == '5.0 TB'
assert human(1024 ** 5) == '1.0 PB'
with pytest.raises(ValueError): human(-1)
"""),
    ("backoff",
     "Write Python `schedule(base: float, factor: float, cap: float, attempts: int) -> list[float]` returning the wait "
     "before each retry: base*factor**i capped at cap, for i in 0..attempts-1. Also `next_delay(prev: float | None, base, "
     "factor, cap) -> float` giving the first delay when prev is None, else min(cap, prev*factor). Validate: base > 0, "
     "factor >= 1, cap >= base, attempts >= 0, else ValueError.",
     r"""
import pytest
from sol import schedule, next_delay
assert schedule(1, 2, 10, 5) == [1, 2, 4, 8, 10] and schedule(0.5, 3, 100, 0) == []
assert next_delay(None, 1, 2, 10) == 1 and next_delay(8, 1, 2, 10) == 10 and next_delay(2, 1, 2, 10) == 4
for bad in [(0, 2, 10, 1), (1, 0.5, 10, 1), (5, 2, 1, 1), (1, 2, 10, -1)]:
    with pytest.raises(ValueError): schedule(*bad)
"""),
    ("spiral",
     "Write Python `spiral(m: list[list[int]]) -> list[int]` returning the elements of a rectangular matrix in clockwise "
     "spiral order starting at the top-left. Empty matrix or empty rows return [].",
     r"""
from sol import spiral
assert spiral([[1, 2, 3], [4, 5, 6], [7, 8, 9]]) == [1, 2, 3, 6, 9, 8, 7, 4, 5]
assert spiral([[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]) == [1, 2, 3, 4, 8, 12, 11, 10, 9, 5, 6, 7]
assert spiral([[1], [2], [3]]) == [1, 2, 3] and spiral([[1, 2]]) == [1, 2] and spiral([]) == [] and spiral([[]]) == []
"""),
    ("phone_e164",
     "Write Python `normalize(raw: str) -> str | None` turning Russian and Czech phone numbers into E.164: Russia: '8 (912) "
     "345-67-89', '+7 912 345 67 89', '79123456789' -> '+79123456789' (10 digits after the country code, first digit 9 for "
     "mobiles or 3/4/8 for landlines); Czech: '+420 777 123 456', '00420777123456', '777 123 456' -> '+420777123456' (9 "
     "digits). Anything else (wrong length, letters, other countries) returns None.",
     r"""
from sol import normalize
assert normalize('8 (912) 345-67-89') == '+79123456789' and normalize('+7 912 345 67 89') == '+79123456789'
assert normalize('79123456789') == '+79123456789' and normalize('+7 495 123-45-67') == '+74951234567'
assert normalize('+420 777 123 456') == '+420777123456' and normalize('00420777123456') == '+420777123456'
assert normalize('777 123 456') == '+420777123456'
for bad in ['12345', '+1 212 555 0100', '8 912 345', 'abc', '', '+7 123 456 78 90']:
    assert normalize(bad) is None, bad
"""),
]
