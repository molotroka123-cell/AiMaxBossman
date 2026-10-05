"""Playwright adapter for the owner's LOCAL trainer «Bossman Poker AI».

Hard boundary (not configurable):
* the only allowed hosts are ``127.0.0.1``, ``localhost`` and ``::1``; any other
  URL is refused before a browser starts, and every browser request to a
  non-loopback host is aborted while the session runs;
* it drives only the trainer's own web page via the DOM (no screen capture, no
  OS-level mouse/keyboard, no other windows or applications);
* no timing randomisation or other "humanisation": actions are clicked as soon
  as the decision is made.

What it does: opens the trainer, selects/creates a local training profile,
opens a cash table against the trainer's simulated opponents, then for every
hero turn reads the state from the DOM, asks ``ProfileAgent`` for a decision,
clicks FOLD / CHECK / CALL / RAISE(+slider) / ALL IN, and logs each decision to
JSONL. ``dry_run`` reads and decides but never clicks a poker action.

DOM contract (read-only, derived from the trainer source, see README_RU.md):
* hero's turn  <=> a visible ``<button>`` with text ``FOLD``;
* hand over    <=> a visible ``<button>`` with text ``DEAL``;
* cards        = small ``div`` pairs "rank\\nsuit-symbol" (10 is shown as "10");
* hero area    = parent of the ``Stack: N`` label; opponent seats carry a
  ``$N`` stack badge; remaining face-up cards are the board;
* ``Pot: N`` and ``To call: N`` labels; raise panel = ``input[type=range]``
  ("raise to" total) + ``CONFIRM``.
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .agent import ProfileAgent
from .cards import cards_str, parse_card
from .engine import Action, LegalActions, Observation
from .profiles import Profile

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_URL = "http://127.0.0.1:8925/"
# Plain cash tables of the local trainer (big blind in chips).
TRAINER_TABLES: dict[str, tuple[str, int]] = {
    "NL2": ("NL2 (1c/2c)", 2),
    "NL5": ("NL5 (2c/5c)", 5),
    "NL10": ("NL10 (5c/10c)", 10),
    "NL25": ("NL25 (10c/25c)", 25),
}
PROFILE_NAME = "BotLab Trainee"


class NotLoopbackError(ValueError):
    pass


def assert_loopback_url(url: str) -> str:
    """Return the URL if it points at this machine's loopback, else raise."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        _ = parts.port  # raises ValueError on garbage ports
    except ValueError as exc:
        raise NotLoopbackError(f"refusing malformed URL {url!r}") from exc
    if parts.scheme not in ("http", "https"):
        raise NotLoopbackError(f"refusing non-http URL {url!r}")
    if parts.username or parts.password:
        raise NotLoopbackError(f"refusing URL with credentials {url!r}")
    if host not in LOOPBACK_HOSTS:
        raise NotLoopbackError(f"refusing non-loopback host {host!r}: botlab only drives the local trainer")
    return url


def is_loopback_request(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme in ("data", "blob", "about"):
        return True
    return (parts.hostname or "").lower() in LOOPBACK_HOSTS


READ_STATE_JS = r"""
() => {
  const CARD_RE = /^(10|[2-9JQKA])\n?([♠♥♦♣])$/;
  const SUIT = { '♠': 's', '♥': 'h', '♦': 'd', '♣': 'c' };
  const num = (s) => {
    if (!s) return null;
    const t = s.replace(/[\s  ,$]/g, '');
    const m = t.match(/^([0-9.]+)([KM]?)$/);
    if (!m) return null;
    const v = parseFloat(m[1]);
    return Math.round(m[2] === 'M' ? v * 1e6 : m[2] === 'K' ? v * 1e3 : v);
  };
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const divs = [...document.querySelectorAll('div')];
  const cardEls = divs.filter((d) => d.children.length === 2 && CARD_RE.test(d.innerText.trim())
      && !(d.style.transform || '').includes('rotate'));
  const cardOf = (d) => { const m = d.innerText.trim().match(CARD_RE); return (m[1] === '10' ? 'T' : m[1]) + SUIT[m[2]]; };
  const stackDiv = divs.find((d) => d.children.length === 0 && /^Stack:/.test(d.innerText.trim()));
  const heroArea = stackDiv ? stackDiv.parentElement : null;
  const hero = [], board = [], shown = [];
  for (const d of cardEls) {
    const c = cardOf(d);
    if (heroArea && heroArea.contains(d)) { if (!hero.includes(c)) hero.push(c); continue; }
    let a = d.parentElement, inSeat = false;
    for (let i = 0; i < 8 && a; i++, a = a.parentElement) {
      if ([...a.children].some((ch) => ch.children.length === 0 && /^\$/.test(ch.innerText.trim()))) { inSeat = true; break; }
    }
    if (inSeat) shown.push(c); else if (!board.includes(c)) board.push(c);
  }
  const leaf = (re) => [...document.querySelectorAll('div,span')].find((d) => re.test((d.innerText || '').trim()) && visible(d));
  const potEl = leaf(/^Pot:\s*\S+$/);
  const toCallEl = leaf(/^To call:\s*\S+$/);
  const btn = (name) => [...document.querySelectorAll('button')].find((b) => visible(b)
      && b.innerText.replace(/\s+/g, ' ').trim().toUpperCase().startsWith(name));
  const callBtn = btn('CALL');
  const allInBtn = btn('ALL IN');
  const slider = document.querySelector('input[type=range]');
  const opp = divs.filter((d) => d.children.length === 0 && /^\$[0-9\s  .,KM]+$/.test(d.innerText.trim())).map((d) => num(d.innerText));
  const logEl = leaf(/^HAND LOG/);
  const headerEl = leaf(/^Hand #\d+/);
  return {
    heroTurn: !!btn('FOLD'), handOver: !!btn('DEAL'),
    hero, board, shown,
    pot: potEl ? num(potEl.innerText.replace(/^Pot:/, '')) : 0,
    toCall: toCallEl ? num(toCallEl.innerText.replace(/^To call:/, '')) : (callBtn ? num(callBtn.innerText.replace(/CALL/i, '')) : 0),
    canCheck: !!btn('CHECK'), canCall: !!callBtn,
    canRaise: !!btn('RAISE') || !!btn('CONFIRM'),
    allInOnly: !!allInBtn && !btn('RAISE') && !btn('CONFIRM'),
    allInAmount: allInBtn ? num(allInBtn.innerText.replace(/ALL IN/i, '')) : null,
    heroStack: stackDiv ? num(stackDiv.innerText.replace(/^Stack:/, '')) : null,
    opponentStacks: opp,
    sliderMin: slider ? Number(slider.min) : null, sliderMax: slider ? Number(slider.max) : null,
    handLog: logEl ? logEl.innerText.split('\n').slice(1, 14) : [],
    header: headerEl ? headerEl.innerText.trim() : null,
  };
}
"""


@dataclass
class UiState:
    raw: dict[str, Any]

    @property
    def hero_turn(self) -> bool:
        return bool(self.raw.get("heroTurn"))

    @property
    def hand_over(self) -> bool:
        return bool(self.raw.get("handOver"))


def observation_from_dom(raw: dict[str, Any], big_blind: int, hand_id: int) -> Observation:
    """Map the trainer's DOM state onto the engine's ``Observation``.

    The trainer does not label the hero's own street bet. Preflop it is
    recovered exactly from the current hand's log ("raises to N", blinds);
    postflop the log has no street markers, so ``my_street_bet`` is taken as 0
    and ``current_bet`` as the amount to call (exact unless the hero bet and
    was raised on the same street).
    """
    hole = tuple(parse_card(c) for c in raw.get("hero", [])[:2])
    board = tuple(parse_card(c) for c in raw.get("board", [])[:5])
    if len(hole) != 2:
        raise ValueError(f"could not read hero cards from DOM: {raw.get('hero')}")
    street = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}.get(len(board))
    if street is None:
        raise ValueError(f"unexpected board size {len(board)}")
    stack = int(raw.get("heroStack") or 0)
    to_call = int(raw.get("toCall") or 0)
    to_call = min(to_call, stack)
    pot = int(raw.get("pot") or 0)
    n_opp = max(1, len(raw.get("opponentStacks") or []))
    current_hand: list[str] = []
    for line in raw.get("handLog", []):  # newest first; stop at this hand's start marker
        if "New Hand" in line:
            break
        current_hand.append(line)
    raise_tos = [int(m.group(1)) for line in current_hand if (m := re.search(r"raises to (\d+)\b", line))]
    if street == "preflop":
        raises = len(raise_tos)
        current_bet = max([big_blind, *raise_tos])
        my_street_bet = max(0, current_bet - to_call)
    else:
        raises = 0
        current_bet = to_call
        my_street_bet = 0
    max_to = stack + my_street_bet
    min_to = min(max_to, current_bet + big_blind) if current_bet else min(max_to, big_blind)
    raise_ok = bool(raw.get("canRaise") or raw.get("allInOnly")) and max_to > current_bet
    legal = LegalActions(
        to_call=to_call,
        can_check=bool(raw.get("canCheck")),
        can_call=not raw.get("canCheck") and to_call > 0,
        can_fold=not raw.get("canCheck"),
        can_bet=raise_ok and current_bet == 0,
        can_raise=raise_ok and current_bet > 0,
        min_to=max_to if raw.get("allInOnly") else min_to,
        max_to=max_to,
    )
    return Observation(
        hand_id=hand_id, seat=0, street=street, hole=hole, board=board, pot=pot, to_call=to_call,
        stack=stack, my_street_bet=my_street_bet, current_bet=current_bet, big_blind=big_blind,
        n_players=n_opp + 1, n_active_opponents=n_opp,
        raises_this_street=raises, position=0, legal=legal,
    )


def _hand_counter(raw: dict[str, Any]) -> int:
    header = raw.get("header") or ""
    m = re.match(r"Hand #(\d+)", header)
    return int(m.group(1)) if m else -1


class TrainerSession:
    def __init__(
        self,
        url: str,
        profile: Profile,
        out_dir: Path,
        table: str = "NL10",
        dry_run: bool = False,
        headless: bool = True,
        screenshots: int = 4,
        seed: int = 1,
        log: Callable[[str], None] = print,
    ) -> None:
        self.url = assert_loopback_url(url)
        if table not in TRAINER_TABLES:
            raise ValueError(f"table must be one of {', '.join(TRAINER_TABLES)}")
        self.table_label, self.big_blind = TRAINER_TABLES[table]
        self.agent = ProfileAgent(profile)
        self.out_dir = out_dir
        self.dry_run = dry_run
        self.headless = headless
        self.screenshots_left = screenshots
        self.rng = random.Random(seed)
        self.log = log
        self.decisions_path = out_dir / "decisions.jsonl"
        self.blocked_requests: list[str] = []

    # ---- browser helpers ------------------------------------------------
    def _guard(self, route: Any) -> None:
        url = route.request.url
        if is_loopback_request(url):
            route.continue_()
        else:
            self.blocked_requests.append(url)
            route.abort()

    def _read(self, page: Any) -> UiState:
        return UiState(page.evaluate(READ_STATE_JS))

    def _shot(self, page: Any, name: str) -> None:
        if self.screenshots_left > 0:
            self.screenshots_left -= 1
            page.screenshot(path=str(self.out_dir / f"{name}.png"))

    def _button(self, page: Any, name: str, exact: bool = True) -> Any:
        return page.get_by_role("button", name=name, exact=exact)

    def _open_table(self, page: Any) -> None:
        page.goto(self.url, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        # Profile picker: the trainer renders profiles as buttons "<icon><name>".
        if not self._button(page, PROFILE_NAME, exact=False).count() and page.locator("input").count():
            page.locator("input").first.fill(PROFILE_NAME)
            self._button(page, "+").click()
            page.wait_for_timeout(400)
        if self._button(page, PROFILE_NAME, exact=False).count():
            self._button(page, PROFILE_NAME, exact=False).first.click()
            page.wait_for_timeout(800)
        page.get_by_text(self.table_label, exact=True).click()
        page.get_by_role("button", name="DEAL", exact=True).wait_for(timeout=15000)

    def _click_action(self, page: Any, action: Action, raw: dict[str, Any]) -> str:
        if action.kind == "fold":
            self._button(page, "FOLD").click()
            return "FOLD"
        if action.kind == "check":
            self._button(page, "CHECK").click()
            return "CHECK"
        if action.kind == "call":
            self._button(page, "CALL", exact=False).first.click()
            return "CALL"
        # bet / raise
        if raw.get("allInOnly"):
            self._button(page, "ALL IN", exact=False).first.click()
            return "ALL IN"
        self._button(page, "RAISE").click()
        slider = page.locator("input[type=range]")
        slider.wait_for(timeout=5000)
        lo, hi = int(slider.get_attribute("min") or 0), int(slider.get_attribute("max") or 0)
        target = max(lo, min(hi, int(action.amount)))
        slider.fill(str(target))
        self._button(page, "CONFIRM").click()
        return f"RAISE to {target} (slider {lo}..{hi})"

    # ---- main loop --------------------------------------------------------
    def run(self, hands: int, hand_timeout: float = 120.0) -> dict[str, Any]:
        from playwright.sync_api import sync_playwright  # optional dependency

        self.out_dir.mkdir(parents=True, exist_ok=True)
        summary: dict[str, Any] = {
            "url": self.url, "table": self.table_label, "dry_run": self.dry_run, "hands_requested": hands,
            "hands_completed": 0, "decisions": 0, "start_stack": None, "end_stack": None, "hand_results": [],
        }
        with sync_playwright() as pw, self.decisions_path.open("a", encoding="utf-8") as jl:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(viewport={"width": 430, "height": 900})
            context.route("**/*", self._guard)
            page = context.new_page()
            try:
                self._open_table(page)
                self._shot(page, "00-table")
                hand_no = 0
                first = self._read(page).raw
                stack_before = first.get("heroStack")
                summary["start_stack"] = stack_before
                counter_at_deal = _hand_counter(first)
                page.get_by_role("button", name="DEAL", exact=True).click()
                deadline = time.monotonic() + hand_timeout
                acted_in_hand = 0
                while hand_no < hands:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"hand {hand_no + 1} did not progress within {hand_timeout}s")
                    st = self._read(page)
                    if st.hero_turn:
                        page.wait_for_timeout(300)  # let card/raise animations settle before reading
                        st = self._read(page)
                        for _ in range(10):  # the pot label is briefly absent during chip animations
                            if (st.raw.get("pot") or 0) > 0 or not st.hero_turn:
                                break
                            page.wait_for_timeout(200)
                            st = self._read(page)
                        if not st.hero_turn:
                            continue
                        obs = observation_from_dom(st.raw, self.big_blind, hand_no)
                        decision = self.agent.decide(obs, self.rng)
                        record = {
                            "ts": datetime.now().isoformat(timespec="milliseconds"),
                            "hand": hand_no + 1, "street": obs.street,
                            "hole": cards_str(obs.hole), "board": cards_str(obs.board),
                            "pot": obs.pot, "to_call": obs.to_call, "stack": obs.stack,
                            "opponents_in_hand": obs.n_active_opponents,
                            "decision": str(decision.action), "reason": decision.reason,
                            "equity": round(decision.equity, 4), "pot_odds": round(decision.pot_odds, 4),
                            "dry_run": self.dry_run,
                        }
                        if self.dry_run:
                            record["clicked"] = None
                            jl.write(json.dumps(record, ensure_ascii=False) + "\n")
                            self._shot(page, f"dry-run-hand{hand_no + 1}")
                            self.log(f"[dry-run] hand {hand_no + 1} {obs.street}: {cards_str(obs.hole)} | {cards_str(obs.board)} -> {decision.action} ({decision.reason})")
                            summary["decisions"] += 1
                            break  # the trainer waits for hero; without clicking the hand cannot continue
                        if acted_in_hand == 0:
                            self._shot(page, f"hand{hand_no + 1:02d}-decision")
                        record["clicked"] = self._click_action(page, decision.action, st.raw)
                        jl.write(json.dumps(record, ensure_ascii=False) + "\n")
                        jl.flush()
                        summary["decisions"] += 1
                        acted_in_hand += 1
                        self.log(f"hand {hand_no + 1} {obs.street}: {cards_str(obs.hole)} | {cards_str(obs.board) or '-'} "
                                 f"pot {obs.pot} call {obs.to_call} -> {record['clicked']} ({decision.reason}, eq {decision.equity:.2f})")
                        page.wait_for_timeout(250)
                    elif st.hand_over and _hand_counter(st.raw) > counter_at_deal:
                        # the trainer's "Hand #N" counter advances when a hand finishes
                        page.wait_for_timeout(400)
                        st = self._read(page)
                        hand_no += 1
                        after = st.raw.get("heroStack")
                        result = {"hand": hand_no, "stack_before": stack_before, "stack_after": after,
                                  "delta": (after - stack_before) if (after is not None and stack_before is not None) else None,
                                  "hero_acted": acted_in_hand, "log_tail": st.raw.get("handLog", [])[:4]}
                        summary["hand_results"].append(result)
                        jl.write(json.dumps({"event": "hand_over", **result}, ensure_ascii=False) + "\n")
                        self.log(f"hand {hand_no} over: stack {stack_before} -> {after}")
                        if hand_no in (1, hands):
                            self._shot(page, f"hand{hand_no:02d}-result")
                        stack_before = after
                        acted_in_hand = 0
                        if hand_no < hands:
                            counter_at_deal = _hand_counter(st.raw)
                            page.get_by_role("button", name="DEAL", exact=True).click()
                            deadline = time.monotonic() + hand_timeout
                    else:
                        page.wait_for_timeout(200)
                summary["hands_completed"] = hand_no
                summary["end_stack"] = self._read(page).raw.get("heroStack")
            except Exception as exc:
                summary["error"] = f"{type(exc).__name__}: {exc}".splitlines()[0]
                page.screenshot(path=str(self.out_dir / "error.png"))
                raise
            finally:
                summary["blocked_non_loopback_requests"] = self.blocked_requests
                (self.out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
                context.close()
                browser.close()
        return summary
