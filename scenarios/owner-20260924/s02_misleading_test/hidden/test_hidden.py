from durations import parse_duration

def test_contract_is_seconds():
    assert parse_duration("1h30m") == 5400
    assert parse_duration("2h") == 7200
    assert parse_duration("45s") == 45
