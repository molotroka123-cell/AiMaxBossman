import time
from bossman.computer_operator.models import *
from bossman.computer_operator.verifier import Verifier
def o(summary="",title=""):return Observation("o",time.time(),{"title":title},summary)
def test_mutation_without_postcondition_not_success():assert not Verifier().verify(ComputerAction.make(ActionKind.CLICK),o()).ok
def test_postcondition_verified():assert Verifier().verify(ComputerAction.make(ActionKind.CLICK,expected=ExpectedState(contains_text="saved")),o("file saved")).ok
def test_wrong_window_fails():assert not Verifier().verify(ComputerAction.make(ActionKind.FOCUS,expected=ExpectedState(window_title_contains="Settings")),o(title="Browser")).ok
