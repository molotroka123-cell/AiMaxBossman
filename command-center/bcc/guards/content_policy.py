"""G5 — content-policy gate for game assets / text (names, prompts, strings of a diff).

Incidents 03.11.2026: a politician-caricature pilot; VELO (nicotine pouch / snus) branding in a game asset.
Roblox (all-ages) and the owner's rules forbid: real people / politicians and their likeness, real brands — above
all tobacco/nicotine/vape, crypto, alcohol, gambling.

Two layers:
  1. deterministic denylist (word-boundary regexes, RU + EN) over the ADDED lines of a diff or a text — a hit is a
     FAIL on its own;
  2. a reviewer model (injected async callable, local by default — game text never needs the cloud) that sees the
     same lines and answers PASS or FLAG:<reason>; FLAG is a FAIL («needs owner review»). Reviewer unavailable ->
     its layer is NOT_RUN and said so; the denylist verdict stands.
Pure: the reviewer is a parameter; no network here."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

# Word-start boundaries: RU stems match inflections (путин -> путина), EN uses whole words.
_B = r"(?<![A-Za-zА-Яа-яЁё0-9_])"
_E = r"(?![A-Za-z0-9_])"

DENYLIST: dict[str, list[str]] = {
    "politician": [
        r"putin", r"путин", r"trump", r"трамп(?!лин)", r"biden", r"байден", r"zelensk\w*", r"зеленск",
        r"lukashenk\w*", r"лукашенк", r"xi jinping", r"си цзиньпин", r"kim jong[- ]un", r"ким чен ын",
        r"obama", r"обам", r"macron", r"макрон", r"navaln\w*", r"навальн", r"medvedev", r"медведев",
        r"erdogan", r"эрдоган", r"netanyahu", r"нетаньяху", r"orb[aá]n", r"орбан", r"fico", r"фицо",
        r"babi[sš]", r"бабиш", r"kamala harris", r"камал\w* харрис", r"vance", r"musk", r"маск\b",
        r"politician",   # RU «политик» is NOT listed: it is also «политика» = policy (live false hit 03.11)
    ],
    "real_person_likeness": [
        r"caricature", r"карикатур", r"likeness of", r"look-?alike", r"двойник", r"deepfake", r"дипфейк",
        r"face of (?:a )?real", r"real person", r"реальн\w* человек", r"celebrity", r"знаменитост",
    ],
    "tobacco_nicotine": [
        r"velo", r"zyn", r"snus", r"снюс", r"nicotine", r"никотин", r"tobacco", r"табак", r"cigarett\w*",
        r"сигарет", r"cigar", r"сигар", r"vape", r"vaping", r"вейп", r"iqos", r"juul", r"elf ?bar",
        r"marlboro", r"мальборо", r"hookah", r"кальян", r"nicotine pouch\w*", r"pod mod",
    ],
    "real_brand": [
        r"coca[- ]?cola", r"pepsi", r"red ?bull", r"monster energy", r"mcdonald'?s", r"kfc", r"burger king",
        r"nike", r"adidas", r"gucci", r"louis vuitton", r"apple inc", r"iphone", r"samsung",
        r"tesla", r"starbucks", r"lego", r"disney", r"marvel", r"pok[eé]mon", r"nintendo", r"playstation", r"xbox",
        r"fortnite", r"minecraft",
    ],
    "crypto": [
        r"crypto\w*", r"крипт", r"bitcoin", r"биткоин", r"btc", r"ethereum", r"эфириум", r"\beth\b", r"nft",
        r"blockchain", r"блокчейн", r"solana", r"dogecoin", r"memecoin", r"мемкоин", r"usdt",
        r"token sale", r"web3", r"pump\.?fun",
    ],
    "alcohol": [
        r"alcohol\w*", r"алкогол", r"beer", r"пиво", r"пива", r"vodka", r"водк", r"whiske?y", r"виски",
        r"wine", r"вино", r"champagne", r"шампанск", r"cocktail", r"коктейл", r"\brum\b", r"\bgin\b",
        r"tequila", r"текил", r"бухл", r"drunk", r"пьян", r"booze", r"liquor", r"ликёр", r"ликер",
    ],
    "gambling": [
        r"casino", r"казино", r"gambl\w*", r"азарт", r"\bbet(?:s|ting)?\b", r"ставк\w* на", r"сделать ставк", r"slot machine",
        r"игров\w* автомат", r"roulette", r"рулетк", r"jackpot", r"джекпот", r"poker", r"покер", r"blackjack",
        r"блэкджек", r"lottery", r"лотере", r"wager", r"bookmaker", r"букмекер", r"loot ?box\w*", r"лутбокс",
    ],
}
#: owner-approved exceptions (lower-case substrings of the matched line) — e.g. a word that is a game term here
DEFAULT_ALLOW: tuple[str, ...] = ()

_COMPILED = {cat: re.compile("|".join(f"{_B}(?:{p}){_E if p[-1].isascii() and p[-1].isalnum() else ''}"
                                      for p in pats), re.I)
             for cat, pats in DENYLIST.items()}


@dataclass
class Hit:
    category: str
    term: str
    file: str
    line: int
    text: str
    in_comment: bool = False
    negated: bool = False       # «no crypto», `crypto_logos: false` — mentioned to exclude it


@dataclass
class PolicyResult:
    hits: list[Hit] = field(default_factory=list)
    reviewer: str = "NOT_RUN"           # PASS | FLAG | NOT_RUN
    reviewer_reason: str = ""
    lines_checked: int = 0

    @property
    def blocking(self) -> list[Hit]:
        return [h for h in self.hits if not h.in_comment and not h.negated]

    @property
    def ok(self) -> bool:
        return not self.blocking and self.reviewer != "FLAG"

    def as_dict(self) -> dict:
        return {"ok": self.ok, "lines_checked": self.lines_checked, "reviewer": self.reviewer,
                "reviewer_reason": self.reviewer_reason, "hits": [h.__dict__ for h in self.hits]}

    def summary_ru(self) -> str:
        warn = len(self.hits) - len(self.blocking)
        head = (f"G5 {'PASS' if self.ok else 'FAIL'}: строк {self.lines_checked}, совпадений в коде/строках "
                f"{len(self.blocking)}, в комментариях/с отрицанием {warn} (предупреждение), "
                f"ревьюер-модель {self.reviewer}"
                + (f" ({self.reviewer_reason[:160]})" if self.reviewer_reason else ""))
        return "\n".join([head] + [f"  [{h.category}] «{h.term}» — {h.file}:{h.line}"
                                   f"{' (комментарий)' if h.in_comment else ' (отрицание)' if h.negated else ''}: "
                                   f"{h.text[:100]}"
                                   for h in sorted(self.hits, key=lambda h: (h.in_comment or h.negated,
                                                                             not h.file.startswith("src/")))[:30]])


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """(file, new line number, text) of every ADDED line of a unified diff."""
    out: list[tuple[str, int, str]] = []
    file, ln = "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            file = raw[4:].strip()
            file = file[2:] if file.startswith("b/") else file
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            ln = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            out.append((file, ln, raw[1:]))
            ln += 1
        elif not raw.startswith("-"):
            ln += 1
    return out


_COMMENT_MARK = {".luau": "--", ".lua": "--", ".py": "#", ".sh": "#", ".toml": "#", ".yml": "#", ".yaml": "#"}


def comment_start(file: str, text: str) -> int:
    """Index where a line comment starts in a source line (outside quotes), or len(text)."""
    mark = next((m for suf, m in _COMMENT_MARK.items() if str(file).lower().endswith(suf)), None)
    if not mark:
        return len(text)
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif text.startswith(mark, i):
            return i
        i += 1
    return len(text)


def scan_lines(lines: list[tuple[str, int, str]], *, allow: tuple[str, ...] = DEFAULT_ALLOW) -> list[Hit]:
    """Every denylist hit; a hit inside a source comment is marked `in_comment` (not shipped to players)."""
    hits: list[Hit] = []
    for file, ln, text in lines:
        low = text.lower()
        if allow and any(a in low for a in allow):
            continue
        # identifiers too: `VIPChampagneBottle` / `velo_can` -> words (asset and Part names are where brands hide)
        orig = text
        text = re.sub(r"(?<=[a-zа-яё0-9])(?=[A-ZА-ЯЁ])|(?<=[A-ZА-ЯЁ])(?=[A-ZА-ЯЁ][a-zа-яё])|_", " ", text)
        cut = comment_start(file, text)
        for cat, rx in _COMPILED.items():
            ms = list(rx.finditer(text))
            if not ms:
                continue
            code = [m for m in ms if m.start() < cut]
            live = [m for m in code if not negated(text, m.start(), m.end())]
            m = (live or code or ms)[0]
            hits.append(Hit(cat, m.group(0), file, ln, orig.strip(), in_comment=not code,
                            negated=bool(code) and not live))
    return hits


_NEG_BEFORE = re.compile(r"(?:(?:\bno|\bnon|\bnever|\bwithout|\bfree of|\bavoid\w*|\bforbid\w*|\bban\w*|"
                         r"\bбез|\bникаких|\bзапрещ\w*|\bизбега\w*)\b[^.;,()\n]{0,24}"
                         r"|(?:\bnot|\bне|\bнет)\s+)$", re.I)   # common «not/не» only right before the term
#: identifiers were split into words above: `crypto_logos_present": false` reads `crypto logos present": false`
_NEG_AFTER = re.compile(r"^[\w ]{0,30}[\"']?\s*[:=]\s*false\b|^\w*\s*[-–]?\s*free\b", re.I)


def negated(text: str, start: int, end: int) -> bool:
    """The term is mentioned to EXCLUDE it: «no currency/crypto symbols», «без крипты», `crypto_logos: false`,
    `nicotine-free`. Live run 03.11: asset manifests saying «no crypto» were the bulk of false hits."""
    return bool(_NEG_BEFORE.search(text[max(0, start - 40):start]) or _NEG_AFTER.match(text[end:end + 30]))


def scan_text(text: str, *, name: str = "<text>", allow: tuple[str, ...] = DEFAULT_ALLOW) -> list[Hit]:
    return scan_lines([(name, i + 1, t) for i, t in enumerate(text.splitlines())], allow=allow)


REVIEW_PROMPT = (
    "You review text and asset names added to an all-ages Roblox game. Forbidden: real people or politicians or their "
    "likeness/caricature; real brands (especially tobacco, nicotine, snus, vape); crypto; alcohol; gambling. "
    "Fictional names are fine. Answer with exactly one line: PASS, or FLAG: <category> — <the offending words>.\n\n"
    "Added lines:\n")


def parse_review(answer: str) -> tuple[str, str]:
    a = re.sub(r"<think>.*?</think>", "", str(answer or ""), flags=re.S).strip()
    first = next((ln.strip() for ln in a.splitlines() if ln.strip()), "")
    if re.match(r"^\W*FLAG\b", first, re.I):
        return "FLAG", first[:300]
    if re.match(r"^\W*PASS\b", first, re.I):
        return "PASS", ""
    return "NOT_RUN", f"unparseable reviewer answer: {first[:120]}"


async def check(lines: list[tuple[str, int, str]], *,
                reviewer: Callable[[str], Awaitable[str]] | None = None,
                allow: tuple[str, ...] = DEFAULT_ALLOW, max_review_chars: int = 12_000) -> PolicyResult:
    res = PolicyResult(lines_checked=len(lines), hits=scan_lines(lines, allow=allow))
    if reviewer is not None and lines:
        body = "\n".join(f"{f}:{n}: {t}" for f, n, t in lines if t.strip())[:max_review_chars]
        try:
            res.reviewer, res.reviewer_reason = parse_review(await reviewer(REVIEW_PROMPT + body))
        except Exception as exc:  # noqa: BLE001 - reviewer is advisory; its failure must not hide the denylist
            res.reviewer, res.reviewer_reason = "NOT_RUN", f"reviewer failed: {type(exc).__name__}: {exc}"[:200]
    return res
