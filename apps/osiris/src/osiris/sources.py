"""Public HTTP only. robots.txt is the gate. No cookies, no account reuse."""
from __future__ import annotations

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .policy import ActionClass, PolicyDenied
from .store import Store

UA = "OSIRIS-local/0.1 (+https://127.0.0.1; public-sources-only)"
POLITE_RPS = 0.5


class RobotsDecision(str):
    ALLOW = "allow"
    DISALLOW = "disallow"
    UNSPECIFIED = "unspecified"


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def parse_robots(body: str, url: str, ua: str = UA) -> str:
    rp = RobotFileParser()
    rp.parse(body.splitlines())
    if not body.strip():
        return RobotsDecision.UNSPECIFIED
    can = rp.can_fetch(ua, url)
    if can:
        if "disallow:" not in body.lower() and "allow:" not in body.lower():
            return RobotsDecision.UNSPECIFIED
        return RobotsDecision.ALLOW
    return RobotsDecision.DISALLOW


def classify_fetch(robots: str) -> ActionClass:
    if robots == RobotsDecision.DISALLOW:
        raise PolicyDenied(
            ActionClass.BYPASS_PROTECTION,
            "robots.txt forbids this path; bypass is level 0",
        )
    if robots == RobotsDecision.ALLOW:
        return ActionClass.PUBLIC_PAGE_ALLOW_ROBOTS
    return ActionClass.SCRAPE_UNSPECIFIED_ROBOTS


class Fetcher:
    def __init__(self, store: Store, transport):
        self.store = store
        self.transport = transport

    def robots_body(self, url: str) -> str:
        host = host_of(url)
        cached = self.store.robots_get(host)
        if cached is not None:
            return cached
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{host}/robots.txt"
        try:
            body = self.transport.get_text(robots_url, headers={"User-Agent": UA})
        except Exception:
            body = ""
        self.store.robots_put(host, body)
        return body

    def decide(self, url: str) -> tuple[str, ActionClass]:
        body = self.robots_body(url)
        robots = parse_robots(body, url)
        action = classify_fetch(robots)
        return robots, action

    def get_public(self, url: str) -> tuple[str, str, ActionClass]:
        robots, action = self.decide(url)
        text = self.transport.get_text(url, headers={"User-Agent": UA})
        return text, robots, action
