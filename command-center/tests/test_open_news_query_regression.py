"""RSS providers can return loose matches: enforce the declared query locally."""
import asyncio
import httpx
import pytest
from bcc import open_news_skill as news

CLIENT = httpx.AsyncClient


@pytest.mark.parametrize('mode,query,expected', [
    ('all', 'alpha beta', ['alpha beta', 'alpha middle beta']),
    ('exact_phrase', 'alpha beta', ['alpha beta']),
    ('any', 'alpha', ['alpha beta', 'alpha middle beta']),
    ('any', 'missing', []),
])
def test_search_filters_real_returned_feed_before_reporting_success(monkeypatch, mode, query, expected):
    titles = ['alpha beta', 'unrelated weather', 'alpha middle beta', 'alphabet']
    payload = ('<rss><channel>' + ''.join(
        f'<item><title>{title}</title><link>https://example.com/{n}</link></item>'
        for n, title in enumerate(titles)) + '</channel></rss>').encode()
    calls = []
    def responder(request):
        calls.append(request)
        return httpx.Response(200, headers={'Content-Type': 'application/rss+xml'}, content=payload)
    monkeypatch.delenv('BOSSMAN_OFFLINE_MODE_ENABLED', raising=False)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: CLIENT(transport=httpx.MockTransport(responder), **kw))
    result = asyncio.run(news.search_news({'query': query, 'query_mode': mode}))
    assert [row['title'] for row in result['results']] == expected
    assert result['status'] == ('FETCHED_RSS' if expected else 'NO_MATCHING_RESULTS')
    assert len(calls) == 1 and calls[0].method == 'GET'
    assert result['freshness_verified'] is False
