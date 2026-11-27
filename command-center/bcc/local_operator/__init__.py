"""Local operator: the owner's installed Ollama models as local chat agents, with computer use and the Harness.

* `inventory` — read-only probe of Ollama (`/api/tags`, `/api/show`): what is installed and what each model can do.
* `provision` — pure plan: registry models + local agents («Локально · <model>», «… · ПК», «Bossman Harness»).
* `bcc.features.local_operator` — owner routes that apply the plan; the agents then appear in the normal chat
  picker and run through the normal engine (approvals, STOP, budgets, receipts) — no second agent loop.
"""
from .inventory import Inventory, LocalModel, probe
from .provision import HARNESS_NAME, plan_agents, plan_models

__all__ = ["HARNESS_NAME", "Inventory", "LocalModel", "plan_agents", "plan_models", "probe"]
