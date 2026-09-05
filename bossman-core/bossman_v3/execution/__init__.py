"""V3.4 Compound Action Resume — оркестрация цепочки шагов.

Один шаг исполняет bossman_v3/computer_agent, durable-состояние держит
bossman_v3/memory; здесь — только склейка и семантика цепочки.
"""
from .compound import CompoundResult, CompoundRunner, PlanStep

__all__ = ["CompoundResult", "CompoundRunner", "PlanStep"]
