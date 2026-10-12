"""Reports direction: readable report over an exchange_volume run. SYNTHETIC fixture only (no client data)."""
from __future__ import annotations

import copy
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from exchange_volume import cli, report  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "SYNTHETIC_export.html"


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    code = cli.main(["run", "--export", str(FIXTURE), "--period", "business", "--out", str(out)])
    assert code in (0, None)
    return out


def test_report_copies_the_calculator_numbers_and_never_calls_turnover_profit(run_dir, tmp_path):
    result, deals = report.load(run_dir / "volume_result.json", run_dir / "deals.csv")
    text = report.render(result, deals)
    assert "**3300**" in text and "**310**" in text                     # exactly the calculator's gross figures
    assert "Это не прибыль и не доход" in text and "не найдена в данных" in text   # commission absent -> said so
    # no sentence anywhere claims a profit: every mention of the word is a negation
    for line in text.splitlines():
        low = line.lower()
        if "прибыл" in low:
            assert "не прибыль" in low or "не рассчитыва" in low, line
    out = report.write(run_dir / "volume_result.json", run_dir / "deals.csv", tmp_path)
    assert Path(out["report"]).is_file() and Path(out["disputed"]).is_file()


def test_every_duplicate_unconfirmed_and_incomplete_message_is_listed_with_its_reference(run_dir):
    result, deals = report.load(run_dir / "volume_result.json", run_dir / "deals.csv")
    rows = {r["ref"]: r for r in report.disputed_rows(result, deals)}
    assert {"2", "4", "5", "9", "10"} <= set(rows)
    assert "дубль сделки #1" in rows["2"]["reason"]                     # a duplicate points at the deal it repeats
    assert "нет USDT-ноги" in rows["4"]["reason"]                       # unpriced, not silently zero
    assert rows["9"]["status"] == "не разобрано" and rows["10"]["status"] == "не разобрано"
    assert "могут быть занижены" in report.render(result, deals)


def test_negative_control_a_fully_classified_export_does_not_claim_incompleteness(run_dir):
    result, deals = report.load(run_dir / "volume_result.json", run_dir / "deals.csv")
    clean = copy.deepcopy(result)
    for key in ("complex_refs", "incomplete_refs", "ambiguous_amount_refs"):
        clean["message_classification"][key] = []
    text = report.render(clean, [d for d in deals if d["status"] == "counted"])
    assert "могут быть занижены" not in text
    assert "неразобранных нет" in text and "не доказывает полноту" in text    # still no claim of exactness


def test_a_different_schema_is_refused_not_guessed(run_dir, tmp_path):
    bad = json.loads((run_dir / "volume_result.json").read_text(encoding="utf-8"))
    bad["schema"] = "swapme.exchange-volume/3"
    path = tmp_path / "r.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported result schema"):
        report.load(path, run_dir / "deals.csv")


def test_disputed_csv_has_one_row_per_disputed_message(run_dir, tmp_path):
    out = report.write(run_dir / "volume_result.json", run_dir / "deals.csv", tmp_path)
    with open(out["disputed"], encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["ref"] for r in rows] == ["2", "4", "5", "9", "10"]


# -- JSON export (Telegram Desktop result.json) ---------------------------------------------------
def _json_export_from(html_path: Path, *, text_as_entities: bool = False) -> str:
    """Build a result.json carrying exactly the messages of the synthetic HTML export."""
    from exchange_volume import parse
    items = []
    for m in parse.parse_export(html_path.read_text(encoding="utf-8")):
        text = [{"type": "plain", "text": m.text}] if text_as_entities else m.text
        items.append({"id": m.msg_id, "type": "message", "date": m.ts_local.isoformat(),
                      "date_unixtime": str(int((m.ts_utc - __import__("datetime").datetime(1970, 1, 1)).total_seconds())),
                      "from": "Synthetic Desk", "from_id": "user1", "text": text})
    items.insert(0, {"id": -1, "type": "service", "date": "2026-01-01T00:00:00", "date_unixtime": "0", "action": "x"})
    return json.dumps({"name": "SYNTHETIC", "type": "personal_chat", "messages": items}, ensure_ascii=False)


def test_json_export_gives_the_same_messages_and_the_same_result_as_html(tmp_path):
    from exchange_volume import parse
    html_msgs = parse.parse_export(FIXTURE.read_text(encoding="utf-8"))
    json_msgs = parse.parse_export_json(_json_export_from(FIXTURE))
    assert [(m.msg_id, m.ts_local, m.utc_offset_min, m.ts_utc, m.text) for m in json_msgs] == \
           [(m.msg_id, m.ts_local, m.utc_offset_min, m.ts_utc, m.text) for m in html_msgs]
    path = tmp_path / "result.json"
    path.write_text(_json_export_from(FIXTURE), encoding="utf-8")
    a, b = tmp_path / "a", tmp_path / "b"
    cli.main(["run", "--export", str(FIXTURE), "--period", "business", "--out", str(a)])
    cli.main(["run", "--export", str(path), "--period", "business", "--out", str(b)])
    ra = json.loads((a / "volume_result.json").read_text(encoding="utf-8"))
    rb = json.loads((b / "volume_result.json").read_text(encoding="utf-8"))
    assert rb["source"]["type"] == "telegram_export_json" and ra["source"]["type"] == "telegram_export_html"
    assert ra["periods"] == rb["periods"] and ra["message_classification"] == rb["message_classification"]
    assert (a / "deals.csv").read_text(encoding="utf-8") == (b / "deals.csv").read_text(encoding="utf-8")


def test_json_text_entities_are_joined_and_a_message_without_unixtime_is_refused():
    from exchange_volume import parse
    msgs = parse.parse_export_json(_json_export_from(FIXTURE, text_as_entities=True))
    assert msgs and all(isinstance(m.text, str) and m.text for m in msgs)
    broken = json.dumps({"messages": [{"id": 1, "type": "message", "date": "2026-03-10T08:00:00", "text": "x"}]})
    with pytest.raises(ValueError, match="date_unixtime"):
        parse.parse_export_json(broken)
    with pytest.raises(ValueError, match="messages"):
        parse.parse_export_json(json.dumps({"chats": {"list": []}}))
