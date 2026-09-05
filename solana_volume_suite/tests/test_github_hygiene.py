import csv
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from solana_volume_suite.tools.github_hygiene import GitHubHygieneSearcher, GitHubRateLimitError, GitHubSearchError, FIELDS


def repo(name="useful", stars=42, **extra):
    return {"name": name, "full_name": "owner/" + name, "html_url": "https://github.com/owner/" + name,
            "stargazers_count": stars, "updated_at": datetime.now(timezone.utc).isoformat(),
            "description": "Useful open source project", "private": False, "language": "Python",
            "license": {"spdx_id": "MIT"}, **extra}


def test_search_repositories():
    def handle(request):
        assert request.url.host == "api.github.com"
        assert "authorization" not in request.headers
        query = request.url.params["q"]
        assert 'is:public stars:>=10 language:"Python" pushed:>' in query
        assert request.url.params["sort"] == "stars"
        assert request.extensions["timeout"]["read"] == 10
        return httpx.Response(200, json={"items": [repo(), repo("better", 100), repo("private", private=True),
            repo("no-license", license=None), repo("archived", archived=True), repo("wrong-url", html_url="https://evil.invalid")]})
    found = GitHubHygieneSearcher(httpx.MockTransport(handle)).search_repositories("safe tooling", 10)
    assert [r["name"] for r in found] == ["better", "useful"]
    assert set(found[0]) == set(FIELDS)


def test_filter_garbage():
    searcher = GitHubHygieneSearcher()
    old = (datetime.now(timezone.utc) - timedelta(days=366)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    inputs = [repo(), repo("popular", 200), repo("low", 4), repo("empty", description=" "),
              repo("stale", updated_at=old), repo("future", updated_at=future),
              repo("bad-date", updated_at="broken"), repo("test"), repo("temp-tools"),
              repo("foo"), repo("bar"), repo("wrong-stars", True), repo()]
    assert [r["name"] for r in searcher.filter_garbage(inputs)] == ["popular", "useful"]


def test_save_to_csv(tmp_path):
    path = tmp_path / "out.csv"
    GitHubHygieneSearcher().save_to_csv([repo(description='=HYPERLINK("bad"),\ntext')], str(path))
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        assert reader.fieldnames == list(FIELDS)
        assert rows[0]["description"].startswith("'=")
    GitHubHygieneSearcher().save_to_csv([], str(path))
    assert path.read_text().strip() == ",".join(FIELDS)


@pytest.mark.parametrize("query", ["is:private", "foo OR bar", "x\nprivate", "", 'x" language:Go'])
def test_qualifier_injection_rejected(query):
    with pytest.raises(ValueError):
        GitHubHygieneSearcher().search_repositories(query)


@pytest.mark.parametrize("status", [403, 429])
def test_upstream_cooldown_no_retry(status):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "120"})
    searcher = GitHubHygieneSearcher(httpx.MockTransport(handle))
    for _ in range(2):
        with pytest.raises(GitHubRateLimitError):
            searcher.search_repositories("safe")
    assert len(calls) == 1


def test_timeout_is_not_empty_success():
    def handle(request):
        raise httpx.ReadTimeout("offline", request=request)
    with pytest.raises(GitHubSearchError):
        GitHubHygieneSearcher(httpx.MockTransport(handle)).search_repositories("safe")


def test_dashboard_search_cache_and_failure(client, monkeypatch):
    from solana_volume_suite.dashboard import safety_app
    searcher = GitHubHygieneSearcher(httpx.MockTransport(lambda req: httpx.Response(200, json={"items": [repo()]})))
    monkeypatch.setattr(safety_app, "searcher", searcher)
    found = client.post("/api/github/search", json={"query": "safe"})
    assert found.status_code == 200
    assert client.get("/api/github/results").json() == found.json()
    searcher.transport = httpx.MockTransport(lambda req: httpx.Response(503))
    assert client.post("/api/github/search", json={"query": "safe"}).status_code == 502
    assert client.get("/api/github/results").json() == found.json()
