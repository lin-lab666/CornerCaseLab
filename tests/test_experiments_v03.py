"""v0.3 tests for the three unified-budget methods.

The evaluator here is a deterministic hash-based FAKE. It exercises the budget
machinery and the allocation policies only; its numbers are not simulation
results and must never be reported as such.
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import unittest

from cornercaselab.budget import (
    FINAL_AUDIT,
    INTERNAL_CONFIRM,
    OUTCOME_FIELDS,
    SEARCH,
    EpisodeBudget,
    plan_spending,
)
from cornercaselab.candidates import (
    AUDIT_SELECTION_SEMANTICS_FROZEN,
    AUDIT_SELECTION_TODO,
    CandidateState,
)
from cornercaselab.domain import Scenario, perturb_scenario
from cornercaselab.experiments import (
    CALIBRATION_SEED,
    METHODS,
    SEED_STREAMS,
    MethodConfig,
    SearchProblem,
    SeedCollisionError,
    SeedPolicyError,
    SeedStreamGuard,
    _iter_derived_seeds,
    compare_methods,
    ensure_evaluation_seed,
    internal_confirm_cap,
    run_method,
    seed_stream_report,
    write_method_output,
)
from cornercaselab.metrics import wilson_interval
from cornercaselab.simulator import SimSettings

SMALL = dict(total_budget=40, search_fraction=0.5, audit_candidates=2, audit_repeats=10)


def _remove_scratch(path: Path, base: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    try:
        base.rmdir()
    except OSError:
        pass


def scratch_dir(test: unittest.TestCase, name: str) -> Path:
    """A workspace-relative scratch directory.

    ``tempfile`` is unusable under the DSH Windows write sandbox, so these tests
    allocate their artifacts inside the workspace and always remove them again
    (including the shared parent once it is empty).
    """
    base = Path(__file__).resolve().parent / "_v03_scratch"
    path = base / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    test.addCleanup(_remove_scratch, path, base)
    return path


class ScriptedEvaluator:
    """Deterministic collision fake with a tunable rate. Counts every call."""

    def __init__(self, rate_percent: int = 30, fail_every: int | None = None):
        self.rate_percent = rate_percent
        self.fail_every = fail_every
        self.calls = 0

    def __call__(self, scenario, sim_seed, policy, settings):
        self.calls += 1
        if self.fail_every and self.calls % self.fail_every == 0:
            raise RuntimeError("scripted evaluator failure")
        score = int(float(scenario.front_gap) * 13 + float(scenario.ramp_x) * 3) % 100
        hit = score < self.rate_percent
        return {"ego_collision": hit, "goal_reached": not hit, "npc_collisions": 0,
                "termination": "collision" if hit else "goal", "error": None,
                "note": "SCRIPTED TEST EVALUATOR: not a simulator result"}


def run_one(method: str, evaluator, seed: int = 4242, total: int = SMALL["total_budget"],
            config: MethodConfig | None = None, purpose: str = "development",
            checkpoint_path: Path | str | None = None,
            checkpoint_every: int | None = 1,
            metadata: dict | None = None):
    plan = plan_spending(total, search_fraction=SMALL["search_fraction"],
                         audit_candidates=SMALL["audit_candidates"],
                         audit_repeats=SMALL["audit_repeats"])
    settings = SimSettings()
    budget = EpisodeBudget(total=total, seed=seed, policy="reactive",
                           settings=settings, plan=plan)
    return run_method(method, out_budget=budget, plan=plan,
                      problem=SearchProblem(policy="reactive", settings=settings),
                      config=config or MethodConfig(), evaluator=evaluator,
                      purpose=purpose, checkpoint_path=checkpoint_path,
                      checkpoint_every=checkpoint_every,
                      metadata=metadata)


class SeedPolicyTests(unittest.TestCase):
    def test_calibration_seed_blocked_for_evaluation(self):
        with self.assertRaises(SeedPolicyError):
            ensure_evaluation_seed(CALIBRATION_SEED, "evaluation")

    def test_calibration_seed_allowed_for_development(self):
        self.assertEqual(ensure_evaluation_seed(CALIBRATION_SEED, "development"),
                         CALIBRATION_SEED)

    def test_other_seed_allowed_for_evaluation(self):
        self.assertEqual(ensure_evaluation_seed(7, "evaluation"), 7)

    def test_bad_purpose_rejected(self):
        with self.assertRaises(ValueError):
            ensure_evaluation_seed(7, "whatever")


class BudgetInvariantTests(unittest.TestCase):
    def test_all_methods_stay_within_the_declared_budget(self):
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator())
                acct = result.accounting
                self.assertLessEqual(acct["episodes_spent"], SMALL["total_budget"])
                self.assertEqual(acct["episodes_spent"],
                                 len(result.ledger))

    def test_no_method_can_borrow_from_the_audit_pool(self):
        """The reserved audit pool must be untouched by search and confirmation."""
        plan = plan_spending(total=SMALL["total_budget"],
                             search_fraction=SMALL["search_fraction"],
                             audit_candidates=SMALL["audit_candidates"],
                             audit_repeats=SMALL["audit_repeats"])
        for method in METHODS:
            with self.subTest(method=method):
                settings = SimSettings()
                budget = EpisodeBudget(total=SMALL["total_budget"], seed=4242,
                                       policy="reactive", settings=settings, plan=plan)
                result = run_method(method, out_budget=budget, plan=plan,
                                    problem=SearchProblem(policy="reactive", settings=settings),
                                    config=MethodConfig(), evaluator=ScriptedEvaluator())
                counts = result.accounting["phase_episodes"]
                search_work = counts[SEARCH] + counts[INTERNAL_CONFIRM]
                self.assertLessEqual(search_work, plan.search_pool)

    def test_confirmation_episodes_are_charged_not_free(self):
        for method in ("fixed_explore_confirm", "adaptive_explore_confirm"):
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator(rate_percent=60))
                counts = result.accounting["phase_episodes"]
                self.assertGreater(counts[INTERNAL_CONFIRM], 0)
                self.assertEqual(result.accounting["confirmation_episodes"],
                                 counts[INTERNAL_CONFIRM] + counts[FINAL_AUDIT])

    def test_random_search_spends_nothing_on_internal_confirmation(self):
        result = run_one("random_search", ScriptedEvaluator(rate_percent=60))
        self.assertEqual(result.accounting["phase_episodes"][INTERNAL_CONFIRM], 0)

    def test_audit_launches_are_exactly_selected_times_audit_repeats(self):
        """audit_repeats is a per-candidate maximum; nobody gets audit_repeats + 1."""
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator(rate_percent=50))
                audit = result.audit
                self.assertEqual(audit["audit_repeats"], SMALL["audit_repeats"])
                self.assertEqual(audit["audit_launched"],
                                 len(result.selected_for_audit) * SMALL["audit_repeats"])
                self.assertLessEqual(audit["audit_max_attempts_per_candidate"],
                                     SMALL["audit_repeats"])
                self.assertEqual(audit["audit_reserved_unspent"],
                                 audit["audit_reserved"] - audit["audit_launched"])
                selected = set(result.selected_for_audit)
                for candidate in result.candidates:
                    if candidate["candidate_id"] in selected:
                        self.assertEqual(candidate["audit_attempts"],
                                         SMALL["audit_repeats"])
                    else:
                        self.assertEqual(candidate["audit_attempts"], 0)

    def test_ledger_has_no_duplicate_episodes(self):
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator())
                keys = [(row["scenario_id"], row["sim_seed"], row["policy"])
                        for row in result.ledger]
                self.assertEqual(len(keys), len(set(keys)))

    def test_every_ledger_row_has_a_known_phase(self):
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator())
                self.assertTrue(all(row["phase"] in (SEARCH, INTERNAL_CONFIRM, FINAL_AUDIT)
                                    for row in result.ledger))

    def test_failed_episodes_cost_budget_and_are_not_safe_results(self):
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator(fail_every=5))
                acct = result.accounting
                self.assertGreater(acct["errors"], 0)
                self.assertEqual(acct["episodes_spent"],
                                 acct["search_episodes"]
                                 + acct["confirmation_episodes"])


class GlobalConfirmationCapTests(unittest.TestCase):
    """Rule 1: pool_fraction is a GLOBAL cap on launched internal confirmations."""

    def _plan(self):
        return plan_spending(SMALL["total_budget"], search_fraction=SMALL["search_fraction"],
                             audit_candidates=SMALL["audit_candidates"],
                             audit_repeats=SMALL["audit_repeats"])

    def test_cap_is_the_floor_of_fraction_times_search_pool(self):
        plan = self._plan()
        self.assertEqual(internal_confirm_cap(MethodConfig(pool_fraction=0.25), plan), 5)
        self.assertEqual(internal_confirm_cap(MethodConfig(pool_fraction=0.5), plan), 10)
        self.assertEqual(internal_confirm_cap(MethodConfig(pool_fraction=1.0), plan), 20)

    def test_a_later_candidate_cannot_reopen_the_global_allowance(self):
        """Discovering B must not hand it a fresh share on top of A's spending.

        With the old per-candidate share (``pool_fraction * search_pool / len``)
        candidate A could absorb 5 confirmations and a later candidate B another
        2-3, overshooting the intended cap of 5.
        """
        config = MethodConfig(pool_fraction=0.25, min_internal_per_candidate=2)
        result = run_one("adaptive_explore_confirm",
                         ScriptedEvaluator(rate_percent=95), config=config)
        cap = internal_confirm_cap(config, self._plan())
        counts = result.accounting["phase_episodes"]
        self.assertEqual(cap, 5)
        self.assertEqual(counts[INTERNAL_CONFIRM], cap)
        self.assertGreaterEqual(len(result.candidates), 2)
        self.assertIn("internal_confirm_global_cap_reached", result.search_stop["reasons"])

    def test_cap_counts_launched_attempts_including_errors(self):
        config = MethodConfig(pool_fraction=0.25, min_internal_per_candidate=1)
        result = run_one("adaptive_explore_confirm",
                         ScriptedEvaluator(rate_percent=95, fail_every=2), config=config)
        internal_rows = [r for r in result.ledger if r["phase"] == INTERNAL_CONFIRM]
        self.assertLessEqual(len(internal_rows), 5)
        self.assertTrue(any(r["outcome"]["error"] is not None for r in internal_rows))
        launched = sum(c["internal_attempts"] for c in result.candidates)
        self.assertEqual(launched, len(internal_rows))

    def test_per_candidate_ceiling_is_a_second_layer(self):
        config = MethodConfig(pool_fraction=1.0, min_internal_per_candidate=1,
                              max_internal_per_candidate=2)
        result = run_one("adaptive_explore_confirm",
                         ScriptedEvaluator(rate_percent=90), config=config)
        for candidate in result.candidates:
            self.assertLessEqual(candidate["internal_attempts"], 2)
            self.assertLessEqual(candidate["internal_episodes"], 2)


class ReserveBeforeLaunchTests(unittest.TestCase):
    """Rule 3: the budget slot exists before the evaluator is launched."""

    def test_every_launched_episode_was_already_charged(self):
        plan = plan_spending(SMALL["total_budget"], search_fraction=SMALL["search_fraction"],
                             audit_candidates=SMALL["audit_candidates"],
                             audit_repeats=SMALL["audit_repeats"])
        settings = SimSettings()
        budget = EpisodeBudget(total=SMALL["total_budget"], seed=4242, policy="reactive",
                               settings=settings, plan=plan)
        observed: list[int] = []

        def evaluator(scenario, sim_seed, policy, sim_settings):
            observed.append(budget.spent)
            return {"ego_collision": False, "goal_reached": True, "npc_collisions": 0,
                    "termination": "goal", "error": None, "note": "test"}

        result = run_method("random_search", out_budget=budget, plan=plan,
                            problem=SearchProblem(policy="reactive", settings=settings),
                            config=MethodConfig(), evaluator=evaluator,
                            purpose="development")
        self.assertEqual(len(observed), result.accounting["episodes_spent"])
        # Launch k happened when exactly k episodes had already been charged.
        self.assertEqual(observed, list(range(1, len(observed) + 1)))


class PurposeAndSeedPolicyTests(unittest.TestCase):
    """Rule 6: run_method really propagates purpose and enforces the seed policy."""

    def test_run_method_rejects_calibration_seed_for_evaluation(self):
        with self.assertRaises(SeedPolicyError):
            run_one("random_search", ScriptedEvaluator(), seed=CALIBRATION_SEED,
                    purpose="evaluation")

    def test_run_method_allows_calibration_seed_for_development(self):
        result = run_one("random_search", ScriptedEvaluator(), seed=CALIBRATION_SEED,
                         purpose="development")
        self.assertEqual(result.purpose, "development")
        self.assertEqual(result.seed, CALIBRATION_SEED)
        self.assertEqual(result.seed_policy["master_seed"], CALIBRATION_SEED)
        self.assertTrue(result.seed_policy["calibration_seed_forbidden_for_evaluation"])

    def test_run_method_rejects_unknown_purpose(self):
        with self.assertRaises(ValueError):
            run_one("random_search", ScriptedEvaluator(), purpose="whatever")

    def test_purpose_is_propagated_from_compare_methods(self):
        payload = compare_methods(methods=["random_search"], total_budget=SMALL["total_budget"],
                                  seed=4242, policy="reactive",
                                  search_fraction=SMALL["search_fraction"],
                                  audit_candidates=SMALL["audit_candidates"],
                                  audit_repeats=SMALL["audit_repeats"],
                                  config=MethodConfig(), evaluator=ScriptedEvaluator(),
                                  purpose="development")
        self.assertEqual(payload["purpose"], "development")
        for result in payload["methods"]:
            self.assertEqual(result["purpose"], "development")
            self.assertEqual(result["seed_policy"]["purpose"], "development")

    def test_compare_methods_rejects_calibration_seed_for_evaluation(self):
        with self.assertRaises(SeedPolicyError):
            compare_methods(methods=["random_search"], total_budget=SMALL["total_budget"],
                            seed=CALIBRATION_SEED, policy="reactive",
                            search_fraction=SMALL["search_fraction"],
                            audit_candidates=SMALL["audit_candidates"],
                            audit_repeats=SMALL["audit_repeats"],
                            config=MethodConfig(), evaluator=ScriptedEvaluator(),
                            purpose="evaluation")


class AttemptAndObservationTests(unittest.TestCase):
    """Rule 7: launched attempts drive the caps; observations drive stability."""

    def test_launched_attempts_are_never_below_valid_observations(self):
        for method in ("fixed_explore_confirm", "adaptive_explore_confirm"):
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator(rate_percent=80, fail_every=3))
                self.assertTrue(any(r["phase"] == INTERNAL_CONFIRM for r in result.ledger))
                for candidate in result.candidates:
                    self.assertGreaterEqual(candidate["internal_attempts"],
                                            candidate["internal_episodes"])
                    self.assertEqual(
                        candidate["internal_episodes"],
                        candidate["internal_collapses"]
                        + candidate["internal_failures"].count(False))

    def test_an_unreachable_min_internal_target_is_reported_not_hidden(self):
        # floor(0.05 * 20) = 1 launched confirmation can never reach a target of 5.
        config = MethodConfig(pool_fraction=0.05, min_internal_per_candidate=5)
        result = run_one("adaptive_explore_confirm",
                         ScriptedEvaluator(rate_percent=95), config=config)
        stop = result.search_stop
        self.assertTrue(stop["min_internal_unmet"])
        self.assertEqual(stop["min_internal_unmet_count"], len(stop["min_internal_unmet"]))
        self.assertIn("internal_confirm_global_cap_reached", stop["reasons"])
        by_id = {c["candidate_id"]: c for c in result.candidates}
        for cid in stop["min_internal_unmet"]:
            self.assertLess(by_id[cid]["internal_episodes"], 5)
        self.assertIn("target, not a guarantee", stop["note"])

    def test_random_search_reports_an_explicit_stop_reason(self):
        result = run_one("random_search", ScriptedEvaluator(rate_percent=50))
        self.assertIn("search_pool_exhausted", result.search_stop["reasons"])


class UnifiedErrorSchemaTests(unittest.TestCase):
    """Rule 8: every phase records the one outcome schema."""

    def test_every_ledger_row_has_the_fixed_outcome_shape(self):
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator(rate_percent=60, fail_every=4))
                for row in result.ledger:
                    self.assertEqual(set(row["outcome"]), set(OUTCOME_FIELDS))

    def test_error_rows_share_the_schema_across_phases(self):
        result = run_one("fixed_explore_confirm",
                         ScriptedEvaluator(rate_percent=70, fail_every=2))
        error_rows = [r for r in result.ledger if r["outcome"]["error"] is not None]
        self.assertTrue(error_rows)
        self.assertTrue({r["phase"] for r in error_rows}
                        <= {SEARCH, INTERNAL_CONFIRM, FINAL_AUDIT})
        for row in error_rows:
            outcome = row["outcome"]
            self.assertEqual(outcome["termination"], "error")
            self.assertFalse(outcome["ego_collision"])
            self.assertFalse(outcome["goal_reached"])
            self.assertEqual(outcome["npc_collisions"], 0)
            self.assertIn("goal_reached", outcome)
            self.assertIn("min_forward_ttc_sampled_s", outcome)


class AccountingIdentityTests(unittest.TestCase):
    def test_methods_share_one_accounting_definition(self):
        results = [run_one(m, ScriptedEvaluator()) for m in METHODS]
        for result in results:
            acct = result.accounting
            self.assertEqual(acct["episodes_spent"],
                             acct["search_episodes"] + acct["confirmation_episodes"])
            self.assertEqual(set(acct["phase_episodes"]), {SEARCH, INTERNAL_CONFIRM, FINAL_AUDIT})
            self.assertEqual(acct["launched_attempts"], acct["episodes_spent"])
            self.assertEqual(acct["valid_observations"],
                             acct["episodes_spent"] - acct["errors"])
        totals = {(r.accounting["budget_total"], r.plan["audit_pool"]) for r in results}
        self.assertEqual(len(totals), 1)

    def test_declared_budget_is_consumed_except_the_unspent_audit_reservation(self):
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator(rate_percent=60))
                acct = result.accounting
                audit = result.audit
                # The search pool is fully consumed ...
                self.assertEqual(acct["phase_episodes"][SEARCH]
                                 + acct["phase_episodes"][INTERNAL_CONFIRM],
                                 result.plan["search_pool"])
                # ... and whatever is left over is exactly the audit reservation
                # that the candidate shortfall made unusable. It is never reused.
                self.assertEqual(acct["episodes_spent"],
                                 result.plan["search_pool"] + audit["audit_launched"])
                self.assertEqual(acct["episodes_remaining"],
                                 audit["audit_reserved_unspent"])
                self.assertEqual(acct["pending_unfinished_episodes"], 0)

    def test_comparison_runs_every_method_under_one_plan(self):
        payload = compare_methods(methods=list(METHODS), total_budget=SMALL["total_budget"],
                                 seed=4242, policy="reactive",
                                 search_fraction=SMALL["search_fraction"],
                                 audit_candidates=SMALL["audit_candidates"],
                                 audit_repeats=SMALL["audit_repeats"],
                                 config=MethodConfig(), evaluator=ScriptedEvaluator())
        self.assertEqual(len(payload["methods"]), 3)
        self.assertEqual({m["plan"]["audit_pool"] for m in payload["methods"]},
                         {SMALL["audit_candidates"] * SMALL["audit_repeats"]})
        self.assertEqual({m["seed"] for m in payload["methods"]}, {4242})

    def test_comparison_refuses_calibration_seed_for_evaluation(self):
        with self.assertRaises(SeedPolicyError):
            compare_methods(methods=["random_search"], total_budget=40, seed=CALIBRATION_SEED,
                            policy="reactive", search_fraction=0.5, audit_candidates=2,
                            audit_repeats=10, config=MethodConfig(),
                            evaluator=ScriptedEvaluator())


class ReproducibilityTests(unittest.TestCase):
    def test_same_seed_reproduces_the_identical_ledger(self):
        for method in METHODS:
            with self.subTest(method=method):
                a = run_one(method, ScriptedEvaluator())
                b = run_one(method, ScriptedEvaluator())
                self.assertEqual([r["scenario_id"] for r in a.ledger],
                                 [r["scenario_id"] for r in b.ledger])
                self.assertEqual([r["phase"] for r in a.ledger],
                                 [r["phase"] for r in b.ledger])
                self.assertEqual(a.accounting, b.accounting)

    def test_different_seed_changes_the_search_stream(self):
        a = run_one("random_search", ScriptedEvaluator(), seed=1)
        b = run_one("random_search", ScriptedEvaluator(), seed=2)
        self.assertNotEqual([r["scenario_id"] for r in a.ledger],
                            [r["scenario_id"] for r in b.ledger])

    def test_distinct_methods_are_genuinely_different_policies(self):
        fixed = run_one("fixed_explore_confirm", ScriptedEvaluator(rate_percent=60))
        naive = run_one("random_search", ScriptedEvaluator(rate_percent=60))
        self.assertGreater(fixed.accounting["phase_episodes"][SEARCH],
                           0)
        self.assertEqual(naive.accounting["phase_episodes"][INTERNAL_CONFIRM], 0)
        self.assertNotEqual(
            fixed.accounting["phase_episodes"][SEARCH],
            naive.accounting["phase_episodes"][SEARCH],
            "A confirmation-spending method must explore fewer fresh scenarios "
            "than pure random search under the same total budget.",
        )

    def test_adaptive_respects_its_per_candidate_ceiling(self):
        config = MethodConfig(min_internal_per_candidate=1, max_internal_per_candidate=2,
                              pool_fraction=0.5)
        result = run_one("adaptive_explore_confirm",
                         ScriptedEvaluator(rate_percent=80), config=config)
        for candidate in result.candidates:
            self.assertLessEqual(candidate["internal_episodes"], 2)


class CandidateAndAuditIntegrityTests(unittest.TestCase):
    def test_search_episodes_never_reuse_a_scenario(self):
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator())
                search_ids = [r["scenario_id"] for r in result.ledger if r["phase"] == SEARCH]
                self.assertEqual(len(search_ids), len(set(search_ids)))

    def test_candidates_are_exactly_the_search_collisions(self):
        result = run_one("random_search", ScriptedEvaluator(rate_percent=40))
        search_hits = {r["scenario_id"] for r in result.ledger
                       if r["phase"] == SEARCH and r["outcome"].get("ego_collision")}
        candidate_ids = {c["candidate_id"] for c in result.candidates}
        self.assertEqual(search_hits, candidate_ids)

    def test_audit_draws_are_fresh_local_perturbations(self):
        result = run_one("fixed_explore_confirm", ScriptedEvaluator(rate_percent=50))
        by_id = {c["candidate_id"]: c for c in result.candidates}
        audit_rows = [r for r in result.ledger if r["phase"] == FINAL_AUDIT]
        self.assertTrue(audit_rows)
        for row in audit_rows:
            base = Scenario.from_dict(by_id[row["candidate_id"]]["scenario"])
            draw = Scenario.from_dict(row["scenario"])
            self.assertLessEqual(abs(draw.front_gap - base.front_gap), 2.0 + 1e-6)
            self.assertLessEqual(abs(draw.ramp_x - base.ramp_x), 2.0 + 1e-6)
            self.assertLessEqual(abs(draw.ego_speed - base.ego_speed), 1.0 + 1e-6)
            self.assertNotEqual(draw.uid, base.uid)

    def test_audit_episodes_are_never_reused_for_internal_confirmation(self):
        result = run_one("fixed_explore_confirm", ScriptedEvaluator(rate_percent=50))
        internal = [(r["scenario_id"], r["sim_seed"]) for r in result.ledger
                    if r["phase"] == INTERNAL_CONFIRM]
        audit = [(r["scenario_id"], r["sim_seed"]) for r in result.ledger
                 if r["phase"] == FINAL_AUDIT]
        self.assertEqual(set(internal) & set(audit), set())

    def test_selection_happens_before_audit_and_is_recorded(self):
        result = run_one("fixed_explore_confirm", ScriptedEvaluator(rate_percent=50))
        self.assertIn("rank_by_internal_stability", result.audit["audit_selection_rule"])
        self.assertLessEqual(len(result.selected_for_audit), SMALL["audit_candidates"])
        for cid in result.selected_for_audit:
            self.assertIn(cid, {c["candidate_id"] for c in result.candidates})

    def test_reported_stability_matches_the_recorded_trials(self):
        result = run_one("fixed_explore_confirm", ScriptedEvaluator(rate_percent=50))
        for candidate in result.candidates:
            if candidate["audit_episodes"]:
                expected = candidate["audit_collapses"] / candidate["audit_episodes"]
                self.assertAlmostEqual(candidate["audit_stability"], expected)
                lo, hi = wilson_interval(candidate["audit_collapses"],
                                         candidate["audit_episodes"])
                self.assertAlmostEqual(candidate["audit_wilson95"][0], lo)
                self.assertAlmostEqual(candidate["audit_wilson95"][1], hi)

    def test_candidate_ids_are_unique(self):
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator(rate_percent=60))
                ids = [c["candidate_id"] for c in result.candidates]
                self.assertEqual(len(ids), len(set(ids)))

    def test_candidates_found_is_not_claimed_as_root_cause_count(self):
        result = run_one("random_search", ScriptedEvaluator(rate_percent=60))
        joined = " ".join(result.notes).lower()
        self.assertIn("not distinct root causes", joined)
        self.assertIn("charged to the same budget counter", joined)


class ProtocolTodoTests(unittest.TestCase):
    """Rule 10: the audit selection semantics stay explicitly unfrozen."""

    def test_result_flags_the_unfrozen_audit_selection_semantics(self):
        result = run_one("random_search", ScriptedEvaluator(rate_percent=60))
        self.assertFalse(result.audit["audit_selection_semantics_frozen"])
        self.assertIn("UNFROZEN", result.audit["audit_selection_todo"])
        self.assertFalse(AUDIT_SELECTION_SEMANTICS_FROZEN)
        self.assertIn("UNFROZEN", AUDIT_SELECTION_TODO)

    def test_random_search_selection_is_not_silently_redefined(self):
        """The placeholder behaviour is observable and unchanged; no new definition."""
        result = run_one("random_search", ScriptedEvaluator(rate_percent=70))
        chosen = result.selected_for_audit
        ranked_by_first_seen = [c["candidate_id"] for c in sorted(
            result.candidates, key=lambda c: (c["first_seen_episode"], c["candidate_id"]))]
        self.assertEqual(chosen, ranked_by_first_seen[:len(chosen)])


class PersistenceAndMetadataTests(unittest.TestCase):
    """Rule 5: v0.3 runs persist reproducibility metadata and never overwrite."""

    def test_method_output_records_the_reproducibility_metadata(self):
        result = run_one("random_search", ScriptedEvaluator())
        out = scratch_dir(self, "method_output") / "run"
        info = write_method_output(out, result)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "ccl-v03-method-run-v1")
        self.assertEqual(manifest["method"], result.method)
        self.assertEqual(manifest["purpose"], result.purpose)
        self.assertEqual(manifest["seed"], result.seed)
        self.assertEqual(manifest["plan"], result.plan)
        self.assertEqual(manifest["method_config"], result.config)
        self.assertIn("seed_policy", manifest)
        meta = manifest["metadata"]
        for key in ("project_version", "python", "platform", "packages",
                    "source_sha256", "git", "created_utc"):
            self.assertIn(key, meta)
        self.assertIn("commit", meta["git"])
        self.assertIn("dirty", meta["git"])
        self.assertEqual(set(meta["packages"]),
                         {"highway-env", "gymnasium", "numpy", "pygame", "pandas",
                          "scipy", "matplotlib", "Pillow"})
        ledger_lines = (out / "ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(ledger_lines), result.accounting["episodes_spent"])
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["accounting"], result.accounting)
        self.assertIn("manifest.json", info["files"])
        self.assertTrue((out / "candidates.json").exists())

    def test_method_output_refuses_to_overwrite_an_existing_run(self):
        result = run_one("random_search", ScriptedEvaluator())
        out = scratch_dir(self, "no_overwrite") / "run"
        write_method_output(out, result)
        with self.assertRaises(FileExistsError):
            write_method_output(out, result)

    def test_comparison_output_refuses_to_overwrite_an_existing_run(self):
        out = scratch_dir(self, "comparison") / "cmp"
        kwargs = dict(methods=["random_search"], total_budget=SMALL["total_budget"],
                      seed=4242, policy="reactive",
                      search_fraction=SMALL["search_fraction"],
                      audit_candidates=SMALL["audit_candidates"],
                      audit_repeats=SMALL["audit_repeats"], config=MethodConfig(),
                      evaluator=ScriptedEvaluator(), purpose="development")
        payload = compare_methods(out=out, **kwargs)
        self.assertEqual(payload["schema"], "ccl-method-comparison-v2")
        self.assertTrue((out / "comparison.json").exists())
        self.assertTrue((out / "random_search" / "checkpoint.json").exists())
        comparison = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
        self.assertIn("metadata", comparison)
        self.assertIn("git", comparison["metadata"])
        with self.assertRaises(FileExistsError):
            compare_methods(out=out, **kwargs)


class SeedStreamGuardTests(unittest.TestCase):
    """Rule 3: derived-seed collisions are detected, never silently repeated."""

    @staticmethod
    def _budget(total: int = 10, plan=None) -> EpisodeBudget:
        return EpisodeBudget(total=total, seed=7, policy="reactive",
                             settings=SimSettings(), plan=plan)

    def test_derived_seed_streams_are_unique_within_a_run(self):
        for method in METHODS:
            with self.subTest(method=method):
                result = run_one(method, ScriptedEvaluator(rate_percent=60))
                report = result.seed_streams
                self.assertEqual(report["duplicate_seeds"], {})
                self.assertEqual(report["seed_space_bits"], 32)
                self.assertTrue(set(report["streams"]) <= set(SEED_STREAMS))
                # Every stream the method actually used is reported.
                self.assertIn("search", report["streams"])
                self.assertIn("simulation", report["streams"])
                if method == "random_search":
                    self.assertNotIn("internal-local", report["streams"])
                else:
                    self.assertIn("internal-local", report["streams"])
                    self.assertIn("internal-nuisance", report["streams"])
                if result.selected_for_audit:
                    self.assertIn("audit-local", report["streams"])
                    self.assertIn("audit-nuisance", report["streams"])
                for stream, claimed in report["streams"].items():
                    self.assertGreater(claimed, 0, stream)
                self.assertGreaterEqual(report["guard_keys"], 2)

    def test_a_duplicate_local_seed_for_one_candidate_is_refused(self):
        guard = SeedStreamGuard(self._budget())
        guard.claim("audit-local", "cand", 1234, 0)
        with self.assertRaises(SeedCollisionError):
            guard.claim("audit-local", "cand", 1234, 1)
        # A different candidate scope is a different draw, not a collision.
        guard.claim("audit-local", "other", 1234, 1)
        # A different stream is a different draw too.
        guard.claim("internal-local", "cand", 1234, 1)

    def test_a_duplicate_nuisance_seed_is_refused(self):
        guard = SeedStreamGuard(self._budget())
        guard.claim("audit-nuisance", None, 77, 0)
        with self.assertRaises(SeedCollisionError):
            guard.claim("audit-nuisance", None, 77, 1)
        # The same value in a different stream is a different draw, not a collision.
        guard.claim("simulation", None, 77, 1)
        # And a different value in the same stream is fine.
        guard.claim("audit-nuisance", None, 78, 2)

    def test_iter_derived_seeds_reconstructs_the_streams_from_the_ledger(self):
        plan = plan_spending(10, search_fraction=0.5, audit_candidates=1, audit_repeats=5)
        budget = self._budget(10, plan)
        state = CandidateState()
        cid = state.register(Scenario(front_gap=30.0), episode_index=0).candidate_id
        base = Scenario(front_gap=30.0)
        budget.reserve(SEARCH, base, sim_seed=11, policy="reactive")
        for r in range(2):
            budget.reserve(FINAL_AUDIT, perturb_scenario(base, 100 + r), sim_seed=200 + r,
                           policy="reactive", candidate_id=cid,
                           note=f"final_audit_round_{r}")
        self.assertEqual(list(_iter_derived_seeds(budget)), [
            ("search", None, budget.seed_for("search", 0), 0),
            ("simulation", None, 11, 0),
            ("audit-local", cid, budget.seed_for("audit-local", cid, 0), 1),
            ("audit-nuisance", None, 200, 1),
            ("audit-local", cid, budget.seed_for("audit-local", cid, 1), 2),
            ("audit-nuisance", None, 201, 2),
        ])

    def test_a_repeated_simulator_seed_is_reported_not_hidden(self):
        budget = self._budget(total=4)
        for i in range(2):
            budget.reserve(SEARCH, Scenario(front_gap=30.0 + i), sim_seed=999,
                           policy="reactive")
        report = seed_stream_report(budget)
        self.assertEqual(report["duplicate_seeds"], {"simulation": [999]})
        self.assertEqual(report["streams"]["simulation"], 2)

    def test_the_guard_is_rebuilt_from_a_hand_built_ledger(self):
        budget = self._budget(total=4)
        budget.reserve(SEARCH, Scenario(front_gap=30.0), sim_seed=5, policy="reactive")
        guard = SeedStreamGuard(budget)
        # The guard already knows the ledger's seeds, so re-claiming one collides.
        with self.assertRaises(SeedCollisionError):
            guard.claim("simulation", None, 5, 1)



class ConfigValidationTests(unittest.TestCase):
    def test_invalid_configs_rejected(self):
        for bad in (MethodConfig(confirm_repeats=0),
                    MethodConfig(max_internal_per_candidate=0),
                    MethodConfig(pool_fraction=0.0),
                    MethodConfig(pool_fraction=1.5),
                    MethodConfig(min_internal_per_candidate=-1)):
            with self.subTest(cfg=bad), self.assertRaises(ValueError):
                bad.validate()

    def test_unknown_method_rejected(self):
        settings = SimSettings()
        plan = plan_spending(total=SMALL["total_budget"],
                             search_fraction=SMALL["search_fraction"],
                             audit_candidates=SMALL["audit_candidates"],
                             audit_repeats=SMALL["audit_repeats"])
        budget = EpisodeBudget(total=SMALL["total_budget"], seed=4242, policy="reactive",
                               settings=settings, plan=plan)
        with self.assertRaises(ValueError):
            run_method("magic_search", out_budget=budget, plan=plan,
                       problem=SearchProblem(policy="reactive", settings=settings),
                       config=MethodConfig(), evaluator=ScriptedEvaluator())

    def test_unknown_policy_rejected(self):
        with self.assertRaises(ValueError):
            SearchProblem(policy="trained_model_that_does_not_exist",
                          settings=SimSettings())


if __name__ == "__main__":
    unittest.main()
