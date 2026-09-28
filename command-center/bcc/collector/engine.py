"""The collection loop itself: one owner-approved page at a time, at a
respectful pace, every fact sourced.

Nothing here calls a model. The pipeline is entirely deterministic
(robots.txt → per-domain delay/cap → real-browser render → keyword/unit
extraction → provenance), which is the point: it keeps working — including
producing a sourced fact bundle — when Claude or any cloud model is
unavailable.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import config, extract
from .browser_fetch import HumanBrowserSession, PageBlocked
from .control import RunControl, Stopped
from .domain_state import DomainState
from .ledger import Ledger
from .models import CollectedFact, PageOutcome, Provenance, RunSummary, UNKNOWN
from .robots import RobotsChecker
from .priority import lower_own_priority


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


class SourceListError(ValueError):
    pass


def load_source_list(path: Path, *, max_pages: int) -> list[str]:
    """Parses the owner-approved source list: one URL per line, blank lines
    and ``#``-comments ignored, http(s) only, de-duplicated in order. The
    collector never adds a URL that is not on this list — no link
    following, no "related pages", no search-engine discovery."""
    path = Path(path)
    if not path.is_file():
        raise SourceListError(f"source list not found: {path}")
    urls: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = urlparse(line)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise SourceListError(f"not an http(s) URL in source list: {line!r}")
        if line in seen:
            continue
        seen.add(line)
        urls.append(line)
    if not urls:
        raise SourceListError(f"source list is empty: {path}")
    cap = max(1, min(int(max_pages), config.MAX_PAGES_CEILING))
    return urls[:cap]


@dataclass(slots=True)
class CollectorConfig:
    topic: str
    sources_path: Path
    max_pages: int = 10
    data_dir: Path = field(default_factory=config.default_data_dir)
    min_delay_s: float = config.DEFAULT_DELAY_S
    daily_cap: int = config.DEFAULT_DAILY_CAP
    headless: bool = True
    human_pace: bool = True
    attributes: tuple[str, ...] = ()
    pause_file: Path | None = None
    run_id: str | None = None


def _write_bundle(run_dir: Path, summary: RunSummary) -> tuple[Path, Path]:
    json_path = run_dir / "facts.json"
    json_path.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2),
                         encoding="utf-8")
    lines = [f"# Collector run: {summary.topic}", "",
             f"- run id: `{summary.run_id}`",
             f"- started (UTC): {summary.started_at_utc}",
             f"- finished (UTC): {summary.finished_at_utc}",
             f"- pages attempted: {len(summary.pages)}",
             f"- facts found: {len([f for f in summary.facts if not f.unknown])}",
             f"- unknown attributes: {len([f for f in summary.facts if f.unknown])}",
             "", "## Pages", ""]
    for page in summary.pages:
        lines.append(f"- **{page.status}** {page.url}"
                     + (f" — {page.title}" if page.title else "")
                     + f" (facts: {page.facts_found}; delay before: {page.delay_before_s:.1f}s)"
                     + (f" — {page.detail}" if page.status != "ok" else ""))
    lines += ["", "## Facts", ""]
    for fact in summary.facts:
        if fact.unknown:
            lines.append(f"- **{fact.subject} / {fact.predicate}**: {UNKNOWN}")
            continue
        prov = fact.provenance
        lines.append(f"- **{fact.subject} / {fact.predicate}** (confidence {fact.confidence:.2f}): "
                     f"\"{fact.value}\" — [{prov.url}]({prov.url}), retrieved {prov.retrieved_at_utc}, "
                     f"page hash `{prov.page_hash[:12]}…`")
    lines += ["", "## Refused by design", ""]
    lines += [f"- {item}" for item in summary.refused_by_design]
    md_path = run_dir / "summary.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


async def run_collection(cfg: CollectorConfig, *,
                         session_factory: Callable[..., HumanBrowserSession] | None = None,
                         on_page: Callable[[PageOutcome], None] | None = None) -> RunSummary:
    """Runs one full collection. Returns the ``RunSummary``; the JSON bundle
    and Markdown summary are written to ``<data_dir>/runs/<run_id>/`` as a
    side effect (also on STOP — whatever was collected so far is saved)."""
    lower_own_priority()
    run_id = cfg.run_id or uuid.uuid4().hex[:12]
    run_dir = Path(cfg.data_dir) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(run_dir)
    control = RunControl(run_dir, pause_file=cfg.pause_file)
    urls = load_source_list(cfg.sources_path, max_pages=cfg.max_pages)
    hosts = sorted({_host_of(u) for u in urls})
    domains = DomainState(run_dir.parent / "domain_state.json",
                          min_delay_s=cfg.min_delay_s, daily_cap=cfg.daily_cap)
    robots = RobotsChecker()
    make_session = session_factory or (
        lambda: HumanBrowserSession(run_dir / "browser", allowed_hosts=hosts,
                                    headless=cfg.headless))
    session = make_session()
    summary = RunSummary(run_id=run_id, topic=cfg.topic, started_at_utc=_utcnow_iso(),
                        finished_at_utc="", refused_by_design=list(config.REFUSED_BY_DESIGN))
    ledger.append({"event": "run_start", "run_id": run_id, "topic": cfg.topic,
                  "sources": urls, "at_utc": summary.started_at_utc})
    seen_predicates: dict[tuple[str, str], set[str]] = {}
    try:
        await session.start()
        for url in urls:
            host = _host_of(url)
            started_at = _utcnow_iso()
            try:
                control.check_stop()
                control.wait_out_pause()
            except Stopped as exc:
                ledger.append({"event": "stopped", "url": url, "reason": str(exc)})
                break
            check = domains.check(host)
            delay_before = 0.0
            if not check.ok:
                outcome = PageOutcome(url=url, host=host, status="skipped_cap", detail=check.reason,
                                      started_at_utc=started_at, finished_at_utc=_utcnow_iso(),
                                      delay_before_s=0.0)
                summary.pages.append(outcome)
                ledger.append({"event": "page_skipped", **outcome.as_dict()})
                if on_page:
                    on_page(outcome)
                continue
            if check.wait_s > 0:
                delay_before = check.wait_s
                try:
                    control.polite_wait(check.wait_s)
                except Stopped as exc:
                    ledger.append({"event": "stopped", "url": url, "reason": str(exc)})
                    break
            verdict = robots.allows(url)
            if not verdict.allowed:
                outcome = PageOutcome(url=url, host=host, status="skipped_robots",
                                      detail=verdict.reason, started_at_utc=started_at,
                                      finished_at_utc=_utcnow_iso(), delay_before_s=delay_before)
                summary.pages.append(outcome)
                ledger.append({"event": "page_skipped", **outcome.as_dict()})
                if on_page:
                    on_page(outcome)
                continue
            try:
                page = await session.read(url, human_pace=cfg.human_pace)
            except PageBlocked as exc:
                if exc.pause_equivalent:
                    # Owner-side pause/takeover on the browser itself: wait
                    # it out exactly like our own PAUSE file, then retry once.
                    control.wait_out_pause()
                    try:
                        page = await session.read(url, human_pace=cfg.human_pace)
                    except PageBlocked as exc2:
                        outcome = PageOutcome(url=url, host=host, status="skipped_error",
                                              detail=str(exc2), started_at_utc=started_at,
                                              finished_at_utc=_utcnow_iso(),
                                              delay_before_s=delay_before)
                        summary.pages.append(outcome)
                        ledger.append({"event": "page_skipped", **outcome.as_dict()})
                        if on_page:
                            on_page(outcome)
                        domains.record_fetch(host)
                        continue
                else:
                    outcome = PageOutcome(url=url, host=host, status="skipped_error", detail=str(exc),
                                          started_at_utc=started_at, finished_at_utc=_utcnow_iso(),
                                          delay_before_s=delay_before)
                    summary.pages.append(outcome)
                    ledger.append({"event": "page_skipped", **outcome.as_dict()})
                    if on_page:
                        on_page(outcome)
                    domains.record_fetch(host)
                    continue
            except Exception as exc:  # noqa: BLE001 -- network/timeout/etc; never crash the run
                outcome = PageOutcome(url=url, host=host, status="skipped_error",
                                      detail=f"{type(exc).__name__}: {exc}",
                                      started_at_utc=started_at, finished_at_utc=_utcnow_iso(),
                                      delay_before_s=delay_before)
                summary.pages.append(outcome)
                ledger.append({"event": "page_skipped", **outcome.as_dict()})
                if on_page:
                    on_page(outcome)
                domains.record_fetch(host)
                continue
            domains.record_fetch(host)
            retrieved_at = _utcnow_iso()
            page_hash = extract.page_sha256(page.text)
            candidates = extract.candidate_facts(subject=cfg.topic, text=page.text, topic=cfg.topic,
                                                url=page.url, retrieved_at_utc=retrieved_at,
                                                robots_allowed=verdict.allowed)
            for cand in candidates:
                prov = Provenance(url=page.url, retrieved_at_utc=retrieved_at, quote=cand["quote"],
                                  selector=cand["selector"], page_hash=page_hash,
                                  robots_txt_allowed=verdict.allowed)
                fact = CollectedFact(subject=cand["subject"], predicate=cand["predicate"],
                                     value=cand["value"], confidence=cand["confidence"],
                                     provenance=prov)
                summary.facts.append(fact)
                seen_predicates.setdefault((fact.subject, fact.predicate), set()).add(fact.value)
                ledger.append({"event": "fact_found", **fact.as_dict()})
            outcome = PageOutcome(url=page.url, host=host, status="ok", detail=verdict.reason,
                                  started_at_utc=started_at, finished_at_utc=_utcnow_iso(),
                                  delay_before_s=delay_before, facts_found=len(candidates),
                                  title=page.title, text_chars=len(page.text))
            summary.pages.append(outcome)
            ledger.append({"event": "page_ok", **outcome.as_dict()})
            if on_page:
                on_page(outcome)
    finally:
        await session.close()
        for attribute in cfg.attributes:
            if not any(attribute.lower() in predicate.lower()
                      for (_subj, predicate) in seen_predicates):
                unknown_prov = Provenance(url="", retrieved_at_utc=_utcnow_iso(), quote="",
                                         selector="", page_hash="", robots_txt_allowed=True)
                summary.facts.append(CollectedFact(subject=cfg.topic, predicate=attribute,
                                                   value=UNKNOWN, confidence=0.0,
                                                   provenance=unknown_prov, unknown=True))
        summary.finished_at_utc = _utcnow_iso()
        _write_bundle(run_dir, summary)
        ledger.append({"event": "run_end", "run_id": run_id, "at_utc": summary.finished_at_utc,
                      "pages": len(summary.pages), "facts": len(summary.facts)})
    return summary
