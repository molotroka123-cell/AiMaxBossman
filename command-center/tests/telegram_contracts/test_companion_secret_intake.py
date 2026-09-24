from __future__ import annotations

import asyncio

import pytest

from bcc.telegram_companion.secret_intake import (
    SecretExecutionResult, SecretField, SecretIntakeError, SecretIntakeManager,
    looks_like_secret_message, request_caption,
)


class LocalExecutor:
    local_only = True
    network_isolated = True
    model_sees_secret = False

    def __init__(self, result=None):
        self.refs = None
        self.result = result or SecretExecutionResult(True, True, "LOGIN_VERIFIED")

    async def apply(self, request, values):
        assert request.target == "Example login"
        assert bytes(values["username"]) == b"alice"
        assert bytes(values["password"]) == b"s3cret"
        self.refs = list(values.values())
        return self.result


class BadExecutor(LocalExecutor):
    network_isolated = False


def begin(manager, executor):
    return manager.begin(
        owner_key="1:1", chat_id=1, target="Example login",
        fields=(SecretField("username", "Email", "username"),
                SecretField("password", "Password")),
        screenshot=b"PNG-redacted", screenshot_redacted=True,
        executor=executor, ttl_seconds=180,
    )


def test_secret_is_one_shot_and_buffers_are_wiped():
    manager = SecretIntakeManager()
    executor = LocalExecutor()
    req = begin(manager, executor)
    manager.bind_request_message("1:1", req.session_id, 10)
    got, result = asyncio.run(manager.consume(
        owner_key="1:1", chat_id=1, message_id=11,
        text="username=alice\npassword=s3cret",
    ))
    assert result.success and result.verified and got.state == "VERIFIED"
    assert manager.pending("1:1") is None
    assert executor.refs and all(set(buf) <= {0} for buf in executor.refs)


def test_executor_must_be_network_isolated_and_model_never_sees_secret():
    manager = SecretIntakeManager()
    with pytest.raises(SecretIntakeError, match="SECRET_EXECUTOR_HAS_NETWORK"):
        begin(manager, BadExecutor())


def test_unredacted_screenshot_refused():
    manager = SecretIntakeManager()
    with pytest.raises(SecretIntakeError, match="SCREENSHOT_NOT_REDACTED"):
        manager.begin(owner_key="1:1", chat_id=1, target="x",
                      fields=(SecretField("password", "Password"),),
                      screenshot=b"x", screenshot_redacted=False,
                      executor=LocalExecutor())


def test_caption_contains_schema_not_secret():
    manager = SecretIntakeManager()
    req = begin(manager, LocalExecutor())
    caption = request_caption(req)
    assert "username" in caption and "password" in caption
    assert "s3cret" not in caption
    assert req.screenshot_sha256 not in caption


@pytest.mark.parametrize("text", [
    "password=hunter2", "пароль=секрет", "api_key=abc", "sk-abcdefghijklmnop",
])
def test_secret_like_message_detection(text):
    assert looks_like_secret_message(text)


def test_single_field_can_be_plain_text():
    manager = SecretIntakeManager()

    class One:
        local_only = True
        network_isolated = True
        model_sees_secret = False
        ref = None
        async def apply(self, request, values):
            self.ref = values["password"]
            assert bytes(self.ref) == b"only-secret"
            return SecretExecutionResult(False, False, "LOGIN_REJECTED")

    one = One()
    manager.begin(owner_key="1:1", chat_id=1, target="x",
                  fields=(SecretField("password", "Password"),),
                  screenshot=b"x", screenshot_redacted=True, executor=one)
    req, result = asyncio.run(manager.consume(owner_key="1:1", chat_id=1, message_id=7,
                                              text="only-secret"))
    assert req.state == "FAILED" and not result.success
    assert set(one.ref) <= {0}
