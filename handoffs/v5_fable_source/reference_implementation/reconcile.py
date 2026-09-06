from .models import Condition, ReconcileResult

def reconcile_after_verified_mission(*, mission_verified, fresh_condition_reader):
    """Mission completion is not sustained objective health."""
    if not mission_verified:
        return ReconcileResult(Condition.UNKNOWN, (), "mission effect not independently verified")
    condition, refs, reason = fresh_condition_reader()
    if condition is Condition.SATISFIED and not refs:
        return ReconcileResult(Condition.UNKNOWN, (), "SATISFIED requires fresh evidence refs")
    return ReconcileResult(condition, refs, reason)
