from __future__ import annotations
import os
from decimal import Decimal
from pathlib import Path
from .governor import CostGovernor
from .models import BudgetPolicy,BudgetScope,HardLimitAction
from .store import SQLiteBudgetStore

def _workspace()->Path:
    try:
        from ..config import settings
        return Path(settings.workspace_dir)
    except Exception:
        return Path(os.environ.get("WORKSPACE_DIR","."))

STORE=SQLiteBudgetStore(Path(os.environ.get(
    "BOSSMAN_COST_DB",str(_workspace()/"_cost_control"/"cost_control.db")
)))

def _emit(kind:str,**data):
    try:
        from .. import events
        events.emit(kind,**data)
    except Exception:
        # Observability failure must not disable the budget boundary.
        pass

GOVERNOR=CostGovernor(STORE,_emit)

def seed_env_policies()->None:
    mapping={
        BudgetScope.RUN:"BOSSMAN_BUDGET_RUN_USD",
        BudgetScope.TASK:"BOSSMAN_BUDGET_TASK_USD",
        BudgetScope.PROJECT:"BOSSMAN_BUDGET_PROJECT_USD",
        BudgetScope.DAILY_GLOBAL:"BOSSMAN_BUDGET_DAILY_USD",
    }
    action=HardLimitAction(os.environ.get("BOSSMAN_BUDGET_HARD_ACTION","stop").strip().lower())
    warning=Decimal(os.environ.get("BOSSMAN_BUDGET_WARNING_FRACTION","0.80"))
    existing={(p.scope,p.subject) for p in STORE.list_policies()}
    for scope,envname in mapping.items():
        raw=os.environ.get(envname,"").strip()
        if raw and (scope,"*") not in existing:
            STORE.set_policy(BudgetPolicy(scope=scope,subject="*",hard_limit_usd=Decimal(raw),
                                          warning_fraction=warning,hard_action=action))
