from bcc.features.twitter import STATUS, twitter_status


def test_frozen_payload():
    assert STATUS["status"] == "frozen"
    assert STATUS["live"] is False
    assert STATUS["level0"] == "sealed"


def test_status_endpoint_shape():
    import asyncio
    body = asyncio.run(twitter_status())
    assert body["ready"] is False
    assert "public_tweet_read" in body["when_thawed"]
