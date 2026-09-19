"""Side-effect-free configuration inventory for the core UI-TARS planner."""
import os


def status() -> dict:
    enabled = os.environ.get("BOSSMAN_COMPUTER_PLANNER", "").strip().lower() == "uitars"
    model = bool(os.environ.get("BOSSMAN_UITARS_MODEL", "").strip())
    return {"status": "configured" if enabled and model else "needs_setup",
            "enabled": enabled, "model_configured": model,
            "inference_verified": False, "mode": "click_grounding"}
