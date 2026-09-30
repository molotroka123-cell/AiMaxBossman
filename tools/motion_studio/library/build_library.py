"""Builds the 100-scenario library (library/*.json) and its two separate dataset files.

Each scenario = brief + facts + spec. Timings are laid out here, not by hand: item pops are
spaced by the measured Kokoro speech length (0.3 s + chars/24), scene voice-over lines follow
one another, and every spec must pass spec.validate() or the build fails. Numbers on screen
come only from `facts`; rows whose facts are illustrative carry "sample": true so a model
learns to take figures from facts, never to invent them.

Two provenance classes, deliberately kept in different dataset files:
  * 44 `add(...)` scenarios      -> dataset/brief_to_spec.jsonl, the file finetune_lora.py reads;
  * 56 `add_candidate(...)` ones -> dataset/candidates/motion_animation_56.jsonl, which no trainer
    reads. Their specs carry meta.status "candidate" and meta.provenance. They are Claude-written
    templates with editable placeholder copy: not owner-approved, not model-generated, and no
    quality score or render approval is claimed for them.

    python tools/motion_studio/library/build_library.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import spec as spec_mod  # noqa: E402


def est(text: str) -> float:
    return spec_mod.VO_BASE_SECONDS + len(text) / spec_mod.VO_CHARS_PER_SECOND


def _place_vo(lines: list[str], start: float, end: float, first: float = 0.1) -> list[dict]:
    out, t = [], start + first
    for line in lines:
        if t + est(line) > end + 0.1:
            raise ValueError(f"voice-over does not fit its scene: {line!r} ({start}-{end})")
        out.append({"t": round(t, 2), "text": line})
        t = t + est(line) + 0.12
    return out


class Film:
    def __init__(self, title: str, bpm: int = 120, key: str = "D", hud: str | None = None, brand: str = "BOSSMAN"):
        self.meta = {"title": title, "bpm": bpm, "key": key, "brand": brand, "hud": hud or f"{brand} // {title.upper()}"[:40],
                     "voice": "am_fenrir"}
        self.scenes: list[dict] = []
        self.t = 0.0

    def _add(self, dur: float, sc: dict, vo: list[str] | None = None) -> "Film":
        sc["start"], sc["end"] = round(self.t, 2), round(self.t + dur, 2)
        if vo:
            sc["vo"] = _place_vo(vo, sc["start"], sc["end"])
        self.scenes.append(sc)
        self.t += dur
        return self

    def title(self, dur, title, kicker=None, typed=None, chip=None, vo=None):
        sc = {"type": "title", "title": title}
        for k, v in (("kicker", kicker), ("typed", typed), ("chip", chip)):
            if v:
                sc[k] = v
        return self._add(dur, sc, vo)

    def bars(self, dur, values, headline_label=None, counter_label=None, x_from=None, x_to=None, highlight=None, vo=None):
        sc = {"type": "bars", "values": values}
        for k, v in (("headline_label", headline_label), ("counter_label", counter_label), ("x_from", x_from),
                     ("x_to", x_to), ("highlight", highlight)):
            if v is not None:
                sc[k] = v
        return self._add(dur, sc, vo)

    def _items(self, start, dur, n, spoken):
        gaps = [max(0.75, est(s) - 0.1) if s else 0.75 for s in spoken[:-1]] if spoken else [0.75] * (n - 1)
        need = sum(gaps) + (est(spoken[-1]) if spoken else 0.5)
        if need > dur - 0.1:
            raise ValueError(f"{n} items with their voice-over need {need:.2f} s, scene has {dur}")
        lead = min(0.25, (dur - need) / 2)
        times, t = [], start + lead
        for i in range(n):
            times.append(round(t, 2))
            if i < n - 1:
                t += gaps[i]
        return times

    def cards(self, dur, heading, items, speak=True):
        spoken = [it[3] if len(it) > 3 else it[0].capitalize() + "." for it in items] if speak else None
        times = self._items(self.t, dur, len(items), spoken)
        sc = {"type": "cards", "heading": heading,
              "items": [{"t": t, "title": it[0], "sub": it[1], "icon": it[2]} for t, it in zip(times, items)]}
        if speak:
            sc["vo"] = [{"t": t, "text": s} for t, s in zip(times, spoken)]
        return self._add(dur, sc)

    def roadmap(self, dur, heading, items, disclaimer="PROJECTION · TARGETS, NOT PROMISES", speak=True):
        spoken = [it[3] for it in items] if speak else None
        times = self._items(self.t, dur, len(items), spoken)
        sc = {"type": "roadmap", "heading": heading, "disclaimer": disclaimer,
              "items": [{"t": t, "version": it[0], "when": it[1], "lines": it[2]} for t, it in zip(times, items)]}
        if speak:
            sc["vo"] = [{"t": t, "text": s} for t, s in zip(times, spoken)]
        return self._add(dur, sc)

    def grid(self, dur, value, label, caption=None, then_title=None, then_sub=None, vo=None):
        sc = {"type": "grid", "value": value, "label": label}
        for k, v in (("caption", caption), ("then_title", then_title), ("then_sub", then_sub)):
            if v:
                sc[k] = v
        return self._add(dur, sc, vo)

    def voice(self, dur, name, lines, status, sub="ON TELEGRAM", vo=None):
        return self._add(dur, {"type": "voice", "name": name, "sub": sub, "lines": lines,
                               "status": [{"text": s, "state": st} for s, st in status]}, vo)

    def sticker(self, dur, lottie, text, sub=None, vo=None):
        sc = {"type": "sticker", "lottie": lottie, "text": text}
        if sub:
            sc["sub"] = sub
        return self._add(dur, sc, vo)

    def logo(self, dur, name, tagline=None, vo=None):
        sc = {"type": "logo", "name": name}
        if tagline:
            sc["tagline"] = tagline
        return self._add(dur, sc, vo)

    def end(self, dur, text, sub=None, vo=None):
        sc = {"type": "end_card", "text": text}
        if sub:
            sc["sub"] = sub
        return self._add(dur, sc, vo)

    def spec(self) -> dict:
        return {"meta": {**self.meta, "duration": round(self.t, 2)}, "scenes": self.scenes}


LIB: list[tuple[str, str, dict, Film, str]] = []

# A candidate says so inside its own spec, so a file read on its own is never mistaken for approved
# work. No digits in this string: generate_spec.unsupported_numbers() treats every number under meta
# as a claim that has to come from FACTS.
CANDIDATE_PROVENANCE = "claude-written template, motion-animation candidate round, not owner-approved"


def add(sid: str, brief: str, facts: dict, film: Film) -> None:
    LIB.append((sid, brief, facts, film, "approved"))


def add_candidate(sid: str, brief: str, facts: dict, film: Film) -> None:
    """A sample template: valid and render-checked, but not owner-approved and not training data."""
    film.meta["status"] = "candidate"
    film.meta["provenance"] = CANDIDATE_PROVENANCE
    LIB.append((sid, brief, facts, film, "candidate"))


# ============================================================ Bossman (real facts from this repository)
COMMITS = [4, 124, 114, 60, 43, 22, 89, 61, 38, 110, 138, 120, 88, 75, 17, 71, 5, 2, 8, 72, 19, 19, 35, 82, 40, 123, 182, 108, 455, 71, 38, 36]
add("bossman_first_commit", "8 s: the day Bossman started.",
    {"first_commit": {"date": "2026-08-27", "hash": "1e6c8c56", "message": "Bossman Control v0.3"}},
    Film("First commit").title(4, "27 AUG", "2026", '$ git commit -m "Bossman Control v0.3"', "1e6c8c56",
                               ["August twenty-seventh.", "The first commit."])
    .logo(4, "BOSSMAN", "DAY ONE · 27 AUG 2026", ["This is where it began."]))
add("bossman_velocity", "10 s: how fast Bossman is being built, per day.",
    {"commits_per_day_aug27_sep27": COMMITS, "total": sum(COMMITS), "record_day": {"date": "2026-09-24", "commits": 455}},
    Film("Velocity", bpm=126).title(2.5, "VELOCITY", "BOSSMAN · 32 DAYS", vo=["Thirty-two days."])
    .bars(5, COMMITS, "DAYS", "COMMITS", "AUG 27", "SEP 27", {"index": 28, "text": "455 IN ONE DAY"},
          ["Nearly twenty-five hundred commits.", "Four hundred fifty-five in one day."])
    .logo(2.5, "BOSSMAN", "BUILT DAILY", ["Built daily."]))
add("bossman_tests", "10 s: Bossman's test suite and that it runs on your machine.",
    {"tests_passed": 5179, "source": "Command Center CI 2026-09-27", "local_first": True},
    Film("Tests").grid(6, 5179, "TESTS PASSED", "COMMAND CENTER CI · 2026-09-27", "YOUR MACHINE",
                       "LOCAL-FIRST · YOUR HARDWARE", ["Over five thousand tests.", "On your own machine."])
    .logo(4, "BOSSMAN", "TESTED · LOCAL-FIRST", ["This is Bossman."]))
add("bossman_capabilities", "12 s: five things Bossman does.",
    {"capabilities": ["agents", "memory", "computer use", "video studio", "Jeff on Telegram"]},
    Film("Capabilities", bpm=124).title(2.5, "BOSSMAN", "YOUR AI OPERATOR", vo=["Meet Bossman."])
    .cards(6.5, "WHAT IT DOES", [("AGENTS", "plan · act · verify", "agents"), ("MEMORY", "retained experience", "memory"),
                                 ("COMPUTER USE", "sees & drives the PC", "cursor", "Computer use."),
                                 ("VIDEO STUDIO", "edit · render · export", "play", "Video studio."),
                                 ("JEFF", "on Telegram", "plane", "Jeff, on Telegram.")])
    .logo(3, "BOSSMAN", "LOCAL-FIRST AI", ["Local first."]))
add("jeff_voice_notes", "10 s: Jeff now hears Telegram voice notes; talking back is next.",
    {"voice_in": "live since 2026-09-27, local Whisper", "voice_out": "not shipped"},
    Film("Jeff voice", key="A", bpm=128, hud="BOSSMAN // JEFF").voice(4.5, "JEFF", ["Jeff, what's on today?"],
        [("VOICE IN — LIVE · LOCAL WHISPER", "live"), ("VOICE OUT — NEXT", "next")],
        vo=["Jeff hears your voice notes.", "Talking back is next."])
    .sticker(3, "noto_face_with_one_eyebrow_raised", "TRY IT", "SEND A VOICE NOTE", ["Try it."])
    .logo(2.5, "JEFF", "ON TELEGRAM", ["This is Jeff."]))
add("bossman_roadmap", "14 s: Bossman roadmap to winter, clearly a projection.",
    {"current": "1.7 (freeze candidate)", "targets": {"1.8": "Oct 2026", "1.9": "Nov 2026", "2.0": "Dec 2026", "3.0": "winter"}},
    Film("Roadmap", bpm=120).title(2.5, "NEXT", "BOSSMAN ROADMAP", vo=["What comes next."])
    .roadmap(8, "WHAT'S NEXT", [("1.8", "OCT 2026", ["freeze closed", "Jeff talks back"], "One point eight."),
                                ("1.9", "NOV 2026", ["learns across tasks"], "One point nine."),
                                ("2.0", "DEC 2026", ["week mode", "first paid pilot"], "Two point oh."),
                                ("3.0", "WINTER", ["own voice & phone"], "Three point oh, by winter.")])
    .end(3.5, "TO BE CONTINUED", "BOSSMAN · WINTER 2026", ["To be continued."]))
add("bossman_privacy", "10 s: Bossman keeps your data on your PC.",
    {"claims": ["local models on owner hardware", "voice audio kept in memory, not sent to a provider", "secrets never logged"]},
    Film("Privacy", key="E", bpm=110).title(2.5, "PRIVATE", "BY DEFAULT", vo=["Private by default."])
    .cards(5, "WHERE YOUR DATA STAYS", [("LOCAL MODELS", "your hardware", "bolt", "Local models."),
                                        ("VOICE", "audio stays on your PC", "shield", "Your voice stays home."),
                                        ("SECRETS", "never logged", "shield", "Secrets never logged.")])
    .logo(2.5, "BOSSMAN", "YOUR MACHINE · YOUR RULES", ["Your rules."]))
add("motion_studio_intro", "12 s: Bossman now makes videos from a brief.",
    {"scene_types": 9, "lottie_animations": 100, "pipeline": ["brief", "spec", "voice", "music", "video"]},
    Film("Motion Studio", key="G", bpm=128, hud="BOSSMAN // MOTION STUDIO").title(2.5, "MOTION", "BOSSMAN STUDIO",
        "$ bossman video --brief launch.txt", vo=["Bossman makes videos now."])
    .cards(6, "BRIEF TO VIDEO", [("BRIEF", "you describe it", "code", "You write a brief."),
                                 ("SCENES", "model writes JSON", "agents", "It writes the scenes."),
                                 ("VOICE", "local Kokoro", "mic", "Adds a voice."),
                                 ("VIDEO", "rendered locally", "play", "And renders it.")])
    .sticker(3.5, "noto_rocket", "100 ANIMATIONS", "BUILT IN · CC BY 4.0",
             ["A hundred animations built in."]))

# ============================================================ generic templates (illustrative facts, "sample": true)
S = {"sample": True}
add("product_launch", "12 s launch teaser for a note-taking app called Quill with three features and a date.",
    {**S, "product": "Quill", "features": ["offline sync", "AI summaries", "end-to-end encryption"], "launch": "Oct 15"},
    Film("Quill launch", key="C", bpm=124, brand="QUILL").title(2.5, "QUILL", "LAUNCHING OCT 15", vo=["Meet Quill."])
    .cards(6, "WHY QUILL", [("OFFLINE SYNC", "works anywhere", "bolt", "Works offline."),
                            ("AI SUMMARIES", "notes in seconds", "agents", "Summarizes for you."),
                            ("ENCRYPTED", "end to end", "shield", "Fully encrypted.")])
    .logo(3.5, "QUILL", "OCTOBER 15", ["October fifteenth."]))
add("weekly_report", "10 s weekly sales report, 7 days of orders, best day highlighted.",
    {**S, "orders_per_day": [42, 51, 38, 66, 71, 94, 58], "days": "Mon..Sun", "best": "Saturday"},
    Film("Weekly report", key="F", bpm=116).title(2, "WEEK 39", "SALES REPORT", vo=["Week thirty-nine."])
    .bars(5, [42, 51, 38, 66, 71, 94, 58], "DAYS", "ORDERS", "MON", "SUN", {"index": 5, "text": "BEST: SATURDAY"},
          ["Four hundred twenty orders.", "Saturday was the best day."])
    .end(3, "SEE YOU MONDAY", "WEEK 40 STARTS NOW", ["See you Monday."]))
add("app_changelog", "10 s app update: version 2.4 with three changes.",
    {**S, "version": "2.4", "changes": ["dark mode", "faster search", "bug fixes"]},
    Film("Update 2.4", key="D", bpm=122).title(2.5, "v2.4", "WHAT'S NEW", vo=["Version two point four."])
    .cards(5, "IN THIS UPDATE", [("DARK MODE", "easy on the eyes", "bolt"), ("FAST SEARCH", "twice as quick", "cursor", "Faster search."),
                                 ("BUG FIXES", "smoother overall", "shield", "Bug fixes.")])
    .end(2.5, "UPDATE NOW", "AVAILABLE TODAY", ["Update now."]))
add("event_teaser", "12 s teaser for a meetup on AI agents, date and three talks.",
    {**S, "event": "Agents Meetup", "date": "Nov 3", "city": "Prague", "talks": 3},
    Film("Meetup", key="A", bpm=128, brand="MEETUP").title(3, "NOV 3", "PRAGUE · AGENTS MEETUP", vo=["November third, Prague."])
    .cards(5.5, "THREE TALKS", [("LOCAL LLMS", "on your own PC", "bolt", "Local models."),
                                ("AGENTS", "that verify work", "agents", "Agents that verify."),
                                ("VOICE", "bots that listen", "mic", "Voice bots.")])
    .end(3.5, "SAVE THE DATE", "FREE ENTRY · LIMITED SEATS", ["Save the date."]))
add("webinar_invite", "10 s invite to a free webinar about home automation.",
    {**S, "topic": "home automation", "date": "Oct 22", "time": "18:00", "price": "free"},
    Film("Webinar", key="G", bpm=112).title(3, "LIVE", "FREE WEBINAR · OCT 22", "$ join --at 18:00", vo=["Free webinar, live."])
    .sticker(3.5, "noto_electric_light_bulb", "SMART HOME", "LEARN IT IN ONE HOUR", ["Learn home automation in an hour."])
    .end(3.5, "OCT 22 · 18:00", "REGISTER FREE", ["Register free."]))
add("hiring", "12 s hiring post: a startup hiring two roles, remote.",
    {**S, "company": "Nimbus", "roles": ["backend engineer", "designer"], "remote": True},
    Film("Hiring", key="E", bpm=124, brand="NIMBUS").title(2.5, "HIRING", "NIMBUS IS GROWING", vo=["We are hiring."])
    .cards(5.5, "OPEN ROLES", [("BACKEND", "python · postgres", "code", "Backend engineer."),
                               ("DESIGNER", "product & brand", "play", "Product designer."),
                               ("REMOTE", "work from anywhere", "plane", "Fully remote.")])
    .end(4, "APPLY NOW", "LINK IN BIO", ["Apply now."]))
add("sale_promo", "8 s flash sale: 30 percent off this weekend.",
    {**S, "discount": "30%", "when": "this weekend"},
    Film("Sale", key="C", bpm=132).sticker(3.5, "noto_fire", "30% OFF", "THIS WEEKEND ONLY", ["Thirty percent off."])
    .end(4.5, "SHOP NOW", "ENDS SUNDAY NIGHT", ["This weekend only.", "Shop now."]))
add("countdown_launch", "10 s countdown to a launch in 3 days.",
    {**S, "days_left": 3, "product": "Orbit"},
    Film("Countdown", key="B", bpm=128, brand="ORBIT").title(3, "3 DAYS", "ORBIT LAUNCH", vo=["Three days to go."])
    .sticker(3.5, "noto_rocket", "ALMOST", "COUNTDOWN STARTED", ["The countdown has started."])
    .logo(3.5, "ORBIT", "LAUNCHING SOON", ["Orbit."]))
add("fitness_progress", "10 s personal fitness recap: 8 weeks of workouts.",
    {**S, "workouts_per_week": [2, 3, 3, 4, 3, 4, 5, 5], "total": 29},
    Film("Fitness", key="D", bpm=128, brand="YOU").title(2, "8 WEEKS", "TRAINING RECAP", vo=["Eight weeks."])
    .bars(5, [2, 3, 3, 4, 3, 4, 5, 5], "WEEKS", "WORKOUTS", "WEEK 1", "WEEK 8", None, ["Twenty-nine workouts."])
    .sticker(3, "noto_flexed_biceps", "KEEP GOING", "WEEK 9 STARTS", ["Keep going."]))
add("study_progress", "10 s study streak recap: 30 days of language practice.",
    {**S, "minutes_per_day_avg": 22, "streak_days": 30, "language": "Czech"},
    Film("Study streak", key="F", bpm=110).title(2.5, "30 DAYS", "CZECH STREAK", vo=["Thirty days of Czech."])
    .grid(4.5, 660, "MINUTES", "30 DAYS × 22 MIN AVERAGE", vo=["Six hundred sixty minutes."])
    .sticker(3, "noto_trophy", "STREAK!", "DAY 31 TOMORROW", ["Streak unlocked."]))
add("travel_recap", "12 s travel recap: 4 cities in 10 days.",
    {**S, "cities": ["Vienna", "Prague", "Berlin", "Krakow"], "days": 10},
    Film("Trip", key="G", bpm=118, brand="TRIP").title(2.5, "10 DAYS", "FOUR CITIES", vo=["Ten days, four cities."])
    .cards(6, "THE ROUTE", [("VIENNA", "coffee & opera", "plane", "Vienna."), ("PRAGUE", "bridges & beer", "plane", "Prague."),
                            ("BERLIN", "art & techno", "plane", "Berlin."), ("KRAKOW", "old town nights", "plane", "Krakow.")])
    .sticker(3.5, "noto_earth_globe_europe_africa", "WHERE NEXT?", "", ["Where next?"]))
add("birthday", "8 s birthday greeting for a friend named Max.",
    {**S, "name": "Max", "age": 30},
    Film("Birthday", key="C", bpm=120, brand="MAX").sticker(4, "noto_birthday_cake", "HAPPY 30TH", "MAX", ["Happy birthday, Max."])
    .end(4, "CHEERS", "TO THE NEXT THIRTY", ["Cheers to the next thirty."]))
add("new_year", "10 s new year greeting from a team.",
    {**S, "year": 2027, "team": "the Bossman team"},
    Film("New year", key="D", bpm=124).title(3, "2027", "HAPPY NEW YEAR", vo=["Happy new year."])
    .sticker(3.5, "noto_party_popper", "THANK YOU", "FOR AN AMAZING YEAR", ["Thank you for an amazing year."])
    .logo(3.5, "BOSSMAN", "SEE YOU IN 2027", ["See you next year."]))
add("game_trailer", "12 s trailer for an indie platformer game.",
    {**S, "game": "BossBlocks", "engine": "Godot", "features": ["respawn", "levels", "boss fights"]},
    Film("Game trailer", key="A", bpm=140, brand="BOSSBLOCKS").title(2.5, "PLAY", "BOSSBLOCKS · GODOT", vo=["Bossblocks."])
    .cards(5.5, "FEATURES", [("LEVELS", "hand-built worlds", "play", "Hand-built levels."),
                             ("BOSS FIGHTS", "learn the patterns", "bolt", "Boss fights."),
                             ("RESPAWN", "press R, go again", "cursor", "Instant respawn.")])
    .end(4, "COMING SOON", "WISHLIST NOW", ["Coming soon."]))
add("podcast_teaser", "10 s teaser for a podcast episode on local AI.",
    {**S, "show": "Offline Minds", "episode": 12, "guest": "a local-AI builder"},
    Film("Podcast", key="E", bpm=100).title(3, "EP 12", "OFFLINE MINDS", vo=["Episode twelve."])
    .voice(4, "HOST", ["Can AI run fully offline?"], [("NEW EPISODE — LIVE", "live")], "PODCAST",
           ["Can AI run fully offline?"])
    .end(3, "LISTEN NOW", "WHEREVER YOU LISTEN", ["Listen now."]))
add("channel_intro", "6 s YouTube channel intro for a tech channel.",
    {**S, "channel": "BUILDLOG"},
    Film("Channel intro", key="D", bpm=128, brand="BUILDLOG").title(3, "BUILDLOG", "WEEKLY DEV LOGS", vo=["Welcome to Buildlog."])
    .logo(3, "BUILDLOG", "NEW EVERY FRIDAY", ["New every Friday."]))
add("reel_hook", "8 s vertical-style hook: one surprising stat and a call to watch.",
    {**S, "stat": "9 of 10 notes are never reopened"},
    Film("Hook", key="B", bpm=130).sticker(4, "noto_face_screaming_in_fear", "9 OF 10",
                                          "NOTES ARE NEVER REOPENED", ["Nine out of ten notes are never reopened."])
    .end(4, "HERE'S WHY", "WATCH TO THE END", ["Here is why."]))
add("restaurant_special", "10 s restaurant weekly special.",
    {**S, "dish": "pumpkin soup", "price": "6 EUR", "days": "Mon-Fri"},
    Film("Special", key="G", bpm=108, brand="BISTRO").title(2.5, "SPECIAL", "THIS WEEK", vo=["This week's special."])
    .sticker(4, "noto_jack_o_lantern", "PUMPKIN SOUP", "6 EUR · MON–FRI", ["Pumpkin soup, six euro."])
    .end(3.5, "SEE YOU", "OPEN DAILY 11–22", ["See you soon."]))
add("course_launch", "12 s launch of an online course with three modules.",
    {**S, "course": "Agents 101", "modules": ["prompts", "tools", "verification"], "price": "free"},
    Film("Course", key="C", bpm=120, brand="AGENTS 101").title(2.5, "AGENTS 101", "FREE COURSE", vo=["Agents one oh one."])
    .cards(5.5, "THREE MODULES", [("PROMPTS", "say what you mean", "code", "Prompts."), ("TOOLS", "let it act", "agents", "Tools."),
                                  ("VERIFY", "trust but check", "shield", "Verification.")])
    .end(4, "START FREE", "ENROLL TODAY", ["Start free today."]))
add("charity_drive", "10 s charity progress update: amount raised so far.",
    {**S, "raised_eur_per_week": [800, 1200, 950, 1600], "goal": 5000, "raised": 4550},
    Film("Charity", key="F", bpm=112).title(2, "4 WEEKS", "WINTER COATS DRIVE", vo=["Four weeks in."])
    .bars(4.5, [800, 1200, 950, 1600], "WEEKS", "EUR RAISED", "WEEK 1", "WEEK 4", {"index": 3, "text": "BEST WEEK"},
          ["Four thousand five hundred fifty euro."])
    .end(3.5, "ALMOST THERE", "GOAL 5000 EUR", ["Almost there."]))
add("follower_milestone", "8 s thank-you for reaching 1000 followers.",
    {**S, "followers": 1000},
    Film("Milestone", key="D", bpm=126).grid(4.5, 1000, "FOLLOWERS", "THANK YOU ALL", vo=["One thousand followers."])
    .sticker(3.5, "noto_heavy_black_heart", "THANK YOU", "", ["Thank you."]))
add("sprint_review", "12 s sprint review: tickets closed per day and three shipped items.",
    {**S, "closed_per_day": [3, 5, 4, 7, 6, 2, 8, 9, 5, 6], "shipped": ["login", "export", "alerts"]},
    Film("Sprint", key="A", bpm=124).bars(4.5, [3, 5, 4, 7, 6, 2, 8, 9, 5, 6], "DAYS", "TICKETS", "DAY 1", "DAY 10", None,
                                          ["Fifty-five tickets closed."])
    .cards(5, "SHIPPED", [("LOGIN", "passkeys", "shield", "Passkey login."), ("EXPORT", "CSV & PDF", "code", "Export."),
                          ("ALERTS", "real time", "bolt", "Live alerts.")])
    .end(2.5, "NEXT SPRINT", "", ["Next sprint."]))
add("uptime_report", "10 s monthly uptime report.",
    {**S, "uptime_percent": "99.95", "incidents": 1, "month": "September"},
    Film("Uptime", key="E", bpm=110).title(3, "99.95%", "SEPTEMBER UPTIME", vo=["Ninety-nine point nine five percent."])
    .cards(4.5, "THE MONTH", [("1 INCIDENT", "resolved in 18 min", "shield", "One incident."),
                              ("0 DATA LOSS", "backups verified", "memory", "No data lost.")])
    .end(2.5, "STATUS PAGE", "ALWAYS PUBLIC", ["Status is public."]))
add("app_rating", "8 s celebrate an app store rating.",
    {**S, "rating": "4.8", "reviews": 2300},
    Film("Rating", key="C", bpm=120).sticker(4, "noto_white_medium_star", "4.8 STARS", "2300 REVIEWS",
                                            ["Four point eight stars."])
    .end(4, "THANK YOU", "KEEP THE FEEDBACK COMING", ["Thank you for the feedback."]))
add("newsletter_teaser", "8 s teaser for this week's newsletter with three topics.",
    {**S, "issue": 41, "topics": ["local AI", "privacy", "tools"]},
    Film("Newsletter", key="G", bpm=118).cards(5, "ISSUE 41", [("LOCAL AI", "what runs at home", "bolt", "Local AI."),
                                                             ("PRIVACY", "what leaks and why", "shield", "Privacy."),
                                                             ("TOOLS", "five new picks", "code", "New tools.")])
    .end(3, "READ IT", "LINK IN BIO", ["Read it now."]))
add("talk_intro", "8 s intro slide for a conference talk.",
    {**S, "talk": "Verified agents", "speaker": "the Bossman team", "event": "DevDays"},
    Film("Talk", key="D", bpm=116).title(4, "VERIFIED", "DEVDAYS · MAIN STAGE", "$ talk --title 'Verified agents'",
                                        vo=["Verified agents."])
    .logo(4, "BOSSMAN", "DEVDAYS 2026", ["By the Bossman team."]))
add("tutorial_steps", "12 s tutorial: install in three steps.",
    {**S, "steps": ["download", "unzip", "run start"], "os": "Windows"},
    Film("Install", key="A", bpm=120).title(2.5, "INSTALL", "3 STEPS · WINDOWS", vo=["Install in three steps."])
    .cards(6, "HOW TO", [("1 DOWNLOAD", "one zip file", "cursor", "Download the zip."),
                         ("2 UNZIP", "anywhere you like", "memory", "Unzip it."),
                         ("3 RUN", "start-bossman", "bolt", "Run start.")])
    .sticker(3.5, "noto_white_heavy_check_mark", "DONE", "THAT'S IT", ["That's it."]))
add("thank_you", "6 s thank-you clip for early testers.",
    {**S, "testers": 25},
    Film("Thanks", key="F", bpm=110).sticker(3, "noto_person_raising_both_hands_in_celebration", "THANK YOU", "25 EARLY TESTERS",
                                            ["Thank you, testers."])
    .end(3, "YOU ROCK", "SEE YOU IN BETA 2", ["You rock."]))
add("team_intro", "12 s meet the team: four roles.",
    {**S, "team": ["founder", "engineer", "designer", "Jeff the bot"]},
    Film("Team", key="G", bpm=122).title(2.5, "THE TEAM", "WHO BUILDS IT", vo=["Meet the team."])
    .cards(6.5, "FOUR OF US", [("FOUNDER", "vision & grit", "bolt", "The founder."), ("ENGINEER", "ships daily", "code", "The engineer."),
                               ("DESIGNER", "makes it clear", "play", "The designer."), ("JEFF", "the bot", "plane", "And Jeff, the bot.")])
    .end(3, "SAY HI", "WE READ EVERYTHING", ["Say hi."]))
add("partner_announcement", "10 s partnership announcement.",
    {**S, "partner": "HomeLab Co", "what": "one-click local AI boxes"},
    Film("Partner", key="E", bpm=118).title(3, "PARTNERS", "BOSSMAN × HOMELAB", vo=["A new partnership."])
    .sticker(3.5, "noto_thumbs_up_sign", "ONE CLICK", "LOCAL AI BOXES", ["One-click local AI boxes."])
    .logo(3.5, "BOSSMAN", "× HOMELAB CO", ["Together."]))
add("pricing_tiers", "12 s pricing: three tiers.",
    {**S, "tiers": {"free": "0", "pro": "9 EUR", "team": "29 EUR"}},
    Film("Pricing", key="C", bpm=120).title(2.5, "PRICING", "SIMPLE & FAIR", vo=["Simple pricing."])
    .cards(6, "PICK A PLAN", [("FREE", "0 EUR · local", "bolt", "Free."), ("PRO", "9 EUR / month", "agents", "Pro, nine euro."),
                              ("TEAM", "29 EUR / month", "memory", "Team, twenty-nine.")])
    .end(3.5, "START FREE", "UPGRADE ANYTIME", ["Start free."]))
add("feature_compare", "12 s comparison: local vs cloud assistant.",
    {**S, "local": ["private", "works offline", "no subscription"], "cloud": ["needs internet"]},
    Film("Local vs cloud", key="D", bpm=124).title(2.5, "LOCAL", "VS CLOUD", vo=["Local versus cloud."])
    .cards(6, "WHY LOCAL", [("PRIVATE", "data stays home", "shield", "Private."), ("OFFLINE", "no internet needed", "bolt", "Works offline."),
                            ("NO RENT", "no subscription", "chart", "No subscription.")])
    .logo(3.5, "BOSSMAN", "LOCAL-FIRST AI", ["Go local."]))
add("coming_soon", "6 s mysterious coming-soon teaser.",
    {**S, "hint": "something that talks back"},
    Film("Teaser", key="B", bpm=90).sticker(3, "noto_eyes", "SOON", "", ["Something is coming."])
    .end(3, "COMING SOON", "IT TALKS BACK", ["It talks back."]))
add("security_update", "10 s security notice: what changed and what users should do.",
    {**S, "fixes": ["session timeout", "stronger password rules"], "action": "update the app"},
    Film("Security", key="E", bpm=108).title(2.5, "SECURITY", "UPDATE 3.1.2", vo=["A security update."])
    .cards(4.5, "WHAT CHANGED", [("SESSIONS", "expire after 30 min", "shield", "Shorter sessions."),
                                 ("PASSWORDS", "stronger rules", "shield", "Stronger passwords.")])
    .end(3, "UPDATE NOW", "TAKES ONE MINUTE", ["Please update now."]))
add("community_call", "10 s invite to a community call with a live demo.",
    {**S, "when": "Thursday 19:00", "demo": "Motion Studio"},
    Film("Community call", key="G", bpm=120).title(3, "THURSDAY", "COMMUNITY CALL · 19:00", vo=["Community call, Thursday."])
    .sticker(3.5, "noto_party_popper", "LIVE DEMO", "MOTION STUDIO", ["Live demo of Motion Studio."])
    .end(3.5, "JOIN US", "LINK IN THE CHAT", ["Join us."]))
add("year_in_review", "14 s year in review: monthly signups and three highlights.",
    {**S, "signups_per_month": [120, 180, 150, 240, 310, 280, 390, 420, 510, 480, 560, 640], "highlights": ["v1", "mobile", "10k users"]},
    Film("Year in review", key="D", bpm=124).bars(5, [120, 180, 150, 240, 310, 280, 390, 420, 510, 480, 560, 640], "MONTHS",
                                                  "SIGNUPS", "JAN", "DEC", {"index": 11, "text": "BEST: DECEMBER"},
                                                  ["Four thousand two hundred eighty signups."])
    .cards(5.5, "HIGHLIGHTS", [("V1", "we launched", "bolt", "We launched."), ("MOBILE", "apps shipped", "play", "Mobile apps."),
                               ("10K USERS", "and counting", "chart", "Ten thousand users.")])
    .end(3.5, "THANK YOU", "SEE YOU NEXT YEAR", ["Thank you."]))


# ============================================================ candidate templates (56)
# Sample templates with editable placeholder copy. No biography, result, price or promise is invented:
# every on-screen word is either a structural label ("STEP TWO") or a slot the owner replaces ("YOUR
# PRODUCT NAME"), and every number drawn by a bars/grid scene is listed in `facts` and marked both
# "sample" and "placeholder" so neither a reader nor a model mistakes it for measured data.
# All 16:9: the engine is fixed at 1920x1080, so no vertical variant is claimed here.
P = {"sample": True, "placeholder": True, "owner_approved": False}

# ---------------------------------------------------------------- personal intro
add_candidate("intro_freelancer", "13 s personal intro for a freelancer: three services and a contact card.",
    {**P, "slots": ["name", "three services", "contact line"]},
    Film("Freelance intro", bpm=118, key="F", brand="YOUR NAME")
    .title(3, "HELLO", "YOUR NAME HERE", vo=["Hello. This is your intro."])
    .cards(6.5, "WHAT I DO", [("SERVICE ONE", "your first offer", "bolt", "Your first service."),
                              ("SERVICE TWO", "your second offer", "code", "Your second service."),
                              ("SERVICE THREE", "your third offer", "play", "And your third.")])
    .logo(3.5, "YOUR NAME", "ADD YOUR TAGLINE HERE", ["Get in touch."]))
DEV_METRIC = 4800
add_candidate("intro_developer", "16 s developer intro: one headline number, then how you work.",
    {**P, "your_metric": DEV_METRIC, "slots": ["name", "stack", "three working habits"]},
    Film("Developer intro", bpm=132, key="A", brand="DEV")
    .title(2.5, "YOUR NAME", "DEVELOPER", "$ whoami", vo=["Your name here."])
    .grid(5.5, DEV_METRIC, "YOUR METRIC", "REPLACE WITH YOUR REAL NUMBER", "YOUR STACK",
          "LIST YOUR TOOLS HERE", vo=["Put your own number here."])
    .cards(5.5, "HOW I WORK", [("PLAN", "scope and estimate", "agents", "I plan first."),
                               ("BUILD", "small, tested steps", "code", "Then I build."),
                               ("VERIFY", "checked before ship", "shield", "And I verify.")])
    .end(2.5, "LET'S TALK", "YOUR CONTACT HERE", ["Let's talk."]))
add_candidate("intro_creator", "13 s channel intro: who you are and what a viewer gets.",
    {**P, "slots": ["handle", "two topics", "posting rhythm"]},
    Film("Creator intro", bpm=126, key="D", brand="CHANNEL")
    .sticker(4, "noto_waving_hand_sign", "HI, I'M ...", "YOUR HANDLE HERE", ["Hi. Welcome to the channel."])
    .cards(6, "WHAT TO EXPECT", [("TOPIC ONE", "your main subject", "play", "Your main topic."),
                                 ("TOPIC TWO", "your second subject", "bolt", "Your second topic."),
                                 ("EVERY WEEK", "your posting rhythm", "memory", "Every week.")])
    .end(3, "SUBSCRIBE", "IF THAT SOUNDS USEFUL", ["Subscribe if that sounds useful."]))
add_candidate("intro_consultant", "15 s consultant intro: a proposed way of working, marked as a plan.",
    {**P, "slots": ["name", "field", "three phases", "contact line"]},
    Film("Consultant intro", bpm=104, key="E", brand="ADVISORY")
    .title(3, "YOUR NAME", "YOUR FIELD HERE", vo=["Your name, your field."])
    .roadmap(8.5, "HOW WE WOULD WORK", [("ONE", "WEEK ONE", ["scope the problem"], "First we scope it."),
                                        ("TWO", "WEEK TWO", ["agree the plan"], "Then we agree a plan."),
                                        ("THREE", "WEEK THREE", ["hand over"], "Then I hand over.")],
             "PROPOSED PLAN, NOT A PROMISED RESULT")
    .logo(3.5, "YOUR NAME", "ADD YOUR CONTACT LINE", ["Let's begin."]))

# ---------------------------------------------------------------- product announcement
add_candidate("product_announce_hardware", "14.5 s hardware announcement: three specs, then availability.",
    {**P, "slots": ["product name", "three specs", "date", "price or link"]},
    Film("Hardware launch", bpm=122, key="C", brand="PRODUCT")
    .title(2.5, "NEW", "YOUR PRODUCT NAME", vo=["Something new."])
    .cards(6, "WHAT IS IN IT", [("SPEC ONE", "your first spec", "bolt", "Your first spec."),
                                ("SPEC TWO", "your second spec", "chart", "Your second spec."),
                                ("SPEC THREE", "your third spec", "memory", "Your third spec.")])
    .sticker(3, "noto_gem_stone", "IN STOCK SOON", "ADD YOUR DATE HERE", ["In stock soon."])
    .logo(3, "PRODUCT", "ADD YOUR PRICE OR LINK", ["Your product."]))
SAAS_USERS = 3600
add_candidate("product_announce_saas", "14 s software release: a headline count, then four features.",
    {**P, "your_users": SAAS_USERS, "slots": ["four features", "signup link"]},
    Film("SaaS launch", bpm=128, key="G", brand="APP")
    .grid(5, SAAS_USERS, "YOUR USERS", "REPLACE WITH YOUR REAL COUNT",
          vo=["Replace this with your own count."])
    .cards(6, "WHAT IS NEW", [("FEATURE ONE", "what it does", "bolt", "Your first feature."),
                              ("FEATURE TWO", "what it does", "agents", "Your second feature."),
                              ("FEATURE THREE", "what it does", "cursor", "Your third feature."),
                              ("FEATURE FOUR", "what it does", "code", "And your fourth.")])
    .end(3, "TRY IT FREE", "ADD YOUR SIGNUP LINK", ["Try it free."]))
add_candidate("product_announce_mobile_app", "13 s app release: out now, two screens, one honest caveat.",
    {**P, "slots": ["app name", "two screens", "store link"]},
    Film("App launch", bpm=134, key="B", brand="APP")
    .sticker(3.5, "noto_rocket", "OUT NOW", "ON YOUR APP STORE", ["Out now."])
    .cards(6.5, "IN THIS RELEASE", [("SCREEN ONE", "name your screen", "play", "Your first screen."),
                                    ("SCREEN TWO", "name your screen", "cursor", "Your second screen."),
                                    ("OFFLINE", "only if yours does", "bolt", "Only if yours really does.")])
    .logo(3, "APP NAME", "ADD YOUR STORE LINK", ["Your app."]))
RESTOCK_UNITS = [12, 18, 9, 24, 15, 21]
add_candidate("product_restock", "11 s restock notice with a six-week stock chart.",
    {**P, "units_per_week": RESTOCK_UNITS, "slots": ["item name", "shop link"]},
    Film("Restock", bpm=116, key="F", brand="SHOP")
    .sticker(3, "noto_bell", "BACK IN STOCK", "YOUR ITEM HERE", ["Back in stock."])
    .bars(5, RESTOCK_UNITS, "WEEKS", "UNITS", "WEEK ONE", "WEEK SIX", {"index": 3, "text": "YOUR BEST WEEK"},
          ["These numbers are placeholders."])
    .end(3, "ORDER NOW", "ADD YOUR SHOP LINK", ["Order now."]))
add_candidate("product_beta_open", "11.5 s beta opening: feedback by voice note, release marked as next.",
    {**P, "slots": ["feedback channel", "signup link"], "public_release": "not shipped"},
    Film("Beta open", bpm=112, key="D", brand="BETA", hud="BETA // FEEDBACK")
    .title(2.5, "BETA", "OPEN FOR TESTERS", vo=["The beta is open."])
    .voice(6, "TESTER", ["Send your feedback as a note."],
           [("VOICE NOTES — OPEN", "live"), ("PUBLIC RELEASE — NEXT", "next")], "REPLACE WITH YOUR CHANNEL",
           vo=["Send your feedback as a voice note.", "A public release comes next."])
    .end(3, "JOIN THE BETA", "ADD YOUR SIGNUP LINK", ["Join the beta."]))

# ---------------------------------------------------------------- teaser
add_candidate("teaser_documentary", "10 s slow documentary teaser: a logline and a date slot.",
    {**P, "slots": ["logline", "release date"]},
    Film("Doc teaser", bpm=88, key="E", brand="FILM")
    .title(3.5, "COMING", "A FILM ABOUT ...", "$ add your logline here", vo=["A film about your subject."])
    .sticker(3.5, "noto_eyes", "WATCH THIS", "ADD YOUR DATE", ["Watch this space."])
    .end(3, "SOON", "YOUR RELEASE DATE HERE", ["Soon."]))
ALBUM_MINUTES = [3, 5, 4, 7, 6, 9, 8, 11, 10, 13]
add_candidate("teaser_album_drop", "13 s fast music teaser: track lengths, then a pre-save.",
    {**P, "minutes_per_track": ALBUM_MINUTES, "slots": ["record title", "pre-save link", "drop date"]},
    Film("Album teaser", bpm=150, key="A", brand="ALBUM")
    .sticker(3, "noto_multiple_musical_notes", "NEW RECORD", "ADD YOUR TITLE", ["A new record."])
    .bars(4.5, ALBUM_MINUTES, "TRACKS", "MINUTES", "TRACK ONE", "TRACK TEN", None,
          ["Put your real lengths here."])
    .sticker(3, "noto_guitar", "PRE-SAVE", "ADD YOUR LINK", ["Pre-save it now."])
    .end(2.5, "DROP DAY", "YOUR DATE HERE", ["Drop day."]))
add_candidate("teaser_feature_drop", "11 s feature teaser: three hints drawn with catalog animations.",
    {**P, "slots": ["three hints", "date"]},
    Film("Feature teaser", bpm=138, key="D", brand="STUDIO")
    .title(2.5, "ALMOST", "ONE MORE THING", vo=["Almost there."])
    .cards(5.5, "THREE HINTS", [("HINT ONE", "your first clue", "lottie:noto_eyes", "Your first clue."),
                                ("HINT TWO", "your second clue", "lottie:noto_thinking_face", "Your second clue."),
                                ("HINT THREE", "your third clue", "lottie:noto_electric_light_bulb", "And your third.")])
    .sticker(3, "noto_hourglass_with_flowing_sand", "NOT YET", "ADD YOUR DATE", ["Not yet."]))
REBRAND_CELLS = 5200
add_candidate("teaser_rebrand", "11.5 s rebrand teaser: same team, new name, full grid.",
    {**P, "grid_cells": REBRAND_CELLS, "slots": ["new name", "new tagline"]},
    Film("Rebrand teaser", bpm=108, key="G", brand="BRAND")
    .grid(5, REBRAND_CELLS, "SAME TEAM", "REPLACE WITH YOUR OWN NUMBER", "NEW NAME",
          "YOUR NEW BRAND LINE HERE", vo=["Same team, new name."])
    .sticker(3, "noto_sparkles", "NEW LOOK", "SAME PEOPLE", ["A new look."])
    .logo(3.5, "NEW NAME", "ADD YOUR NEW TAGLINE", ["Your new name."]))

# ---------------------------------------------------------------- mini story
add_candidate("story_side_project", "13 s four-beat story: itch, try, snag, fix.",
    {**P, "slots": ["the itch", "what you built", "what broke", "what you learned"]},
    Film("Side project", bpm=118, key="F", brand="STORY")
    .title(3, "IT BEGAN", "WITH ONE IDEA", vo=["It began with one idea."])
    .cards(7, "HOW IT WENT", [("THE ITCH", "what bothered you", "lottie:noto_thinking_face", "Something bothered you."),
                              ("THE TRY", "what you built", "code", "So you built something."),
                              ("THE SNAG", "what broke", "lottie:noto_collision_symbol", "Then it broke."),
                              ("THE FIX", "what you learned", "lottie:noto_electric_light_bulb", "And you learned why.")])
    .end(3, "STILL GOING", "ADD YOUR NEXT STEP", ["Still going."]))
CUSTOMER_COUNT = 2400
add_candidate("story_first_customer", "11.5 s story: the first customer, then where the count is now.",
    {**P, "your_count_now": CUSTOMER_COUNT, "slots": ["first customer", "next goal"]},
    Film("First customer", bpm=124, key="C", brand="STORY")
    .sticker(3, "noto_person_with_folded_hands", "THE FIRST ONE", "YOUR FIRST CUSTOMER", ["The first one."])
    .grid(5.5, CUSTOMER_COUNT, "YOUR COUNT NOW", "REPLACE WITH YOUR REAL NUMBER", "FROM ONE",
          "ONE CUSTOMER BECAME YOUR COUNT", vo=["Replace this with your real number."])
    .end(3, "KEEP SELLING", "ADD YOUR NEXT GOAL", ["Keep going."]))
add_candidate("story_bug_hunt", "13.5 s bug story: report, reproduction, cause, fix with a test.",
    {**P, "slots": ["the report", "the reproduction", "the cause", "the test name"]},
    Film("Bug hunt", bpm=142, key="B", brand="STORY", hud="STORY // BUG HUNT")
    .title(2.5, "ONE BUG", "A SHORT HUNT", vo=["One bug."])
    .cards(6, "THE HUNT", [("THE REPORT", "what users saw", "lottie:noto_warning_sign", "A report came in."),
                           ("THE REPRO", "how you saw it too", "cursor", "You reproduced it."),
                           ("THE CAUSE", "what was really wrong", "lottie:noto_brain", "You found the cause.")])
    .sticker(2.5, "noto_white_heavy_check_mark", "FIXED", "ADD YOUR TEST NAME", ["Fixed, with a test."])
    .end(2.5, "NEXT ONE", "BUGS ARE NEVER DONE", ["On to the next."]))
WORKSHOP_PEOPLE = [2, 6, 9, 14, 11, 7, 4, 3]
add_candidate("story_workshop_day", "16 s workshop diary: attendance through the day, then three parts.",
    {**P, "people_per_hour": WORKSHOP_PEOPLE, "slots": ["three parts", "next date"]},
    Film("Workshop day", bpm=120, key="D", brand="WORKSHOP")
    .title(2.5, "ONE DAY", "A WORKSHOP DIARY", vo=["One day, start to finish."])
    .bars(5, WORKSHOP_PEOPLE, "HOURS", "PEOPLE", "MORNING", "EVENING", {"index": 3, "text": "YOUR BUSIEST HOUR"},
          ["These hours are placeholders."])
    .cards(5.5, "THE THREE PARTS", [("WARM UP", "how you opened", "lottie:noto_hot_beverage", "You opened warm."),
                                    ("THE CORE", "the main exercise", "agents", "Then the main part."),
                                    ("WRAP UP", "how you closed", "lottie:noto_clapping_hands_sign", "And you closed it.")])
    .logo(3, "WORKSHOP", "ADD YOUR NEXT DATE", ["Same again soon."]))

# ---------------------------------------------------------------- progress and timeline
BUILDLOG_COMMITS = [3, 7, 5, 12, 9, 2, 0, 14, 11, 8, 6, 15, 13, 4, 1, 10, 17, 12, 9, 7,
                    3, 21, 16, 11, 8, 5, 13, 19, 14, 6]
add_candidate("progress_build_log", "14 s build log: thirty days of work, then what the shape shows.",
    {**P, "commits_per_day": BUILDLOG_COMMITS, "slots": ["your own daily counts"]},
    Film("Build log", bpm=126, key="A", brand="BUILDLOG")
    .bars(6, BUILDLOG_COMMITS, "DAYS", "COMMITS", "DAY ONE", "DAY THIRTY", {"index": 21, "text": "YOUR BEST DAY"},
          ["Put your own daily numbers here.", "The tallest bar is yours to set."])
    .cards(5, "WHAT IT SHOWS", [("RHYTHM", "your working pattern", "chart", "Your rhythm."),
                                ("GAPS", "your quiet days", "memory", "Your quiet days."),
                                ("PEAKS", "your big pushes", "bolt", "Your big pushes.")])
    .end(3, "STILL LOGGING", "ADD YOUR NEXT WEEK", ["Still logging."]))
TRAINING_MINUTES = 1800
TRAINING_KM = [12, 15, 18, 16, 22, 25, 21, 28, 31, 27, 34, 38, 30, 42]
add_candidate("progress_marathon_training", "12.5 s training block: total minutes, then weekly distance.",
    {**P, "your_minutes": TRAINING_MINUTES, "km_per_week": TRAINING_KM, "slots": ["race date"]},
    Film("Training block", bpm=146, key="G", brand="TRAINING")
    .grid(4.5, TRAINING_MINUTES, "YOUR MINUTES", "REPLACE WITH YOUR OWN TOTAL",
          vo=["Replace this with your own total."])
    .bars(5, TRAINING_KM, "WEEKS", "KM", "WEEK ONE", "LAST WEEK", {"index": 13, "text": "YOUR LONGEST WEEK"},
          ["Your own distances go here."])
    .end(3, "RACE WEEK", "ADD YOUR RACE DATE", ["Race week next."]))
RENOVATION_HOURS = 2600
add_candidate("progress_renovation", "16 s slow renovation update: stages, hours logged, what is left.",
    {**P, "your_hours": RENOVATION_HOURS, "slots": ["three stages", "finish date"]},
    Film("Renovation", bpm=100, key="F", brand="PROJECT")
    .title(2.5, "PROGRESS", "ROOM BY ROOM", vo=["Room by room."])
    .cards(6, "THE STAGES", [("STRIP OUT", "what came out", "cursor", "First it came out."),
                             ("REBUILD", "what went in", "code", "Then it went in."),
                             ("FINISH", "what is left", "lottie:noto_sparkles", "Then the finish.")])
    .grid(5, RENOVATION_HOURS, "YOUR HOURS", "REPLACE WITH YOUR REAL HOURS", "SO FAR",
          "YOUR REMAINING WORK HERE", vo=["Your own hours go here."])
    .end(2.5, "NEARLY THERE", "ADD YOUR FINISH DATE", ["Nearly there."]))
add_candidate("timeline_company_history", "14.5 s past timeline: four years from your own records.",
    {**P, "slots": ["four years", "founding line"]},
    Film("History", bpm=114, key="C", brand="COMPANY")
    .roadmap(8, "YOUR OWN TIMELINE", [("ONE", "YEAR ONE", ["what you started"], "Year one."),
                                      ("TWO", "YEAR TWO", ["what you added"], "Year two."),
                                      ("THREE", "YEAR THREE", ["what you learned"], "Year three."),
                                      ("NOW", "TODAY", ["where you are"], "And today.")],
             "FILL FROM YOUR OWN RECORDS, NOT GUESSES")
    .sticker(3, "noto_hourglass", "SO FAR", "ADD YOUR OWN YEARS", ["So far."])
    .logo(3.5, "COMPANY", "ADD YOUR FOUNDING LINE", ["Your company."]))
add_candidate("timeline_release_train", "13.5 s forward plan: next, then, maybe - dates can move.",
    {**P, "slots": ["next release", "the one after", "a maybe"]},
    Film("Release train", bpm=130, key="D", brand="RELEASES")
    .title(2.5, "WHAT NEXT", "YOUR RELEASE PLAN", vo=["Your release plan."])
    .roadmap(8, "PLANNED, NOT PROMISED", [("NEXT", "SOON", ["your near work"], "The next one."),
                                          ("THEN", "LATER", ["your following work"], "Then the one after."),
                                          ("MAYBE", "UNSET", ["only if it fits"], "And maybe this.")],
             "TARGETS ONLY, DATES CAN MOVE")
    .end(3, "NO PROMISES", "DATES MOVE, THAT IS NORMAL", ["Dates move. That is normal."]))

# ---------------------------------------------------------------- before and after
LOAD_SCORE = [38, 36, 37, 35, 22, 19, 18, 17, 16, 15]
add_candidate("before_after_load_time", "15.5 s before/after: one measured series, then what changed.",
    {**P, "score_per_step": LOAD_SCORE, "slots": ["the one change", "your measurements"]},
    Film("Load time", bpm=134, key="B", brand="BEFORE AFTER")
    .title(2.5, "BEFORE", "AND AFTER", vo=["Before, and after."])
    .bars(5, LOAD_SCORE, "STEPS", "SCORE", "BEFORE", "AFTER", {"index": 4, "text": "WHERE YOURS CHANGED"},
          ["Your own measurements go here."])
    .cards(5, "WHAT CHANGED", [("ONE CHANGE", "name your change", "code", "Name your change."),
                               ("MEASURED", "before and after", "chart", "Measure both sides.")])
    .end(3, "MEASURE IT", "NEVER GUESS THE GAIN", ["Measure it. Never guess."]))
add_candidate("before_after_workspace", "14 s before/after with two stickers around what you changed.",
    {**P, "slots": ["old setup", "new setup", "three changes"]},
    Film("Workspace", bpm=110, key="E", brand="SETUP")
    .sticker(3, "noto_collision_symbol", "BEFORE", "YOUR OLD SETUP", ["Before."])
    .cards(5.5, "WHAT YOU CHANGED", [("CLEARED", "what you removed", "memory", "You removed things."),
                                     ("ADDED", "what you added", "bolt", "You added things."),
                                     ("KEPT", "what still works", "shield", "You kept what works.")])
    .sticker(3, "noto_sparkles", "AFTER", "YOUR NEW SETUP", ["After."])
    .end(2.5, "YOUR TURN", "ADD YOUR OWN PHOTOS", ["Your turn."]))
INVOICE_MINUTES = 3200
add_candidate("before_after_invoice_flow", "14.5 s process change: the old way, then your measured saving.",
    {**P, "your_minutes": INVOICE_MINUTES, "slots": ["three old-way problems", "measured saving"]},
    Film("Invoice flow", bpm=118, key="F", brand="PROCESS")
    .cards(6, "THE OLD WAY", [("BY HAND", "how it used to go", "cursor", "It went by hand."),
                              ("WAITING", "where it got stuck", "lottie:noto_hourglass", "And it got stuck."),
                              ("ERRORS", "what went wrong", "lottie:noto_cross_mark", "Things went wrong.")])
    .grid(5.5, INVOICE_MINUTES, "YOUR MINUTES", "MINUTES YOUR OWN LOG SHOWS", "THE NEW WAY",
          "PUT YOUR MEASURED SAVING HERE", vo=["Put your measured saving here."])
    .end(3, "MEASURE BOTH", "BEFORE AND AFTER, SAME TEST", ["Measure both sides, same test."]))
BATTERY_HOURS = [4, 4, 5, 4, 5, 9, 10, 9, 11, 10, 11, 12]
add_candidate("before_after_battery_life", "11 s two-run comparison with an honest conditional caption.",
    {**P, "hours_per_test": BATTERY_HOURS, "slots": ["device", "test conditions"]},
    Film("Battery", bpm=124, key="G", brand="DEVICE")
    .bars(5.5, BATTERY_HOURS, "TESTS", "HOURS", "OLD", "NEW", {"index": 5, "text": "YOUR OWN SPLIT"},
          ["Two runs, your own numbers."])
    .sticker(3, "noto_high_voltage_sign", "LONGER", "ONLY IF YOURS IS", ["Longer, only if yours is."])
    .end(2.5, "SHOW THE TEST", "SAME DEVICE, SAME LOAD", ["Show the test."]))

# ---------------------------------------------------------------- comparison
add_candidate("compare_plans_vs", "13.5 s five-way comparison: the widest card row the engine draws.",
    {**P, "slots": ["five options", "your criteria"]},
    Film("Plans", bpm=120, key="C", brand="PLANS")
    .title(3, "COMPARE", "FIVE OPTIONS", vo=["Five options."])
    .cards(7.5, "SIDE BY SIDE", [("PLAN A", "your first option", "chart", "Plan A."),
                               ("PLAN B", "your second option", "memory", "Plan B."),
                               ("PLAN C", "your third option", "bolt", "Plan C."),
                               ("PLAN D", "your fourth option", "shield", "Plan D."),
                               ("PLAN E", "your fifth option", "agents", "Plan E.")])
    .end(3, "PICK ONE", "ADD YOUR OWN CRITERIA", ["Pick one."]))
MANUAL_MINUTES = [30, 28, 31, 29, 30, 12, 11, 13, 12, 11, 12, 10]
add_candidate("compare_manual_vs_automated", "15.5 s manual versus automated, with both costs named.",
    {**P, "minutes_per_run": MANUAL_MINUTES, "slots": ["setup cost", "run cost"]},
    Film("Manual vs auto", bpm=128, key="A", brand="COMPARE")
    .title(2.5, "BY HAND", "OR AUTOMATED", vo=["By hand, or automated."])
    .bars(5, MANUAL_MINUTES, "RUNS", "MINUTES", "BY HAND", "AUTOMATED", {"index": 5, "text": "WHERE YOURS SPLITS"},
          ["Both sides, your own timings."])
    .cards(5, "WHAT TO WEIGH", [("SETUP COST", "what it costs once", "memory", "Setup costs once."),
                                ("RUN COST", "what it costs each time", "bolt", "Running costs every time.")])
    .logo(3, "COMPARE", "MEASURE BEFORE YOU SWITCH", ["Measure before you switch."]))
add_candidate("compare_two_routes", "15 s two options framed by flag stickers, three axes between them.",
    {**P, "slots": ["route one", "route two", "why you chose"]},
    Film("Two routes", bpm=112, key="D", brand="ROUTES")
    .sticker(3.5, "noto_triangular_flag_on_post", "ROUTE ONE", "YOUR FIRST OPTION", ["Route one."])
    .cards(6, "WHAT DIFFERS", [("TIME", "how long each takes", "lottie:noto_alarm_clock", "Time taken."),
                                 ("COST", "what each costs", "lottie:noto_money_with_wings", "Cost."),
                                 ("RISK", "what can go wrong", "shield", "And risk.")])
    .sticker(3, "noto_chequered_flag", "ROUTE TWO", "YOUR SECOND OPTION", ["Route two."])
    .end(2.5, "YOUR CALL", "WRITE DOWN WHY YOU CHOSE", ["Your call."]))
add_candidate("compare_old_new_version", "15.5 s old versus new, with the next column marked as a plan.",
    {**P, "slots": ["kept", "changed", "removed", "changelog link"]},
    Film("Old and new", bpm=122, key="E", brand="VERSIONS")
    .cards(5.5, "WHAT MOVED", [("KEPT", "what did not change", "shield", "What stayed."),
                               ("CHANGED", "what is different now", "cursor", "What changed."),
                               ("GONE", "what you removed", "lottie:noto_cross_mark", "And what went.")])
    .roadmap(7, "WHERE IT IS GOING", [("OLD", "SHIPPED", ["your old version"], "The old one."),
                                      ("NEW", "SHIPPED", ["your new version"], "The new one."),
                                      ("NEXT", "PLANNED", ["not built yet"], "And the next one.")],
             "THE LAST COLUMN IS A PLAN, NOT A FACT")
    .end(3, "READ THE NOTES", "ADD YOUR CHANGELOG LINK", ["Read the notes."]))

# ---------------------------------------------------------------- step explanation
add_candidate("steps_onboarding", "12 s first-run walkthrough in four numbered cards.",
    {**P, "slots": ["four steps", "help link"]},
    Film("Onboarding", bpm=120, key="G", brand="SETUP", hud="SETUP // FIRST RUN")
    .title(2.5, "FIRST RUN", "FOUR STEPS", vo=["Four steps."])
    .cards(6.5, "GETTING STARTED", [("ONE ACCOUNT", "create it", "cursor", "First, an account."),
                                    ("TWO CONNECT", "link your data", "memory", "Then connect your data."),
                                    ("THREE CHECK", "see it worked", "shield", "Check it worked."),
                                    ("FOUR GO", "start using it", "bolt", "Then go.")])
    .sticker(3, "noto_white_heavy_check_mark", "READY", "ADD YOUR HELP LINK", ["Ready."]))
add_candidate("steps_recipe", "13 s five-step method with the timings left to the owner.",
    {**P, "slots": ["dish", "five steps", "your own times"]},
    Film("Recipe", bpm=106, key="F", brand="KITCHEN")
    .title(2.5, "FIVE STEPS", "YOUR DISH HERE", vo=["Five steps."])
    .cards(7.5, "HOW TO MAKE IT", [("PREP", "your ingredients", "memory", "Prep first."),
                                   ("HEAT", "your pan or oven", "lottie:noto_fire", "Then heat."),
                                   ("COOK", "your main step", "lottie:noto_hot_beverage", "Then cook."),
                                   ("REST", "let it settle", "lottie:noto_hourglass", "Let it rest."),
                                   ("SERVE", "plate it up", "lottie:noto_clinking_glasses", "Then serve.")])
    .end(3, "ADD YOUR TIMES", "COOKING TIMES ARE YOURS", ["Add your own times."]))
BACKUP_FILES = 4200
add_candidate("steps_backup_routine", "14.5 s backup routine ending on the restore test, not the copy.",
    {**P, "your_files": BACKUP_FILES, "slots": ["where copies go", "rotation"]},
    Film("Backups", bpm=96, key="E", brand="BACKUP")
    .cards(6, "THE ROUTINE", [("COPY", "where it goes", "memory", "Copy it."),
                              ("VERIFY", "read it back", "shield", "Verify it."),
                              ("ROTATE", "keep more than one", "lottie:noto_hourglass", "Rotate copies.")])
    .grid(5, BACKUP_FILES, "YOUR FILES", "REPLACE WITH YOUR REAL COUNT", "RESTORE TEST",
          "AN UNTESTED BACKUP IS NOT ONE", vo=["An untested backup is not a backup."])
    .logo(3.5, "BACKUP", "TEST THE RESTORE, NOT THE COPY", ["Test the restore."]))
add_candidate("steps_bug_report", "12.5 s support explainer: the four things a useful report carries.",
    {**P, "slots": ["tracker link"]},
    Film("Bug report", bpm=132, key="B", brand="SUPPORT")
    .title(3, "REPORT IT", "FOUR THINGS TO SEND", vo=["Four things to send."])
    .cards(6.5, "WHAT HELPS MOST", [("WHAT YOU DID", "the exact steps", "cursor", "The exact steps."),
                                    ("WHAT HAPPENED", "what you saw", "lottie:noto_warning_sign", "What you saw."),
                                    ("WHAT YOU WANT", "what you expected", "lottie:noto_thinking_face", "What you expected."),
                                    ("YOUR SETUP", "version and system", "code", "And your setup.")])
    .end(3, "SEND IT", "ADD YOUR TRACKER LINK", ["Send it."]))
add_candidate("steps_setup_printer", "13.5 s troubleshooting card: three checks, one change at a time.",
    {**P, "slots": ["support link"]},
    Film("Printer setup", bpm=114, key="C", brand="SETUP")
    .sticker(2.5, "noto_warning_sign", "NOT WORKING", "THE USUAL CAUSE", ["Not working."])
    .cards(6, "THREE CHECKS", [("POWER", "is it really on", "bolt", "Is it on."),
                               ("NETWORK", "same network as you", "memory", "Same network."),
                               ("DRIVER", "the right one", "code", "The right driver.")])
    .sticker(2.5, "noto_ok_hand_sign", "TRY AGAIN", "ONE CHANGE AT A TIME", ["Try again."])
    .end(2.5, "STILL STUCK", "ADD YOUR SUPPORT LINK", ["Still stuck? Ask."]))

# ---------------------------------------------------------------- fact cards
add_candidate("facts_energy_saving", "12.5 s fact card set that points at the owner's own bill.",
    {**P, "slots": ["four readings from your bill"]},
    Film("Energy", bpm=108, key="F", brand="FACTS")
    .title(2.5, "FOUR FACTS", "FROM YOUR OWN BILL", vo=["Four facts from your own bill."])
    .cards(7, "CHECK THESE FOUR", [("STANDBY", "your idle draw", "bolt", "Your idle draw."),
                                   ("LIGHTING", "your fitting count", "lottie:noto_electric_light_bulb", "Your lighting."),
                                   ("HEATING", "your set point", "lottie:noto_fire", "Your heating."),
                                   ("HOT WATER", "your usage", "lottie:noto_hot_beverage", "And hot water.")])
    .end(3, "READ YOUR BILL", "NUMBERS COME FROM YOUR METER", ["Read your own bill."]))
add_candidate("facts_sleep_habits", "12.5 s very slow fact set: three things to log before concluding.",
    {**P, "slots": ["three log fields"]},
    Film("Sleep", bpm=84, key="E", brand="FACTS")
    .sticker(3.5, "noto_hourglass", "YOUR SLEEP", "TRACK IT FIRST", ["Track it first."])
    .cards(6, "THREE THINGS TO LOG", [("TIME IN BED", "your own hours", "lottie:noto_alarm_clock", "Hours in bed."),
                                      ("WAKE UPS", "how many", "lottie:noto_eyes", "How often you woke."),
                                      ("HOW YOU FELT", "your own score", "lottie:noto_thinking_face", "And how you felt.")])
    .end(3, "LOG A WEEK", "THEN READ YOUR OWN DATA", ["Log a week first."]))
SHELF_ITEMS = 3400
add_candidate("facts_library_stats", "13.5 s collection count, then three ways to slice it.",
    {**P, "your_items": SHELF_ITEMS, "slots": ["three slices"]},
    Film("Collection", bpm=116, key="D", brand="FACTS")
    .grid(5, SHELF_ITEMS, "YOUR ITEMS", "COUNT YOUR OWN SHELF", "YOUR SHELF",
          "REPLACE EVERY NUMBER WITH YOURS", vo=["Count your own shelf."])
    .cards(5.5, "THREE SLICES", [("BY YEAR", "your own split", "chart", "By year."),
                                 ("BY KIND", "your own split", "memory", "By kind."),
                                 ("UNREAD", "your honest count", "lottie:noto_eyes", "And unread.")])
    .end(3, "COUNT IT", "GUESSES ARE NOT FACTS", ["Guesses are not facts."]))
WATER_LITRES = [90, 85, 92, 98, 120, 145, 160, 155, 130, 105, 95, 88]
add_candidate("facts_water_use", "15 s meter-first fact set: twelve months, then where it goes.",
    {**P, "litres_per_month": WATER_LITRES, "slots": ["inside share", "outside share"]},
    Film("Water", bpm=102, key="G", brand="FACTS")
    .title(2.5, "YOUR USE", "READ YOUR OWN METER", vo=["Read your own meter."])
    .bars(5, WATER_LITRES, "MONTHS", "LITRES", "JAN", "DEC", {"index": 6, "text": "YOUR OWN PEAK"},
          ["Twelve months, your own meter."])
    .cards(5, "WHERE IT GOES", [("INSIDE", "your own share", "memory", "Inside."),
                                ("OUTSIDE", "your own share", "lottie:noto_seedling", "Outside.")])
    .end(2.5, "METER FIRST", "NO METER, NO NUMBER", ["Meter first."]))
add_candidate("facts_recycling", "13 s four-bin explainer that says the rules vary locally.",
    {**P, "slots": ["four local rules"]},
    Film("Recycling", bpm=120, key="C", brand="FACTS")
    .sticker(3, "noto_earth_globe_europe_africa", "YOUR STREAM", "CHECK YOUR OWN RULES",
             ["Check your own local rules."])
    .cards(7, "FOUR BINS", [("PAPER", "your local rule", "memory", "Paper."),
                            ("GLASS", "your local rule", "lottie:noto_clinking_glasses", "Glass."),
                            ("PLASTIC", "your local rule", "lottie:noto_collision_symbol", "Plastic."),
                            ("THE REST", "your local rule", "lottie:noto_cross_mark", "And the rest.")])
    .sticker(3, "noto_four_leaf_clover", "RULES VARY", "BY TOWN, BY YEAR", ["Rules vary by town."]))

# ---------------------------------------------------------------- journey
TRIP_KM = [220, 180, 340, 120, 400, 260, 150, 310]
add_candidate("journey_road_trip", "16 s road trip: four legs, then distance per day.",
    {**P, "km_per_day": TRIP_KM, "slots": ["four stops", "map link"]},
    Film("Road trip", bpm=128, key="A", brand="TRIP")
    .title(2.5, "THE ROUTE", "YOUR OWN STOPS", vo=["Your own route."])
    .cards(6.5, "FOUR STOPS", [("FIRST LEG", "your drive time", "plane", "The first leg."),
                               ("A BREAK", "where you stop", "lottie:noto_hot_beverage", "A break."),
                               ("A DETOUR", "your extra stop", "lottie:noto_fox_face", "A detour."),
                               ("LAST LEG", "your final drive", "lottie:noto_chequered_flag", "And the last leg.")])
    .bars(4.5, TRIP_KM, "DAYS", "KM", "DAY ONE", "LAST DAY", None, ["Your own distances."])
    .end(2.5, "PLAN YOURS", "ADD YOUR OWN MAP LINK", ["Plan yours."]))
TRAIL_METRES = [180, 220, 260, 240, 300, 360, 410, 390, 450, 520,
                580, 640, 700, 760, 820, 900, 860, 780, 700, 620]
add_candidate("journey_hiking_trail", "14 s trail profile between two stickers, conditions caveat at the end.",
    {**P, "metres_per_point": TRAIL_METRES, "slots": ["route name", "your own time"]},
    Film("Trail", bpm=118, key="E", brand="TRAIL")
    .sticker(3, "noto_seedling", "THE TRAIL", "YOUR OWN ROUTE", ["Your own route."])
    .bars(5.5, TRAIL_METRES, "POINTS", "METRES", "START", "SUMMIT", {"index": 15, "text": "YOUR HIGH POINT"},
          ["Your own elevation profile."])
    .sticker(3, "noto_glowing_star", "THE TOP", "ADD YOUR OWN TIME", ["The top."])
    .end(2.5, "WALK YOURS", "CONDITIONS CHANGE, CHECK THEM", ["Check conditions first."]))
add_candidate("journey_relocation", "17.5 s move plan in four phases, plus two things people forget.",
    {**P, "slots": ["four phases", "who to tell"]},
    Film("Moving", bpm=110, key="F", brand="MOVE")
    .title(2.5, "MOVING", "YOUR OWN TIMELINE", vo=["Your own timeline."])
    .roadmap(7.5, "THE FOUR PHASES", [("ONE", "BEFORE", ["paperwork first"], "Paperwork first."),
                                      ("TWO", "PACKING", ["what goes, what not"], "Then packing."),
                                      ("THREE", "THE MOVE", ["one day, many parts"], "Then the move."),
                                      ("FOUR", "AFTER", ["settling in"], "Then settling in.")],
             "YOUR DATES, NOT A GUARANTEE")
    .cards(5, "TWO EASY MISSES", [("ADDRESS", "who needs telling", "plane", "Who to tell."),
                                  ("KEYS", "old and new", "shield", "Keys, old and new.")])
    .end(2.5, "YOUR LIST", "EVERY MOVE IS DIFFERENT", ["Every move differs."]))
STUDY_MINUTES = 4400
STUDY_SESSIONS = [8, 12, 10, 15, 14, 18, 16, 20, 19, 22, 21, 26]
add_candidate("journey_language_year", "13 s year of study: total minutes, then monthly sessions.",
    {**P, "your_minutes": STUDY_MINUTES, "sessions_per_month": STUDY_SESSIONS, "slots": ["next target"]},
    Film("A year of", bpm=122, key="D", brand="STUDY")
    .grid(5, STUDY_MINUTES, "YOUR MINUTES", "SUM YOUR OWN LOG", "A YEAR IN",
          "REPLACE WITH YOUR REAL TOTAL", vo=["Sum your own log."])
    .bars(5, STUDY_SESSIONS, "MONTHS", "SESSIONS", "MONTH ONE", "MONTH TWELVE",
          {"index": 11, "text": "YOUR BEST MONTH"}, ["Your own monthly counts."])
    .sticker(3, "noto_brain", "KEEP GOING", "ADD YOUR NEXT TARGET", ["Keep going."]))

# ---------------------------------------------------------------- invitation
add_candidate("invite_open_house", "11.5 s plain invitation: when, where, what for.",
    {**P, "slots": ["date and time", "address", "reason", "rsvp link"]},
    Film("Open house", bpm=116, key="C", brand="INVITE")
    .title(3, "YOU ARE", "INVITED", vo=["You are invited."])
    .cards(5.5, "THE DETAILS", [("WHEN", "your date and time", "lottie:noto_alarm_clock", "When."),
                                ("WHERE", "your address", "plane", "Where."),
                                ("WHAT FOR", "your reason", "lottie:noto_party_popper", "And what for.")])
    .end(3, "COME ALONG", "ADD YOUR RSVP LINK", ["Come along."]))
add_candidate("invite_workshop", "12.5 s workshop invitation: four things an attendee gets.",
    {**P, "slots": ["topic", "group cap", "date and place"]},
    Film("Workshop invite", bpm=124, key="G", brand="INVITE")
    .sticker(3, "noto_cheering_megaphone", "A WORKSHOP", "ADD YOUR TOPIC", ["A workshop."])
    .cards(6.5, "WHAT YOU GET", [("HANDS ON", "you build it", "code", "You build it."),
                                 ("SMALL GROUP", "your own cap", "agents", "A small group."),
                                 ("TAKE HOME", "what you keep", "memory", "You keep the work."),
                                 ("NO SLIDES", "or however yours runs", "play", "However yours runs.")])
    .logo(3, "INVITE", "ADD YOUR DATE AND PLACE", ["Come and build."]))
HACK_TEAMS = [2, 5, 8, 11, 14, 16, 15, 13, 12, 9, 6, 4]
add_candidate("invite_hackathon", "15.5 s fast build-day invite with a schedule chart and the rules.",
    {**P, "teams_per_hour": HACK_TEAMS, "slots": ["theme", "time box", "form link"]},
    Film("Hackathon", bpm=148, key="B", brand="HACK")
    .title(2.5, "BUILD DAY", "YOUR OWN EVENT", "$ add your theme here", vo=["One build day."])
    .bars(4.5, HACK_TEAMS, "HOURS", "TEAMS", "HOUR ONE", "LAST HOUR", None, ["Your own schedule here."])
    .cards(5.5, "THE RULES", [("YOUR THEME", "set it yourself", "bolt", "Set your theme."),
                              ("YOUR TIME BOX", "set it yourself", "lottie:noto_alarm_clock", "Set your time box."),
                              ("YOUR DEMO", "how you show it", "play", "Then you demo.")])
    .end(3, "SIGN UP", "ADD YOUR OWN FORM LINK", ["Sign up."]))
add_candidate("invite_book_club", "11.5 s invitation answered by voice note rather than a form.",
    {**P, "slots": ["book title", "group channel"], "your_date": "not set"},
    Film("Book club", bpm=100, key="E", brand="CLUB", hud="CLUB // INVITE")
    .sticker(3, "noto_hot_beverage", "BOOK CLUB", "ADD YOUR TITLE", ["A book club."])
    .voice(6, "CLUB", ["Reply with a voice note."],
           [("REPLIES — OPEN", "live"), ("YOUR DATE — NEXT", "next")], "REPLACE WITH YOUR GROUP",
           vo=["Reply with a voice note.", "Your date comes next."])
    .end(2.5, "JOIN IN", "ADD YOUR OWN CHANNEL", ["Join in."]))

# ---------------------------------------------------------------- thanks
VOLUNTEER_HOURS = 2800
add_candidate("thanks_volunteers", "11 s thank-you built on hours from your own sign-in sheet.",
    {**P, "your_hours": VOLUNTEER_HOURS, "slots": ["team name", "next date"]},
    Film("Volunteers", bpm=112, key="F", brand="THANKS")
    .sticker(3, "noto_clapping_hands_sign", "THANK YOU", "YOUR OWN TEAM", ["Thank you."])
    .grid(5, VOLUNTEER_HOURS, "YOUR HOURS", "SUM YOUR OWN SIGN IN SHEET", "TOGETHER",
          "REPLACE WITH YOUR REAL HOURS", vo=["Sum your own sign in sheet."])
    .end(3, "AGAIN SOON", "ADD YOUR NEXT DATE", ["Again soon."]))
add_candidate("thanks_beta_group", "13.5 s thank-you to testers: what they gave, then what is next.",
    {**P, "slots": ["three contributions", "your update"]},
    Film("Beta thanks", bpm=120, key="D", brand="THANKS")
    .title(2.5, "THANK YOU", "TO THE TESTERS", vo=["Thank you, testers."])
    .cards(5.5, "WHAT YOU GAVE US", [("REPORTS", "your findings", "lottie:noto_warning_sign", "Your reports."),
                                     ("IDEAS", "your suggestions", "lottie:noto_electric_light_bulb", "Your ideas."),
                                     ("PATIENCE", "through the rough bits", "lottie:noto_person_with_folded_hands",
                                      "And your patience.")])
    .sticker(3, "noto_purple_heart", "REALLY", "THANK YOU", ["Really, thank you."])
    .end(2.5, "WHAT NEXT", "ADD YOUR OWN UPDATE", ["Here is what is next."]))
add_candidate("thanks_mentor", "12.5 s slow personal thank-you in three specific beats.",
    {**P, "slots": ["their name", "closing line"]},
    Film("Thank you", bpm=94, key="E", brand="THANKS")
    .title(3, "THANK YOU", "ADD THEIR NAME", vo=["Thank you."])
    .cards(6, "THREE THINGS", [("YOU LISTENED", "when it mattered", "mic", "You listened."),
                               ("YOU ASKED", "the hard question", "lottie:noto_thinking_face", "You asked the hard one."),
                               ("YOU STAYED", "through the long bit", "shield", "And you stayed.")])
    .logo(3.5, "THANK YOU", "ADD YOUR OWN CLOSING LINE", ["Thank you, really."]))
YEAR_ORDERS = [40, 55, 48, 62, 70, 66, 81, 77, 90, 85, 96, 110]
add_candidate("thanks_customers_year", "12 s year thank-you led by a twelve-month chart.",
    {**P, "orders_per_month": YEAR_ORDERS, "slots": ["next year plan"]},
    Film("A year", bpm=126, key="C", brand="THANKS")
    .bars(6, YEAR_ORDERS, "MONTHS", "ORDERS", "MONTH ONE", "MONTH TWELVE",
          {"index": 11, "text": "YOUR OWN BEST"}, ["Your own monthly numbers."])
    .sticker(3.5, "noto_heavy_black_heart", "THANK YOU", "FOR A WHOLE YEAR", ["Thank you for the year."])
    .end(2.5, "SAME AGAIN", "ADD YOUR NEXT YEAR PLAN", ["Same again next year."]))

# ---------------------------------------------------------------- other formats
QUARTER_UNITS = [12, 18, 15, 22, 19, 25, 21, 28, 24, 31, 27, 34, 30]
add_candidate("recap_quarter_review", "23 s five-scene quarter review: data, readings, then aims.",
    {**P, "units_per_week": QUARTER_UNITS, "slots": ["three readings", "three aims", "evidence link"]},
    Film("Quarter", bpm=124, key="D", brand="REVIEW")
    .title(2.5, "A QUARTER", "YOUR OWN REVIEW", vo=["Your own quarter."])
    .bars(5, QUARTER_UNITS, "WEEKS", "YOUR UNIT", "WEEK ONE", "LAST WEEK",
          {"index": 12, "text": "YOUR LAST WEEK"}, ["Your own weekly numbers."])
    .cards(5.5, "THREE READINGS", [("WENT WELL", "your own list", "lottie:noto_white_heavy_check_mark",
                                    "What went well."),
                                   ("WENT BADLY", "your own list", "lottie:noto_cross_mark", "What went badly."),
                                   ("UNCLEAR", "still no data", "lottie:noto_thinking_face", "And what is unclear.")])
    .roadmap(7, "NEXT QUARTER", [("ONE", "PLANNED", ["your first aim"], "First aim."),
                                 ("TWO", "PLANNED", ["your second aim"], "Second aim."),
                                 ("THREE", "MAYBE", ["only if there is room"], "And maybe a third.")],
             "AIMS FOR NEXT QUARTER, NOT RESULTS")
    .end(3, "REVIEW IT", "ADD YOUR OWN EVIDENCE", ["Review it with evidence."]))
add_candidate("alert_maintenance_window", "11.5 s planned-maintenance notice: what breaks and what does not.",
    {**P, "slots": ["window", "affected list", "status link"]},
    Film("Maintenance", bpm=104, key="E", brand="STATUS")
    .sticker(3, "noto_police_cars_revolving_light", "MAINTENANCE", "ADD YOUR WINDOW", ["Planned maintenance."])
    .cards(5.5, "WHAT TO EXPECT", [("WHEN", "your own window", "lottie:noto_alarm_clock", "When."),
                                   ("WHAT BREAKS", "your own list", "lottie:noto_warning_sign", "What will be down."),
                                   ("WHAT DOES NOT", "your own list", "shield", "And what stays up.")])
    .end(3, "STATUS PAGE", "ADD YOUR OWN STATUS LINK", ["Watch the status page."]))
FAQ_NUMBER = 5000
add_candidate("faq_single_answer", "11.5 s single-question explainer with one headline answer.",
    {**P, "your_answer": FAQ_NUMBER, "slots": ["the question", "one line answer", "faq link"]},
    Film("One question", bpm=108, key="G", brand="FAQ")
    .title(3, "ONE ASK", "THE ONE YOU GET MOST", "$ add your question here",
           vo=["The question you get most."])
    .grid(5.5, FAQ_NUMBER, "YOUR ANSWER", "PUT THE REAL NUMBER HERE", "IN SHORT",
          "ADD YOUR ONE LINE ANSWER HERE", vo=["Put the real number here."])
    .end(3, "ASK AGAIN", "ADD YOUR OWN FAQ LINK", ["Ask again any time."]))
POLL_VOTES = [18, 24, 41, 12, 9]
add_candidate("poll_results_share", "16.5 s poll result: five options, the winner, and what happens now.",
    {**P, "votes_per_option": POLL_VOTES, "slots": ["five options", "next question"]},
    Film("Poll results", bpm=130, key="A", brand="POLL")
    .title(3, "YOU VOTED", "YOUR OWN POLL", vo=["You voted."])
    .bars(5.5, POLL_VOTES, "OPTIONS", "VOTES", "OPTION ONE", "OPTION FIVE",
          {"index": 2, "text": "YOUR OWN WINNER"}, ["Your own vote counts here."])
    .cards(5, "WHAT WE DO NOW", [("THE WINNER", "what you will do", "lottie:noto_first_place_medal", "The winner."),
                                 ("THE REST", "what waits", "lottie:noto_hourglass", "And the rest waits.")])
    .logo(3, "POLL", "ADD YOUR NEXT QUESTION", ["Next question soon."]))


CANDIDATE_DATASET = "candidates/motion_animation_56.jsonl"


def main() -> None:
    out_dir = HERE
    approved, candidates, bad = [], [], []
    ids = [row[0] for row in LIB]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"duplicate scenario ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    for sid, brief, facts, film, kind in LIB:
        spec = film.spec()
        errors = spec_mod.validate(spec)
        if errors:
            bad.append((sid, errors))
            continue
        # newline="\n": the default on Windows turns every "\n" into CRLF, so a rebuild on the owner
        # PC rewrote all 44 committed LF files with identical content but different bytes.
        (out_dir / f"{sid}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8", newline="\n")
        if kind == "approved":
            approved.append({"id": f"lib_{sid}", "source": "motion-studio library (Claude-written template)",
                             "brief": brief, "facts": facts, "spec": spec})
        else:
            candidates.append({"id": f"cand_{sid}", "status": "candidate", "owner_approved": False,
                               "training_use": "excluded", "provenance": CANDIDATE_PROVENANCE,
                               "source": "motion-studio library (Claude-written candidate template)",
                               "brief": brief, "facts": facts, "spec": spec})
    if bad:
        for sid, errors in bad:
            print("INVALID", sid, *errors[:5], sep="\n  ")
        raise SystemExit(1)
    ds = ROOT / "dataset" / "brief_to_spec.jsonl"
    keep = [line for line in ds.read_text(encoding="utf-8").splitlines() if line and not json.loads(line)["id"].startswith("lib_")]
    ds.write_text("\n".join(keep + [json.dumps(r, ensure_ascii=False) for r in approved]) + "\n",
                  encoding="utf-8", newline="\n")
    # Candidates get their own file: finetune_lora.py reads only brief_to_spec.jsonl, so nothing here
    # becomes training data until the owner approves a row and moves it across deliberately.
    cand = ROOT / "dataset" / CANDIDATE_DATASET
    cand.parent.mkdir(parents=True, exist_ok=True)
    cand.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in candidates) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"LIBRARY {len(approved) + len(candidates)} scenarios valid "
          f"({len(approved)} approved, {len(candidates)} candidate); dataset rows: "
          f"{len(keep) + len(approved)} approved + {len(candidates)} candidate (not trained on)")


if __name__ == "__main__":
    main()
