"""Tests for V7 Phase 1 Reality OS Core Components."""
import unittest
from v7_mission_ir import Constraint, ConstraintType, MissionIR, Objective, ResourceBudget
from v7_world_state_graph import WorldStateGraph
from v7_strategy_engine import StrategyEngine
from v7_shadow_utility_router import ShadowUtilityRouter


class TestV7Phase1Core(unittest.TestCase):
    def setUp(self):
        self.graph = WorldStateGraph()
        self.engine = StrategyEngine(self.graph)
        self.router = ShadowUtilityRouter(self.engine)

    def test_mission_ir_creation_and_provenance(self):
        mission = MissionIR(
            id="m-001",
            owner_intent="Reorganize and tag files",
            objectives=[Objective(id="obj-1", target_state="files_sorted")],
            constraints=[Constraint(type=ConstraintType.MAX_COST_USD, value=0.10)],
            budget=ResourceBudget(max_cost_usd=0.10),
        )
        self.assertTrue(mission.provenance_hash)
        d = mission.to_dict()
        restored = MissionIR.from_dict(d)
        self.assertEqual(restored.id, "m-001")
        self.assertEqual(restored.provenance_hash, mission.provenance_hash)

    def test_world_state_graph_freshness(self):
        self.graph.set_node("file_1", "file", {"name": "test.txt"}, source="fs_watcher")
        node = self.graph.get_node("file_1")
        self.assertIsNotNone(node)
        self.assertGreater(self.graph.freshness_score("file_1"), 0.9)

    def test_strategy_engine_and_utility_shadow_router(self):
        mission = MissionIR(
            id="m-002",
            owner_intent="Execute local analysis",
            objectives=[Objective(id="obj-2", target_state="analysis_complete")],
            constraints=[],
            budget=ResourceBudget(max_cost_usd=0.01, disallow_cloud_fallback=True),
        )
        # Should filter out agentic cloud candidate due to budget
        strategies = self.engine.generate_candidate_strategies(mission)
        self.assertEqual(len(strategies), 1)
        self.assertEqual(strategies[0].name, "LocalDeterministicExecution")

        # Shadow routing check
        chosen = self.router.route_shadow(mission, legacy_choice="LegacyLocal")
        self.assertEqual(chosen.name, "LocalDeterministicExecution")
        self.assertEqual(len(self.router.logs), 1)
        self.assertTrue(self.router.logs[0].discrepancy)


if __name__ == "__main__":
    unittest.main()
