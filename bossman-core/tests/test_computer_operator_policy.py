from bossman.computer_operator.models import *
from bossman.computer_operator.policy import ComputerPolicy
def a(kind,**kw):return ComputerAction.make(kind,expected=ExpectedState(contains_text="ok"),**kw)
def test_observe_only_denies_input():assert not ComputerPolicy().classify(a(ActionKind.CLICK),mode=TaskMode.OBSERVE_ONLY).allow
def test_low_confidence_vision_denied():assert not ComputerPolicy().classify(a(ActionKind.CLICK,confidence=.1,source="vision"),mode=TaskMode.CONTROL).allow
def test_payment_requires_approval_even_if_model_says_false():
    d=ComputerPolicy().classify(a(ActionKind.CLICK,args={"semantic":"pay","requires_approval":False}),mode=TaskMode.CONTROL)
    assert d.allow and d.requires_approval
def test_file_url_denied():assert not ComputerPolicy().classify(a(ActionKind.BROWSER,args={"op":"navigate","url":"file:///etc/passwd"}),mode=TaskMode.CONTROL).allow
