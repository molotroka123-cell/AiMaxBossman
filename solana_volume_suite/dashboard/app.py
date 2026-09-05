"""Compatibility entrypoint for the safety-only control plane."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from solana_volume_suite.dashboard.safety_app import app  # noqa: E402,F401
