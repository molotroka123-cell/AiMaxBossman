import pytest

from tools.owner_journeys import owner_notify


@pytest.fixture(autouse=True)
def _no_real_owner_messages(monkeypatch, tmp_path_factory):
    """No test may reach the owner's real пульт or local Bossman core."""
    fake_dir = tmp_path_factory.mktemp("no-companion")
    monkeypatch.setattr(owner_notify, "COMPANION_DIR", fake_dir)

    def refuse(self, text):
        raise AssertionError("a test tried to send a real Telegram message")

    monkeypatch.setattr(owner_notify.CompanionTelegram, "send", refuse)
    for var in ("TG_COMPANION_BOT_TOKEN", "TG_COMPANION_CORE_TOKEN", "TG_COMPANION_CORE_URL"):
        monkeypatch.delenv(var, raising=False)
