import re
from fastapi.testclient import TestClient
from bossman_accountant.api import build_app


def test_loft_ui_is_russian_self_contained_and_phone_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    r = TestClient(build_app()).get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert 'lang="ru"' in html and "viewport-fit=cover" in html
    for label in ("Обзор", "Документы", "Задачи", "Отчёты", "Журнал пуст"):
        assert label in html
    # no external network dependency: no absolute URLs in src/href/url()/fetch
    assert not re.search(r"(?:src|href)=[\"']https?://|url\(\s*[\"']?https?://|fetch\([\"']https?://|@import", html)
    # relative API paths only, so the page also works behind the Bossman panel proxy
    assert "fetch(path" in html and "'api/transactions'" in html and "'/api/" not in html
    # exactly one inline script: the Bossman proxy hash-locks it with a CSP
    assert len(re.findall(r"<script>", html)) == 1
    assert "Demo" not in html and "демо-данных нет" in html.lower()


def test_ui_is_packaged_with_the_module():
    from bossman_accountant.ui import UI_PATH
    assert UI_PATH.is_file()
