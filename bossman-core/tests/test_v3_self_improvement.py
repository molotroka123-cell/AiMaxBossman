from bossman_v3.self_improvement import *

def R(**kw):
    d=dict(verified_success=.9,quality=.9,cost=10,latency=10,tokens=100,peak_ram=100,peak_vram=0,retries=1,security_failures=0); d.update(kw); return BenchmarkResult(**d)

def test_pareto_candidate_can_be_promoted():
    lab=SelfImprovementLab(); d=lab.evaluate(R(),R(cost=8,latency=9),delta_verified_utility=3,delta_resource_cost=1,delta_complexity_cost=.5)
    assert d.promotable and d.pareto_improvement

def test_security_regression_blocks_promotion():
    d=SelfImprovementLab().evaluate(R(),R(cost=8,security_failures=1),delta_verified_utility=10,delta_resource_cost=1,delta_complexity_cost=1)
    assert not d.promotable

def test_architecture_amplification_factor():
    assert SelfImprovementLab.architecture_amplification_factor(.9,1,.9,2)==2
