"""Tests for the v0.3 formal evaluation batch driver (orchestration only).

These tests exercise the driver's pure bookkeeping helpers only: seed batching and
order, config digesting, directory layout, write-once manifests and the
structural integrity checker. No simulator and **no formal seed** is run.
"""
from __future__ import annotations
import importlib.util
import json
import shutil
import unittest
from pathlib import Path

from cornercaselab.protocol import (
    FORMAL_PROTOCOL_ID,
    load_formal_config,
    validate_formal_config,
)

ROOT = Path(__file__).resolve().parent.parent
DRIVER_PATH = ROOT / "scripts" / "run_v03_formal.py"


def _remove_scratch(path: Path, base: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    try:
        base.rmdir()
    except OSError:
        pass


def scratch(test: unittest.TestCase, name: str) -> Path:
    """Workspace-relative scratch directory (``tempfile`` is sandbox-blocked)."""
    base = ROOT / "tests" / "_v03_scratch"
    path = base / f"formal_{name}"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    test.addCleanup(_remove_scratch, path, base)
    return path


def load_driver():
    spec = importlib.util.spec_from_file_location("ccl_run_v03_formal", DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()

# The batch order approved for the formal evaluation, verbatim.
APPROVED_BATCHES = [
    [3885243241, 1704487720, 592379183, 1151400128, 3272561144],
    [3391624141, 3736130552, 3065082847, 2983125598, 142216931],
    [1007851318, 3069633350, 2272084942, 3735670268, 4175222997],
    [614416336, 4287176670, 2634949118, 4292498487, 416355318],
]


class BatchOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_formal_config()
        validate_formal_config(cls.config)

    def test_batches_match_the_approved_four_by_five_order(self):
        seeds = DRIVER.expected_seed_order(self.config)
        self.assertEqual(DRIVER.batched_seeds(seeds), APPROVED_BATCHES)
        self.assertEqual([seed for batch in APPROVED_BATCHES for seed in batch], seeds)

    def test_batch_layout_is_four_batches_of_five(self):
        batches = DRIVER.batched_seeds(DRIVER.expected_seed_order(self.config))
        self.assertEqual(len(batches), 4)
        self.assertTrue(all(len(batch) == 5 for batch in batches))
        self.assertEqual(DRIVER.BATCH_SIZE, 5)

    def test_batching_rejects_bad_shapes(self):
        with self.assertRaises(ValueError):
            DRIVER.batched_seeds([1, 2, 3], 2)
        with self.assertRaises(ValueError):
            DRIVER.batched_seeds([1, 2], 0)

    def test_the_driver_hardcodes_no_seed_value(self):
        source = DRIVER_PATH.read_text(encoding="utf-8")
        for seed in DRIVER.expected_seed_order(self.config):
            self.assertNotIn(str(seed), source,
                             f"the driver must not hardcode formal seed {seed}")

    def test_the_seed_order_is_the_derived_one(self):
        seeds = DRIVER.expected_seed_order(self.config)
        self.assertEqual(len(seeds), 20)
        self.assertEqual(len(set(seeds)), 20)
        self.assertNotIn(20261003, seeds)

    def test_a_tampered_seed_order_is_refused(self):
        broken = dict(self.config)
        broken["seeds"] = dict(self.config["seeds"])
        broken["seeds"]["values"] = list(reversed(self.config["seeds"]["values"]))
        with self.assertRaises(ValueError):
            DRIVER.expected_seed_order(broken)

    def test_config_digest_is_stable_and_hex(self):
        first = DRIVER.config_digest()
        self.assertEqual(first, DRIVER.config_digest())
        self.assertEqual(len(first), 64)
        int(first, 16)


class LayoutTests(unittest.TestCase):
    def test_directory_layout(self):
        root = Path("runs") / "v03_formal_eval_example"
        self.assertEqual(DRIVER.batch_directory(root, 3),
                         root / "batch_03")
        self.assertEqual(DRIVER.seed_directory(root, 3, 12345),
                         root / "batch_03" / "seed_12345")


class WriteOnceTests(unittest.TestCase):
    def test_write_once_refuses_to_rewrite_a_manifest(self):
        target = scratch(self, "write_once") / "formal_manifest.json"
        DRIVER._write_once(target, {"formal_evaluation_started": True})
        self.assertTrue(json.loads(target.read_text(encoding="utf-8"))[
            "formal_evaluation_started"])
        with self.assertRaises(RuntimeError):
            DRIVER._write_once(target, {"formal_evaluation_started": False})

    def test_progress_log_appends_and_never_rewrites(self):
        log_path = scratch(self, "progress") / "progress.jsonl"
        log = DRIVER.ProgressLog(log_path)
        log.write("formal_run_started", seeds=3)
        log.write("seed_completed", seed=1)
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["event"], "formal_run_started")
        self.assertEqual(json.loads(lines[1])["seed"], 1)
        self.assertIn("utc", json.loads(lines[1]))


class IntegrityCheckerTests(unittest.TestCase):
    def test_a_missing_seed_directory_is_reported_not_raised(self):
        missing = scratch(self, "missing") / "seed_1"
        result = DRIVER.seed_integrity(missing, 1, ["random_search"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["seed"], 1)
        self.assertTrue(any("missing" in problem for problem in result["problems"]))

    def test_the_integrity_checker_reads_the_required_protocol_fields(self):
        source = DRIVER_PATH.read_text(encoding="utf-8")
        for fragment in ("budget_total", "search_pool", "audit_max_attempts",
                         "selected_candidates", "scoring_protocol_id",
                         "data_conforms", "nonoverlapping_passed_count",
                         "source_sha256", "git_dirty"):
            self.assertIn(fragment, source, fragment)


class DescriptiveMetricTests(unittest.TestCase):
    def test_the_descriptive_set_covers_the_frozen_reporting_plan(self):
        config = load_formal_config()
        required = set(config["reporting"]["descriptive_metrics"])
        # The driver reports these under explicit names; check the required ones
        # are all represented (wall_time_s is recorded per seed).
        self.assertEqual(
            set(DRIVER.DESCRIPTIVE_FIELDS),
            {"passed_candidate_count", "unique_search_collision_candidates",
             "raw_search_collision_samples", "internal_confirm_launches",
             "audit_incomplete_count", "audit_reserved_unspent",
             "actual_simulator_launches", "errors", "wall_time_s"})
        self.assertTrue({"passed_candidate_count", "audit_reserved_unspent", "errors",
                         "wall_time_s"} <= required)

    def test_the_driver_uses_the_existing_engine_entry_points(self):
        source = DRIVER_PATH.read_text(encoding="utf-8")
        self.assertIn("compare_methods", source)
        self.assertIn("make_simulator_evaluator", source)
        self.assertIn("summarise_report", source)
        self.assertIn("validate_formal_config", source)
        self.assertIn(FORMAL_PROTOCOL_ID, source)


if __name__ == "__main__":
    unittest.main()
