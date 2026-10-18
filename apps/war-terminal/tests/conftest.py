import pathlib
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fixtures import fake_get, fake_post  # noqa: E402
from warterminal.api import create_app  # noqa: E402

@pytest.fixture(autouse=True)
def _no_real_bossman(monkeypatch):
    """Tests must never reach the owner's running Bossman (:8801) through the translation gateway."""
    monkeypatch.setenv("WAR_TERMINAL_BOSSMAN_URL", "off")


TOK = {"viewer": "tv", "analyst": "ta", "compliance": "tc", "owner": "to"}
TODAY = date(2026, 10, 17)


def H(role):
    return {"Authorization": f"Bearer {TOK[role]}"}


class Env:
    def __init__(self, tmp_path, live=True, ted=None):
        self.app = create_app(tmp_path, live=live, tokens=TOK, post=fake_post(ted=ted), get=fake_get, today=TODAY)
        self.c = TestClient(self.app)
        self.store = self.app.state.store

    def req(self, method, path, role="owner", **kw):
        return self.c.request(method, "/api/v1" + path, headers=H(role) if role else {}, **kw)

    def ingest_ted(self):
        r = self.req("POST", "/ingest", "analyst", json={"source": "ted"})
        assert r.status_code == 200, r.text
        return r.json()

    def key(self, pub):
        return f"EU:{pub}"

    def tender(self, pub):
        return self.req("GET", f"/tenders/EU:{pub}", "viewer").json()


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)
