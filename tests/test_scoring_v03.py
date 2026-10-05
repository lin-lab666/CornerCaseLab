"""v0.3 scoring-layer tests.

Everything here is IN MEMORY: no saved run is read, no simulator is launched and
no evaluator is invoked. These tests exercise only the offline scoring protocol
``v03_scoring_v1``.
"""
from __future__ import annotations
import itertools
import unittest

from cornercaselab.domain import BOUNDS, PERTURBATION, Scenario
from cornercaselab.metrics import wilson_interval
from cornercaselab.scoring import (
    INCOMPLETE,
    INVALID,
    NOT_APPLICABLE,
    NOT_PASSED,
    PASSED,
    PASS_LOWER_BOUND,
    REQUIRED_AUDIT_REPEATS,
    SCORING_PROTOCOL_ID,
    SPATIAL_EXACT_LIMIT,
    ScoringDataError,
    candidate_csv_rows,
    maximum_nonoverlapping_subset,
    neighbourhood,
    neighbourhoods_overlap,
    planned_repeats_from_summary,
    run_eligibility,
    run_is_complete,
    score_candidate,
    score_method,
    wilson95,
)

BASE = dict(ego_speed=27.0, front_gap=45.0, front_speed=25.0, ramp_x=110.0,
            ramp_speed=20.0, ramp_target_speed=30.0)


def scenario(**overrides) -> Scenario:
    values = dict(BASE)
    values.update(overrides)
    return Scenario(**values)


def candidate(s: Scenario, *, collapses: int = 15, episodes: int = 30,
              attempts: int | None = None, failures: list | None = None,
              selected: bool = True) -> dict:
    """A saved-looking candidate record with self-consistent audit counters."""
    if failures is None:
        failures = [True] * collapses + [False] * (episodes - collapses)
    return {
        "candidate_id": s.uid,
        "scenario": s.to_dict(),
        "audit_attempts": episodes if attempts is None else attempts,
        "audit_episodes": episodes,
        "audit_collapses": collapses,
        "audit_failures": failures,
        "selected_for_audit": selected,
    }


def nb(**overrides):
    return neighbourhood(scenario(**overrides))


class WilsonProtocolTests(unittest.TestCase):
    def test_wilson95_reuses_the_existing_interval_unchanged(self):
        for successes in range(0, 31):
            self.assertEqual(wilson95(successes, 30),
                             wilson_interval(successes, 30, z=1.959963984540054))

    def test_the_pass_boundary_is_between_20_and_21_of_30(self):
        self.assertLess(wilson95(20, 30)[0], PASS_LOWER_BOUND)
        self.assertGreater(wilson95(21, 30)[0], PASS_LOWER_BOUND)

    def test_protocol_identity(self):
        self.assertEqual(SCORING_PROTOCOL_ID, "v03_scoring_v1")
        self.assertEqual(REQUIRED_AUDIT_REPEATS, 30)
        self.assertEqual(PASS_LOWER_BOUND, 0.5)


class NeighbourhoodTests(unittest.TestCase):
    def test_bounds_reuse_the_frozen_radii(self):
        bounds = neighbourhood(scenario())
        by_name = dict(zip(BOUNDS, bounds))
        self.assertEqual(by_name["front_gap"], (43.0, 47.0))       # +/- 2 m
        self.assertEqual(by_name["ramp_x"], (108.0, 112.0))        # +/- 2 m
        self.assertEqual(by_name["ego_speed"], (26.0, 28.0))       # +/- 1 m/s
        self.assertEqual(by_name["front_speed"], (24.0, 26.0))
        self.assertEqual(by_name["ramp_speed"], (19.0, 21.0))
        self.assertEqual(by_name["ramp_target_speed"], (29.0, 31.0))
        for name in BOUNDS:
            self.assertEqual(PERTURBATION[name],
                             2.0 if name in {"front_gap", "ramp_x"} else 1.0)

    def test_near_a_domain_bound_the_interval_is_intersected_not_exceeded(self):
        low = dict(zip(BOUNDS, neighbourhood(scenario(front_gap=19.0))))
        self.assertEqual(low["front_gap"], (18.0, 21.0))
        high = dict(zip(BOUNDS, neighbourhood(scenario(front_gap=79.0))))
        self.assertEqual(high["front_gap"], (77.0, 80.0))

    def test_a_shared_boundary_counts_as_overlap(self):
        touching = nb(front_gap=44.0)          # [42, 46]; [38, 42] touches at 42
        self.assertTrue(neighbourhoods_overlap(nb(front_gap=40.0), touching))
        separated = nb(front_gap=45.0)         # [43, 47]; strictly above 42
        self.assertFalse(neighbourhoods_overlap(nb(front_gap=40.0), separated))

    def test_one_separated_dimension_is_enough_for_not_overlapping(self):
        self.assertFalse(neighbourhoods_overlap(nb(front_gap=20.0), nb(front_gap=70.0)))
        self.assertTrue(neighbourhoods_overlap(nb(), nb()))


class SpatialDedupTests(unittest.TestCase):
    def test_identical_neighbourhoods_count_once(self):
        same = nb()
        self.assertEqual(maximum_nonoverlapping_subset({"a": same, "b": same}), ["a"])

    def test_two_separated_neighbourhoods_count_twice(self):
        result = maximum_nonoverlapping_subset({"a": nb(front_gap=30.0),
                                                "b": nb(front_gap=70.0)})
        self.assertEqual(result, ["a", "b"])

    def test_empty_input_scores_zero(self):
        self.assertEqual(maximum_nonoverlapping_subset({}), [])

    def test_touching_boundaries_are_treated_as_overlapping(self):
        one = nb(front_gap=40.0)               # [38, 42]
        two = nb(front_gap=44.0)               # [42, 46]
        self.assertEqual(maximum_nonoverlapping_subset({"a": one, "b": two}), ["a"])

    def test_a_chain_is_solved_exactly_not_by_connected_components(self):
        """A-B overlap, B-C overlap, A-C separated -> maximum 2, not the 1 component."""
        chain = {"a": nb(front_gap=40.0),      # [38, 42]
                 "b": nb(front_gap=44.0),      # [42, 46] touches a
                 "c": nb(front_gap=48.0)}      # [46, 50] touches b, separated from a
        self.assertTrue(neighbourhoods_overlap(chain["a"], chain["b"]))
        self.assertTrue(neighbourhoods_overlap(chain["b"], chain["c"]))
        self.assertFalse(neighbourhoods_overlap(chain["a"], chain["c"]))
        self.assertEqual(maximum_nonoverlapping_subset(chain), ["a", "c"])

    def test_ties_break_lexicographically_on_candidate_ids(self):
        work = {"aaa": nb(front_gap=40.0),     # overlaps bbb
                "bbb": nb(front_gap=44.0),
                "zzz": nb(front_gap=70.0)}     # separated from both
        self.assertEqual(maximum_nonoverlapping_subset(work), ["aaa", "zzz"])
        self.assertEqual(maximum_nonoverlapping_subset(dict(reversed(list(work.items())))),
                         ["aaa", "zzz"])

    def test_input_order_does_not_change_the_answer(self):
        work = {"c": nb(front_gap=48.0), "a": nb(front_gap=40.0), "b": nb(front_gap=44.0)}
        first = maximum_nonoverlapping_subset(work)
        shuffled = {"b": work["b"], "c": work["c"], "a": work["a"]}
        self.assertEqual(maximum_nonoverlapping_subset(shuffled), first)
        self.assertEqual(first, ["a", "c"])

    def test_beyond_the_exact_limit_is_refused_not_approximated(self):
        above = {f"c{i:03d}": nb(front_gap=20.0 + i) for i in range(SPATIAL_EXACT_LIMIT + 1)}
        with self.assertRaises(ScoringDataError):
            maximum_nonoverlapping_subset(above)
        at_limit = {f"c{i:03d}": nb(front_gap=20.0 + i) for i in range(SPATIAL_EXACT_LIMIT)}
        chosen = maximum_nonoverlapping_subset(at_limit)
        self.assertTrue(chosen)
        self.assertLessEqual(len(chosen), SPATIAL_EXACT_LIMIT)
        for first, second in itertools.combinations(chosen, 2):
            self.assertFalse(neighbourhoods_overlap(at_limit[first], at_limit[second]))

    def test_the_solver_matches_brute_force_exactness(self):
        """A six-vertex overlap path: the exact answer is 3, not 1 or 2."""
        work = {f"v{i}": nb(front_gap=40.0 + 4.0 * i) for i in range(6)}
        ids = sorted(work)
        brute = 0
        for size in range(len(ids), 0, -1):
            if any(all(not neighbourhoods_overlap(work[a], work[b])
                       for a, b in itertools.combinations(combo, 2))
                   for combo in itertools.combinations(ids, size)):
                brute = size
                break
        chosen = maximum_nonoverlapping_subset(work)
        self.assertEqual(brute, 3)
        self.assertEqual(len(chosen), brute)
        for first, second in itertools.combinations(chosen, 2):
            self.assertFalse(neighbourhoods_overlap(work[first], work[second]))


class CandidateStabilityTests(unittest.TestCase):
    def test_20_of_30_does_not_pass(self):
        score = score_candidate(candidate(scenario(), collapses=20), planned_repeats=30)
        self.assertEqual(score["classification"], NOT_PASSED)
        self.assertIs(score["stable"], False)
        self.assertLess(score["wilson_lower"], PASS_LOWER_BOUND)
        self.assertEqual(score["valid_observations"], 30)
        self.assertEqual(score["audit_errors"], 0)

    def test_21_of_30_passes(self):
        score = score_candidate(candidate(scenario(), collapses=21), planned_repeats=30)
        self.assertEqual(score["classification"], PASSED)
        self.assertIs(score["stable"], True)
        self.assertGreater(score["wilson_lower"], PASS_LOWER_BOUND)
        self.assertAlmostEqual(score["audit_collapse_rate"], 0.7)

    def test_an_error_shortfall_is_incomplete_not_unstable(self):
        score = score_candidate(candidate(scenario(), collapses=20, episodes=28, attempts=30),
                                planned_repeats=30)
        self.assertEqual(score["classification"], INCOMPLETE)
        # null, never False: a shortfall is not evidence of instability.
        self.assertIsNone(score["stable"])
        self.assertIsNone(score["wilson_lower"])
        self.assertEqual(score["valid_observations"], 28)
        self.assertEqual(score["audit_errors"], 2)
        self.assertIn("not be read as unstable", " ".join(score["reasons"]))

    def test_a_five_repeat_run_is_not_applicable(self):
        score = score_candidate(candidate(scenario(), collapses=3, episodes=5),
                                planned_repeats=5)
        self.assertEqual(score["classification"], NOT_APPLICABLE)
        self.assertIsNone(score["stable"])
        self.assertEqual(score["valid_observations"], 5)
        self.assertIn("n=30", " ".join(score["reasons"]))

    def test_an_unfinished_n30_run_is_not_applicable(self):
        score = score_candidate(candidate(scenario(), collapses=30), planned_repeats=30,
                                run_complete=False)
        self.assertEqual(score["classification"], NOT_APPLICABLE)
        self.assertIsNone(score["stable"])
        self.assertIn("not finished", " ".join(score["reasons"]))

    def test_illegal_counts_are_invalid_never_silently_passed(self):
        good = candidate(scenario(), collapses=30)
        cases = {
            "attempts exceed planned": dict(good, audit_attempts=31),
            "observations exceed planned": dict(good, audit_episodes=31,
                                               audit_failures=[True] * 31),
            "observations exceed attempts": dict(good, audit_attempts=29),
            "collapses exceed observations": dict(good, audit_collapses=31,
                                                  audit_failures=[True] * 30),
            "failure list length mismatch": dict(good, audit_failures=[True] * 29),
            "failure list sum mismatch": dict(
                good, audit_failures=[True] * 29 + [False], audit_collapses=30),
            "negative counts": dict(good, audit_attempts=-1),
        }
        for label, row in cases.items():
            with self.subTest(case=label):
                score = score_candidate(row, planned_repeats=30)
                self.assertEqual(score["classification"], INVALID)
                self.assertIsNone(score["stable"])
                self.assertTrue(score["reasons"])

    def test_missing_required_fields_are_reported(self):
        for field in ("candidate_id", "scenario", "audit_attempts", "audit_episodes",
                      "audit_collapses", "audit_failures"):
            with self.subTest(missing=field):
                row = candidate(scenario(), collapses=30)
                del row[field]
                score = score_candidate(row, planned_repeats=30)
                self.assertEqual(score["classification"], INVALID)
                self.assertIn(field, " ".join(score["reasons"]))

    def test_a_wrong_scenario_shape_is_invalid(self):
        row = candidate(scenario(), collapses=30)
        row["scenario"] = {"front_gap": 45.0}
        score = score_candidate(row, planned_repeats=30)
        self.assertEqual(score["classification"], INVALID)


class MethodScoringTests(unittest.TestCase):
    def test_an_empty_compliant_finished_run_scores_zero_not_null(self):
        score = score_method(method="m", candidates=[], planned_repeats=30,
                             run_complete=True)
        self.assertTrue(score["stability_criterion_applicable"])
        self.assertEqual(score["passed_candidate_count"], 0)
        self.assertEqual(score["nonoverlapping_passed_count"], 0)
        self.assertEqual(score["representative_candidate_ids"], [])
        self.assertEqual(score["formal_score"], 0)
        self.assertIsNone(score["formal_score_reason"])

    def test_a_five_repeat_run_has_a_null_formal_score(self):
        score = score_method(method="m", candidates=[candidate(scenario(front_gap=40.0),
                                                             collapses=3, episodes=5)],
                             planned_repeats=5, run_complete=True)
        self.assertFalse(score["stability_criterion_applicable"])
        self.assertIsNone(score["formal_score"])
        self.assertIsNone(score["nonoverlapping_passed_count"])
        self.assertIsNone(score["representative_candidate_ids"])
        self.assertEqual(score["not_applicable_candidate_count"], 1)

    def test_a_non_conforming_run_has_a_null_formal_score(self):
        row = candidate(scenario(front_gap=40.0), collapses=30)
        bad = dict(row, audit_attempts=31)
        score = score_method(method="m", candidates=[row, bad], planned_repeats=30,
                             run_complete=True)
        self.assertFalse(score["data_conforms"])
        self.assertIsNone(score["formal_score"])
        self.assertEqual(score["invalid_candidate_count"], 1)
        self.assertIn(bad["candidate_id"], score["invalid_candidate_ids"])

    def test_incomplete_candidates_are_excluded_from_the_passing_set(self):
        passed = candidate(scenario(front_gap=40.0), collapses=30)
        short = candidate(scenario(front_gap=70.0), collapses=20, episodes=28, attempts=30)
        score = score_method(method="m", candidates=[passed, short], planned_repeats=30,
                             run_complete=True)
        self.assertEqual(score["passed_candidate_count"], 1)
        self.assertEqual(score["incomplete_candidate_count"], 1)
        self.assertEqual(score["incomplete_candidate_ids"], [short["candidate_id"]])
        self.assertEqual(score["nonoverlapping_passed_count"], 1)
        self.assertEqual(score["formal_score"], 1)

    def test_only_passed_candidates_participate_in_the_spatial_score(self):
        """Two overlapping passes plus one separated non-pass must still score 1."""
        first = candidate(scenario(front_gap=40.0), collapses=30)
        second = candidate(scenario(front_gap=44.0), collapses=30)   # overlaps first
        failing = candidate(scenario(front_gap=70.0), collapses=5)   # separated, not passed
        score = score_method(method="m", candidates=[first, second, failing],
                             planned_repeats=30, run_complete=True)
        self.assertEqual(score["passed_candidate_count"], 2)
        self.assertEqual(score["not_passed_candidate_count"], 1)
        # Including the non-passed neighbour would have allowed 2.
        self.assertEqual(score["nonoverlapping_passed_count"], 1)

    def test_two_separated_passes_score_two(self):
        rows = [candidate(scenario(front_gap=30.0), collapses=30),
                candidate(scenario(front_gap=70.0), collapses=30)]
        score = score_method(method="m", candidates=rows, planned_repeats=30,
                             run_complete=True)
        self.assertEqual(score["passed_candidate_count"], 2)
        self.assertEqual(score["nonoverlapping_passed_count"], 2)
        self.assertEqual(score["representative_candidate_ids"],
                         sorted(row["candidate_id"] for row in rows))

    def test_candidate_order_does_not_change_score_or_tie_break(self):
        rows = [candidate(scenario(front_gap=40.0), collapses=30),
                candidate(scenario(front_gap=44.0), collapses=30),
                candidate(scenario(front_gap=70.0), collapses=30)]
        forward = score_method(method="m", candidates=rows, planned_repeats=30,
                               run_complete=True)
        backward = score_method(method="m", candidates=list(reversed(rows)),
                                planned_repeats=30, run_complete=True)
        self.assertEqual(forward["nonoverlapping_passed_count"],
                         backward["nonoverlapping_passed_count"])
        self.assertEqual(forward["representative_candidate_ids"],
                         backward["representative_candidate_ids"])
        self.assertEqual(forward["formal_score"], backward["formal_score"])

    def test_only_selected_candidates_are_scored(self):
        audited = candidate(scenario(front_gap=40.0), collapses=30, selected=True)
        unselected = candidate(scenario(front_gap=70.0), collapses=30, selected=False)
        score = score_method(method="m", candidates=[audited, unselected],
                             planned_repeats=30, run_complete=True,
                             selected_for_audit=[audited["candidate_id"]])
        self.assertEqual(score["audited_candidate_count"], 1)
        self.assertEqual(score["not_selected_count"], 1)
        self.assertEqual(score["passed_candidate_count"], 1)
        self.assertEqual(score["formal_score"], 1)

    def test_beyond_the_exact_limit_reports_unsupported_not_a_score(self):
        rows = [candidate(scenario(front_gap=20.0 + 1.5 * i), collapses=30)
                for i in range(SPATIAL_EXACT_LIMIT + 1)]
        score = score_method(method="m", candidates=rows, planned_repeats=30,
                             run_complete=True)
        self.assertEqual(score["spatial"]["status"], "unsupported")
        self.assertIsNone(score["formal_score"])
        self.assertIn("exact-solve limit", score["formal_score_reason"])

    def test_the_metric_is_labelled_as_neighbourhoods_not_root_causes(self):
        score = score_method(method="m", candidates=[], planned_repeats=30,
                             run_complete=True)
        text = (score["metric_name"] + " " + score["metric_caveat"]).lower()
        self.assertIn("neighbourhood", text)
        self.assertIn("not a count of distinct root causes", text)
        self.assertIn("connected components", text)


class FormatHelperTests(unittest.TestCase):
    def test_planned_repeats_is_read_from_either_layout(self):
        self.assertEqual(planned_repeats_from_summary({"plan": {"audit_repeats": 30}}), 30)
        self.assertEqual(
            planned_repeats_from_summary({"accounting": {"plan": {"audit_repeats": 5}}}), 5)
        with self.assertRaises(ScoringDataError):
            planned_repeats_from_summary({"accounting": {}})

    def test_run_completion_uses_accounting_and_checkpoint_stage(self):
        self.assertEqual(run_is_complete({"accounting": {"pending_unfinished_episodes": 0}},
                                         {"run": {"stage": "complete"}})[0], True)
        self.assertEqual(run_is_complete({"accounting": {"pending_unfinished_episodes": 1}},
                                         {"run": {"stage": "search"}})[0], False)
        self.assertEqual(run_is_complete({"accounting": {"pending_unfinished_episodes": 0}},
                                         {"run": {"stage": "search"}})[0], False)
        self.assertEqual(run_is_complete({"accounting": {}}, None)[0], False)

    def test_csv_rows_carry_counts_intervals_and_neighbourhood_bounds(self):
        row = candidate(scenario(front_gap=45.0), collapses=21)
        method_score = score_method(method="m", candidates=[row], planned_repeats=30,
                                    run_complete=True)
        csv_rows = candidate_csv_rows(method_score,
                                      {row["candidate_id"]: row["scenario"]})
        self.assertEqual(len(csv_rows), 1)
        out = csv_rows[0]
        self.assertEqual(out["classification"], PASSED)
        self.assertEqual(out["stable"], "true")
        self.assertEqual(out["audit_attempts"], 30)
        self.assertEqual(out["valid_observations"], 30)
        self.assertEqual(out["audit_errors"], 0)
        self.assertEqual(out["nb_front_gap_low"], "43.000000")
        self.assertEqual(out["nb_front_gap_high"], "47.000000")
        self.assertEqual(out["nonoverlapping_representative"], "true")
        self.assertTrue(out["wilson_lower"])

    def test_csv_rows_for_a_not_applicable_run_leave_stability_blank(self):
        row = candidate(scenario(), collapses=3, episodes=5)
        method_score = score_method(method="m", candidates=[row], planned_repeats=5,
                                    run_complete=True)
        out = candidate_csv_rows(method_score, {row["candidate_id"]: row["scenario"]})[0]
        self.assertEqual(out["classification"], NOT_APPLICABLE)
        self.assertEqual(out["stable"], "")
        self.assertEqual(out["wilson_lower"], "")


class EligibilityTests(unittest.TestCase):
    def test_eligibility_requires_a_finished_n30_run(self):
        self.assertTrue(run_eligibility(30, True)[0])
        self.assertFalse(run_eligibility(30, False)[0])
        self.assertFalse(run_eligibility(5, True)[0])
        self.assertIn("n=30", run_eligibility(5, True)[1])


if __name__ == "__main__":
    unittest.main()
