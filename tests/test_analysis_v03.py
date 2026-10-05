"""Tests for the frozen analysis plan of ``cornercaselab_v03_eval_v1``.

Everything here runs on synthetic data: these tests validate the reporting plan
implementation and produce no formal statistic. No simulator and no formal seed
is involved.
"""
from __future__ import annotations
import unittest

from cornercaselab.analysis import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_CONFIDENCE,
    DEFAULT_RESAMPLES,
    PRIMARY_CONTRAST,
    SECONDARY_CONTRASTS,
    paired_bootstrap_ci,
    paired_contrasts,
    percentile,
    summarise_report,
    summarise_scores,
)
from cornercaselab.protocol import load_formal_config


class PercentileTests(unittest.TestCase):
    def test_linear_interpolation_between_order_statistics(self):
        values = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(percentile(values, 0.0), 1.0)
        self.assertAlmostEqual(percentile(values, 0.25), 1.75)
        self.assertAlmostEqual(percentile(values, 0.5), 2.5)
        self.assertAlmostEqual(percentile(values, 0.75), 3.25)
        self.assertAlmostEqual(percentile(values, 1.0), 4.0)

    def test_single_value_and_bad_arguments(self):
        self.assertAlmostEqual(percentile([7.0], 0.3), 7.0)
        with self.assertRaises(ValueError):
            percentile([], 0.5)
        with self.assertRaises(ValueError):
            percentile([1.0, 2.0], 1.5)


class SummaryStatisticsTests(unittest.TestCase):
    def test_mean_median_std_and_iqr_on_a_hand_checked_vector(self):
        summary = summarise_scores([0, 1, 2, 3, 4])
        self.assertEqual(summary["n"], 5)
        self.assertAlmostEqual(summary["mean"], 2.0)
        self.assertAlmostEqual(summary["median"], 2.0)
        self.assertAlmostEqual(summary["standard_deviation"], 1.5811388300841898)
        self.assertEqual(summary["standard_deviation_ddof"], 1)
        self.assertAlmostEqual(summary["q1"], 1.0)
        self.assertAlmostEqual(summary["q3"], 3.0)
        self.assertAlmostEqual(summary["iqr"], 2.0)
        self.assertEqual((summary["min"], summary["max"]), (0.0, 4.0))

    def test_sample_standard_deviation_is_ddof_one(self):
        summary = summarise_scores([1, 2, 3, 4])
        self.assertAlmostEqual(summary["mean"], 2.5)
        self.assertAlmostEqual(summary["median"], 2.5)
        self.assertAlmostEqual(summary["standard_deviation"], 1.2909944487358056)
        self.assertAlmostEqual(summary["iqr"], 1.5)

    def test_a_single_value_has_no_sample_deviation(self):
        summary = summarise_scores([3])
        self.assertEqual(summary["n"], 1)
        self.assertAlmostEqual(summary["mean"], 3.0)
        self.assertIsNone(summary["standard_deviation"])
        self.assertAlmostEqual(summary["iqr"], 0.0)

    def test_an_empty_vector_cannot_be_summarised(self):
        with self.assertRaises(ValueError):
            summarise_scores([])


class PairedBootstrapTests(unittest.TestCase):
    def test_constant_differences_collapse_the_interval(self):
        interval = paired_bootstrap_ci([2.0, 2.0, 2.0, 2.0])
        self.assertAlmostEqual(interval["mean_difference"], 2.0)
        self.assertAlmostEqual(interval["ci_low"], 2.0)
        self.assertAlmostEqual(interval["ci_high"], 2.0)
        self.assertEqual(interval["n_pairs"], 4)
        self.assertTrue(interval["paired"])
        self.assertEqual(interval["method"], "percentile paired bootstrap")

    def test_the_interval_brackets_the_point_estimate(self):
        differences = [3, -1, 0, 2, 5, -2, 1, 4]
        interval = paired_bootstrap_ci(differences)
        self.assertLessEqual(interval["ci_low"], interval["mean_difference"])
        self.assertGreaterEqual(interval["ci_high"], interval["mean_difference"])

    def test_the_same_seed_reproduces_the_same_interval(self):
        differences = [3, -1, 0, 2, 5, -2, 1, 4]
        first = paired_bootstrap_ci(differences, resamples=500, seed=12345)
        second = paired_bootstrap_ci(differences, resamples=500, seed=12345)
        self.assertEqual(first, second)
        other = paired_bootstrap_ci(differences, resamples=500, seed=999)
        self.assertNotEqual((first["ci_low"], first["ci_high"]),
                            (other["ci_low"], other["ci_high"]))

    def test_defaults_are_the_frozen_plan(self):
        interval = paired_bootstrap_ci([1.0, 0.0], resamples=50)
        self.assertEqual(interval["resamples"], 50)
        self.assertAlmostEqual(interval["confidence"], DEFAULT_CONFIDENCE)
        self.assertIn("row-major", interval["difference_draw_order"])

    def test_bad_arguments_are_refused(self):
        with self.assertRaises(ValueError):
            paired_bootstrap_ci([])
        with self.assertRaises(ValueError):
            paired_bootstrap_ci([1.0], resamples=0)
        with self.assertRaises(ValueError):
            paired_bootstrap_ci([1.0], confidence=1.0)


class PairedContrastTests(unittest.TestCase):
    @staticmethod
    def _scores(length: int = 20) -> dict:
        return {
            "random_search": [float(length - index) for index in range(length)],
            "fixed_explore_confirm": [float(length) for _ in range(length)],
            "adaptive_explore_confirm": [float(2 * length) for _ in range(length)],
        }

    def test_the_three_frozen_contrasts_are_produced_with_the_right_signs(self):
        reports = paired_contrasts(self._scores(), resamples=200)
        labels = [report["contrast"] for report in reports]
        self.assertEqual(labels, [
            "adaptive_explore_confirm - random_search",
            "adaptive_explore_confirm - fixed_explore_confirm",
            "fixed_explore_confirm - random_search",
        ])
        self.assertEqual([report["role"] for report in reports],
                         ["primary", "secondary", "secondary"])
        primary = reports[0]
        # adaptive is the constant 40, random runs 20..1 -> mean difference 40 - 10.5
        self.assertAlmostEqual(primary["mean_difference"], 29.5)
        self.assertEqual(primary["n_pairs"], 20)
        self.assertLessEqual(primary["ci_low"], primary["mean_difference"])
        self.assertGreaterEqual(primary["ci_high"], primary["mean_difference"])

    def test_mismatched_or_unknown_methods_are_refused(self):
        scores = self._scores()
        scores["fixed_explore_confirm"] = [1.0]
        with self.assertRaises(ValueError):
            paired_contrasts(scores, resamples=10)
        with self.assertRaises(KeyError):
            paired_contrasts({"random_search": [1.0]}, resamples=10)

    def test_default_contrasts_match_the_frozen_config(self):
        config = load_formal_config()
        primary = f"{PRIMARY_CONTRAST[0]} - {PRIMARY_CONTRAST[1]}"
        self.assertEqual([primary], config["reporting"]["paired_contrasts_primary"])
        secondary = [f"{a} - {b}" for a, b in SECONDARY_CONTRASTS]
        self.assertEqual(secondary, config["reporting"]["paired_contrasts_secondary"])


class ReportPlanTests(unittest.TestCase):
    def test_the_report_keeps_raw_scores_and_summaries(self):
        scores = {"a": [1.0, 2.0, 3.0], "b": [0.0, 0.0, 1.0]}
        report = summarise_report(scores, contrasts=[("a", "b")], resamples=100)
        self.assertEqual(report["raw_scores"]["a"], [1.0, 2.0, 3.0])
        self.assertEqual(report["per_method"]["a"]["n"], 3)
        self.assertAlmostEqual(report["per_method"]["b"]["mean"], 1.0 / 3.0)
        self.assertEqual(len(report["paired_contrasts"]), 1)
        self.assertEqual(report["paired_contrasts"][0]["role"], "secondary")
        self.assertIn("p-values are not the primary criterion",
                      report["primary_success_criterion"])

    def test_frozen_bootstrap_parameters_match_the_config(self):
        interval = load_formal_config()["reporting"]["interval"]
        self.assertEqual(DEFAULT_RESAMPLES, interval["resamples"])
        self.assertEqual(DEFAULT_BOOTSTRAP_SEED, interval["bootstrap_seed"])
        self.assertAlmostEqual(DEFAULT_CONFIDENCE, interval["confidence"])
        self.assertEqual(DEFAULT_RESAMPLES, 10000)
        self.assertEqual(DEFAULT_BOOTSTRAP_SEED, 314159265)

    def test_the_report_is_deterministic(self):
        scores = {"a": [3.0, 1.0, 4.0, 1.0, 5.0], "b": [2.0, 2.0, 1.0, 0.0, 4.0]}
        first = summarise_report(scores, contrasts=[("a", "b")], resamples=300)
        second = summarise_report(scores, contrasts=[("a", "b")], resamples=300)
        self.assertEqual(first, second)

    def test_the_report_refuses_to_default_to_absent_methods(self):
        with self.assertRaises(KeyError):
            summarise_report({"a": [1.0], "b": [2.0]}, resamples=10)


if __name__ == "__main__":
    unittest.main()
