"""Requires real HighwayEnv; skipped when dependencies are unavailable.
No simulator mocks are used in this file.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest

from cornercaselab.domain import Scenario
from cornercaselab.simulator import SimSettings, make_environment, evaluate

READY = all(importlib.util.find_spec(p) is not None for p in ("highway_env", "gymnasium", "numpy", "pygame", "PIL"))


@unittest.skipUnless(READY, "Real simulator dependencies are not installed")
class SimulatorIntegrationTests(unittest.TestCase):
    def test_initial_conditions(self):
        s = Scenario()
        env = make_environment(s, SimSettings())
        try:
            obs, _ = env.reset(seed=123)
            self.assertEqual(obs.shape, (6, 5))
            self.assertEqual(len(env.road.vehicles), 3)
            ego, lead, ramp = env.road.vehicles
            self.assertAlmostEqual(ego.speed, s.ego_speed)
            self.assertAlmostEqual(float(lead.position[0]-ego.position[0])-5, s.front_gap)
            self.assertAlmostEqual(float(ramp.position[0]), s.ramp_x)
            self.assertTrue(all(v.on_road and not v.crashed for v in env.road.vehicles))
        finally:
            env.close()

    def test_same_seed_trace_replay(self):
        settings = SimSettings(horizon_s=2.0)
        a = evaluate(Scenario(), 123, settings=settings)
        b = evaluate(Scenario(), 123, settings=settings)
        self.assertEqual(a["trace_sha256_rounded_9dp"], b["trace_sha256_rounded_9dp"])
        self.assertEqual(a["termination"], b["termination"])

    def test_seed_changes_actual_nuisance(self):
        settings = SimSettings(horizon_s=1.0)
        a = evaluate(Scenario(), 123, settings=settings)
        b = evaluate(Scenario(), 124, settings=settings)
        self.assertNotEqual(a["initial_npc_delta"], b["initial_npc_delta"])

    def test_headless_gif(self):
        with tempfile.TemporaryDirectory() as td:
            gif = Path(td)/"smoke.gif"
            result = evaluate(Scenario(), 123, settings=SimSettings(horizon_s=1), gif=gif)
            self.assertTrue(gif.exists())
            self.assertGreater(gif.stat().st_size, 100)
            self.assertGreater(result["policy_steps"], 0)


if __name__ == "__main__":
    unittest.main()
