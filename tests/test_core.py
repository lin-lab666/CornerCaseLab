"""Unit tests use pure functions and explicit mocks, NOT driving experiments."""
from __future__ import annotations
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from cornercaselab import __version__
from cornercaselab.cli import main
from cornercaselab.domain import Scenario, BOUNDS, PERTURBATION, canonical_json, check_seed, derive_seed, sample_scenario, perturb_scenario
from cornercaselab.experiment import run_trials
from cornercaselab.metrics import longitudinal_ttc, wilson_interval
from cornercaselab.policy import action_for, IDLE, FASTER, SLOWER
from cornercaselab.simulator import SimSettings
from cornercaselab.storage import read_json, write_json, source_digest


class ScenarioTests(unittest.TestCase):
    def test_default_roundtrip(self):
        s = Scenario()
        self.assertEqual(s, Scenario.from_dict(s.to_dict()))

    def test_identity_numeric_normalisation(self):
        self.assertEqual(Scenario(ego_speed=27).uid, Scenario(ego_speed=27.0).uid)

    def test_changed_parameter_changes_identity(self):
        self.assertNotEqual(Scenario().uid, Scenario(front_gap=46).uid)

    def test_reject_nonfinite(self):
        for v in [float("nan"), float("inf"), -float("inf")]:
            with self.subTest(v=v), self.assertRaises(ValueError):
                Scenario(ego_speed=v)

    def test_reject_nonnumeric(self):
        for v in [True, "27", None]:
            with self.subTest(v=v), self.assertRaises(ValueError):
                Scenario(ego_speed=v)

    def test_reject_outside_bounds(self):
        with self.assertRaises(ValueError):
            Scenario(front_gap=0)

    def test_strict_schema(self):
        with self.assertRaises(ValueError):
            Scenario.from_dict({"ego_speed": 27})
        with self.assertRaises(ValueError):
            Scenario.from_dict({**Scenario().to_dict(), "weather": "rain"})

    def test_seed_contract(self):
        for seed in [-1, 2**32, True, 1.5]:
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                check_seed(seed)
        self.assertEqual(check_seed(2**32-1), 2**32-1)

    def test_seed_stream_separation(self):
        a = derive_seed(1, "search", 0)
        self.assertEqual(a, derive_seed(1, "search", 0))
        self.assertNotEqual(a, derive_seed(1, "simulation", 0))
        self.assertNotEqual(a, derive_seed(1, "search", 1))

    def test_sampling_repeatability(self):
        self.assertEqual(sample_scenario(42), sample_scenario(42))
        self.assertNotEqual(sample_scenario(42), sample_scenario(43))

    def test_sampling_bounds(self):
        for seed in range(100):
            s = sample_scenario(seed)
            for name, (lo, hi) in BOUNDS.items():
                self.assertTrue(lo <= getattr(s, name) <= hi)

    def test_local_perturbations_change_parameters(self):
        base = Scenario()
        perturbed = perturb_scenario(base, 1)
        self.assertNotEqual(base, perturbed)
        self.assertEqual(perturbed, perturb_scenario(base, 1))
        for name, radius in PERTURBATION.items():
            self.assertLessEqual(abs(getattr(base, name)-getattr(perturbed, name)), radius+1e-6)

    def test_local_bound_intersection(self):
        s = Scenario(**{k: lo for k, (lo, hi) in BOUNDS.items()})
        for seed in range(50):
            p = perturb_scenario(s, seed)
            for name, (lo, hi) in BOUNDS.items():
                self.assertTrue(lo <= getattr(p, name) <= min(hi, lo+PERTURBATION[name]))

    def test_json_rejects_nan(self):
        with self.assertRaises(ValueError):
            canonical_json({"x": float("nan")})


class MetricPolicyTests(unittest.TestCase):
    def test_forward_ttc(self):
        obs = [[1, 0, 4, 30, 0], [1, 25, 4, 20, 0]]
        self.assertEqual(longitudinal_ttc(obs), 2.0)

    def test_ttc_excludes_adjacent_lane(self):
        self.assertIsNone(longitudinal_ttc([[1, 0, 4, 30, 0], [1, 25, 8, 20, 0]]))

    def test_ttc_excludes_nonclosing(self):
        self.assertIsNone(longitudinal_ttc([[1, 0, 4, 20, 0], [1, 25, 4, 30, 0]]))

    def test_ttc_excludes_absent(self):
        self.assertIsNone(longitudinal_ttc([[1, 0, 4, 30, 0], [0, 2, 4, 0, 0]]))

    def test_ttc_overlapping_projection(self):
        self.assertEqual(longitudinal_ttc([[1, 0, 4, 30, 0], [1, 3, 4, 20, 0]]), 0.0)

    def test_wilson_boundaries(self):
        lo, hi = wilson_interval(0, 20)
        self.assertAlmostEqual(lo, 0.0)
        self.assertGreater(hi, 0.1)
        lo, hi = wilson_interval(20, 20)
        self.assertAlmostEqual(hi, 1.0)
        self.assertLess(lo, 0.9)

    def test_wilson_symmetric(self):
        lo, hi = wilson_interval(10, 20)
        self.assertAlmostEqual(lo+hi, 1.0)

    def test_wilson_invalid(self):
        for s, n in [(0, 0), (-1, 10), (11, 10)]:
            with self.subTest(s=s, n=n), self.assertRaises(ValueError):
                wilson_interval(s, n)

    def test_reactive_brakes(self):
        self.assertEqual(action_for([[1, 0, 4, 30, 0], [1, 20, 4, 20, 0]]), SLOWER)

    def test_cruise_ignores_hazard(self):
        self.assertEqual(action_for([[1, 0, 4, 30, 0], [1, 20, 4, 20, 0]], "cruise"), IDLE)

    def test_free_road_speed_actions(self):
        self.assertEqual(action_for([[1, 0, 4, 20, 0]]), FASTER)
        self.assertEqual(action_for([[1, 0, 4, 30, 0]]), IDLE)
        self.assertEqual(action_for([[1, 0, 4, 33, 0]]), SLOWER)

    def test_unknown_policy(self):
        with self.assertRaises(ValueError):
            action_for([[1, 0, 4, 30, 0]], "trained_model_that_does_not_exist")

    def test_sim_settings(self):
        self.assertEqual(SimSettings().policy_hz, 5)
        with self.assertRaises(ValueError):
            SimSettings(simulation_hz=15, policy_hz=4)
        with self.assertRaises(ValueError):
            SimSettings(horizon_s=0)


class StorageExperimentTests(unittest.TestCase):
    def test_atomic_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/"nested"/"record.json"
            write_json(p, {"label": "单元测试", "n": 2})
            self.assertEqual(read_json(p)["n"], 2)
            self.assertEqual(list(p.parent.glob(".writing-*")), [])

    def test_source_digest_repeatable(self):
        self.assertEqual(source_digest(), source_digest())
        self.assertEqual(len(source_digest()), 64)

    def _specs(self, count=3):
        return [{"scenario": Scenario().to_dict(), "scenario_id": Scenario().uid,
                 "sim_seed": i} for i in range(count)]

    @staticmethod
    def _mock_eval(scene, seed, policy, settings):
        return {"termination": "collision" if seed == 0 else "goal",
                "ego_collision": seed == 0, "goal_reached": seed != 0,
                "note": "UNIT TEST MOCK: not a simulator result"}

    def test_budget_and_records(self):
        with tempfile.TemporaryDirectory() as td, redirect_stdout(io.StringIO()):
            out = Path(td)/"run"
            summary = run_trials(out, self._specs(), policy="reactive", settings=SimSettings(),
                purpose="unit_test_mock", evaluator=self._mock_eval)
            self.assertEqual(summary["launched_episodes"], 3)
            self.assertEqual(summary["valid_completed"], 3)
            self.assertEqual(summary["ego_collisions"], 1)
            self.assertEqual(len(list((out/"cases").glob("*.json"))), 3)
            ledger = (out/"ledger.jsonl").read_text().splitlines()
            self.assertEqual(len(ledger), 6)
            self.assertEqual(read_json(out/"manifest.json")["status"], "complete")

    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileExistsError):
                run_trials(Path(td), self._specs(), policy="reactive", settings=SimSettings(),
                    purpose="unit_test_mock", evaluator=self._mock_eval)

    def test_failed_episode_counts_budget_not_safety(self):
        def fail_on_second(scene, seed, policy, settings):
            if seed == 1:
                raise RuntimeError("deliberate unit-test failure")
            return self._mock_eval(scene, seed, policy, settings)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)/"run"
            with self.assertRaises(RuntimeError):
                run_trials(out, self._specs(), policy="reactive", settings=SimSettings(),
                    purpose="unit_test_mock", evaluator=fail_on_second)
            s = read_json(out/"summary.json")
            self.assertEqual(s["launched_episodes"], 2)
            self.assertEqual(s["valid_completed"], 1)
            self.assertEqual(s["errors"], 1)
            self.assertEqual(s["status"], "interrupted_or_error")
            self.assertEqual(read_json(out/"cases"/"000001.json")["status"], "error")

    def test_interrupt_preserves_completed_data(self):
        def interrupted(scene, seed, policy, settings):
            if seed == 1:
                raise KeyboardInterrupt()
            return self._mock_eval(scene, seed, policy, settings)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)/"run"
            with self.assertRaises(KeyboardInterrupt):
                run_trials(out, self._specs(), policy="reactive", settings=SimSettings(),
                    purpose="unit_test_mock", evaluator=interrupted)
            s = read_json(out/"summary.json")
            self.assertEqual(s["valid_completed"], 1)
            self.assertEqual(s["pending_or_interrupted"], 1)
            self.assertTrue((out/"cases"/"000000.json").exists())

    def test_confirmation_interval_is_not_search_interval(self):
        with tempfile.TemporaryDirectory() as td, redirect_stdout(io.StringIO()):
            out = Path(td)/"run"
            s = run_trials(out, self._specs(), policy="reactive", settings=SimSettings(),
                purpose="unit_test_mock", evaluator=self._mock_eval)
            self.assertNotIn("local_fixed_n_wilson95", s)

    def test_sample_cli_needs_no_sim(self):
        with tempfile.TemporaryDirectory() as td, redirect_stdout(io.StringIO()):
            out = Path(td)/"sample.json"
            self.assertEqual(main(["sample", "--count", "2", "--seed", "42", "--out", str(out)]), 0)
            result = read_json(out)
            self.assertEqual(len(result["trials"]), 2)
            self.assertEqual(result["status"], "sampled_only_not_simulated")


class VersionConsistencyTests(unittest.TestCase):
    """The runtime version must agree with the packaged project version."""

    def test_runtime_version_matches_pyproject(self):
        root = Path(__file__).resolve().parent.parent
        declared = tomllib.loads((root/"pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(__version__, declared["project"]["version"])

    def test_the_declared_version_is_the_v03_release(self):
        self.assertEqual(__version__, "0.3.0")


if __name__ == "__main__":
    unittest.main()
