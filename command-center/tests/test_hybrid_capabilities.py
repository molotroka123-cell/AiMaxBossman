
import pytest
import time
from bcc.hybrid.capabilities import (
    RuntimeIdentity,
    EffectCorrelation,
    Observation,
    EvidenceCandidate,
    OperationCancelledError,
    OperationTimeoutError,
)

def test_effect_correlation_cancellation():
    cancelled = False
    corr = EffectCorrelation(
        correlation_id="corr-1",
        task_id="task-1",
        run_id="run-1",
        deadline_epoch_s=time.time() + 10.0,
        cancellation_token=lambda: cancelled,
    )
    assert not corr.is_cancelled()
    assert not corr.is_expired()

    cancelled = True
    assert corr.is_cancelled()

def test_effect_correlation_expiration():
    corr = EffectCorrelation(
        correlation_id="corr-2",
        task_id="task-1",
        run_id="run-1",
        deadline_epoch_s=time.time() - 1.0,
    )
    assert corr.is_expired()
