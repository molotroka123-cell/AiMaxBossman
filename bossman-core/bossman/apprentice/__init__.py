"""Universal Computer Apprentice — thin orchestration over existing subsystems.
All flags default OFF; with the master flag off execution refuses (typed)."""
from . import flags
from .errors import ApprenticeDisabled, ApprenticeError
from .models import ApprenticeState, ApprenticeTask, PlanStep, Plan, SemanticTarget, AppIdentity, RiskClass

__all__ = ["flags", "ApprenticeDisabled", "ApprenticeError", "ApprenticeState", "ApprenticeTask", "PlanStep",
           "Plan", "SemanticTarget", "AppIdentity", "RiskClass"]
