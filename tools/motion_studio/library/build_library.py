"""Builds the 44-scenario library (library/*.json) and appends them to dataset/brief_to_spec.jsonl.

Each scenario = brief + facts + spec. Timings are laid out here, not by hand: item pops are
spaced by the measured Kokoro speech length (0.3 s + chars/24), scene voice-over lines follow
one another, and every spec must pass spec.validate() or the build fails. Numbers on screen
come only from `facts`; rows whose facts are illustrative carry "sample": true so a model
learns to take figures from facts, never to invent them.

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


LIB: list[tuple[str, str, dict, Film]] = []


def add(sid: str, brief: str, facts: dict, film: Film) -> None:
    LIB.append((sid, brief, facts, film))


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


def main() -> None:
    out_dir = HERE
    rows, bad = [], []
    for sid, brief, facts, film in LIB:
        spec = film.spec()
        errors = spec_mod.validate(spec)
        if errors:
            bad.append((sid, errors))
            continue
        (out_dir / f"{sid}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        rows.append({"id": f"lib_{sid}", "source": "motion-studio library (Claude-written template)", "brief": brief,
                     "facts": facts, "spec": spec})
    if bad:
        for sid, errors in bad:
            print("INVALID", sid, *errors[:5], sep="\n  ")
        raise SystemExit(1)
    ds = ROOT / "dataset" / "brief_to_spec.jsonl"
    keep = [line for line in ds.read_text(encoding="utf-8").splitlines() if line and not json.loads(line)["id"].startswith("lib_")]
    ds.write_text("\n".join(keep + [json.dumps(r, ensure_ascii=False) for r in rows]) + "\n", encoding="utf-8")
    print(f"LIBRARY {len(rows)} scenarios valid; dataset rows: {len(keep) + len(rows)}")


if __name__ == "__main__":
    main()
