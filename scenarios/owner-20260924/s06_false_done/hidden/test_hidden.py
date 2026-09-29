import json, pathlib
def test_report_is_real():
    d = json.loads((pathlib.Path.cwd() / "report" / "summary.json").read_text())
    assert d == {"by_region": {"north": 150, "south": 80, "east": 50}, "total": 280}
