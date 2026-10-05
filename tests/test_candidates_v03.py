"""v0.3 candidate-state and final-audit tests.

The evaluator below is a deterministic scripted fake. Its outputs are NOT
simulator results and must never be reported as such.
"""
from __future__ import annotations
import unittest

from cornercaselab.budget import (
    SEARCH,
    FINAL_AUDIT,
    INTERNAL_CONFIRM,
    EpisodeBudget,
    error_outcome,
    interrupted_outcome,
    plan_spending,
)
from cornercaselab.candidates import (
    AUDIT_SELECTION_RULE,
    AUDIT_SELECTION_SEMANTICS_FROZEN,
    AUDIT_SELECTION_TODO,
    AUDIT_UNSPENT_RULE,
    CANDIDATE_FIELDS,
    Candidate,
    CandidateState,
    build_audit_worklist,
    run_final_audit,
)
from cornercaselab.domain import Scenario, perturb_scenario
from cornercaselab.simulator import SimSettings


class ScriptedEvaluator:
    """Deterministic stand-in for the simulator. Counts every call."""

    def __init__(self, collide_when=None, fail_when=None):
        self.calls = 0
        self.collide_when = collide_when or (lambda scenario, seed: False)
        self.fail_when = fail_when or (lambda scenario, seed: False)

    def __call__(self, scenario, sim_seed, policy, settings):
        self.calls += 1
        if self.fail_when(scenario, sim_seed):
            raise RuntimeError("scripted evaluator failure")
        hit = bool(self.collide_when(scenario, sim_seed))
        return {"ego_collision": hit, "termination": "collision" if hit else "goal",
                "note": "SCRIPTED TEST EVALUATOR: not a simulator result"}


def base_scenario(i: int = 0) -> Scenario:
    return Scenario(front_gap=30.0 + 3.0 * i, ego_speed=25.0 + 0.5 * i)


class CandidateStateTests(unittest.TestCase):
    def test_register_returns_same_object_for_identical_parameters(self):
        state = CandidateState()
        a = base_scenario(1)
        first = state.register(a, episode_index=0, termination="collision")
        again = state.register(base_scenario(1), episode_index=5, termination="collision")
        self.assertEqual(first.candidate_id, again.candidate_id)
        self.assertEqual(len(state), 1)
        # The first sighting is what counts; re-registration must not rewrite it.
        self.assertEqual(again.first_seen_episode, 0)

    def test_distinct_parameters_are_distinct_candidates(self):
        state = CandidateState()
        state.register(base_scenario(1), episode_index=0)
        state.register(base_scenario(2), episode_index=1)
        self.assertEqual(len(state), 2)

    def test_candidate_id_is_a_configuration_hash_not_a_root_cause(self):
        candidate = Candidate(candidate_id="deadbeef", first_seen_episode=0,
                              scenario=base_scenario(0).to_dict())
        self.assertEqual(candidate.candidate_id, "deadbeef")
        self.assertEqual(candidate.first_seen_episode, 0)

    def test_assignment_updates_internal_counters(self):
        state = CandidateState()
        candidate = state.register(base_scenario(1), episode_index=0)
        state.record_assignment(candidate.candidate_id, episode_index=1,
                                phase=INTERNAL_CONFIRM, collapsed=True)
        state.record_assignment(candidate.candidate_id, episode_index=2,
                                phase=INTERNAL_CONFIRM, collapsed=False)
        self.assertEqual(candidate.internal_episodes, 2)
        self.assertEqual(candidate.internal_collapses, 1)
        self.assertAlmostEqual(candidate.internal_stability, 0.5)
        lo, hi = candidate.internal_wilson95
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)

    def test_search_phase_cannot_be_attributed_to_a_candidate(self):
        state = CandidateState()
        candidate = state.register(base_scenario(1), episode_index=0)
        with self.assertRaises(ValueError):
            state.record_assignment(candidate.candidate_id, episode_index=1,
                                    phase=SEARCH, collapsed=True)

    def test_unknown_candidate_assignment_rejected(self):
        state = CandidateState()
        with self.assertRaises(KeyError):
            state.record_assignment("nope", episode_index=0, phase=INTERNAL_CONFIRM,
                                    collapsed=True)

    def test_double_attribution_of_one_episode_is_refused(self):
        state = CandidateState()
        c = state.register(base_scenario(1), episode_index=0)
        state.record_assignment(c.candidate_id, episode_index=1,
                                phase=INTERNAL_CONFIRM, collapsed=True)
        with self.assertRaises(ValueError):
            state.record_assignment(c.candidate_id, episode_index=1,
                                    phase=INTERNAL_CONFIRM, collapsed=True)

    def test_unconfirmed_candidate_has_no_stability_claim(self):
        state = CandidateState()
        candidate = state.register(base_scenario(3), episode_index=0)
        self.assertIsNone(candidate.internal_stability)
        self.assertIsNone(candidate.internal_wilson95)
        self.assertIsNone(candidate.audit_stability)
        self.assertIsNone(candidate.audit_wilson95)

    def test_candidate_has_no_parent_or_neighbourhood_field(self):
        """Rule 9: no neighbourhood expansion exists, so no dead parent field."""
        candidate = Candidate(candidate_id="deadbeef", first_seen_episode=0,
                              scenario=base_scenario(0).to_dict())
        self.assertFalse(hasattr(candidate, "parent_candidate_id"))
        self.assertNotIn("parent_candidate_id", candidate.to_dict())
        self.assertNotIn("parent_candidate_id", set(CANDIDATE_FIELDS))


class AttemptAndObservationTests(unittest.TestCase):
    """Rule 7: launched attempts and valid observations are separate counters."""

    def _budget(self, total: int = 20) -> EpisodeBudget:
        plan = plan_spending(total, search_fraction=0.5, audit_candidates=1, audit_repeats=10)
        return EpisodeBudget(total=total, seed=11, policy="reactive",
                             settings=SimSettings(), plan=plan)

    def test_attempt_does_not_imply_an_observation(self):
        state = CandidateState()
        candidate = state.register(base_scenario(0), episode_index=0)
        state.record_attempt(candidate.candidate_id, phase=INTERNAL_CONFIRM)
        self.assertEqual(candidate.internal_attempts, 1)
        # A failed launch leaves no stability evidence behind.
        self.assertEqual(candidate.internal_episodes, 0)
        self.assertIsNone(candidate.internal_stability)
        state.record_assignment(candidate.candidate_id, episode_index=1,
                                phase=INTERNAL_CONFIRM, collapsed=True)
        self.assertEqual(candidate.internal_attempts, 1)
        self.assertEqual(candidate.internal_episodes, 1)

    def test_search_phase_cannot_be_counted_as_an_attempt(self):
        state = CandidateState()
        candidate = state.register(base_scenario(0), episode_index=0)
        with self.assertRaises(ValueError):
            state.record_attempt(candidate.candidate_id, phase=SEARCH)

    def test_unknown_candidate_attempt_rejected(self):
        state = CandidateState()
        with self.assertRaises(KeyError):
            state.record_attempt("ghost", phase=INTERNAL_CONFIRM)

    def test_rebuild_launch_counts_follows_the_ledger(self):
        budget = self._budget()
        state = CandidateState()
        candidate = state.register(base_scenario(0), episode_index=0)
        for i in range(3):
            scenario = perturb_scenario(base_scenario(0), 500 + i)
            record = budget.reserve(INTERNAL_CONFIRM, scenario, sim_seed=600 + i,
                                    policy="reactive", candidate_id=candidate.candidate_id)
            if i == 1:
                budget.complete(record.episode_index, error_outcome(RuntimeError("x")))
            else:
                budget.complete(record.episode_index, {"ego_collision": True})
                state.record_assignment(candidate.candidate_id,
                                        episode_index=record.episode_index,
                                        phase=INTERNAL_CONFIRM, collapsed=True)
        # Deliberately corrupt the counter; the ledger is authoritative.
        candidate.internal_attempts = 99
        state.rebuild_launch_counts(budget)
        self.assertEqual(candidate.internal_attempts, 3)
        self.assertEqual(candidate.internal_episodes, 2)


class AuditWorklistTests(unittest.TestCase):
    """Final Audit is exactly `selected x audit_repeats`; there is no backfill."""

    def test_build_audit_worklist_is_exactly_selected_times_repeats(self):
        work = build_audit_worklist(["a", "b"], 3)
        self.assertEqual(work, [("a", 0), ("b", 0), ("a", 1), ("b", 1), ("a", 2), ("b", 2)])
        self.assertEqual(len(work), 6)
        self.assertEqual([cid for cid, _ in work].count("a"), 3)
        self.assertEqual([cid for cid, _ in work].count("b"), 3)

    def test_build_audit_worklist_round_indices_are_unique_per_candidate(self):
        work = build_audit_worklist(["a", "b", "c"], 4)
        for cid in ("a", "b", "c"):
            self.assertEqual([r for c, r in work if c == cid], [0, 1, 2, 3])

    def test_build_audit_worklist_never_backfills_a_short_selection(self):
        """One selected candidate must NOT absorb the unused reservation."""
        self.assertEqual(build_audit_worklist(["a"], 2), [("a", 0), ("a", 1)])
        self.assertEqual(len(build_audit_worklist(["a"], 30)), 30)

    def test_build_audit_worklist_never_invents_a_candidate(self):
        self.assertEqual(build_audit_worklist([], 5), [])
        self.assertEqual(build_audit_worklist([], 30), [])

    def test_build_audit_worklist_rejects_bad_rounds(self):
        for bad in (0, -1):
            with self.subTest(rounds=bad), self.assertRaises(ValueError):
                build_audit_worklist(["a"], bad)


class AuditSelectionTests(unittest.TestCase):
    def _state_with(self, stabilities):
        """stabilities: list of (collapses, n) in registration order."""
        state = CandidateState()
        for i, (collapses, n) in enumerate(stabilities):
            candidate = state.register(base_scenario(i), episode_index=i)
            for k in range(n):
                state.record_assignment(candidate.candidate_id, episode_index=100 + i * 50 + k,
                                        phase=INTERNAL_CONFIRM, collapsed=k < collapses)
        return state

    def test_selection_ranks_by_internal_stability_then_first_seen(self):
        state = self._state_with([(1, 2), (5, 5), (0, 3)])
        #             stability:    0.5        1.0        0.0
        chosen = state.select_for_audit(2)
        best = state.get(state.items[1].candidate_id)
        second = state.get(state.items[0].candidate_id)
        self.assertEqual(chosen, [best.candidate_id, second.candidate_id])

    def test_selection_ties_break_on_first_seen_then_id(self):
        state = self._state_with([(1, 2), (1, 2), (1, 2)])
        chosen = state.select_for_audit(2)
        self.assertEqual(len(chosen), 2)
        # Equal stability -> earlier first sighting wins, never a random pick.
        self.assertEqual(chosen[0], state.items[0].candidate_id)
        self.assertEqual(chosen[1], state.items[1].candidate_id)

    def test_selection_marks_chosen_candidates(self):
        state = self._state_with([(2, 2), (1, 2)])
        chosen = state.select_for_audit(1)
        self.assertEqual(state.get(chosen[0]).selected_for_audit, True)
        others = [c for c in state.items if c.candidate_id not in chosen]
        self.assertTrue(all(not c.selected_for_audit for c in others))

    def test_selection_never_returns_more_than_available(self):
        state = self._state_with([(1, 2)])
        self.assertEqual(len(state.select_for_audit(10)), 1)

    def test_selection_and_unspent_rules_are_pinned(self):
        self.assertEqual(AUDIT_SELECTION_RULE,
                         "rank_by_internal_stability_desc_then_first_seen_asc_v1")
        self.assertEqual(AUDIT_UNSPENT_RULE,
                         "no_backfill_leave_unused_audit_reservation_unspent_v1")

    def test_audit_selection_semantics_are_explicitly_unfrozen(self):
        """Rule 10: the cross-method meaning of the rule is a protocol TODO."""
        self.assertFalse(AUDIT_SELECTION_SEMANTICS_FROZEN)
        self.assertIn("UNFROZEN", AUDIT_SELECTION_TODO)
        self.assertIn("first-seen", AUDIT_SELECTION_TODO)

    def test_worklist_is_round_robin_and_complete(self):
        state = self._state_with([(2, 2), (2, 2)])
        ids = [c.candidate_id for c in state.items]
        work = state.audit_worklist(ids, 3)
        self.assertEqual([w[1] for w in work], [0, 0, 1, 1, 2, 2])
        self.assertEqual([w[0] for w in work],
                         [ids[0], ids[1], ids[0], ids[1], ids[0], ids[1]])

    def test_worklist_rejects_unknown_candidate(self):
        state = CandidateState()
        with self.assertRaises(KeyError):
            state.audit_worklist(["ghost"], 1)


class FinalAuditTests(unittest.TestCase):
    def _budget(self, total, plan, evaluator, seed=11):
        return EpisodeBudget(total=total, seed=seed, policy="reactive",
                             settings=SimSettings(), plan=plan)

    def test_audit_spends_exactly_the_reserved_allocation(self):
        plan = plan_spending(40, search_fraction=0.5, audit_candidates=2, audit_repeats=10)
        state = CandidateState()
        for i in range(2):
            state.register(base_scenario(i), episode_index=i)
        selected = state.select_for_audit(2)
        evaluator = ScriptedEvaluator()
        budget = self._budget(40, plan, evaluator)
        report = run_final_audit(state, budget, selected=selected, rounds=10,
                                 evaluator=evaluator)
        self.assertEqual(report["audit_launched"], 20)
        self.assertEqual(report["audit_completed"], 20)
        self.assertEqual(report["audit_reserved"], 20)
        self.assertEqual(report["audit_reserved_unspent"], 0)
        self.assertEqual(report["selected_candidate_count"], 2)
        self.assertEqual(report["audit_max_attempts_per_candidate"], 10)
        self.assertEqual(report["audit_incomplete_candidates"], [])
        self.assertEqual(budget.phase_counts()[FINAL_AUDIT], 20)
        self.assertEqual(budget.spent, 20)
        self.assertEqual(evaluator.calls, 20)
        for cid in selected:
            self.assertEqual(state.get(cid).audit_episodes, 10)
            self.assertEqual(state.get(cid).audit_attempts, 10)

    def test_no_candidate_can_exceed_audit_repeats(self):
        """audit_repeats is a hard per-candidate maximum, even when the pool could
        pay for more (two candidates selected out of a four-candidate reservation)."""
        plan = plan_spending(40, search_fraction=0.5, audit_candidates=4, audit_repeats=5)
        state = CandidateState()
        ids = [state.register(base_scenario(i), episode_index=i).candidate_id
               for i in range(4)]
        selected = state.select_for_audit(2)
        evaluator = ScriptedEvaluator()
        budget = self._budget(40, plan, evaluator)
        report = run_final_audit(state, budget, selected=selected, rounds=5,
                                 evaluator=evaluator)
        self.assertEqual(len(selected), 2)
        self.assertEqual(report["audit_launched"], 10)
        self.assertEqual(report["audit_max_attempts_per_candidate"], 5)
        self.assertEqual(report["audit_reserved"], 20)
        self.assertEqual(report["audit_reserved_unspent"], 10)
        for cid in selected:
            self.assertEqual(state.get(cid).audit_attempts, 5)
        for cid in set(ids) - set(selected):
            self.assertEqual(state.get(cid).audit_attempts, 0)
        self.assertEqual(budget.spent, 10)

    def test_unused_audit_allocation_is_left_unspent_not_backfilled(self):
        plan = plan_spending(10, search_fraction=0.6, audit_candidates=2, audit_repeats=2)
        state = CandidateState()
        only = state.register(base_scenario(0), episode_index=0)
        other = state.register(base_scenario(1), episode_index=1)
        evaluator = ScriptedEvaluator()
        budget = self._budget(10, plan, evaluator)
        report = run_final_audit(state, budget, selected=[only.candidate_id], rounds=2,
                                 evaluator=evaluator)
        self.assertEqual(report["audit_reserved"], 4)
        self.assertEqual(report["audit_launched"], 2)
        self.assertEqual(report["audit_reserved_unspent"], 2)
        self.assertEqual(report["selected_candidate_count"], 1)
        self.assertEqual(only.audit_attempts, 2)
        self.assertEqual(only.audit_episodes, 2)
        self.assertTrue(only.audit_complete)
        self.assertEqual(other.audit_attempts, 0)
        # The unused allocation was not converted into anything else.
        self.assertEqual(budget.spent, 2)
        self.assertEqual(budget.remaining, 8)

    def test_audit_uses_fresh_draws_not_the_base_scenario(self):
        plan = plan_spending(20, search_fraction=0.5, audit_candidates=1, audit_repeats=10)
        state = CandidateState()
        candidate = state.register(base_scenario(0), episode_index=0)
        evaluator = ScriptedEvaluator()
        budget = self._budget(20, plan, evaluator)
        run_final_audit(state, budget, selected=[candidate.candidate_id], rounds=10,
                        evaluator=evaluator)
        draws = [e.scenario for e in budget.entries if e.phase == FINAL_AUDIT]
        self.assertEqual(len(draws), 10)
        for draw in draws:
            s = Scenario.from_dict(draw)
            self.assertLessEqual(abs(s.front_gap - 30.0), 2.0 + 1e-6)
            self.assertLessEqual(abs(s.ego_speed - 25.0), 1.0 + 1e-6)
        self.assertEqual(len({tuple(sorted(d.items())) for d in draws}), 10)

    def test_an_audit_error_is_charged_but_never_rerun(self):
        plan = plan_spending(10, search_fraction=0.5, audit_candidates=1, audit_repeats=5)
        state = CandidateState()
        candidate = state.register(base_scenario(0), episode_index=0)
        seen = {"n": 0}

        def fail_first_two(scenario, seed):
            seen["n"] += 1
            return seen["n"] <= 2

        evaluator = ScriptedEvaluator(fail_when=fail_first_two)
        budget = self._budget(10, plan, evaluator)
        report = run_final_audit(state, budget, selected=[candidate.candidate_id],
                                 rounds=5, evaluator=evaluator)
        # Five planned launches, two of which errored: no replacement launches.
        self.assertEqual(report["audit_launched"], 5)
        self.assertEqual(report["audit_errors"], 2)
        self.assertEqual(report["audit_completed"], 3)
        self.assertEqual(candidate.audit_attempts, 5)
        self.assertEqual(candidate.audit_episodes, 3)
        self.assertLess(candidate.audit_episodes, 5)
        self.assertFalse(candidate.audit_complete)
        self.assertEqual(report["audit_incomplete_candidates"], [candidate.candidate_id])
        self.assertEqual(report["audit_max_attempts_per_candidate"], 5)
        self.assertEqual(budget.accounting()["errors"], report["audit_errors"])

    def test_audit_schedule_does_not_depend_on_audit_outcomes(self):
        plan = plan_spending(20, search_fraction=0.5, audit_candidates=2, audit_repeats=5)
        for collide in (False, True):
            with self.subTest(collide=collide):
                state = CandidateState()
                ids = [state.register(base_scenario(i), episode_index=i).candidate_id
                       for i in range(2)]
                evaluator = ScriptedEvaluator(collide_when=lambda scenario, seed: collide)
                budget = self._budget(20, plan, evaluator)
                report = run_final_audit(state, budget, selected=ids, rounds=5,
                                         evaluator=evaluator)
                self.assertEqual(report["audit_launched"], 10)
                self.assertEqual(report["audit_worklist"],
                                 [[ids[0], 0], [ids[1], 0], [ids[0], 1], [ids[1], 1],
                                  [ids[0], 2], [ids[1], 2], [ids[0], 3], [ids[1], 3],
                                  [ids[0], 4], [ids[1], 4]])
                for cid in ids:
                    self.assertEqual(state.get(cid).audit_attempts, 5)

    def test_audit_stops_at_the_audit_pool(self):
        plan = plan_spending(12, search_fraction=0.5, audit_candidates=1, audit_repeats=6)
        state = CandidateState()
        candidate = state.register(base_scenario(0), episode_index=0)
        evaluator = ScriptedEvaluator()
        budget = self._budget(12, plan, evaluator)
        report = run_final_audit(state, budget, selected=[candidate.candidate_id],
                                 rounds=6, evaluator=evaluator)
        self.assertEqual(report["audit_launched"], 6)
        self.assertEqual(report["audit_reserved_unspent"], 0)
        self.assertEqual(budget.phase_counts()[FINAL_AUDIT], 6)

    def test_audit_never_exceeds_the_pool_even_if_rounds_is_larger(self):
        plan = plan_spending(12, search_fraction=0.5, audit_candidates=1, audit_repeats=6)
        state = CandidateState()
        candidate = state.register(base_scenario(0), episode_index=0)
        evaluator = ScriptedEvaluator()
        budget = self._budget(12, plan, evaluator)
        report = run_final_audit(state, budget, selected=[candidate.candidate_id],
                                 rounds=99, evaluator=evaluator)
        self.assertEqual(report["audit_launched"], 6)
        self.assertEqual(budget.phase_counts()[FINAL_AUDIT], 6)
        self.assertEqual(budget.spent, 6)

    def test_empty_selection_spends_nothing_and_records_the_unspent_reservation(self):
        plan = plan_spending(10, search_fraction=0.5, audit_candidates=1, audit_repeats=5)
        state = CandidateState()
        evaluator = ScriptedEvaluator()
        budget = self._budget(10, plan, evaluator)
        report = run_final_audit(state, budget, selected=[], rounds=5, evaluator=evaluator)
        self.assertEqual(report["audit_launched"], 0)
        self.assertEqual(report["selected_candidate_count"], 0)
        self.assertEqual(report["audit_reserved"], 5)
        self.assertEqual(report["audit_reserved_unspent"], 5)
        self.assertEqual(report["audit_unspent_rule"], AUDIT_UNSPENT_RULE)
        self.assertEqual(budget.spent, 0)

    def test_audit_marks_completion_only_when_rounds_are_observed(self):
        plan = plan_spending(10, search_fraction=0.5, audit_candidates=1, audit_repeats=5)
        state = CandidateState()
        candidate = state.register(base_scenario(0), episode_index=0)
        evaluator = ScriptedEvaluator()
        budget = self._budget(10, plan, evaluator)
        run_final_audit(state, budget, selected=[candidate.candidate_id], rounds=5,
                        evaluator=evaluator)
        self.assertTrue(candidate.audit_complete)
        self.assertEqual(candidate.audit_episodes, 5)

    def test_candidate_state_roundtrip_is_exact(self):
        state = CandidateState()
        candidate = state.register(base_scenario(1), episode_index=0, termination="collision")
        state.record_attempt(candidate.candidate_id, phase=INTERNAL_CONFIRM)
        state.record_assignment(candidate.candidate_id, episode_index=1,
                                phase=INTERNAL_CONFIRM, collapsed=True)
        restored = CandidateState.from_dict(state.to_dict())
        self.assertEqual(restored.to_dict(), state.to_dict())
        self.assertEqual([c.to_dict() for c in restored.items],
                         [c.to_dict() for c in state.items])
        self.assertEqual(restored.get(candidate.candidate_id).internal_attempts, 1)


class InterruptedOutcomeTests(unittest.TestCase):
    def test_interrupted_outcome_uses_the_shared_schema(self):
        from cornercaselab.budget import OUTCOME_FIELDS, TERMINATION_INTERRUPTED
        budget = EpisodeBudget(total=2, seed=7, policy="reactive", settings=SimSettings())
        scenario = base_scenario(0)
        record = budget.reserve(SEARCH, scenario, sim_seed=5, policy="reactive")
        outcome = interrupted_outcome(record)
        self.assertEqual(set(outcome), set(OUTCOME_FIELDS))
        self.assertEqual(outcome["termination"], TERMINATION_INTERRUPTED)
        self.assertFalse(outcome["ego_collision"])
        self.assertIsNotNone(outcome["error"])
        budget.complete(record.episode_index, outcome)
        # Charged once, sealed as an error, and never a valid observation.
        self.assertEqual(budget.spent, 1)
        self.assertEqual(record.status, "error")
        acct = budget.accounting()
        self.assertEqual(acct["errors"], 1)
        self.assertEqual(acct["interrupted_episodes"], 1)
        self.assertEqual(acct["valid_observations"], 0)
        self.assertEqual(acct["collisions"], 0)


if __name__ == "__main__":
    unittest.main()
