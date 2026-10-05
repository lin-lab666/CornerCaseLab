"""v0.3 unified budget-accounting tests.

These tests use an explicit deterministic fake evaluator. They are NOT driving
experiments and must never be described as simulator results.
"""
from __future__ import annotations
from dataclasses import asdict
import unittest

from cornercaselab.budget import (
    CONFIRMATION_PHASES,
    FINAL_AUDIT,
    INTERNAL_CONFIRM,
    OUTCOME_FIELDS,
    SEARCH,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_RESERVED,
    EpisodeBudget,
    BudgetExhausted,
    SpendPlan,
    error_outcome,
    plan_spending,
)
from cornercaselab.domain import Scenario
from cornercaselab.simulator import SimSettings


def distinct(i: int) -> tuple[Scenario, int]:
    """A distinct (scenario, nuisanceseed) pair: real episodes are never exact repeats."""
    return Scenario(front_gap=18.0 + 0.5 * i), 1000 + i


class SpendPlanTests(unittest.TestCase):
    def test_plan_arithmetic(self):
        plan = plan_spending(1000, search_fraction=0.8, audit_candidates=10, audit_repeats=20)
        self.assertEqual(plan.total, 1000)
        self.assertEqual(plan.audit_pool, 200)
        self.assertEqual(plan.search_pool, 800)
        self.assertEqual(plan.audit_per_candidate, 20)

    def test_plan_rejects_more_than_budget(self):
        with self.assertRaises(ValueError):
            plan_spending(100, search_fraction=0.9, audit_candidates=10, audit_repeats=20)

    def test_plan_rejects_bad_fraction(self):
        for bad in (0.0, 1.5, -0.2):
            with self.subTest(f=bad), self.assertRaises(ValueError):
                plan_spending(100, search_fraction=bad, audit_candidates=10, audit_repeats=20)

    def test_plan_requires_positive_audit(self):
        with self.assertRaises(ValueError):
            plan_spending(100, search_fraction=0.5, audit_candidates=0, audit_repeats=20)


class SpendPlanClosureTests(unittest.TestCase):
    """Rule 2: search_pool + audit_pool == total, or the plan is rejected."""

    def test_plan_closes_exactly(self):
        plan = plan_spending(1000, search_fraction=0.8, audit_candidates=10, audit_repeats=20)
        self.assertEqual(plan.search_pool + plan.audit_pool, plan.total)

    def test_non_closing_plan_is_rejected(self):
        """A plan that would leave unconsumable episodes must not be accepted."""
        cases = [
            # floor(1000 * 0.75) = 750, + 200 = 950: 50 episodes could never be spent.
            dict(total=1000, search_fraction=0.75, audit_candidates=10, audit_repeats=20),
            # floor(100 * 0.5) = 50, + 21 = 71.
            dict(total=100, search_fraction=0.5, audit_candidates=3, audit_repeats=7),
            # floor(37 * 0.6) = 22, + 14 = 36.
            dict(total=37, search_fraction=0.6, audit_candidates=2, audit_repeats=7),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                plan_spending(**case)

    def test_search_fraction_is_not_silently_ignored(self):
        """A legal fraction is honoured; an illegal one is refused, never coerced."""
        plan = plan_spending(40, search_fraction=0.5, audit_candidates=2, audit_repeats=10)
        self.assertEqual(plan.search_pool, 20)
        with self.assertRaises(ValueError):
            plan_spending(40, search_fraction=0.6, audit_candidates=2, audit_repeats=10)

    def test_whole_declared_budget_is_consumable(self):
        plan = plan_spending(40, search_fraction=0.5, audit_candidates=2, audit_repeats=10)
        budget = EpisodeBudget(total=40, seed=7, policy="reactive",
                               settings=SimSettings(), plan=plan)
        for i in range(20):
            scenario, sim_seed = distinct(i)
            budget.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                         outcome={"ego_collision": False})
        for i in range(20, 40):
            scenario, sim_seed = distinct(i)
            budget.spend(FINAL_AUDIT, scenario, sim_seed=sim_seed, policy="reactive",
                         outcome={"ego_collision": False})
        self.assertEqual(budget.spent, 40)
        self.assertEqual(budget.remaining, 0)
        self.assertEqual(budget.accounting()["episodes_remaining"], 0)


class BudgetLedgerTests(unittest.TestCase):
    def test_every_phase_charges_the_same_counter(self):
        b = EpisodeBudget(total=10, seed=7, policy="reactive", settings=SimSettings())
        for i, phase in enumerate((SEARCH, INTERNAL_CONFIRM, FINAL_AUDIT)):
            scenario, sim_seed = distinct(i)
            b.spend(phase, scenario, sim_seed=sim_seed, policy="reactive",
                    outcome={"ego_collision": False})
        self.assertEqual(b.spent, 3)
        self.assertEqual(b.remaining, 7)
        self.assertEqual([e.phase for e in b.entries], [SEARCH, INTERNAL_CONFIRM, FINAL_AUDIT])

    def test_confirmation_is_not_free(self):
        b = EpisodeBudget(total=4, seed=7, policy="reactive", settings=SimSettings())
        for i in range(3):
            scenario, sim_seed = distinct(i)
            b.spend(INTERNAL_CONFIRM, scenario, sim_seed=sim_seed, policy="reactive",
                    outcome={"ego_collision": True})
        self.assertEqual(b.spent, 3)
        self.assertEqual(b.phase_counts()[INTERNAL_CONFIRM], 3)
        self.assertIn(INTERNAL_CONFIRM, CONFIRMATION_PHASES)

    def test_cannot_exceed_total_budget(self):
        b = EpisodeBudget(total=2, seed=7, policy="reactive", settings=SimSettings())
        s0, n0 = distinct(0)
        s1, n1 = distinct(1)
        s2, n2 = distinct(2)
        b.spend(SEARCH, s0, sim_seed=n0, policy="reactive", outcome={"ego_collision": False})
        b.spend(SEARCH, s1, sim_seed=n1, policy="reactive", outcome={"ego_collision": False})
        with self.assertRaises(BudgetExhausted):
            b.spend(SEARCH, s2, sim_seed=n2, policy="reactive",
                    outcome={"ego_collision": False})
        self.assertEqual(b.spent, 2)

    def test_phase_pools_are_enforced(self):
        plan = plan_spending(10, search_fraction=0.5, audit_candidates=1, audit_repeats=5)
        b = EpisodeBudget(total=10, seed=7, policy="reactive", settings=SimSettings(), plan=plan)
        for i in range(5):
            scenario, sim_seed = distinct(i)
            b.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                    outcome={"ego_collision": False})
        # Search pool is now exhausted even though the audit pool is untouched.
        over_scenario, over_seed = distinct(50)
        with self.assertRaises(BudgetExhausted):
            b.spend(SEARCH, over_scenario, sim_seed=over_seed, policy="reactive",
                    outcome={"ego_collision": False})
        for i in range(5, 10):
            scenario, sim_seed = distinct(i)
            b.spend(FINAL_AUDIT, scenario, sim_seed=sim_seed, policy="reactive",
                    outcome={"ego_collision": False})
        self.assertEqual(b.spent, 10)

    def test_episode_indices_are_unique_and_ordered(self):
        b = EpisodeBudget(total=6, seed=7, policy="reactive", settings=SimSettings())
        for i in range(6):
            scenario, sim_seed = distinct(i)
            b.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                    outcome={"ego_collision": False})
        self.assertEqual([e.episode_index for e in b.entries], list(range(6)))

    def test_exact_repeat_is_refused_by_default(self):
        """An exact rerun is a reproducibility check, not a fresh episode."""
        b = EpisodeBudget(total=5, seed=7, policy="reactive", settings=SimSettings())
        scenario, sim_seed = distinct(0)
        b.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                outcome={"ego_collision": False})
        with self.assertRaises(BudgetExhausted):
            b.spend(INTERNAL_CONFIRM, scenario, sim_seed=sim_seed, policy="reactive",
                    outcome={"ego_collision": False})
        replay = EpisodeBudget(total=5, seed=7, policy="reactive", settings=SimSettings(),
                               globally_unique=False)
        replay.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                     outcome={"ego_collision": False})
        replay.spend(INTERNAL_CONFIRM, scenario, sim_seed=sim_seed, policy="reactive",
                     outcome={"ego_collision": False})
        self.assertEqual(replay.spent, 2)

    def test_seed_derivation_is_deterministic_and_stream_separated(self):
        b = EpisodeBudget(total=8, seed=7, policy="reactive", settings=SimSettings())
        self.assertEqual(b.seed_for("search", 0), b.seed_for("search", 0))
        self.assertNotEqual(b.seed_for("search", 0), b.seed_for("simulation", 0))
        self.assertNotEqual(b.seed_for("simulation", 0), b.seed_for("simulation", 1))

    def test_ledger_is_serialisable(self):
        b = EpisodeBudget(total=2, seed=7, policy="reactive", settings=SimSettings())
        scenario, sim_seed = distinct(0)
        b.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                outcome={"ego_collision": True, "termination": "collision"})
        row = b.entries[0].to_dict()
        self.assertEqual(row["episode_index"], 0)
        self.assertEqual(row["phase"], SEARCH)
        self.assertTrue(row["outcome"]["ego_collision"])
        self.assertEqual(asdict(Scenario())["ego_speed"], 27.0)

    def test_rejects_bad_total(self):
        for bad in (0, -1):
            with self.subTest(total=bad), self.assertRaises(ValueError):
                EpisodeBudget(total=bad, seed=7, policy="reactive", settings=SimSettings())

    def test_unknown_phase_rejected(self):
        b = EpisodeBudget(total=2, seed=7, policy="reactive", settings=SimSettings())
        scenario, sim_seed = distinct(0)
        with self.assertRaises(ValueError):
            b.spend("free_lunch", scenario, sim_seed=sim_seed, policy="reactive",
                    outcome={"ego_collision": False})


class ReserveCompleteTests(unittest.TestCase):
    """Rule 3: an episode occupies budget BEFORE its evaluator is launched."""

    def _budget(self, total: int = 3) -> EpisodeBudget:
        return EpisodeBudget(total=total, seed=7, policy="reactive", settings=SimSettings())

    def test_reserve_charges_before_any_launch(self):
        b = self._budget()
        scenario, sim_seed = distinct(0)
        record = b.reserve(SEARCH, scenario, sim_seed=sim_seed, policy="reactive")
        self.assertEqual(b.spent, 1)
        self.assertEqual(record.status, STATUS_RESERVED)
        self.assertIsNone(record.outcome)
        self.assertEqual(b.accounting()["pending_unfinished_episodes"], 1)
        self.assertEqual(b.accounting()["valid_observations"], 0)

    def test_complete_records_the_outcome_and_the_status(self):
        b = self._budget()
        scenario, sim_seed = distinct(0)
        record = b.reserve(SEARCH, scenario, sim_seed=sim_seed, policy="reactive")
        done = b.complete(record.episode_index, {"ego_collision": True})
        self.assertIs(done, record)
        self.assertEqual(record.status, STATUS_COMPLETED)
        self.assertTrue(record.outcome["ego_collision"])
        self.assertEqual(b.accounting()["pending_unfinished_episodes"], 0)
        self.assertEqual(b.accounting()["valid_observations"], 1)
        self.assertEqual(b.accounting()["collisions"], 1)

    def test_reserved_episode_still_exists_when_the_evaluator_raises(self):
        b = self._budget()
        scenario, sim_seed = distinct(0)
        record = b.reserve(SEARCH, scenario, sim_seed=sim_seed, policy="reactive")
        b.complete(record.episode_index, error_outcome(RuntimeError("boom")))
        # The episode is charged, recorded and never counted as a safe result.
        self.assertEqual(b.spent, 1)
        self.assertEqual(record.status, STATUS_ERROR)
        self.assertEqual(record.outcome["error"], "RuntimeError: boom")
        self.assertEqual(b.accounting()["errors"], 1)
        self.assertEqual(b.accounting()["collisions"], 0)
        self.assertEqual(b.accounting()["valid_observations"], 0)

    def test_completing_twice_is_refused(self):
        b = self._budget()
        scenario, sim_seed = distinct(0)
        record = b.reserve(SEARCH, scenario, sim_seed=sim_seed, policy="reactive")
        b.complete(record.episode_index, {"ego_collision": False})
        with self.assertRaises(ValueError):
            b.complete(record.episode_index, {"ego_collision": True})

    def test_reserve_refuses_past_the_total(self):
        b = self._budget(total=1)
        s0, n0 = distinct(0)
        s1, n1 = distinct(1)
        b.reserve(SEARCH, s0, sim_seed=n0, policy="reactive")
        with self.assertRaises(BudgetExhausted):
            b.reserve(SEARCH, s1, sim_seed=n1, policy="reactive")
        self.assertEqual(b.spent, 1)

    def test_spend_is_reserve_plus_complete(self):
        b = self._budget(total=2)
        scenario, sim_seed = distinct(0)
        record = b.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                         outcome={"ego_collision": True})
        self.assertEqual(record.status, STATUS_COMPLETED)
        self.assertEqual(b.spent, 1)

    def test_launched_attempts_and_valid_observations_are_distinguished(self):
        b = self._budget(total=3)
        for i, error in enumerate((False, True, False)):
            scenario, sim_seed = distinct(i)
            record = b.reserve(SEARCH, scenario, sim_seed=sim_seed, policy="reactive")
            outcome = error_outcome(ValueError("x")) if error else {"ego_collision": True}
            b.complete(record.episode_index, outcome)
        acct = b.accounting()
        self.assertEqual(acct["launched_attempts"], 3)
        self.assertEqual(acct["valid_observations"], 2)
        self.assertEqual(acct["errors"], 1)
        self.assertEqual(acct["collisions"], 2)


class UnifiedOutcomeSchemaTests(unittest.TestCase):
    """Rule 8: search, confirmation and audit record the same outcome shape."""

    def test_error_outcome_carries_every_fixed_field(self):
        row = error_outcome(ValueError("bad scenario"))
        self.assertEqual(set(row), set(OUTCOME_FIELDS))
        self.assertEqual(row["termination"], "error")
        self.assertFalse(row["ego_collision"])
        self.assertFalse(row["goal_reached"])
        self.assertEqual(row["npc_collisions"], 0)
        self.assertIn("ValueError", row["error"])

    def test_every_phase_records_an_identical_error_shape(self):
        b = EpisodeBudget(total=3, seed=7, policy="reactive", settings=SimSettings())
        for i, phase in enumerate((SEARCH, INTERNAL_CONFIRM, FINAL_AUDIT)):
            scenario, sim_seed = distinct(i)
            record = b.reserve(phase, scenario, sim_seed=sim_seed, policy="reactive")
            b.complete(record.episode_index, error_outcome(ValueError("x")))
        for entry in b.entries:
            outcome = entry.outcome
            self.assertEqual(set(outcome), set(OUTCOME_FIELDS))
            self.assertEqual(outcome["termination"], "error")
            self.assertFalse(outcome["ego_collision"])
            self.assertEqual(outcome["npc_collisions"], 0)
            self.assertEqual(outcome["sim_time_s"], None)

    def test_a_missing_fixed_field_is_filled_not_dropped(self):
        b = EpisodeBudget(total=1, seed=7, policy="reactive", settings=SimSettings())
        scenario, sim_seed = distinct(0)
        record = b.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                         outcome={"ego_collision": True})
        self.assertEqual(set(record.outcome), set(OUTCOME_FIELDS))
        self.assertTrue(record.outcome["ego_collision"])
        self.assertIn("goal_reached", record.outcome)
        self.assertIn("npc_collisions", record.outcome)


class BudgetPersistenceTests(unittest.TestCase):
    def test_budget_dict_roundtrip_is_exact(self):
        plan = plan_spending(10, search_fraction=0.5, audit_candidates=1, audit_repeats=5)
        b = EpisodeBudget(total=10, seed=7, policy="reactive", settings=SimSettings(), plan=plan)
        for i in range(5):
            scenario, sim_seed = distinct(i)
            b.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                    outcome={"ego_collision": i % 2 == 0})
        for i in range(5, 8):
            scenario, sim_seed = distinct(i)
            record = b.reserve(FINAL_AUDIT, scenario, sim_seed=sim_seed, policy="reactive",
                               candidate_id="cand")
            b.complete(record.episode_index, error_outcome(RuntimeError("x")))
        restored = EpisodeBudget.from_dict(b.to_dict())
        self.assertEqual(restored.to_dict(), b.to_dict())
        self.assertEqual(restored.accounting(), b.accounting())
        self.assertEqual([e.status for e in restored.entries], [e.status for e in b.entries])
        self.assertEqual(restored.plan, b.plan)

    def test_plan_from_dict_ignores_the_derived_property(self):
        plan = plan_spending(40, search_fraction=0.5, audit_candidates=2, audit_repeats=10)
        restored = SpendPlan.from_dict(plan.to_dict())
        self.assertEqual(restored, plan)
        self.assertIn("audit_per_candidate", plan.to_dict())

    def test_a_non_dense_ledger_is_rejected(self):
        b = EpisodeBudget(total=2, seed=7, policy="reactive", settings=SimSettings())
        scenario, sim_seed = distinct(0)
        b.spend(SEARCH, scenario, sim_seed=sim_seed, policy="reactive",
                outcome={"ego_collision": False})
        payload = b.to_dict()
        payload["entries"][0]["episode_index"] = 5
        with self.assertRaises(ValueError):
            EpisodeBudget.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
