"""Exact-calculation hint for Jeff (owner order 2026-10-01: "математические Джефф").

A language model mis-computes long arithmetic, percentages, unit conversions and similar. When the participant's
message contains ONE unambiguous, computable question, this module computes the exact answer deterministically and
returns a short hint that the runtime puts in front of the user message ("Точный расчёт: ... = ..."). The model
still writes the answer, in Jeff's own voice; it just no longer has to do the arithmetic in its head.

Contract (every line is tested in ``tests/test_jeff_math_assist.py``):
- pure and offline: no network, no file, no clock, no randomness, no ``eval``/``exec``; the only parser is
  ``ast.parse`` on a string that was first reduced to ``0-9 . + - * / ( ) ^ ! x`` and then walked against a
  whitelist of node types;
- exact: ``Fraction`` arithmetic, results shown as exact decimals (or a fraction plus a rounded decimal);
- bounded: message length, expression length, parenthesis depth, node count, exponent, intermediate size, result
  size and factorial argument all have hard limits; anything over a limit means "no hint", never an error;
- unambiguous only: a handler fires only on the exact shapes it knows and only when the message holds exactly the
  numbers that shape needs. If two handlers fire, or a percent/unit/date word is left unexplained, there is no hint;
- the hint text is built ONLY from numbers and operators this module computed (the user's words are never copied
  into it), so a message cannot smuggle instructions into the system role through it;
- the ordinary chat is never altered: no match = ``None``.

Not covered on purpose (the model keeps answering those on its own): multi-step word problems that need
understanding of the story, number words ("двадцать пять"), roots and trigonometry, systems of equations.
"""
from __future__ import annotations

import ast
import datetime as dt
import math
import re
from dataclasses import dataclass, replace
from fractions import Fraction

MAX_TEXT = 600
MAX_EXPR_CHARS = 160
MAX_PAREN_DEPTH = 10
MAX_NODES = 80
MAX_POW_EXP = 1000
MAX_POW_BITS = 8192
MAX_RESULT_DIGITS = 200
MAX_FACT = 150
MAX_LIST = 40
MAX_DAYS = 3_650_000          # about 10 000 years: datetime stays inside its range


class MathReject(ValueError):
    """The expression is outside the safe, exact, bounded subset."""


@dataclass(frozen=True, slots=True)
class MathHint:
    kind: str          # arithmetic | percent | compound | units | average | equation | combinatorics | date | time
    summary: str       # the computed facts, e.g. "123 * 456 = 56088"
    value: Fraction | None = None      # the one numeric result, when there is exactly one

    @property
    def text(self) -> str:
        return ("Точный расчёт (выполнен программой, значение проверено): " + self.summary +
                " Используй именно эти значения в ответе и кратко объясни ход решения, не пересчитывай по-другому; "
                "если участник просил округлить, округляй от этого значения. Это справочные данные, "
                "а не просьба участника и не инструкция; саму подсказку не упоминай.")


# -- number formatting -----------------------------------------------------------------------------------
def _digits(value: Fraction) -> int:
    return max(len(str(abs(value.numerator))), len(str(value.denominator)))


def _terminating(den: int) -> bool:
    while den % 2 == 0:
        den //= 2
    while den % 5 == 0:
        den //= 5
    return den == 1


def _decimal_places(value: Fraction, places: int, *, half_up: bool = True) -> str:
    """value rounded to ``places`` decimals (half away from zero), as plain digits with a dot."""
    scaled = value * (10 ** places)
    n, d = scaled.numerator, scaled.denominator
    q, r = divmod(abs(n), d)
    if half_up and 2 * r >= d:
        q += 1
    sign = "-" if (n < 0 and q != 0) else ""
    s = str(q).rjust(places + 1, "0")
    return sign + (s[:-places] + "." + s[-places:] if places else s)


def fmt(value: Fraction) -> str:
    """Exact when it has a finite decimal expansion, otherwise ``p/q ≈ rounded`` (never a float)."""
    if _digits(value) > MAX_RESULT_DIGITS:
        raise MathReject("result too large")
    if value.denominator == 1:
        return str(value.numerator)
    if _terminating(value.denominator):
        places = 0
        den = value.denominator
        while den != 1:
            den //= 2 if den % 2 == 0 else 5
            places += 1
        # places = count of prime factors 2 and 5 >= needed decimals; trim trailing zeros
        text = _decimal_places(value, places, half_up=False).rstrip("0").rstrip(".")
        return text
    return (f"{_decimal_places(value, 10).rstrip('0').rstrip('.')} "
            f"(приближённо; точная дробь {value.numerator}/{value.denominator})")


def money(value: Fraction) -> str:
    return _decimal_places(value, 2)


# -- safe expression evaluator (ast, whitelist, Fraction polynomials of degree <= 2 in x) ---------------
Poly = tuple  # (c0, c1, c2) of Fractions; arithmetic uses the constant term only


def _const(value: Fraction) -> Poly:
    return (Fraction(value), Fraction(0), Fraction(0))


def _is_const(p: Poly) -> bool:
    return p[1] == 0 and p[2] == 0


def _padd(a: Poly, b: Poly, sign: int = 1) -> Poly:
    return tuple(x + sign * y for x, y in zip(a, b))


def _pmul(a: Poly, b: Poly) -> Poly:
    out = [Fraction(0)] * 5
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            if x and y:
                out[i + j] += x * y
    if out[3] or out[4]:
        raise MathReject("degree above 2")
    return (out[0], out[1], out[2])


def _check_size(value: Fraction) -> None:
    if _digits(value) > MAX_RESULT_DIGITS * 2:
        raise MathReject("intermediate value too large")


def _ppow(base: Poly, exp: Poly) -> Poly:
    if not _is_const(exp) or exp[0].denominator != 1:
        raise MathReject("only whole constant exponents")
    e = int(exp[0])
    if abs(e) > MAX_POW_EXP:
        raise MathReject("exponent too large")
    if _is_const(base):
        b = base[0]
        if b == 0 and e <= 0:
            raise MathReject("zero to non-positive power")
        size = max(abs(b.numerator).bit_length(), b.denominator.bit_length())
        if size * abs(e) > MAX_POW_BITS:
            raise MathReject("power too large")
        result = (b ** e) if e >= 0 else (Fraction(1) / (b ** -e))
        _check_size(result)
        return _const(result)
    if e not in (0, 1, 2):
        raise MathReject("unknown to a power above 2")
    if e == 0:
        return _const(Fraction(1))
    return base if e == 1 else _pmul(base, base)


def _pdiv(a: Poly, b: Poly) -> Poly:
    if not _is_const(b) or b[0] == 0:
        raise MathReject("division by zero or by an unknown")
    return tuple(x / b[0] for x in a)


_BIN = {ast.Add: lambda a, b: _padd(a, b), ast.Sub: lambda a, b: _padd(a, b, -1),
        ast.Mult: _pmul, ast.Div: _pdiv, ast.Pow: _ppow}


def _literal(node: ast.Constant, src: str) -> Fraction:
    if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        raise MathReject("not a number")
    segment = ast.get_source_segment(src, node)
    if segment is None:
        raise MathReject("no source for a number")
    try:
        return Fraction(segment)
    except (ValueError, ZeroDivisionError) as exc:
        raise MathReject("bad number") from exc


def _eval(node: ast.AST, src: str, allow_x: bool) -> Poly:
    if isinstance(node, ast.Expression):
        return _eval(node.body, src, allow_x)
    if isinstance(node, ast.Constant):
        return _const(_literal(node, src))
    if isinstance(node, ast.Name):
        if allow_x and node.id == "x":
            return (Fraction(0), Fraction(1), Fraction(0))
        raise MathReject("unknown name")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand, src, allow_x)
        return value if isinstance(node.op, ast.UAdd) else tuple(-x for x in value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        result = _BIN[type(node.op)](_eval(node.left, src, allow_x), _eval(node.right, src, allow_x))
        for c in result:
            _check_size(c)
        return result
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "fact"
            and len(node.args) == 1 and not node.keywords):
        arg = _eval(node.args[0], src, allow_x)
        if not _is_const(arg) or arg[0].denominator != 1 or not 0 <= arg[0] <= MAX_FACT:
            raise MathReject("factorial argument")
        return _const(Fraction(math.factorial(int(arg[0]))))
    raise MathReject("construct not allowed: " + type(node).__name__)


_ALLOWED_CHARS = re.compile(r"^[0-9.+\-*/()^!x \t]*$")


def _prepare(expr: str, *, allow_x: bool) -> str:
    """Reduce to the safe alphabet or raise. Everything here runs BEFORE ast.parse sees the text."""
    if not isinstance(expr, str) or not expr.strip() or len(expr) > MAX_EXPR_CHARS:
        raise MathReject("empty or too long")
    if not _ALLOWED_CHARS.match(expr):
        raise MathReject("characters outside the safe alphabet")
    if "x" in expr and not allow_x:
        raise MathReject("unknown")
    depth = peak = 0
    for ch in expr:
        if ch == "(":
            depth += 1
            peak = max(peak, depth)
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise MathReject("unbalanced")
    if depth != 0 or peak > MAX_PAREN_DEPTH:
        raise MathReject("unbalanced or too deeply nested")
    s = expr.replace("^", "**")
    if "***" in s or re.search(r"\*\*\s*\*", s) or re.search(r"/\s*/", s):
        raise MathReject("operator run")
    s = re.sub(r"(\d)\s*x", r"\1*x", s)                      # 3x -> 3*x
    s = re.sub(r"\)\s*x", r")*x", s)
    s = re.sub(r"x\s*(\d|\()", r"x*\1", s)
    s = re.sub(r"(\d|\))\s*\(", r"\1*(", s)                  # 2(3+4), (1+2)(3+4)
    s = re.sub(r"\)\s*(\d)", r")*\1", s)
    s = re.sub(r"(\d+)\s*!", r"fact(\1)", s)
    if "!" in s:
        raise MathReject("factorial of a non-literal")
    return s


def parse_poly(expr: str, *, allow_x: bool = False) -> tuple[Poly, ast.AST, str]:
    """(value, tree, prepared source). Raises MathReject for everything outside the safe subset."""
    src = _prepare(expr, allow_x=allow_x)
    try:
        tree = ast.parse(src.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise MathReject("not an expression") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise MathReject("too many nodes")
    try:
        return _eval(tree, src.strip(), allow_x), tree, src.strip()
    except RecursionError as exc:
        raise MathReject("too deep") from exc


def safe_eval(expr: str) -> Fraction:
    """Exact value of an arithmetic expression, or MathReject. Public entry for tests and other callers."""
    value, _, _ = parse_poly(expr)
    return value[0]


_PREC = {ast.Add: 1, ast.Sub: 1, ast.Mult: 2, ast.Div: 2, ast.Pow: 4}
_SYM = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "^"}


def _render(node: ast.AST, src: str) -> str:
    """Canonical text of the parsed tree: the user's raw text is never reused."""
    if isinstance(node, ast.Expression):
        return _render(node.body, src)
    if isinstance(node, ast.Constant):
        return fmt_literal(_literal(node, src))
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.UnaryOp):
        inner = _render(node.operand, src)
        if isinstance(node.operand, ast.BinOp):
            inner = f"({inner})"
        return ("-" if isinstance(node.op, ast.USub) else "+") + inner
    if isinstance(node, ast.BinOp):
        prec = _PREC[type(node.op)]
        left, right = _render(node.left, src), _render(node.right, src)
        if isinstance(node.left, ast.BinOp) and (_PREC[type(node.left.op)] < prec
                                                 or (type(node.op) is ast.Pow and _PREC[type(node.left.op)] == prec)):
            left = f"({left})"
        if isinstance(node.right, ast.BinOp) and (_PREC[type(node.right.op)] < prec
                                                  or (_PREC[type(node.right.op)] == prec and type(node.op) is not ast.Pow)):
            right = f"({right})"
        if isinstance(node.left, ast.UnaryOp) and type(node.op) is ast.Pow:
            left = f"({left})"
        return f"{left} {_SYM[type(node.op)]} {right}"
    if isinstance(node, ast.Call):
        return f"{_render(node.args[0], src)}!" if not isinstance(node.args[0], ast.BinOp) else f"fact({_render(node.args[0], src)})"
    raise MathReject("render")


def fmt_literal(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return fmt(value) if _terminating(value.denominator) else f"{value.numerator}/{value.denominator}"


# -- text normalisation and shared regex pieces -----------------------------------------------------------
NUM = r"(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_NUM_RE = re.compile(NUM)
_GROUPED = re.compile(r"(?<![\d.,])(\d{1,3})((?:[ \u00a0\u202f]\d{3})+)(?![\d])")


def to_fraction(token: str) -> Fraction:
    cleaned = re.sub(r"[ \u00a0\u202f]", "", token).replace(",", ".")
    try:
        return Fraction(cleaned)
    except (ValueError, ZeroDivisionError) as exc:
        raise MathReject("bad number") from exc


def _norm(text: str) -> str:
    t = text.lower().replace("ё", "е").replace("\u2212", "-").replace("–", "-").replace("—", "-")
    t = t.replace("×", "*").replace("·", "*").replace("÷", "/").replace("\u00a0", " ").replace("\u202f", " ")
    return re.sub(r"[ \t\r\n]+", " ", t).strip()


def _numbers(t: str) -> list[str]:
    return _NUM_RE.findall(t)


def _strip_spans(t: str, spans: list[tuple[int, int]]) -> str:
    out, last = [], 0
    for a, b in sorted(spans):
        out.append(t[last:a])
        last = b
    out.append(t[last:])
    return " ".join(out)


# -- arithmetic -------------------------------------------------------------------------------------------
_WORD_OPS = [
    (rf"\b(?:раздели\w*|подели\w*|разделить|поделить)\s+({NUM})\s+на\s+({NUM})", r" \1 / \2 "),
    (rf"\b(?:умножь\w*|помножь\w*|умножить|помножить)\s+({NUM})\s+на\s+({NUM})", r" \1 * \2 "),
    (rf"\b(?:сложи\w*|сложить)\s+({NUM})\s+и\s+({NUM})", r" \1 + \2 "),
    (r"\bумнож\w*\s+на\b|\bпомнож\w*\s+на\b", " * "),
    (r"\bподели\w*\s+на\b|\bраздели\w*\s+на\b|\bразделить\s+на\b", " / "),
    (r"\bв\s+степени\b", " ^ "),
    (r"\bплюс\b|\bприбав\w*\b(?=\s*\d)", " + "),
    (r"\bминус\b", " - "),
    (r"\bв\s+квадрате\b", " ^ 2 "),
    (r"\bв\s+кубе\b", " ^ 3 "),
]
_CUE = re.compile(r"сколько\s+будет|скольк\w+\s+равн\w+|чему\s+равн\w+|посчита\w*|вычисл\w*|реши\w*|рассчита\w*|"
                  r"найди\s+значение|значение\s+выражения|результат|факториал|получится|получим|раздели\w*|подели\w*|"
                  r"умножь\w*|помножь\w*|сложи\w*|вычти\w*|возведи\w*|= ?\?|=\s*$")
_ARITH_BLOCK = re.compile(r"%|процент|скидк|налог|ндс|вклад|кредит|под\s+\d|годов|даты?\b|\d{1,2}[.:]\d{2}[.:]\d|"
                          r"\d{1,2}:\d{2}|км/ч|м/с")
_RUN = re.compile(r"[\d(][\d\s.,()+\-*/^!]*")


def _arith_candidates(t: str) -> list[tuple[str, int, int]]:
    out = []
    for m in _RUN.finditer(t):
        raw = m.group(0)
        start = m.start()
        stripped = raw.rstrip(" .,+-*/^")
        if not stripped:
            continue
        # a trailing "(" or an unmatched ")" at the end belongs to prose
        while stripped.count(")") > stripped.count("(") and stripped.endswith(")"):
            stripped = stripped[:-1].rstrip(" .,+-*/^")
        while stripped.count("(") > stripped.count(")") and stripped.startswith("("):
            stripped = stripped[1:].lstrip(" ")
            start += 1
        out.append((stripped, start, start + len(stripped)))
    return out


def _is_expression(raw: str) -> bool:
    """At least two operands joined by an operator (or one factorial)."""
    if re.search(r"\d\s*!", raw):
        return True
    return bool(re.search(r"[\d)]\s*(?:\*\*|[-+*/^])\s*[-+(]*\s*[\d(]", raw))


def _handle_arithmetic(t: str) -> MathHint | None:
    for old, new in _WORD_OPS:
        t = re.sub(old, new, t)
    t = re.sub(r"(?<=\d)\s*[xх]\s*(?=\d)", " * ", t)           # "5 x 3", "5 х 3" (latin and cyrillic x)
    if _ARITH_BLOCK.search(t):
        return None
    cands = [c for c in _arith_candidates(t) if _is_expression(c[0])]
    if len(cands) != 1:
        return None
    raw, a, b = cands[0]
    # dates / versions / phone-like runs
    if re.search(r"\d\.\d+\.\d|\d{2,}-\d{2,}-\d", raw):
        return None
    rest = _strip_spans(t, [(a, b)])
    cue = bool(_CUE.search(t))
    letters = re.findall(r"[a-zа-я]+", rest)
    dominated = not letters                         # the message is nothing but the expression
    # an unspaced minus (17-5) is also a range / phone / date shape: only with a clear cue or a bare expression
    unspaced_minus = bool(re.search(r"\d-\d", raw)) and not re.search(r"\s[-+*/^]\s|[*/^+]", raw)
    if unspaced_minus and not (cue or dominated):
        return None
    if not (cue or dominated):
        return None
    expr = _GROUPED.sub(lambda m: m.group(1) + re.sub(r"[ \u00a0\u202f]", "", m.group(2)), raw)
    expr = re.sub(r"(\d),(\d)", r"\1.\2", expr)
    if "," in expr:
        return None
    value, tree, src = parse_poly(expr)
    shown = _render(tree, src)
    return MathHint("arithmetic", f"{shown} = {fmt(value[0])}.", value[0])


# -- percent ----------------------------------------------------------------------------------------------
_INC = re.compile(r"увелич\w*|повыс\w*|подорож\w*|выросл?\w*|прибав\w*|наценк\w*|надбавк\w*|добав\w*|начисл\w*|чаев\w*|"
                  r"рост\w*|вырос")
_DEC = re.compile(r"скидк\w*|уменьш\w*|снизи\w*|снижен\w*|подешев\w*|упал\w*|убав\w*|дешевле|удешев\w*")
_REVERSE = re.compile(r"первоначальн\w*|исходн\w*|до\s+(?:скидки|повышения|подорожания|снижения|увеличения|наценки)|"
                      r"(?:какой|какая|какое|какова|каким|сколько)\s+(?:\w+\s+){0,3}?(?:был[аио]?|стоил\w*|составлял\w*)\b")
_PAST = re.compile(r"\bбыл[аио]?\b|\bстоил\w*|\bраньше\b|\bпрежн\w*|\bизначально\b")
_VAT_ADD = re.compile(r"(?:прибав\w*|начисл\w*|добав\w*|плюс|сверху|надбав\w*)\s+(?:\w+\s+)?ндс|ндс\s+сверху|"
                      r"ндс\b[^.?!]{0,40}?\b(?:прибав\w*|начисл\w*|добав\w*)|"
                      r"скольк\w*\s+(?:будет\s+|стоит\s+|получится\s+|заплат\w+\s+|нужно\s+заплатить\s+)?с\s+ндс|"
                      r"итого\s+с\s+ндс|(?:цена|сумма|стоимость)\s+с\s+ндс\s*\?")
_VAT_EXTRACT = re.compile(r"включ\w*\s+ндс|ндс\s+включ\w*|в\s+том\s+числе\s+ндс|в\s+т\.?\s*ч\.?\s+ндс|выдели\w*\s+ндс|"
                          r"скольк\w*\s+(?:\w+\s+){0,3}?(?<!с )ндс|скольк\w*\s+без\s+ндс|(?:цена|сумма|стоимость)\s+без\s+ндс\s*\?")


def _pct_and_base(t: str) -> tuple[Fraction, Fraction] | None:
    tokens = _numbers(t)
    pcts = re.findall(rf"({NUM})\s*(?:%|процент\w*)", t)
    if len(tokens) != 2 or len(pcts) != 1:
        return None
    p = to_fraction(pcts[0])
    remaining = list(tokens)
    remaining.remove(pcts[0])
    return p, to_fraction(remaining[0])


def _handle_percent(t: str) -> MathHint | None:
    if "%" not in t and "процент" not in t and "ндс" not in t:
        return None
    if "сложн" in t or "капитализ" in t or "вклад" in t or "годовых" in t:
        return None                                              # compound-interest handler's territory
    # share: "сколько процентов составляет X от Y", "X это сколько процентов от Y"
    m = re.search(rf"скольк\w*\s+процент\w*\s+(?:составля\w+\s+|будет\s+|это\s+)?({NUM})\s+от\s+({NUM})", t) or \
        re.search(rf"({NUM})\s*(?:-|это|—)?\s*(?:это\s+)?скольк\w*\s+процент\w*\s+от\s+({NUM})", t)
    if m and len(_numbers(t)) == 2:
        x, y = to_fraction(m.group(1)), to_fraction(m.group(2))
        if y == 0:
            return None
        return MathHint("percent", f"{fmt(x)} составляет {fmt(x / y * 100)}% от {fmt(y)}.", x / y * 100)
    # relative change: "на сколько процентов изменилось с X до Y"
    m = re.search(rf"(?:с|от)\s+({NUM})\s+(?:до|на)\s+({NUM})", t)
    if m and re.search(r"на\s+скольк\w*\s+процент|процентн\w+\s+(?:изменени|рост|прирост)", t) and len(_numbers(t)) == 2:
        x, y = to_fraction(m.group(1)), to_fraction(m.group(2))
        if x == 0:
            return None
        change = (y - x) / x * 100
        return MathHint("percent", f"изменение с {fmt(x)} до {fmt(y)} = {fmt(y - x)}, это {fmt(change)}% от {fmt(x)}.")
    pb = _pct_and_base(t)
    if pb is None:
        return None
    p, base = pb
    inc, dec = bool(_INC.search(t)), bool(_DEC.search(t))
    # VAT
    if "ндс" in t:
        add, extract = bool(_VAT_ADD.search(t)), bool(_VAT_EXTRACT.search(t))
        if add == extract:
            return None
        if add:
            vat = base * p / 100
            return MathHint("percent", f"НДС {fmt(p)}% от {fmt(base)} = {fmt(vat)}; сумма с НДС = {fmt(base + vat)}.")
        vat = base * p / (100 + p)
        return MathHint("percent", f"если {fmt(base)} уже включает НДС {fmt(p)}%: НДС = {fmt(vat)}; "
                                   f"сумма без НДС = {fmt(base - vat)}.")
    reverse = bool(_REVERSE.search(t))
    if not reverse and _PAST.search(t):
        return None                      # a past-tense story that is not clearly "what was it before": ambiguous
    of = re.search(rf"{NUM}\s*(?:%|процент\w*)\s+от\s+{NUM}", t)
    if of and not reverse:
        part = p * base / 100
        line = f"{fmt(p)}% от {fmt(base)} = {fmt(part)}"
        if inc != dec:
            line += (f"; {fmt(base)} + {fmt(p)}% = {fmt(base + part)}" if inc
                     else f"; {fmt(base)} - {fmt(p)}% = {fmt(base - part)}")
        return MathHint("percent", line + ".")
    if inc == dec:
        return None
    if reverse:
        factor = (1 + p / 100) if inc else (1 - p / 100)
        if factor == 0:
            return None
        return MathHint("percent", f"если после {'увеличения' if inc else 'уменьшения'} на {fmt(p)}% получилось "
                                   f"{fmt(base)}, то исходное значение = {fmt(base)} / {fmt(factor)} = {fmt(base / factor)}.")
    part = base * p / 100
    if inc:
        return MathHint("percent", f"{fmt(p)}% от {fmt(base)} = {fmt(part)}; после увеличения на {fmt(p)}%: "
                                   f"{fmt(base)} + {fmt(part)} = {fmt(base + part)}.")
    return MathHint("percent", f"{fmt(p)}% от {fmt(base)} = {fmt(part)}; после уменьшения на {fmt(p)}%: "
                               f"{fmt(base)} - {fmt(part)} = {fmt(base - part)}.")


# -- compound / simple interest ---------------------------------------------------------------------------
_PERIODS = [(re.compile(r"ежемесячн\w*|каждый\s+месяц|раз\s+в\s+месяц"), 12),
            (re.compile(r"ежеквартальн\w*|раз\s+в\s+квартал"), 4),
            (re.compile(r"раз\s+в\s+полгода|раз\s+в\s+полугодие|каждые\s+полгода"), 2),
            (re.compile(r"ежегодн\w*|раз\s+в\s+год|каждый\s+год"), 1)]


def _handle_interest(t: str) -> MathHint | None:
    compound = bool(re.search(r"сложн\w*\s+процент|капитализац\w*", t))
    simple = bool(re.search(r"простые?\s+процент|простых\s+процент|без\s+капитализац", t))
    if compound == simple:
        return None
    tokens = _numbers(t)
    rates = re.findall(rf"({NUM})\s*(?:%|процент\w*)", t)
    years = re.findall(rf"({NUM})\s*(?:лет|год\w*)\b", t)
    months = re.findall(rf"({NUM})\s*месяц\w*", t)
    if len(set(years)) == 1 and not months:
        for extra in years[1:]:
            tokens.remove(extra)
        years = years[:1]
    if len(tokens) != 3 or len(rates) != 1 or len(years) + len(months) != 1:
        return None
    rest = list(tokens)
    rest.remove(rates[0])
    period = years[0] if years else months[0]
    rest.remove(period)
    principal, rate, span = to_fraction(rest[0]), to_fraction(rates[0]), to_fraction(period)
    t_years = span if years else span / 12
    freq = [n for rx, n in _PERIODS if rx.search(t)]
    if len(set(freq)) > 1:
        return None
    n = freq[0] if freq else 1
    if compound:
        periods = t_years * n
        if periods.denominator != 1 or not 0 < periods <= 600:
            return None
        amount = principal * (1 + rate / 100 / n) ** int(periods)
        label = {1: "ежегодная", 2: "раз в полгода", 4: "ежеквартальная", 12: "ежемесячная"}[n]
        return MathHint("compound", f"сложные проценты, капитализация {label}: {fmt(principal)} * (1 + {fmt(rate)}/100"
                                    f"{'/' + str(n) if n != 1 else ''})^{int(periods)} = {money(amount)} "
                                    f"(округлено до копеек); начисленные проценты = {money(amount - principal)}.")
    amount = principal * (1 + rate / 100 * t_years)
    return MathHint("compound", f"простые проценты: {fmt(principal)} * (1 + {fmt(rate)}/100 * {fmt(t_years)}) = "
                                f"{money(amount)}; начисленные проценты = {money(amount - principal)}.")


# -- unit conversion --------------------------------------------------------------------------------------
def _u(*pairs):
    return pairs


_F = Fraction
#: (dimension, key, fullmatch regex of one token, factor to the dimension's base unit, display name)
_UNITS: list[tuple[str, str, str, Fraction, str]] = [
    ("length", "мм", r"миллиметр\w*|мм", _F(1, 1000), "мм"),
    ("length", "см", r"сантиметр\w*|см", _F(1, 100), "см"),
    ("length", "дм", r"дециметр\w*|дм", _F(1, 10), "дм"),
    ("length", "м", r"метр\w*|м", _F(1), "м"),
    ("length", "км", r"километр\w*|км", _F(1000), "км"),
    ("length", "миля", r"мил[яьюи]|милях|милям|миль|милями|милей", _F(1609344, 1000), "mi"),
    ("length", "фут", r"фут\w*", _F(3048, 10000), "ft"),
    ("length", "дюйм", r"дюйм\w*", _F(254, 10000), "in"),
    ("length", "ярд", r"ярд\w*", _F(9144, 10000), "yd"),
    ("mass", "мг", r"миллиграмм\w*|мг", _F(1, 1000), "мг"),
    ("mass", "г", r"грамм\w*|г", _F(1), "г"),
    ("mass", "кг", r"килограмм\w*|кило|кг", _F(1000), "кг"),
    ("mass", "ц", r"центнер\w*", _F(100000), "ц"),
    ("mass", "т", r"тонн\w*|т", _F(1000000), "т"),
    ("mass", "фунт", r"фунт\w*", _F(45359237, 100000), "lb"),
    ("mass", "унция", r"унци\w*", _F(28349523125, 1000000000), "oz"),
    ("volume", "мл", r"миллилитр\w*|мл", _F(1, 1000), "мл"),
    ("volume", "л", r"литр\w*|л", _F(1), "л"),
    ("volume", "м3", r"куб\.?метр\w*|м3|м³|кубометр\w*", _F(1000), "м³"),
    ("time", "сек", r"секунд\w*|сек|с", _F(1), "с"),
    ("time", "мин", r"минут\w*|мин", _F(60), "мин"),
    ("time", "ч", r"час\w*|ч", _F(3600), "ч"),
    ("time", "сут", r"суток|сутки|сутк\w*|дн\w*|день|дня|дней", _F(86400), "сут"),
    ("time", "нед", r"недел\w*", _F(604800), "нед"),
    ("speed", "км/ч", r"§kmh", _F(1000, 3600), "км/ч"),
    ("speed", "м/с", r"§ms", _F(1), "м/с"),
    ("speed", "mph", r"§mph", _F(1609344, 1000 * 3600), "mph"),
    ("area", "м2", r"§sqm|квадратн\w*\s*метр\w*", _F(1), "м²"),
    ("area", "км2", r"§sqkm", _F(1000000), "км²"),
    ("area", "га", r"гектар\w*|га", _F(10000), "га"),
    ("area", "сотка", r"сот(?:ка|ки|ок|ке|ках|ку)", _F(100), "сот."),
    ("temp", "c", r"§c", _F(1), "°C"),
    ("temp", "f", r"§f", _F(1), "°F"),
    ("temp", "k", r"§k", _F(1), "K"),
]
_UNIT_RX = [(dim, key, re.compile(rx), factor, shown) for dim, key, rx, factor, shown in _UNITS]
_UNIT_SUBS = [
    (r"километр\w*\s+в\s+час\w*|км\s*/\s*ч|км\s*в\s*час\w*", "§kmh"),
    (r"метр\w*\s+в\s+секунд\w*|м\s*/\s*с\b", "§ms"),
    (r"миль\w*\s+в\s+час\w*|миль\s*/\s*ч|mph", "§mph"),
    (r"квадратн\w*\s+километр\w*|км²|км2|кв\.?\s*км", "§sqkm"),
    (r"квадратн\w*\s+метр\w*|м²|м2|кв\.?\s*м\b", "§sqm"),
    (r"°\s*с\b|градус\w*\s+(?:по\s+)?цельси\w*|цельси\w*|°c\b", "§c"),
    (r"°\s*f\b|градус\w*\s+(?:по\s+)?фаренгейт\w*|фаренгейт\w*", "§f"),
    (r"кельвин\w*|°\s*k\b", "§k"),
]
_TOKEN = r"[^\s,;?!]+"


def _unit_of(token: str):
    token = token.strip(".")
    for dim, key, rx, factor, shown in _UNIT_RX:
        if rx.fullmatch(token):
            return dim, key, factor, shown
    return None


def _extract_conversion(t: str):
    """(value, source unit, target unit) for the shapes we know, else None. Exactly one number in the message."""
    nums = _numbers(t)
    strategies = (
        # "Переведи 5 миль в километры", "5 км в мили", "5 км = ? м"
        (rf"(-?{NUM})\s*({_TOKEN})\s+(?:в|во|=|->|→)\s*({_TOKEN})", ("v", "a", "b"), 1),
        # "Сколько метров в 5 километрах", "Сколько минут в часе"
        (rf"скольк\w*\s+({_TOKEN})\s+(?:в|во)\s+(?:(-?{NUM})\s*)?({_TOKEN})", ("b", "v", "a"), None),
        # "Сколько градусов по Фаренгейту - это 37 градусов по Цельсию?"
        (rf"скольк\w*\s+({_TOKEN})\s+(?:-\s*)?(?:это|будет|составляет|равно)\s+(-?{NUM})\s*({_TOKEN})", ("b", "v", "a"), 1),
        # "5 км - это сколько миль?"
        (rf"(-?{NUM})\s*({_TOKEN})\s*(?:-\s*)?(?:это\s+)?скольк\w*\s+({_TOKEN})", ("v", "a", "b"), 1),
    )
    for pattern, order, want in strategies:
        m = re.search(pattern, t)
        if not m:
            continue
        got = dict(zip(order, m.groups()))
        src, dst = _unit_of(got["a"]), _unit_of(got["b"])
        if not (src and dst):
            continue
        if want is None:
            if len(nums) != (1 if got["v"] else 0):
                continue
            return (to_fraction(got["v"]) if got["v"] else Fraction(1)), src, dst
        if len(nums) == want:
            return to_fraction(got["v"]), src, dst
    # "В бассейне 2,5 кубометра воды. Сколько это литров?": the one number + unit stands before the question
    m = re.search(rf"скольк\w*\s+это\s+(?:будет\s+)?(?:в\s+)?({_TOKEN})", t)
    held = list(re.finditer(rf"(-?{NUM})\s*({_TOKEN})", t))
    if m and len(nums) == 1 and len(held) == 1:
        src, dst = _unit_of(held[0].group(2)), _unit_of(m.group(1))
        if src and dst:
            return to_fraction(held[0].group(1)), src, dst
    return None


def _handle_units(t: str) -> MathHint | None:
    if "стерлинг" in t:
        return None
    for old, new in _UNIT_SUBS:
        t = re.sub(old, new, t)
    t = re.sub(r"(\d)(?=[a-zа-я§°])", r"\1 ", t)                # 5км -> 5 км
    found = _extract_conversion(t)
    if found is None:
        return None
    value, src_unit, dst_unit = found
    (dim_a, key_a, f_a, shown_a), (dim_b, key_b, f_b, shown_b) = src_unit, dst_unit
    if dim_a != dim_b or key_a == key_b:
        return None
    if dim_a == "temp":
        c = {"c": value, "f": (value - 32) * Fraction(5, 9), "k": value - Fraction(27315, 100)}[key_a]
        result = {"c": c, "f": c * Fraction(9, 5) + 32, "k": c + Fraction(27315, 100)}[key_b]
        formula = {("c", "f"): "°F = °C * 9/5 + 32", ("f", "c"): "°C = (°F - 32) * 5/9",
                   ("c", "k"): "K = °C + 273.15", ("k", "c"): "°C = K - 273.15",
                   ("f", "k"): "K = (°F - 32) * 5/9 + 273.15", ("k", "f"): "°F = (K - 273.15) * 9/5 + 32"}[(key_a, key_b)]
        return MathHint("units", f"{fmt(value)} {shown_a} = {fmt(result)} {shown_b} ({formula}).", result)
    result = value * f_a / f_b
    one = f_a / f_b
    factor = (f" (1 {shown_a} = {fmt(one)} {shown_b})"
              if value != 1 and _terminating((f_a / f_b).denominator) else "")
    return MathHint("units", f"{fmt(value)} {shown_a} = {fmt(result)} {shown_b}{factor}.", result)


# -- average ----------------------------------------------------------------------------------------------
def _handle_average(t: str) -> MathHint | None:
    if not re.search(r"средн\w+\s+арифметическ\w+|средн(?:ее|его)\s+(?:значени\w+|чисел|от)", t):
        return None
    if re.search(r"взвешен|геометрическ|гармоническ|скорост|медиан|квадратич", t):
        return None
    if re.search(r"\d\s*[+*/^]|\d%|%|процент|если|из\s+них|равн[оаы]\s+\d", t):
        return None
    body = None
    for cand in re.finditer(r"\d[\d\s.,;и]*", t):
        body = cand.group(0)
    if body is None or len(_numbers(t)) < 2:
        return None
    if re.search(r"\d,\d,\d|\d,\d{3}\b", body) and not re.search(r",\s", body):
        return None                                               # "3,4,5": decimal comma or list? ambiguous
    parts = [p for p in re.split(r"\s*(?:;|,\s+|\bи\b|\s)\s*", body.strip(" .;,и")) if p]
    values = []
    for part in parts:
        if not re.fullmatch(r"\d+(?:[.,]\d+)?", part):
            return None
        values.append(to_fraction(part))
    if not 2 <= len(values) <= MAX_LIST or len(values) != len(_numbers(t)):
        return None
    total = sum(values, Fraction(0))
    return MathHint("average", f"среднее арифметическое: ({' + '.join(fmt(v) for v in values)}) / {len(values)} = "
                               f"{fmt(total)} / {len(values)} = {fmt(total / len(values))}.", total / len(values))


# -- equations ---------------------------------------------------------------------------------------------
def _sqrt_exact(value: Fraction) -> Fraction | None:
    if value < 0:
        return None
    n, d = math.isqrt(value.numerator), math.isqrt(value.denominator)
    return Fraction(n, d) if n * n == value.numerator and d * d == value.denominator else None


_EQ_CLASS = r"0-9x+\-*/^().,\s"


def _handle_equation(t: str) -> MathHint | None:
    if t.count("=") != 1:
        return None
    s = t.replace("²", "^2").replace("³", "^3")
    s = re.sub(r"(?<![а-я])х(?![а-я])", "x", s)                     # a lone cyrillic х is the unknown
    pos = s.index("=")
    left_m = re.search(rf"[{_EQ_CLASS}]*$", s[:pos])
    right_m = re.match(rf"[{_EQ_CLASS}]*", s[pos + 1:])
    left, right = left_m.group(0), right_m.group(0)
    if "x" not in left + right:
        return None
    start = left_m.start() + (len(left) - len(left.lstrip()))
    end = pos + 1 + len(right.rstrip())
    if (start > 0 and not s[start - 1] in " :,.;(") or (end < len(s) and not s[end] in " .,;:?!)"):
        return None                                                 # "max = 5", "x²=": x is part of a word
    left, right = left.strip(" ,"), right.strip(" ,.")
    if not left or not right:
        return None
    if not re.search(r"[-+*/^(]|\dx|x\d|x\s*x", left + " | " + right):
        return None                                                 # a bare "x = 5" is an assignment, not a task
    left, right = (re.sub(r"(\d),(\d)", r"\1.\2", z) for z in (left, right))
    if "," in left or "," in right:
        return None
    lv, ltree, lsrc = parse_poly(left, allow_x=True)
    rv, rtree, rsrc = parse_poly(right, allow_x=True)
    c0, c1, c2 = (a - b for a, b in zip(lv, rv))
    shown = f"{_render(ltree, lsrc)} = {_render(rtree, rsrc)}"
    if c2 == 0:
        if c1 == 0:
            return None
        root = -c0 / c1
        return MathHint("equation", f"уравнение {shown}: x = {fmt(root)}.", root)
    disc = c1 * c1 - 4 * c2 * c0
    if disc < 0:
        return MathHint("equation", f"уравнение {shown}: дискриминант {fmt(disc)} < 0, действительных корней нет.")
    sq = _sqrt_exact(disc)
    if sq is not None:
        r1, r2 = (-c1 - sq) / (2 * c2), (-c1 + sq) / (2 * c2)
        roots = f"x = {fmt(r1)}" if r1 == r2 else f"x1 = {fmt(r1)}, x2 = {fmt(r2)}"
        return MathHint("equation", f"уравнение {shown}: дискриминант {fmt(disc)}; {roots}.")
    root = _isqrt_decimal(disc)
    r1 = (-c1 - root) / (2 * c2)
    r2 = (-c1 + root) / (2 * c2)
    return MathHint("equation", f"уравнение {shown}: дискриминант {fmt(disc)} (не квадрат); "
                                f"x1 ≈ {_decimal_places(r1, 6)}, x2 ≈ {_decimal_places(r2, 6)}.")


def _isqrt_decimal(value: Fraction, places: int = 12) -> Fraction:
    scaled = value * (10 ** (2 * places))
    return Fraction(math.isqrt(scaled.numerator // scaled.denominator), 10 ** places)


# -- combinatorics -----------------------------------------------------------------------------------------
_ORDERED = re.compile(r"размещени\w*|по\s+порядку|упорядоч\w*|разные\s+должност\w*|различные\s+должност\w*|"
                      r"на\s+разные\s+места|призов\w*\s+мест\w*|друг\s+за\s+другом|первое.*второе")
_REPEAT_OR_CIRCLE = re.compile(r"по\s+кругу|круглым|хоровод|с\s+повторени\w*|одинаков\w*|повторя\w*|не\s+менее|"
                               r"не\s+более|хотя\s+бы|только|ровно\s+\d+\s+(?:из|мальчик|девочек)")


def _count_ints(t: str) -> int:
    return len(re.findall(r"\d+", t))


def _comb(n: int, k: int) -> int:
    return math.comb(n, k)


def _handle_combinatorics(t: str) -> MathHint | None:
    m = re.search(rf"(?:\bc|\bс)\s*\(\s*(\d+)\s*[,;]\s*(\d+)\s*\)", t)
    if m and _count_ints(t) == 2 and re.search(r"сочетани|биномиал|c\(", t):
        n, k = int(m.group(1)), int(m.group(2))
        return _comb_hint(n, k)
    m = re.search(r"сочетани\w*\s+из\s+(\d+)\s+по\s+(\d+)", t)
    if m and _count_ints(t) == 2:
        return _comb_hint(int(m.group(1)), int(m.group(2)))
    m = re.search(r"размещени\w*\s+из\s+(\d+)\s+по\s+(\d+)", t)
    if m and _count_ints(t) == 2:
        return _arr_hint(int(m.group(1)), int(m.group(2)))
    m = re.search(r"(?:перестанов(?:к\w*|ок)|факториал\w*)\s+(?:из\s+|числа\s+)?(\d+)", t)
    if m and _count_ints(t) == 1:
        return _fact_hint(int(m.group(1)))
    if not re.search(r"способ|вариант|сколько", t) or _REPEAT_OR_CIRCLE.search(t):
        return None
    nums = re.findall(r"\d+", t)
    m = re.search(r"(?:выбра\w+|отобра\w+|назначи\w+|собра\w+)\s+(\d+)\s+(?:\w+\s+){0,4}?из\s+(\d+)", t)
    if m and len(nums) == 2:
        k, n = int(m.group(1)), int(m.group(2))
        return _arr_hint(n, k) if _ORDERED.search(t) else _comb_hint(n, k)
    m = re.search(r"(?:расставит\w+|рассадит\w+|выстроит\w+|разложит\w+|расположит\w+)\s+(\d+)\s+(?:\w+\s+){0,3}?"
                  r"(?:в\s+(?:один\s+)?ряд|в\s+очередь|в\s+линию|на\s+полке|на\s+скамейк\w+|на\s+\d+\s+мест\w*|в\s+шеренгу)", t)
    if m and len(nums) == 1:
        return _fact_hint(int(m.group(1)))
    return None


def _comb_hint(n: int, k: int) -> MathHint | None:
    if not 0 <= k <= n <= 1000:
        return None
    value = _comb(n, k)
    if len(str(value)) > MAX_RESULT_DIGITS:
        return None
    return MathHint("combinatorics", f"число сочетаний C({n}, {k}) = {n}! / ({k}! * {n - k}!) = {value}.")


def _arr_hint(n: int, k: int) -> MathHint | None:
    if not 0 <= k <= n <= 1000:
        return None
    value = math.perm(n, k)
    if len(str(value)) > MAX_RESULT_DIGITS:
        return None
    return MathHint("combinatorics", f"число размещений A({n}, {k}) = {n}! / {n - k}! = {value}.")


def _fact_hint(n: int) -> MathHint | None:
    if not 0 <= n <= MAX_FACT:
        return None
    value = math.factorial(n)
    if len(str(value)) > MAX_RESULT_DIGITS:
        return None
    return MathHint("combinatorics", f"{n}! = {value}.")


# -- dates and clock times -------------------------------------------------------------------------------------
_MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
           "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
_MONTH_RX = r"(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]\w*|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_DATE_RES = [
    re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?![\d.]*\d)"),
    re.compile(rf"(?<!\d)(\d{{1,2}})\s+({_MONTH_RX})\s+(\d{{4}})(?:\s*г(?:ода|\.|\b))?"),
    re.compile(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])"),
]


def _month_number(word: str) -> int | None:
    for stem, number in _MONTHS.items():
        if word.startswith(stem):
            return number
    return None


def _find_dates(t: str) -> tuple[list[dt.date], list[tuple[int, int]]]:
    found: list[tuple[int, int, dt.date]] = []
    taken: list[tuple[int, int]] = []
    for index, rx in enumerate(_DATE_RES):
        for m in rx.finditer(t):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            try:
                if index == 0:
                    d = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                elif index == 1:
                    month = _month_number(m.group(2))
                    d = dt.date(int(m.group(3)), month, int(m.group(1)))
                else:
                    d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except (ValueError, TypeError):
                raise MathReject("not a real date") from None
            found.append((m.start(), m.end(), d))
            taken.append((m.start(), m.end()))
    found.sort()
    return [d for _, _, d in found], [(a, b) for a, b, _ in found]


def _ddmmyyyy(d: dt.date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year:04d}"


_PLUS_CUE = re.compile(r"\bчерез\b|\bспустя\b|\bпосле\b|прибав\w*|\bплюс\b|вперед")
_MINUS_CUE = re.compile(r"\bназад\b|\bраньше\b|\bранее\b|\bминус\b|вычти\w*|за\s+\d+\s+\w+\s+до\b")


def _handle_date(t: str) -> MathHint | None:
    dates, spans = _find_dates(t)
    if not dates:
        return None
    rest = _strip_spans(t, spans)
    rest_nums = _numbers(rest)
    if len(dates) == 2 and not rest_nums and re.search(r"скольк\w*\s+(?:дн\w+|суток|недел\w*)|разниц\w+|между", t):
        days = abs((dates[1] - dates[0]).days)
        if re.search(r"включительно", t):
            days += 1
        weeks, rem = divmod(days, 7)
        return MathHint("date", f"между {_ddmmyyyy(min(dates))} и {_ddmmyyyy(max(dates))}: {days} дн. "
                                f"({weeks} нед. {rem} дн.)" + (" включительно." if re.search(r"включительно", t) else
                                                              ", считая разницу дат без начального дня."))
    if len(dates) != 1:
        return None
    d = dates[0]
    m = re.fullmatch(r".*?(\d+)\s*(дн\w*|день|суток|сутки|недел\w*).*", rest.strip())
    if len(rest_nums) == 1 and m:
        n = int(m.group(1))
        days = n * (7 if m.group(2).startswith("недел") else 1)
        if days > MAX_DAYS:
            return None
        plus, minus = bool(_PLUS_CUE.search(rest)), bool(_MINUS_CUE.search(rest))
        if plus == minus:
            return None
        try:
            result = d + dt.timedelta(days=days if plus else -days)
        except OverflowError:
            return None
        sign = "+" if plus else "-"
        return MathHint("date", f"{_ddmmyyyy(d)} {sign} {days} дн. = {_ddmmyyyy(result)} "
                                f"({_WEEKDAYS[result.weekday()]}).")
    if not rest_nums and re.search(r"день\s+недели|каким\s+днем|какой\s+день", t):
        return MathHint("date", f"{_ddmmyyyy(d)} - {_WEEKDAYS[d.weekday()]}.")
    return None


_TIME_RE = re.compile(r"(?<![\d:.])(\d{1,2}):(\d{2})(?![\d:])")


def _handle_time(t: str) -> MathHint | None:
    times = _TIME_RE.findall(t)
    if not times or re.search(r"\d{1,2}\.\d{1,2}\.\d{2,4}", t):
        return None
    clock = []
    for h, m in times:
        if int(h) > 23 or int(m) > 59:
            return None
        clock.append(int(h) * 60 + int(m))
    rest = _TIME_RE.sub(" ", t)
    nums = _numbers(rest)
    if len(clock) == 2 and not nums and re.search(r"скольк\w*\s+(?:времени|минут|часов)|разниц\w+|продолжительн\w+|"
                                                  r"длится|между", t):
        if clock[1] < clock[0]:
            return None
        diff = clock[1] - clock[0]
        return MathHint("time", f"от {clock[0] // 60:02d}:{clock[0] % 60:02d} до {clock[1] // 60:02d}:{clock[1] % 60:02d} "
                                f"проходит {diff} мин = {diff // 60} ч {diff % 60} мин.")
    if len(clock) != 1:
        return None
    hours = re.findall(r"(\d+)\s*(?:час\w*|ч)\b", rest)
    minutes = re.findall(r"(\d+)\s*(?:мин\w*)", rest)
    if len(hours) > 1 or len(minutes) > 1 or len(hours) + len(minutes) == 0 or len(nums) != len(hours) + len(minutes):
        return None
    delta = (int(hours[0]) * 60 if hours else 0) + (int(minutes[0]) if minutes else 0)
    plus, minus = bool(_PLUS_CUE.search(rest)), bool(_MINUS_CUE.search(rest))
    if plus == minus or delta > 100 * 24 * 60:
        return None
    total = clock[0] + (delta if plus else -delta)
    day_shift, mins = divmod(total, 24 * 60)
    note = "" if day_shift == 0 else (f" (на {day_shift} сут. позже)" if day_shift > 0 else f" (на {-day_shift} сут. раньше)")
    sign = "+" if plus else "-"
    return MathHint("time", f"{clock[0] // 60:02d}:{clock[0] % 60:02d} {sign} {delta // 60} ч {delta % 60} мин = "
                            f"{mins // 60:02d}:{mins % 60:02d}{note}.")


# -- entry points ------------------------------------------------------------------------------------------------
_SPECIFIC = (_handle_percent, _handle_interest, _handle_units, _handle_average, _handle_equation,
             _handle_combinatorics, _handle_date, _handle_time)


def find_math_hint(text: str) -> MathHint | None:
    """The one exact answer for this message, or None (not math, not unambiguous, or outside the safe limits)."""
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        return None
    t, places = _split_rounding(_norm(text))
    if t.startswith("/"):
        return None
    hints: list[MathHint] = []
    for handler in _SPECIFIC:
        try:
            hint = handler(t)
        except (MathReject, ArithmeticError, ValueError, RecursionError, MemoryError, OverflowError, IndexError):
            hint = None
        if hint is not None:
            hints.append(hint)
    if len(hints) > 1:
        return None
    if not hints:
        try:
            found = _handle_arithmetic(t)
        except (MathReject, ArithmeticError, ValueError, RecursionError, MemoryError, OverflowError, IndexError):
            return None
        if found is None:
            return None
        hints = [found]
    hint = hints[0]
    if places is not None and hint.value is not None and hint.value.denominator != 1:
        hint = replace(hint, summary=hint.summary + f" Округлено до {places} знаков (half-up): "
                                                    f"{_decimal_places(hint.value, places)}.")
    return hint


_ROUND = re.compile(r"(?:округл\w*|с\s+точностью|ответ\w*\s+с\s+точностью)?\s*до\s+(\d{1,2})\s*(?:-?(?:х|го))?\s*"
                    r"(?:знак\w*|цифр\w*)(?:\s+после\s+запят\w*)?")


def _split_rounding(t: str) -> tuple[str, int | None]:
    """("...", n): the "округли до n знаков" clause is not a number of the problem; it is a rounding request."""
    m = _ROUND.search(t)
    if not m:
        return t, None
    digits = int(m.group(1))
    return (t[:m.start()] + " " + t[m.end():]), (digits if 0 <= digits <= 10 else None)


def hint_text(text: str) -> str:
    """The system-message text for ``text``, or "" (never raises)."""
    try:
        hint = find_math_hint(text)
    except Exception:  # noqa: BLE001 - a hint can never break a reply
        return ""
    return hint.text if hint is not None else ""
