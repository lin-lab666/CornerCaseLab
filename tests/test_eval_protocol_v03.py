"""Tests for the frozen v0.3 formal evaluation protocol.

These tests validate the frozen config against the protocol document AND against
the live code. They run no simulator and, critically, **no formal evaluation
seed**: the formal seeds appear here only as data to be checked.
"""
from __future__ import annotations
import copy
from hashlib import sha256
import unittest
from pathlib import Path

from cornercaselab.budget import plan_spending
from cornercaselab.candidates import AUDIT_SELECTION_RULE, AUDIT_UNSPENT_RULE
from cornercaselab.domain import Scenario
from cornercaselab.experiments import (
    CALIBRATION_SEED,
    METHODS,
    MethodConfig,
    SeedPolicyError,
    ensure_evaluation_seed,
    internal_confirm_cap,
)
from cornercaselab.protocol import (
    FORMAL_PROTOCOL_ID,
    FORMAL_SEED_COUNT,
    FORMAL_SEED_DERIVATION,
    ProtocolConfigError,
    check_evaluation_seed,
    derive_formal_seeds,
    documentation_problems,
    formal_doc_path,
    formal_method_config,
    formal_plan,
    formal_seeds,
    load_formal_config,
    validate_formal_config,
)
from cornercaselab.scoring import (
    NOT_PASSED,
    PASSED,
    PASS_LOWER_BOUND,
    REQUIRED_AUDIT_REPEATS,
    SCORING_PROTOCOL_ID,
    SPATIAL_EXACT_LIMIT,
    WILSON_Z,
    score_candidate,
    wilson95,
)


def tampered(config: dict, path: tuple, value) -> dict:
    """A deep copy of the config with one nested field replaced."""
    clone = copy.deepcopy(config)
    node = clone
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return clone


class FrozenConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_formal_config()

    def test_the_frozen_config_validates_against_the_protocol_and_the_code(self):
        summary = validate_formal_config(self.config)
        self.assertEqual(summary["protocol_id"], FORMAL_PROTOCOL_ID)
        self.assertEqual(summary["total_episode_budget"], 1000)
        self.assertEqual(summary["search_pool"], 700)
        self.assertEqual(summary["audit_pool"], 300)
        self.assertEqual(summary["audit_candidates"], 10)
        self.assertEqual(summary["audit_repeats"], 30)
        self.assertEqual(summary["global_internal_confirmation_cap"], 175)
        self.assertEqual(summary["primary_metric"], "SNY")
        self.assertEqual(summary["primary_metric_field"], "nonoverlapping_passed_count")
        self.assertEqual(summary["scoring_protocol_id"], SCORING_PROTOCOL_ID)

    def test_budget_closes_exactly_through_the_engine(self):
        plan = formal_plan(self.config)
        self.assertEqual((plan.total, plan.search_pool, plan.audit_pool), (1000, 700, 300))
        self.assertEqual(plan.audit_candidates * plan.audit_repeats, plan.audit_pool)
        self.assertEqual(plan.search_pool + plan.audit_pool, plan.total)
        with self.assertRaises(ValueError):
            plan_spending(1000, search_fraction=0.6, audit_candidates=10, audit_repeats=30)

    def test_method_parameters_match_the_engine(self):
        config = formal_method_config(self.config)
        self.assertEqual(config, MethodConfig(confirm_repeats=2, pool_fraction=0.25,
                                              min_internal_per_candidate=1,
                                              max_internal_per_candidate=5))
        self.assertEqual(internal_confirm_cap(config, formal_plan(self.config)), 175)
        self.assertEqual(config.confirm_repeats, 2)
        self.assertEqual(config.max_internal_per_candidate, 5)

    def test_selection_rule_and_unspent_rule_match_the_code(self):
        self.assertEqual(self.config["final_audit_selection"]["rule_id"],
                         AUDIT_SELECTION_RULE)
        self.assertEqual(self.config["audit"]["unspent_rule"], AUDIT_UNSPENT_RULE)
        self.assertEqual(self.config["audit"]["backfill"], "none")

    def test_primary_metric_limits_and_caveats(self):
        limit = self.config["scoring"]["primary_metric"]["exact_solver_limit"]
        self.assertEqual(limit, SPATIAL_EXACT_LIMIT)
        # The frozen audit reservation must fit inside the exact solver.
        self.assertGreaterEqual(limit, self.config["audit"]["audit_candidates"])
        caveats = " ".join(
            self.config["scoring"]["primary_metric"]["must_not_be_interpreted_as"]).lower()
        self.assertIn("distinct root causes", caveats)
        self.assertIn("probability", caveats)
        self.assertIn("failure regions", caveats)

    def test_calibration_seed_is_rejected_for_evaluation(self):
        for seed in formal_seeds(self.config):
            self.assertNotEqual(seed, CALIBRATION_SEED)
        with self.assertRaises(SeedPolicyError):
            check_evaluation_seed(CALIBRATION_SEED)
        with self.assertRaises(SeedPolicyError):
            ensure_evaluation_seed(CALIBRATION_SEED, "evaluation")
        self.assertEqual(ensure_evaluation_seed(CALIBRATION_SEED, "development"),
                         CALIBRATION_SEED)
        self.assertFalse(self.config["formal_evaluation_started"])

    def test_every_formal_seed_would_be_accepted_for_evaluation(self):
        for seed in formal_seeds(self.config):
            self.assertEqual(ensure_evaluation_seed(seed, "evaluation"), seed)

    def test_the_document_matches_the_config_and_the_code(self):
        self.assertEqual(documentation_problems(self.config), [])
        text = Path(formal_doc_path()).read_text(encoding="utf-8")
        self.assertIn(FORMAL_PROTOCOL_ID, text)
        # The documented seed derivation must name the exact hashed label,
        # the endianness and the index range.
        self.assertIn("CornerCaseLab-v0.3-formal-{i}", text)
        self.assertIn("big-endian", text)
        self.assertIn("i = 0..19", text)
        self.assertIn("READY FOR FORMAL EVALUATION", text)

    def test_the_document_checker_reports_a_missing_document(self):
        problems = documentation_problems(self.config, Path("does-not-exist.md"))
        self.assertTrue(problems)
        self.assertIn("not found", problems[0])


class FormalSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_formal_config()

    def test_exactly_twenty_unique_seeds_in_fixed_order(self):
        seeds = self.config["seeds"]["values"]
        self.assertEqual(len(seeds), FORMAL_SEED_COUNT)
        self.assertEqual(len(set(seeds)), FORMAL_SEED_COUNT)
        self.assertEqual(seeds, derive_formal_seeds(FORMAL_SEED_COUNT))
        self.assertTrue(all(0 <= seed < 2 ** 32 for seed in seeds))

    def test_stored_seeds_equal_their_documented_derivation(self):
        expected = []
        for index in range(FORMAL_SEED_COUNT):
            payload = f"CornerCaseLab-v0.3-formal-{index}".encode()
            expected.append(int.from_bytes(sha256(payload).digest()[:4], "big"))
        self.assertEqual(self.config["seeds"]["values"], expected)
        self.assertEqual(expected[0], 3885243241)
        self.assertEqual(expected[19], 416355318)

    def test_no_calibration_seed_and_no_duplicate(self):
        seeds = self.config["seeds"]["values"]
        self.assertNotIn(CALIBRATION_SEED, seeds)
        self.assertEqual(self.config["seeds"]["count"], FORMAL_SEED_COUNT)
        self.assertTrue(self.config["seeds"]["paired"])

    def test_reordering_or_editing_a_seed_is_refused(self):
        reordered = tampered(self.config, ("seeds", "values"),
                             list(reversed(self.config["seeds"]["values"])))
        with self.assertRaises(ProtocolConfigError):
            validate_formal_config(reordered)
        edited = tampered(self.config, ("seeds", "values"),
                          [1] + self.config["seeds"]["values"][1:])
        with self.assertRaises(ProtocolConfigError):
            validate_formal_config(edited)
        short = tampered(self.config, ("seeds", "values"),
                         self.config["seeds"]["values"][:-1])
        with self.assertRaises(ProtocolConfigError):
            validate_formal_config(short)
        with_calibration = tampered(self.config, ("seeds", "values"),
                                    self.config["seeds"]["values"][:-1] + [CALIBRATION_SEED])
        with self.assertRaises(ProtocolConfigError):
            validate_formal_config(with_calibration)


class TamperedConfigTests(unittest.TestCase):
    """Any deviation from the frozen protocol must be refused, not tolerated."""

    @classmethod
    def setUpClass(cls):
        cls.config = load_formal_config()

    def _assert_refused(self, path: tuple, value) -> None:
        with self.subTest(field=".".join(path), value=value):
            with self.assertRaises(ProtocolConfigError):
                validate_formal_config(tampered(self.config, path, value))

    def test_budget_changes_are_refused(self):
        for path, value in ((("budget", "total_episode_budget"), 999),
                            (("budget", "search_pool"), 800),
                            (("budget", "audit_pool"), 200),
                            (("budget", "search_fraction"), 0.8),
                            (("budget", "renormalise_on_unspent_audit"), True)):
            self._assert_refused(path, value)

    def test_audit_changes_are_refused(self):
        for path, value in ((("audit", "audit_candidates"), 12),
                            (("audit", "audit_candidates"), 20),
                            (("audit", "audit_repeats"), 20),
                            (("audit", "audit_repeats"), 31),
                            (("audit", "backfill"), "extend_selected_round_robin"),
                            (("audit", "unspent_rule"), "renormalise_v1")):
            self._assert_refused(path, value)

    def test_method_parameter_changes_are_refused(self):
        for path, value in ((("method_parameters", "confirm_repeats"), 1),
                            (("method_parameters", "confirm_repeats"), 3),
                            (("method_parameters", "pool_fraction"), 0.5),
                            (("method_parameters", "min_internal_per_candidate"), 2),
                            (("method_parameters", "max_internal_per_candidate"), 3),
                            (("method_parameters", "global_internal_confirmation_cap"), 200)):
            self._assert_refused(path, value)

    def test_scoring_changes_are_refused(self):
        for path, value in (
                (("scoring", "scoring_protocol_id"), "v03_scoring_v2"),
                (("scoring", "required_audit_repeats"), 20),
                (("scoring", "stable_criterion", "phase"), "all_phases"),
                (("scoring", "stable_criterion", "mixes_in"), ["internal_confirm"]),
                (("scoring", "stable_criterion", "z"), 1.96),
                (("scoring", "stable_criterion", "continuity_correction"), True),
                (("scoring", "stable_criterion", "lower_bound_strictly_greater_than"), 0.4),
                (("scoring", "stable_criterion", "minimum_passed_collapses_of_30"), 20),
                (("scoring", "primary_metric", "exact_solver_limit"), 5),
                (("scoring", "primary_metric", "field"), "passed_candidate_count"),
                (("scoring", "primary_metric", "exact_solver"), False)):
            self._assert_refused(path, value)

    def test_selection_and_identity_changes_are_refused(self):
        for path, value in (
                (("final_audit_selection", "rule_id"), "first_seen_only_v1"),
                (("final_audit_selection", "implementation_frozen"), False),
                (("protocol_id",), "some_other_protocol"),
                (("status",), "draft"),
                (("purpose",), "development"),
                (("policy",), "reactive"),
                (("formal_evaluation_started",), True),
                (("calibration_seed_development_only",), 12345)):
            self._assert_refused(path, value)

    def test_reporting_changes_are_refused(self):
        for path, value in (
                (("reporting", "interval", "resamples"), 1000),
                (("reporting", "interval", "bootstrap_seed"), 1),
                (("reporting", "interval", "confidence"), 0.9),
                (("reporting", "paired_contrasts_primary"),
                 ["random_search - adaptive_explore_confirm"]),
                (("reporting", "summary_statistics"), ["mean"])):
            self._assert_refused(path, value)

    def test_missing_top_level_sections_are_refused(self):
        for section in ("budget", "audit", "method_parameters", "scoring", "seeds",
                        "reporting", "final_audit_selection"):
            with self.subTest(missing=section):
                clone = copy.deepcopy(self.config)
                del clone[section]
                with self.assertRaises(ProtocolConfigError):
                    validate_formal_config(clone)


class StableCriterionTests(unittest.TestCase):
    """The frozen numeric boundary, exercised through the scoring layer."""

    @staticmethod
    def _row(collapses: int) -> dict:
        return {
            "candidate_id": "0123456789abcdef",
            "scenario": Scenario().to_dict(),
            "audit_attempts": 30,
            "audit_episodes": 30,
            "audit_collapses": collapses,
            "audit_failures": [True] * collapses + [False] * (30 - collapses),
        }

    def test_the_boundary_is_20_fail_and_21_pass(self):
        self.assertLess(wilson95(20, 30)[0], PASS_LOWER_BOUND)
        self.assertGreater(wilson95(21, 30)[0], PASS_LOWER_BOUND)
        self.assertEqual(score_candidate(self._row(20), planned_repeats=30)["classification"],
                         NOT_PASSED)
        self.assertEqual(score_candidate(self._row(21), planned_repeats=30)["classification"],
                         PASSED)

    def test_a_shortfall_is_incomplete_with_null_stability(self):
        row = dict(self._row(20), audit_episodes=28, audit_attempts=30,
                   audit_failures=[True] * 20 + [False] * 8)
        score = score_candidate(row, planned_repeats=30)
        self.assertEqual(score["classification"], "incomplete")
        self.assertIsNone(score["stable"])

    def test_the_frozen_z_and_repeat_count_match_the_scoring_layer(self):
        self.assertEqual(WILSON_Z, 1.959963984540054)
        self.assertEqual(REQUIRED_AUDIT_REPEATS, 30)
        self.assertEqual(self._row(30)["audit_attempts"], REQUIRED_AUDIT_REPEATS)

    def test_the_frozen_method_list_matches_the_engine(self):
        self.assertEqual(load_formal_config()["methods"], list(METHODS))


if __name__ == "__main__":
    unittest.main()
