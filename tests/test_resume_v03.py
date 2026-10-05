"""v0.3 resumability tests.

Only development fake evaluators are used here: no simulator and no formal
experiment. Artifacts go into a workspace-relative scratch directory, because
``tempfile`` is blocked by the DSH Windows write sandbox.

The two interruption classes are tested separately and must not be conflated:

* **A. episode boundary** -- the process stopped between episodes, so the durable
  checkpoint is a clean boundary. Resume must reproduce an uninterrupted run.
* **B. in-flight** -- the process died after a durable reservation but before the
  outcome was persisted. The episode is sealed as interrupted/error and its
  evaluator is NOT called again. Accounting stays correct and no episode is ever
  launched twice while being charged once, but the run is deliberately NOT
  claimed to be result-equivalent.
"""
from __future__ import annotations
import shutil
import unittest
from pathlib import Path

from cornercaselab.budget import (
    FINAL_AUDIT,
    INTERNAL_CONFIRM,
    SEARCH,
    EpisodeBudget,
    plan_spending,
)
from cornercaselab.checkpoint import (
    CHECKPOINT_SCHEMA,
    STAGE_COMPLETE,
    STAGE_SEARCH,
    RunCheckpoint,
    load_checkpoint,
    save_checkpoint,
)
from cornercaselab.experiments import (
    METHODS,
    MethodConfig,
    MethodRunner,
    SearchProblem,
    resume_method,
    run_method,
)
from cornercaselab.simulator import SimSettings

SMALL = dict(total_budget=40, search_fraction=0.5, audit_candidates=2, audit_repeats=10)
RATE = 45


class CountingEvaluator:
    """Deterministic collision fake that records every REAL evaluator entry.

    The launch is recorded *before* any optional interrupt, so an entry that
    immediately raises still counts as a real launch -- which is exactly the case
    the free-replay accounting must not hide.
    """

    def __init__(self, launches: list, rate_percent: int = RATE,
                 interrupt_after: int | None = None,
                 fail_every: int | None = None):
        self.launches = launches
        self.rate_percent = rate_percent
        self.interrupt_after = interrupt_after
        self.fail_every = fail_every
        self._entered = 0

    def __call__(self, scenario, sim_seed, policy, settings):
        self._entered += 1
        self.launches.append((scenario.uid, sim_seed))
        if self.interrupt_after is not None and self._entered > self.interrupt_after:
            raise KeyboardInterrupt
        if self.fail_every and self._entered % self.fail_every == 0:
            raise RuntimeError("scripted evaluator failure")
        score = int(float(scenario.front_gap) * 13 + float(scenario.ramp_x) * 3) % 100
        hit = score < self.rate_percent
        return {"ego_collision": hit, "goal_reached": not hit, "npc_collisions": 0,
                "termination": "collision" if hit else "goal", "error": None,
                "note": "SCRIPTED TEST EVALUATOR: not a simulator result"}


def _remove_scratch(path: Path, base: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    try:
        base.rmdir()
    except OSError:
        pass


def scratch(test: unittest.TestCase, name: str) -> Path:
    base = Path(__file__).resolve().parent / "_v03_scratch"
    path = base / f"resume_{name}"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    test.addCleanup(_remove_scratch, path, base)
    return path


def _plan():
    return plan_spending(SMALL["total_budget"], search_fraction=SMALL["search_fraction"],
                         audit_candidates=SMALL["audit_candidates"],
                         audit_repeats=SMALL["audit_repeats"])


def _budget(seed: int = 4242) -> EpisodeBudget:
    return EpisodeBudget(total=SMALL["total_budget"], seed=seed, policy="reactive",
                         settings=SimSettings(), plan=_plan())


def _problem() -> SearchProblem:
    return SearchProblem(policy="reactive", settings=SimSettings())


def _run(method: str, evaluator, seed: int = 4242, config: MethodConfig | None = None,
         **kwargs):
    return run_method(method, out_budget=_budget(seed), plan=_plan(),
                      problem=_problem(), config=config or MethodConfig(),
                      evaluator=evaluator, purpose="development", **kwargs)


def _advance(method: str, evaluator, path, steps: int,
             config: MethodConfig | None = None) -> MethodRunner:
    """Run exactly `steps` episodes and stop at an episode boundary."""
    runner = MethodRunner(method, budget=_budget(), plan=_plan(), problem=_problem(),
                          config=config or MethodConfig(), purpose="development",
                          evaluator=evaluator)
    runner.run(checkpoint_path=path, checkpoint_every=1, max_episodes=steps)
    return runner


def view(result) -> list[tuple]:
    """The deterministic part of a run: everything except wall-clock metadata."""
    return [(r["episode_index"], r["phase"], r["scenario_id"], r["sim_seed"],
             r["candidate_id"], r["note"], r["status"],
             r["outcome"]["ego_collision"], r["outcome"]["error"])
            for r in result.ledger]


class CheckpointRoundTripTests(unittest.TestCase):
    def test_finished_run_checkpoint_restores_everything(self):
        path = scratch(self, "roundtrip") / "nested" / "checkpoint.json"
        result = _run("adaptive_explore_confirm", CountingEvaluator([]),
                      checkpoint_path=path, checkpoint_every=5)
        self.assertTrue(path.exists())
        checkpoint = load_checkpoint(path)
        self.assertEqual(checkpoint.schema, CHECKPOINT_SCHEMA)
        self.assertEqual(checkpoint.method, "adaptive_explore_confirm")
        self.assertEqual(checkpoint.purpose, "development")
        self.assertEqual(checkpoint.seed, result.seed)
        self.assertEqual(checkpoint.policy, "reactive")
        self.assertEqual(checkpoint.run["stage"], STAGE_COMPLETE)
        self.assertEqual(checkpoint.run["search_index"],
                         result.accounting["phase_episodes"][SEARCH])
        budget = checkpoint.restore_budget()
        self.assertEqual(budget.ledger_rows(), result.ledger)
        self.assertEqual(budget.accounting(), result.accounting)
        state = checkpoint.restore_candidates()
        self.assertEqual([c.to_dict() for c in state.items], result.candidates)
        self.assertEqual(checkpoint.restore_run_state().audit_selected,
                         result.selected_for_audit)
        for key in ("git", "python", "packages", "source_sha256", "created_utc"):
            self.assertIn(key, checkpoint.metadata)

    def test_resaving_a_loaded_checkpoint_is_stable(self):
        path = scratch(self, "stable") / "checkpoint.json"
        _run("fixed_explore_confirm", CountingEvaluator([]),
             checkpoint_path=path, checkpoint_every=3)
        first = load_checkpoint(path)
        second_path = scratch(self, "stable2") / "checkpoint.json"
        save_checkpoint(second_path, RunCheckpoint.from_dict(first.to_dict()))
        self.assertEqual(load_checkpoint(second_path).to_dict(), first.to_dict())

    def test_checkpoint_rejects_a_foreign_schema(self):
        path = scratch(self, "schema") / "checkpoint.json"
        _run("random_search", CountingEvaluator([]), checkpoint_path=path,
             checkpoint_every=5)
        payload = load_checkpoint(path).to_dict()
        payload["schema"] = "not-a-ccl-checkpoint"
        with self.assertRaises(ValueError):
            RunCheckpoint.from_dict(payload)

    def test_resuming_a_finished_run_launches_nothing(self):
        path = scratch(self, "finished") / "checkpoint.json"
        full = _run("random_search", CountingEvaluator([]), checkpoint_path=path,
                    checkpoint_every=1)
        launches: list = []
        resumed = resume_method(path, evaluator=CountingEvaluator(launches))
        self.assertEqual(launches, [])
        self.assertEqual(view(resumed), view(full))
        self.assertEqual(resumed.accounting, full.accounting)

    def test_a_checkpoint_from_another_method_is_refused(self):
        path = scratch(self, "foreign") / "checkpoint.json"
        _run("random_search", CountingEvaluator([]), checkpoint_path=path,
             checkpoint_every=1)
        checkpoint = load_checkpoint(path)
        with self.assertRaises(ValueError):
            MethodRunner("fixed_explore_confirm", budget=checkpoint.restore_budget(),
                         plan=_plan(), problem=_problem(),
                         config=MethodConfig(), purpose="development",
                         evaluator=CountingEvaluator([]),
                         state=checkpoint.restore_candidates(),
                         run_state=checkpoint.restore_run_state())


class DurableReservationTests(unittest.TestCase):
    def test_every_reservation_is_durable_before_its_evaluator_runs(self):
        """The frozen requirement: reserve durably, THEN launch."""
        path = scratch(self, "durable") / "checkpoint.json"
        observed: list[tuple[int, str]] = []

        def evaluator(scenario, sim_seed, policy, settings):
            entries = load_checkpoint(path).budget["entries"]
            observed.append((len(entries), entries[-1]["status"]))
            return {"ego_collision": False, "goal_reached": True, "npc_collisions": 0,
                    "termination": "goal", "error": None, "note": "test"}

        result = _run("random_search", evaluator, checkpoint_path=path,
                      checkpoint_every=1)
        self.assertEqual(len(observed), result.accounting["episodes_spent"])
        for launch_number, (entry_count, status) in enumerate(observed, start=1):
            # This episode is on disk AND still reserved when the evaluator starts.
            self.assertEqual(entry_count, launch_number)
            self.assertEqual(status, "reserved")

    def test_episode_boundary_interruption_is_result_equivalent(self):
        """A: stopping between episodes must reproduce an uninterrupted run."""
        for method in METHODS:
            with self.subTest(method=method):
                full = _run(method, CountingEvaluator([]))
                path = scratch(self, f"boundary_{method}") / "checkpoint.json"
                runner = _advance(method, CountingEvaluator([]), path, steps=6)
                self.assertFalse(runner.done)
                checkpoint = load_checkpoint(path)
                self.assertEqual(len(checkpoint.budget["entries"]), 6)
                self.assertEqual(checkpoint.run["stage"], STAGE_SEARCH)
                self.assertFalse([e for e in checkpoint.budget["entries"]
                                  if e["status"] == "reserved"])
                resumed = resume_method(path, evaluator=CountingEvaluator([]))
                self.assertEqual(view(resumed), view(full))
                self.assertEqual(resumed.accounting, full.accounting)
                self.assertEqual(resumed.candidates, full.candidates)
                self.assertEqual(resumed.search_stop, full.search_stop)
                self.assertEqual(resumed.selected_for_audit, full.selected_for_audit)
                self.assertEqual(resumed.audit, full.audit)
                self.assertEqual(resumed.seed_streams, full.seed_streams)

    def test_an_in_flight_interruption_seals_the_episode_and_never_relaunches_it(self):
        """B: in-flight. Not equivalent, but no free replay and accounting holds."""
        method = "random_search"
        full = _run(method, CountingEvaluator([]))
        path = scratch(self, "inflight") / "checkpoint.json"
        launches: list = []
        with self.assertRaises(KeyboardInterrupt):
            _run(method, CountingEvaluator(launches, interrupt_after=6),
                 checkpoint_path=path, checkpoint_every=1)
        # Six episodes completed and the seventh was genuinely entered.
        self.assertEqual(len(launches), 7)
        checkpoint = load_checkpoint(path)
        entries = checkpoint.budget["entries"]
        self.assertEqual(len(entries), 7)
        self.assertEqual(entries[-1]["status"], "reserved")
        self.assertIsNone(entries[-1]["outcome"])

        # Resume: the reserved episode is SEALED, not re-launched.
        resumed = resume_method(path, evaluator=CountingEvaluator(launches))
        self.assertEqual(resumed.accounting["interrupted_episodes"], 1)
        self.assertEqual(resumed.accounting["pending_unfinished_episodes"], 0)
        # The decisive property: real evaluator launches == ledger rows.
        self.assertEqual(len(launches), len(resumed.ledger))
        self.assertEqual(len(resumed.ledger), resumed.accounting["episodes_spent"])

        sealed = resumed.ledger[6]
        self.assertEqual(sealed["episode_index"], 6)
        self.assertEqual(sealed["phase"], SEARCH)
        self.assertEqual(sealed["status"], "error")
        self.assertEqual(sealed["outcome"]["termination"], "interrupted")
        self.assertIsNotNone(sealed["outcome"]["error"])
        # Identity of the sealed episode is preserved verbatim from the reservation.
        self.assertEqual(sealed["scenario_id"], entries[6]["scenario_id"])
        self.assertEqual(sealed["scenario"], entries[6]["scenario"])
        self.assertEqual(sealed["sim_seed"], entries[6]["sim_seed"])
        self.assertEqual(sealed["policy"], entries[6]["policy"])

        # Not equivalent to the uninterrupted run -- and not claimed to be.
        self.assertNotEqual(view(resumed), view(full))
        self.assertEqual([r[:5] for r in view(resumed)[:6]],
                         [r[:5] for r in view(full)[:6]])
        self.assertEqual(resumed.accounting["episodes_spent"],
                         full.accounting["episodes_spent"])
        self.assertEqual(resumed.accounting["errors"],
                         full.accounting["errors"] + 1)

        # No seed or index is reused after the seal.
        keys = [(r["scenario_id"], r["sim_seed"]) for r in resumed.ledger]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual([r["episode_index"] for r in resumed.ledger],
                         list(range(len(resumed.ledger))))

    def test_the_launch_accounting_equals_the_ledger_across_a_resume(self):
        for method in METHODS:
            with self.subTest(method=method):
                path = scratch(self, f"accounting_{method}") / "checkpoint.json"
                launches: list = []
                with self.assertRaises(KeyboardInterrupt):
                    _run(method, CountingEvaluator(launches, interrupt_after=5),
                         checkpoint_path=path, checkpoint_every=1)
                self.assertEqual(len(launches), 6)
                resumed = resume_method(path, evaluator=CountingEvaluator(launches))
                # Without the seal this would be len(ledger) + 1.
                self.assertEqual(len(launches), len(resumed.ledger))

    def test_the_interrupted_prefix_is_preserved_verbatim(self):
        method = "fixed_explore_confirm"
        full = _run(method, CountingEvaluator([]))
        path = scratch(self, "prefix") / "checkpoint.json"
        with self.assertRaises(KeyboardInterrupt):
            _run(method, CountingEvaluator([], interrupt_after=7),
                 checkpoint_path=path, checkpoint_every=1)
        checkpoint = load_checkpoint(path)
        prefix = [(e["episode_index"], e["phase"], e["scenario_id"], e["sim_seed"])
                  for e in checkpoint.budget["entries"][:-1]]
        resumed = resume_method(path, evaluator=CountingEvaluator([]))
        self.assertEqual(
            [(r["episode_index"], r["phase"], r["scenario_id"], r["sim_seed"])
             for r in resumed.ledger[:len(prefix)]],
            prefix)
        self.assertEqual(
            [(r["episode_index"], r["phase"], r["scenario_id"], r["sim_seed"])
             for r in full.ledger[:len(prefix)]],
            prefix)

    def test_a_resumed_run_can_itself_be_interrupted_and_resumed(self):
        method = "adaptive_explore_confirm"
        full = _run(method, CountingEvaluator([]))
        launches: list = []
        first_path = scratch(self, "twice_a") / "checkpoint.json"
        with self.assertRaises(KeyboardInterrupt):
            _run(method, CountingEvaluator(launches, interrupt_after=5),
                 checkpoint_path=first_path, checkpoint_every=1)
        second_path = scratch(self, "twice_b") / "checkpoint.json"
        with self.assertRaises(KeyboardInterrupt):
            resume_method(first_path,
                          evaluator=CountingEvaluator(launches, interrupt_after=4),
                          checkpoint_out=second_path, checkpoint_every=1)
        self.assertTrue(second_path.exists())
        resumed = resume_method(second_path, evaluator=CountingEvaluator(launches))
        # Two episodes were sealed by the two in-flight interruptions ...
        self.assertEqual(resumed.accounting["interrupted_episodes"], 2)
        self.assertEqual(resumed.accounting["pending_unfinished_episodes"], 0)
        # ... and still: one ledger row per real evaluator launch.
        self.assertEqual(len(launches), len(resumed.ledger))
        self.assertEqual(len(resumed.ledger), resumed.accounting["episodes_spent"])
        self.assertNotEqual(view(resumed), view(full))
        keys = [(r["scenario_id"], r["sim_seed"]) for r in resumed.ledger]
        self.assertEqual(len(keys), len(set(keys)))

    def test_resume_never_reuses_a_scenario_seed_or_round_index(self):
        method = "adaptive_explore_confirm"
        path = scratch(self, "no_reuse") / "checkpoint.json"
        with self.assertRaises(KeyboardInterrupt):
            _run(method, CountingEvaluator([], rate_percent=80, interrupt_after=9),
                 checkpoint_path=path, checkpoint_every=1,
                 config=MethodConfig(pool_fraction=0.25))
        resumed = resume_method(path,
                               evaluator=CountingEvaluator([], rate_percent=80))
        keys = [(r["scenario_id"], r["sim_seed"]) for r in resumed.ledger]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual([r["episode_index"] for r in resumed.ledger],
                         list(range(len(resumed.ledger))))
        rounds = [(r["candidate_id"], r["note"]) for r in resumed.ledger
                  if r["phase"] in (INTERNAL_CONFIRM, FINAL_AUDIT)]
        self.assertEqual(len(rounds), len(set(rounds)))
        self.assertEqual(resumed.audit["audit_max_attempts_per_candidate"],
                         resumed.audit["audit_repeats"])


if __name__ == "__main__":
    unittest.main()
