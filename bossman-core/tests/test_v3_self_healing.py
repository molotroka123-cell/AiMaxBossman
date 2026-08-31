from bossman_v3.self_healing import *
class FM:
    def __init__(self,h=()): self.h=list(h)
    def query(self,s,limit=20): return self.h
    def record(self,e): self.h.append(e)

def test_no_blind_repeat():
    c=SelfHealingController(FM())
    s=[RecoveryStrategy("refresh",RecoveryAction.RETRY,.9,.1,.1),RecoveryStrategy("replan",RecoveryAction.REPLAN,.8,.2,.1)]
    d=c.decide(signature="x",strategies=s,value_success=1,attempted=["refresh"])
    assert d.strategy=="replan"

def test_negative_economics_does_not_retry():
    c=SelfHealingController(FM())
    s=[RecoveryStrategy("retry",RecoveryAction.RETRY,.1,1,.5)]
    d=c.decide(signature="x",strategies=s,value_success=1)
    assert d.action in {RecoveryAction.REPLAN,RecoveryAction.ABORT}
