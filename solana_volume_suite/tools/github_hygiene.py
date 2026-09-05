"""Public metadata search only; no credentials, clones, downloads or rate-limit retries."""
import csv
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

FIELDS = ("name", "full_name", "html_url", "stargazers_count", "updated_at", "description")
# Conservative open-source license allowlist; excluded licenses need manual review.
OPEN_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "Unlicense"}
OPEN_LICENSES |= {base + suffix for base in ("GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "AGPL-3.0")
                  for suffix in ("", "-only", "-or-later")}


class GitHubSearchError(RuntimeError):
    pass


class GitHubRateLimitError(GitHubSearchError):
    def __init__(self, retry_after=60):
        self.retry_after = retry_after
        super().__init__("GitHub rate limit reached; wait before retrying")


class GitHubHygieneSearcher:
    """One page (up to 100 repos), sorted by stars; explicit license required.

    Popularity and recent updates are discovery heuristics, not a security audit.
    The lock serializes requests and preserves GitHub's cooldown in this process.
    """
    def __init__(self, transport=None):
        self.transport = transport
        self._lock = threading.Lock()
        self._retry_at = 0.0

    @staticmethod
    def validate_query(query, min_stars, language):
        if (not isinstance(query, str) or not 1 <= len(query.strip()) <= 200
                or not re.fullmatch(r"[\w .+/#-]+", query)
                or any(word.upper() in {"OR", "AND", "NOT"} for word in query.split())):
            raise ValueError("Use plain search words; GitHub qualifiers and Boolean operators are disabled")
        if type(min_stars) is not int or not 0 <= min_stars <= 1_000_000_000:
            raise ValueError("min_stars must be a nonnegative integer")
        if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 +#.-]{0,39}", language):
            raise ValueError("Invalid language")

    def search_repositories(self, query: str, min_stars: int = 0, language: str = "Python") -> list[dict]:
        self.validate_query(query, min_stars, language)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date().isoformat()
        params = {"q": f'{query.strip()} is:public stars:>={min_stars} language:"{language}" pushed:>{cutoff} archived:false',
                  "sort": "stars", "order": "desc", "per_page": 100}
        with self._lock:
            if time.monotonic() < self._retry_at:
                raise GitHubRateLimitError(math.ceil(self._retry_at - time.monotonic()))
            try:
                with httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False,
                                  transport=self.transport, headers={"Accept": "application/vnd.github+json",
                                  "X-GitHub-Api-Version": "2022-11-28",
                                  "User-Agent": "AiMaxBossman-Public-Hygiene"}) as client:
                    response = client.get("https://api.github.com/search/repositories", params=params)
                if response.status_code in (403, 429):
                    delay = 60
                    try:
                        delay = max(delay, int(response.headers.get("Retry-After", "0")),
                                    math.ceil(float(response.headers.get("X-RateLimit-Reset", "0")) - time.time()))
                    except (ValueError, OverflowError):
                        pass
                    self._retry_at = time.monotonic() + delay
                    raise GitHubRateLimitError(delay)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                    raise GitHubSearchError("Invalid GitHub search response")
                if data.get("incomplete_results"):
                    raise GitHubSearchError("GitHub returned incomplete results; narrow the query")
                results = []
                for repo in data["items"][:100]:
                    if not isinstance(repo, dict):
                        continue
                    license_info = repo.get("license") or {}
                    if (repo.get("private") is not False or repo.get("archived") or repo.get("disabled")
                            or not isinstance(license_info, dict)
                            or license_info.get("spdx_id") not in OPEN_LICENSES):
                        continue
                    if not isinstance(repo.get("language"), str) or repo["language"].lower() != language.lower():
                        continue
                    item = {field: repo.get(field) for field in FIELDS}
                    if self._valid_metadata(item) and item["stargazers_count"] >= min_stars:
                        results.append(item)
                return sorted(results, key=lambda r: (-r["stargazers_count"], r["full_name"]))
            except GitHubSearchError:
                raise
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise GitHubSearchError("GitHub search unavailable or returned invalid data") from exc

    @staticmethod
    def _valid_metadata(repo):
        if not isinstance(repo, dict):
            return False
        full_name = repo.get("full_name", "")
        return (isinstance(full_name, str)
                and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name) is not None
                and repo.get("html_url") == f"https://github.com/{full_name}"
                and repo.get("name") == full_name.split("/")[-1]
                and type(repo.get("stargazers_count")) is int)

    def filter_garbage(self, repos: list[dict]) -> list[dict]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=365)
        clean = {}
        for repo in repos:
            if not self._valid_metadata(repo) or repo["stargazers_count"] < 5:
                continue
            if repo.get("private") is True or repo.get("archived") or repo.get("disabled"):
                continue
            description = repo.get("description")
            if not isinstance(description, str) or not description.strip():
                continue
            if any(part in {"test", "temp", "tmp", "foo", "bar"} for part in re.split(r"[-_.\d]+", repo["name"].lower())):
                continue
            try:
                updated = datetime.fromisoformat(repo["updated_at"].replace("Z", "+00:00"))
                if updated.tzinfo is None or not cutoff <= updated <= now:
                    continue
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
            clean[repo["full_name"]] = {key: repo.get(key) for key in FIELDS}
        return sorted(clean.values(), key=lambda r: (-r["stargazers_count"], r["full_name"]))

    def save_to_csv(self, repos: list[dict], filename: str = "github_hygiene_results.csv"):
        # Prefix formula-like text, including leading whitespace/control characters.
        def safe(value):
            value = "" if value is None else str(value)
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value
        path = Path(filename)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for repo in self.filter_garbage(repos):
                writer.writerow({key: safe(repo[key]) for key in FIELDS})
