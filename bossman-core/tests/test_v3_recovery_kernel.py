import pytest
from bossman_v3.recovery_kernel import *

def test_restore_only_verified(tmp_path):
    k=RecoveryKernel(FileCheckpointStore(tmp_path))
    good=k.checkpoint({"x":1},verified=True); bad=k.checkpoint({"x":2},verified=False)
    assert k.restore(good.checkpoint_id)["x"]==1
    with pytest.raises(PermissionError): k.restore(bad.checkpoint_id)

def test_loop_and_budget_detection(tmp_path):
    k=RecoveryKernel(FileCheckpointStore(tmp_path),budget_limit=10,repeat_limit=2)
    assert k.evaluate(spent=1,action={"a":1},state={"s":1},outcome={"o":1}) is None
    assert k.evaluate(spent=1,action={"a":1},state={"s":1},outcome={"o":1}).kind=="REPLAN"
    assert k.evaluate(spent=11,action={},state={},outcome={}).kind=="ABORT_OR_APPROVAL"
