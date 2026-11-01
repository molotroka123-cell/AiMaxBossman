from bcc.features import tools_cloud as tc

def test_submicro_paid_estimate_rounds_up():
    assert tc.estimate_cost({'price_in_1m':0.01,'price_out_1m':0.01},1,1) == 0.000001

def test_paid_reservation_rejects_zero_and_enforces_micro_budget(tmp_path):
    row, error = tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.0,budget_usd=0.000001,scope='day')
    assert row is None and error
    row, error = tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.0000001,budget_usd=0.000001,scope='day')
    assert row and row['quoted_cost_usd'] == 0.000001
    row, error = tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.0000001,budget_usd=0.000001,scope='day')
    assert row is None and error
