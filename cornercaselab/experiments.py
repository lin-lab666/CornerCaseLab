"""v0.3 unified-budget comparison of three search/allocation methods.

All three methods spend through one :class:`~cornercaselab.budget.EpisodeBudget`.
They differ only in *how they allocate the shared search pool*, never in how
episodes are counted, seeded or audited:

``random_search``
    Draws fresh scenarios until the search pool is empty. No internal
    confirmation at all, so it pays zero confirmation overhead.
``fixed_explore_confirm``
    After every discovered collision candidate, immediately spends a fixed
    ``confirm_repeats`` internal confirmation episodes on it, until the search
    pool is empty or the budget runs out.
``adaptive_explore_confirm``
    Chooses each next episode between "explore one new scenario" and "confirm
    candidate c", using its own observed internal stability as input, under a
    global internal-confirmation cap of ``floor(pool_fraction * search_pool)``.
    The allocation rule is the method under test; it is not used as audit
    evidence.

Research-integrity notes kept in the output
------------------------------------------
* Internal confirmation is adaptively selected evidence. It is reported as
  *exploration* output and can never substitute for the fixed-n final audit.
* ``candidates_found`` counts distinct parameter configurations. That is a
  scenario-hash count, NOT a count of distinct root causes or independent bugs.
* Failed episodes cost budget and are excluded from quality numerators rather
  than being counted as safe.
* ``launched_attempts`` (every launch, including failures) drives the budget and
  every pool/launch cap; ``valid_observations`` (error-free episodes) is what
  stability intervals are computed on.
* The final audit is fixed-n per selected candidate: ``audit_repeats`` is a
  per-candidate MAXIMUM and unused audit allocation is left unspent and reported,
  never backfilled onto an existing candidate and never returned to search.
* Resume semantics are split explicitly. An *episode-boundary* interruption
  resumes equal to an uninterrupted run. An *in-flight* interruption (died after
  a durable reservation, before the outcome was persisted) seals that episode as
  interrupted/error: its budget slot stays spent and its evaluator is NOT called
  again, so no episode is ever launched twice while being charged once. Such a
  run is therefore NOT claimed to be result-equivalent.
* Derived seeds are checked for collisions inside a run
  (:class:`SeedStreamGuard`); ``derive_seed`` keeps only 32 bits, so streams that
  are separate by construction are not provably disjoint.
* The cross-method *meaning* of the audit selection rule is still an explicit,
  unfrozen protocol TODO; see
  :data:`~cornercaselab.candidates.AUDIT_SELECTION_TODO`.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
import math
import time
from typing import Any, Callable, Iterator, Mapping

from .budget import (
    FINAL_AUDIT,
    INTERNAL_CONFIRM,
    SEARCH,
    EpisodeBudget,
    EpisodeRecord,
    SpendPlan,
    error_outcome,
    interrupted_outcome,
    plan_spending,
)
from .candidates import (
    CandidateState,
    audit_report,
    build_audit_worklist,
)
from .checkpoint import (
    STAGE_AUDIT,
    STAGE_COMPLETE,
    STAGE_SEARCH,
    RunCheckpoint,
    RunState,
    load_checkpoint,
    save_checkpoint,
)
from .domain import Scenario, check_seed, perturb_scenario, sample_scenario
from .policy import POLICIES
from .simulator import SimSettings
from .storage import append_jsonl, metadata as storage_metadata, write_json

#: The v0.2 calibration seed. Permitted for calibration only; using it for the
#: formal evaluation would turn calibration evidence into evaluation evidence.
CALIBRATION_SEED = 20261003
METHODS = ("random_search", "fixed_explore_confirm", "adaptive_explore_confirm")
PURPOSES = ("evaluation", "development")

METHOD_RUN_SCHEMA = "ccl-v03-method-run-v1"
METHOD_SUMMARY_SCHEMA = "ccl-v03-method-summary-v1"
COMPARISON_SCHEMA = "ccl-method-comparison-v2"

#: Seed streams, each a distinct sha256 label off the one master seed.
SEED_STREAMS = ("search", "simulation", "internal-local", "internal-nuisance",
                "audit-local", "audit-nuisance")

METHOD_CONFIG_FIELDS = ("confirm_repeats", "max_internal_per_candidate",
                        "pool_fraction", "min_internal_per_candidate")


class SeedPolicyError(ValueError):
    """Raised when a run tries to reuse the calibration seed for evaluation."""


def ensure_evaluation_seed(seed: int, purpose: str) -> int:
    """Validate a run's declared purpose and its master seed.

    ``development`` may reuse the calibration seed; ``evaluation`` may not,
    because that would turn calibration evidence into evaluation evidence.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}, not {purpose!r}.")
    check_seed(seed)
    if purpose == "evaluation" and seed == CALIBRATION_SEED:
        raise SeedPolicyError(
            f"Seed {CALIBRATION_SEED} is the v0.2 calibration seed. Using it for a "
            f"formal evaluation would reuse calibration evidence as evaluation "
            f"evidence. Choose an independent seed, or declare purpose='development'."
        )
    return seed


def seed_policy(seed: int, purpose: str) -> dict[str, Any]:
    """The auditable description of which seed streams a run consumed."""
    return {
        "master_seed": seed,
        "purpose": purpose,
        "calibration_seed": CALIBRATION_SEED,
        "calibration_seed_forbidden_for_evaluation": True,
        "seed_streams": list(SEED_STREAMS),
        "derivation": "sha256(canonical_json([master, *labels]))[:4] big-endian",
    }


@dataclass(frozen=True)
class SearchProblem:
    """Everything the methods need, excluding the allocation policy itself."""
    policy: str
    settings: SimSettings
    sampler: Callable[[int], Scenario] = sample_scenario
    perturb: Callable[[Scenario, int], Scenario] = perturb_scenario

    def __post_init__(self) -> None:
        if self.policy not in POLICIES:
            raise ValueError(f"Unknown policy {self.policy!r}; choose {POLICIES}.")


@dataclass(frozen=True)
class MethodConfig:
    """Allocation parameters. Total budget and audit reservation are NOT here."""
    confirm_repeats: int = 1
    max_internal_per_candidate: int | None = None
    pool_fraction: float = 0.25
    min_internal_per_candidate: int = 2

    def validate(self) -> None:
        if self.confirm_repeats < 1:
            raise ValueError("confirm_repeats must be >= 1.")
        if self.max_internal_per_candidate is not None and self.max_internal_per_candidate < 1:
            raise ValueError("max_internal_per_candidate must be >= 1 or None.")
        if not 0.0 < self.pool_fraction <= 1.0:
            raise ValueError("pool_fraction must be in (0, 1].")
        if self.min_internal_per_candidate < 0:
            raise ValueError("min_internal_per_candidate must be >= 0.")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MethodConfig:
        return cls(**{name: value[name] for name in METHOD_CONFIG_FIELDS})


def internal_confirm_cap(config: MethodConfig, plan: SpendPlan) -> int:
    """``floor(pool_fraction * search_pool)``.

    This is a GLOBAL cap on the number of *launched* internal-confirmation
    episodes summed over every candidate, not a per-candidate share that each
    newly discovered candidate re-opens for itself.
    """
    return math.floor(config.pool_fraction * plan.search_pool)


@dataclass
class MethodResult:
    method: str
    seed: int
    purpose: str
    budget_total: int
    policy: str
    accounting: dict[str, Any]
    plan: dict[str, Any]
    config: dict[str, Any]
    seed_policy: dict[str, Any]
    search_stop: dict[str, Any]
    candidates: list[dict[str, Any]]
    selected_for_audit: list[str]
    audit: dict[str, Any]
    seed_streams: dict[str, Any]
    search_collisions: int
    naive_collisions: int
    wall_time_s: float
    metadata: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    ledger: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def simulator_outcome(result: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise a real :func:`cornercaselab.simulator.evaluate` result.

    Distinguishes an ego collision from an NPC-only collision, an off-road exit,
    a timeout and a software error; they are never collapsed into one number.
    """
    return {
        "ego_collision": bool(result.get("ego_collision")),
        "goal_reached": bool(result.get("goal_reached")),
        "npc_collisions": int(result.get("npc_collisions", 0)),
        "termination": result.get("termination"),
        "min_forward_ttc_sampled_s": result.get("min_forward_ttc_sampled_s"),
        "sim_time_s": result.get("sim_time_s"),
        "error": None,
        "note": "real HighwayEnv episode",
    }


def make_simulator_evaluator():
    """Lazy real-simulator evaluator: importing it is what costs a simulator call."""
    def evaluator(scenario: Scenario, sim_seed: int, policy: str, settings: SimSettings):
        from .simulator import evaluate  # imported lazily to keep tests dependency-free

        return simulator_outcome(evaluate(scenario, sim_seed, policy, settings))
    return evaluator


Evaluator = Callable[[Scenario, int, str, SimSettings], dict[str, Any]]


def _launch(evaluator: Evaluator, scenario: Scenario, sim_seed: int,
            policy: str, settings: SimSettings) -> dict[str, Any]:
    """Run one episode evaluator.

    An ``Exception`` becomes the one shared error outcome, so a failing episode
    is still charged and recorded. ``KeyboardInterrupt``/``SystemExit`` are not
    swallowed: they must be able to stop a run so the checkpoint can resume it.
    """
    try:
        return dict(evaluator(scenario, sim_seed, policy, settings))
    except Exception as exc:  # counted, never hidden in a safe denominator
        return error_outcome(exc)


# ------------------------------------------------------------ seed guard
class SeedCollisionError(RuntimeError):
    """Raised when two episodes of one v0.3 run derive the same seed.

    ``derive_seed`` keeps only the first 32 bits of a sha256, so the seed streams
    are separate by construction but not provably disjoint. A collision inside one
    stream would silently duplicate a perturbation or a nuisance draw, so it is
    refused rather than recorded twice.
    """


def _iter_derived_seeds(budget: EpisodeBudget) -> Iterator[tuple[str, str | None, int, int]]:
    """Yield ``(stream, scope, seed, episode_index)`` for every ledger row.

    A pure function of the ledger, so it needs no extra persisted state and stays
    correct across a resume. Search rows consume the ``search`` (scenario
    sampler) and ``simulation`` (nuisance) streams by ordinal. Confirmation and
    audit rows consume a phase-specific local-perturbation stream scoped to their
    candidate -- the round index is the row's ordinal within its
    ``(phase, candidate)`` pair, which is exactly how it was assigned -- plus a
    phase-specific nuisance stream whose seed is the stored ``sim_seed`` the
    evaluator actually received.
    """
    search_ordinal = 0
    round_ordinals: dict[tuple[str, str], int] = {}
    for record in budget.entries:
        if record.phase == SEARCH:
            yield ("search", None,
                   budget.seed_for("search", search_ordinal), record.episode_index)
            yield ("simulation", None, record.sim_seed, record.episode_index)
            search_ordinal += 1
            continue
        if record.phase not in (INTERNAL_CONFIRM, FINAL_AUDIT):
            continue
        candidate_id = record.candidate_id
        if candidate_id is None:
            continue
        key = (record.phase, candidate_id)
        round_index = round_ordinals.get(key, 0)
        round_ordinals[key] = round_index + 1
        local_label = "internal-local" if record.phase == INTERNAL_CONFIRM else "audit-local"
        nuisance_label = ("internal-nuisance" if record.phase == INTERNAL_CONFIRM
                          else "audit-nuisance")
        yield (local_label, candidate_id,
               budget.seed_for(local_label, candidate_id, round_index),
               record.episode_index)
        yield (nuisance_label, None, record.sim_seed, record.episode_index)


class SeedStreamGuard:
    """Refuse a derived-seed collision inside one run instead of hiding it.

    Rebuilt from the ledger, so a resumed run knows every seed its earlier
    episodes already consumed and cannot silently repeat one.
    """

    def __init__(self, budget: EpisodeBudget) -> None:
        self._claimed: dict[tuple[str, str | None, int], int] = {}
        for stream, scope, seed, episode_index in _iter_derived_seeds(budget):
            self.claim(stream, scope, seed, episode_index)

    def claim(self, stream: str, scope: str | None, seed: int, episode_index: int) -> None:
        """Claim one derived seed for an episode that is about to be launched."""
        key = (stream, scope, seed)
        previous = self._claimed.get(key)
        if previous is not None:
            where = f" for candidate {scope}" if scope else ""
            raise SeedCollisionError(
                f"Derived seed collision in stream {stream!r}{where}: episode "
                f"{episode_index} derived seed {seed}, already consumed by episode "
                f"{previous}. derive_seed keeps only 32 bits, so independent streams "
                f"are not provably disjoint; refusing to silently repeat a "
                f"perturbation or nuisance draw. Change the master seed."
            )
        self._claimed[key] = episode_index

    def __len__(self) -> int:
        return len(self._claimed)


def seed_stream_report(budget: EpisodeBudget) -> dict[str, Any]:
    """Record the derived seeds a run consumed, and any duplicate among them."""
    per_stream: dict[str, int] = {}
    per_scope: dict[str, list[int]] = {}
    for stream, scope, seed, _episode_index in _iter_derived_seeds(budget):
        per_stream[stream] = per_stream.get(stream, 0) + 1
        per_scope.setdefault(stream if scope is None else f"{stream}::{scope}",
                             []).append(seed)
    duplicates = {key: sorted(seed for seed, count in Counter(seeds).items() if count > 1)
                  for key, seeds in per_scope.items()}
    return {
        "streams": dict(sorted(per_stream.items())),
        "guard_keys": len(per_scope),
        "duplicate_seeds": {k: v for k, v in sorted(duplicates.items()) if v},
        "seed_streams_declared": list(SEED_STREAMS),
        "seed_space_bits": 32,
        "rule": ("derived seeds must be unique per stream (and per candidate for the "
                 "local-perturbation streams); SeedStreamGuard raises "
                 "SeedCollisionError instead of silently repeating a draw"),
        "note": ("derive_seed returns the first 4 bytes of a sha256, so the streams are "
                 "separate by construction but not provably disjoint."),
    }


# --------------------------------------------------------------- decisions
def _next_confirm_target(state: CandidateState, config: MethodConfig) -> str | None:
    items = state.items
    if not items:
        return None

    def within_candidate_cap(candidate) -> bool:
        return (config.max_internal_per_candidate is None
                or candidate.internal_attempts < config.max_internal_per_candidate)

    under = [c for c in items
             if c.internal_episodes < config.min_internal_per_candidate
             and within_candidate_cap(c)]
    if under:
        under.sort(key=lambda c: (c.internal_episodes, c.first_seen_episode, c.candidate_id))
        return under[0].candidate_id
    eligible = [c for c in items if within_candidate_cap(c)]
    if not eligible:
        return None
    # Highest Wilson lower bound wins; ties go to the earlier candidate.
    ranked = sorted(eligible, key=lambda c: (
        -(c.internal_wilson95[0] if c.internal_wilson95 else -1.0),
        c.internal_episodes, c.first_seen_episode, c.candidate_id))
    return ranked[0].candidate_id


def _next_action(method: str, budget: EpisodeBudget, plan: SpendPlan,
                 config: MethodConfig, state: CandidateState
                 ) -> tuple | None:
    """The method's next episode, or ``None`` when its search phase is over."""
    if method == "random_search":
        if budget.phase_remaining(SEARCH) > 0 and budget.remaining > 0:
            return ("explore",)
        return None

    if method == "fixed_explore_confirm":
        # Confirm the newest candidate before exploring again, exactly as
        # "explore, then immediately spend confirm_repeats on what was found".
        if state.items:
            newest = state.items[-1]
            if (newest.internal_attempts < config.confirm_repeats
                    and budget.phase_remaining(INTERNAL_CONFIRM) > 0
                    and budget.remaining > 0):
                return ("confirm", newest.candidate_id, newest.internal_attempts)
        if budget.phase_remaining(SEARCH) > 0 and budget.remaining > 0:
            return ("explore",)
        return None

    if method == "adaptive_explore_confirm":
        explore_remaining = budget.phase_remaining(SEARCH)
        confirm_remaining = budget.phase_remaining(INTERNAL_CONFIRM)
        if explore_remaining <= 0 and confirm_remaining <= 0:
            return None
        launched_internal = sum(c.internal_attempts for c in state.items)
        global_left = internal_confirm_cap(config, plan) - launched_internal
        target = _next_confirm_target(state, config)
        if target is not None and confirm_remaining > 0 and global_left > 0:
            return ("confirm", target, state.get(target).internal_attempts)
        if explore_remaining > 0:
            return ("explore",)
        return None

    raise ValueError(f"Unknown method {method!r}; choose {METHODS}.")


def search_stop_report(method: str, budget: EpisodeBudget, plan: SpendPlan,
                       config: MethodConfig, state: CandidateState) -> dict[str, Any]:
    """Why the search phase ended, and whether a target was silently abandoned."""
    reasons: list[str] = []
    if budget.phase_remaining(SEARCH) <= 0 or budget.phase_remaining(INTERNAL_CONFIRM) <= 0:
        reasons.append("search_pool_exhausted")
    if budget.remaining <= 0:
        reasons.append("total_budget_exhausted")
    if method == "adaptive_explore_confirm":
        if sum(c.internal_attempts for c in state.items) >= internal_confirm_cap(config, plan):
            reasons.append("internal_confirm_global_cap_reached")
    if method == "fixed_explore_confirm" and state.items:
        if state.items[-1].internal_attempts < config.confirm_repeats:
            reasons.append("fixed_confirmation_budget_shortfall")
    if not reasons:
        reasons.append("search_finished")
    unmet = sorted(c.candidate_id for c in state.items
                   if c.internal_episodes < config.min_internal_per_candidate)
    return {
        "reasons": reasons,
        "min_internal_per_candidate": config.min_internal_per_candidate,
        "min_internal_attempts_target": config.min_internal_per_candidate,
        "min_internal_unmet": unmet,
        "min_internal_unmet_count": len(unmet),
        "note": ("min_internal_per_candidate is a target, not a guarantee. When the "
                 "global confirmation cap or the remaining budget cannot cover it, "
                 "confirmation stops explicitly and the unmet candidates are listed "
                 "here instead of being silently mis-ranked or retried forever."),
    }


# ------------------------------------------------------------------ runner
class MethodRunner:
    """Episode-stepping, checkpointable executor for one method.

    Each step launches at most one episode. An episode is *reserved* (its budget
    slot is spent and it enters the ledger) before the evaluator runs, and
    *completed* afterwards, so a launched simulator call always has budget
    already occupied. Checkpoints written between episodes therefore capture a
    state that can be resumed without repeating an episode, reusing a seed or
    changing the accounting.
    """

    def __init__(self, method: str, *, budget: EpisodeBudget, plan: SpendPlan,
                 problem: SearchProblem, config: MethodConfig, purpose: str,
                 evaluator: Evaluator, metadata: dict[str, Any] | None = None,
                 state: CandidateState | None = None,
                 run_state: RunState | None = None) -> None:
        if method not in METHODS:
            raise ValueError(f"Unknown method {method!r}; choose {METHODS}.")
        config.validate()
        ensure_evaluation_seed(budget.seed, purpose)
        if plan.total != budget.total:
            raise ValueError("plan.total must equal the declared total budget.")
        self.method = method
        self.budget = budget
        self.plan = plan
        self.problem = problem
        self.config = config
        self.purpose = purpose
        self.evaluator = evaluator
        self.metadata = metadata if metadata is not None else storage_metadata()
        self.state = state if state is not None else CandidateState()
        self.rs = run_state if run_state is not None else RunState(method=method)
        if self.rs.method != method:
            raise ValueError(
                f"Checkpoint belongs to method {self.rs.method!r}, not {method!r}."
            )
        self._checkpoint_path: Path | None = None
        # The ledger is authoritative for launch counters and search indices, so
        # a resumed run cannot drift from an uninterrupted one.
        self.state.rebuild_launch_counts(self.budget)
        self.rs.search_index = self.budget.phase_counts()[SEARCH]
        self._seal_reserved()
        # Rebuilt AFTER sealing, so every seed the earlier episodes consumed --
        # including the sealed ones -- is known before anything new is launched.
        self.seeds = SeedStreamGuard(self.budget)

    # ---------------------------------------------------------------- state
    @property
    def done(self) -> bool:
        return self.rs.stage == STAGE_COMPLETE

    def _seal_reserved(self) -> None:
        """Seal durable reservations that never completed; never re-launch them.

        Frozen resume semantics: once an episode is durably reserved its budget
        slot is consumed. If the process died before the outcome was persisted,
        re-running the evaluator would make one ledger row correspond to two real
        evaluator launches -- a free replay. The episode is therefore sealed with
        an interrupted outcome that keeps its episode index, phase, scenario and
        seeds (see :func:`cornercaselab.budget.interrupted_outcome`), and it is
        never attributed a stability observation. The narrow window "durably
        reserved but the evaluator was never actually entered" is charged the same
        conservative way.

        Consequence, stated plainly: an *in-flight* interruption does NOT leave a
        run equivalent to an uninterrupted one -- the interrupted episode becomes
        an interrupted/error row. Only an episode-boundary interruption is
        result-equivalent.
        """
        for record in self.budget.reserved_unfinished:
            self.budget.complete(record.episode_index, interrupted_outcome(record))

    def _register_search_hit(self, record: EpisodeRecord, scenario: Scenario,
                             outcome: Mapping[str, Any]) -> None:
        if outcome.get("error") is None and outcome.get("ego_collision"):
            self.state.register(scenario, episode_index=record.episode_index,
                                termination=outcome.get("termination"))

    def _apply_observation(self, record: EpisodeRecord, scenario: Scenario,
                           outcome: Mapping[str, Any]) -> None:
        if record.phase == SEARCH:
            self._register_search_hit(record, scenario, outcome)
            return
        candidate_id = record.candidate_id
        if candidate_id is None or candidate_id not in self.state:
            return
        if outcome.get("error") is not None:
            return
        try:
            self.state.record_assignment(
                candidate_id, episode_index=record.episode_index, phase=record.phase,
                collapsed=bool(outcome.get("ego_collision")))
        except ValueError:
            # Already attributed before the crash; the ledger stays authoritative.
            pass

    # -------------------------------------------------------------- steps
    def step(self) -> EpisodeRecord | None:
        """Advance by one episode (or one stage transition)."""
        if self.done:
            raise RuntimeError("The method run is already complete.")
        if self.rs.stage == STAGE_SEARCH:
            return self._step_search()
        if self.rs.stage == STAGE_AUDIT:
            return self._step_audit()
        raise RuntimeError(f"Unknown runner stage {self.rs.stage!r}.")

    def _step_search(self) -> EpisodeRecord | None:
        action = _next_action(self.method, self.budget, self.plan, self.config, self.state)
        if action is None:
            self._begin_audit()
            return None
        if action[0] == "explore":
            index = self.budget.phase_counts()[SEARCH]
            record = self._explore(index)
        else:
            _, candidate_id, round_index = action
            record = self._confirm(candidate_id, round_index)
        self.rs.search_index = self.budget.phase_counts()[SEARCH]
        return record

    def _begin_audit(self) -> None:
        report = search_stop_report(self.method, self.budget, self.plan,
                                    self.config, self.state)
        self.rs.stop_reason = ",".join(report["reasons"])
        self.rs.min_internal_unmet = list(report["min_internal_unmet"])
        selected = self.state.select_for_audit(self.plan.audit_candidates)
        self.rs.audit_selected = list(selected)
        # Exactly selected x audit_repeats. audit_repeats is a per-candidate
        # MAXIMUM and unused audit allocation is left unspent, never backfilled.
        work = build_audit_worklist(selected, self.plan.audit_repeats)
        self.rs.audit_work = [[cid, r] for cid, r in work]
        self.rs.audit_cursor = 0
        self.rs.stage = STAGE_AUDIT

    def _step_audit(self) -> EpisodeRecord | None:
        if self.rs.audit_cursor >= len(self.rs.audit_work):
            self._finish()
            return None
        if self.budget.remaining <= 0 or self.budget.phase_remaining(FINAL_AUDIT) <= 0:
            self.rs.audit_cursor = len(self.rs.audit_work)
            self._finish()
            return None
        candidate_id, round_index = self.rs.audit_work[self.rs.audit_cursor]
        # Consume the worklist slot before launching, so an interruption cannot
        # make the resumed run re-use this round index (and therefore its seed).
        self.rs.audit_cursor += 1
        return self._audit(str(candidate_id), int(round_index))

    def _finish(self) -> None:
        for candidate_id in self.rs.audit_selected:
            candidate = self.state.get(candidate_id)
            candidate.audit_complete = candidate.audit_episodes >= self.plan.audit_repeats
        self.rs.stage = STAGE_COMPLETE

    # --------------------------------------------------------- episodes
    def _checkpoint_reservation(self) -> None:
        """Make a reservation durable BEFORE its evaluator is launched.

        Mandatory for every resumable run. Without it an interruption between
        launch and completion would leave no durable record, and resume would
        re-decide -- and therefore re-launch -- the episode from scratch, which is
        exactly the free replay this design forbids.
        """
        if self._checkpoint_path is not None:
            self.save(self._checkpoint_path)

    def _explore(self, index: int) -> EpisodeRecord:
        local_seed = self.budget.seed_for("search", index)
        sim_seed = self.budget.seed_for("simulation", index)
        prospective = len(self.budget.entries)
        self.seeds.claim("search", None, local_seed, prospective)
        self.seeds.claim("simulation", None, sim_seed, prospective)
        scenario = self.problem.sampler(local_seed)
        record = self.budget.reserve(SEARCH, scenario, sim_seed=sim_seed,
                                     policy=self.problem.policy)
        self._checkpoint_reservation()
        outcome = _launch(self.evaluator, scenario, sim_seed,
                          self.problem.policy, self.problem.settings)
        self.budget.complete(record.episode_index, outcome)
        self._register_search_hit(record, scenario, outcome)
        return record

    def _confirm(self, candidate_id: str, round_index: int) -> EpisodeRecord:
        candidate = self.state.get(candidate_id)
        base = Scenario.from_dict(candidate.scenario)
        local_seed = self.budget.seed_for("internal-local", candidate_id, round_index)
        sim_seed = self.budget.seed_for("internal-nuisance", candidate_id, round_index)
        prospective = len(self.budget.entries)
        self.seeds.claim("internal-local", candidate_id, local_seed, prospective)
        self.seeds.claim("internal-nuisance", None, sim_seed, prospective)
        scenario = self.problem.perturb(base, local_seed)
        record = self.budget.reserve(INTERNAL_CONFIRM, scenario, sim_seed=sim_seed,
                                     policy=self.problem.policy,
                                     candidate_id=candidate_id,
                                     note=f"internal_confirm_round_{round_index}")
        self.state.record_attempt(candidate_id, phase=INTERNAL_CONFIRM)
        self._checkpoint_reservation()
        outcome = _launch(self.evaluator, scenario, sim_seed,
                          self.problem.policy, self.problem.settings)
        self.budget.complete(record.episode_index, outcome)
        if outcome.get("error") is None:
            self.state.record_assignment(candidate_id, episode_index=record.episode_index,
                                         phase=INTERNAL_CONFIRM,
                                         collapsed=bool(outcome.get("ego_collision")))
        return record

    def _audit(self, candidate_id: str, round_index: int) -> EpisodeRecord:
        candidate = self.state.get(candidate_id)
        local_seed = self.budget.seed_for("audit-local", candidate_id, round_index)
        sim_seed = self.budget.seed_for("audit-nuisance", candidate_id, round_index)
        prospective = len(self.budget.entries)
        self.seeds.claim("audit-local", candidate_id, local_seed, prospective)
        self.seeds.claim("audit-nuisance", None, sim_seed, prospective)
        scenario = self.problem.perturb(Scenario.from_dict(candidate.scenario), local_seed)
        record = self.budget.reserve(FINAL_AUDIT, scenario, sim_seed=sim_seed,
                                     policy=self.problem.policy,
                                     candidate_id=candidate_id,
                                     note=f"final_audit_round_{round_index}")
        self.state.record_attempt(candidate_id, phase=FINAL_AUDIT)
        self._checkpoint_reservation()
        outcome = _launch(self.evaluator, scenario, sim_seed,
                          self.problem.policy, self.problem.settings)
        self.budget.complete(record.episode_index, outcome)
        if outcome.get("error") is None:
            self.state.record_assignment(candidate_id, episode_index=record.episode_index,
                                         phase=FINAL_AUDIT,
                                         collapsed=bool(outcome.get("ego_collision")))
        return record

    # --------------------------------------------------------------- drive
    def run(self, *, checkpoint_path: Path | str | None = None,
            checkpoint_every: int | None = 1,
            max_episodes: int | None = None) -> MethodRunner:
        """Drive the method.

        ``max_episodes`` stops at an *episode boundary* after that many launches,
        leaving a clean checkpoint with no reservation in flight (used to produce
        and test a boundary interruption). ``None`` runs to completion.
        """
        if checkpoint_every is not None and checkpoint_every < 1:
            raise ValueError("checkpoint_every must be >= 1 or None.")
        if max_episodes is not None and max_episodes < 1:
            raise ValueError("max_episodes must be >= 1 or None.")
        self._checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        if self._checkpoint_path is not None:
            # A resumable run has a durable boundary before its first launch, and
            # every later reservation is made durable in _checkpoint_reservation()
            # BEFORE its evaluator runs. checkpoint_every only adds extra
            # completion-boundary snapshots on top of that.
            self.save(self._checkpoint_path)
        launched = 0
        guard = 0
        while not self.done:
            guard += 1
            if guard > 4 * (self.plan.total + 4):
                raise RuntimeError("Method runner failed to terminate; aborting.")
            record = self.step()
            if record is None:
                continue
            launched += 1
            if (self._checkpoint_path is not None and checkpoint_every
                    and launched % checkpoint_every == 0):
                self.save(self._checkpoint_path)
            if max_episodes is not None and launched >= max_episodes:
                break
        if self._checkpoint_path is not None:
            self.save(self._checkpoint_path)
        return self

    # ---------------------------------------------------------- persistence
    def checkpoint(self) -> RunCheckpoint:
        self.rs.search_index = self.budget.phase_counts()[SEARCH]
        return RunCheckpoint(
            method=self.method, purpose=self.purpose, seed=self.budget.seed,
            policy=self.budget.policy, settings=asdict(self.budget.settings),
            plan=self.plan.to_dict(), config=asdict(self.config),
            budget=self.budget.to_dict(), candidates=self.state.to_dict(),
            run=self.rs.to_dict(), metadata=self.metadata)

    def save(self, path: Path | str) -> Path:
        return save_checkpoint(path, self.checkpoint())

    # --------------------------------------------------------------- report
    def result(self, start: float | None = None) -> MethodResult:
        accounting = self.budget.accounting()
        audit = audit_report(self.state, self.budget,
                             selected=list(self.rs.audit_selected),
                             rounds=self.plan.audit_repeats,
                             work=[(str(cid), int(r)) for cid, r in self.rs.audit_work])
        search_collisions = sum(
            1 for e in self.budget.entries
            if e.phase == SEARCH and e.outcome is not None
            and e.outcome.get("ego_collision"))
        return MethodResult(
            method=self.method, seed=self.budget.seed, purpose=self.purpose,
            budget_total=self.budget.total, policy=self.budget.policy,
            accounting=accounting, plan=self.plan.to_dict(),
            config=asdict(self.config), seed_policy=seed_policy(self.budget.seed, self.purpose),
            search_stop=search_stop_report(self.method, self.budget, self.plan,
                                           self.config, self.state),
            candidates=[c.to_dict() for c in self.state.items],
            selected_for_audit=list(self.rs.audit_selected), audit=audit,
            seed_streams=seed_stream_report(self.budget),
            search_collisions=search_collisions,
            naive_collisions=accounting["collisions"],
            wall_time_s=(time.perf_counter() - start) if start is not None else 0.0,
            metadata=self.metadata,
            notes=[
                "candidates_found counts distinct parameter configurations (scenario "
                "hashes), NOT distinct root causes or independent bugs.",
                "Internal confirmation is adaptively selected evidence for the search "
                "phase; it is never used as the final audit's evidence.",
                "The final audit is fixed-n per selected candidate: audit_repeats is a "
                "per-candidate maximum, its episodes are charged to the same budget "
                "counter, and unused audit allocation is left unspent rather than "
                "backfilled onto a candidate.",
                "Allocation-rule parameters are methodology choices that must be frozen "
                "before any formal comparison.",
                "Resume semantics: an episode-boundary interruption is result-equivalent "
                "to an uninterrupted run; an in-flight interruption seals the episode it "
                "caught as interrupted/error and is NOT result-equivalent (see "
                "budget.interrupted_outcome).",
                "The cross-method audit selection semantics are NOT frozen yet; see "
                "audit_selection_todo in this result.",
            ],
            ledger=self.budget.ledger_rows(),
        )


# ------------------------------------------------------------------ entry
def run_method(method: str, *, out_budget: EpisodeBudget, plan: SpendPlan,
               problem: SearchProblem, config: MethodConfig, evaluator: Evaluator,
               purpose: str = "development", checkpoint_path: Path | str | None = None,
               checkpoint_every: int | None = 1,
               metadata: dict[str, Any] | None = None) -> MethodResult:
    """Run one method to completion of its budget; returns raw, unattributed facts.

    When ``checkpoint_path`` is given the run is resumable: every episode
    reservation is made durable before its evaluator is launched.
    """

    start = time.perf_counter()
    runner = MethodRunner(method, budget=out_budget, plan=plan, problem=problem,
                          config=config, purpose=purpose, evaluator=evaluator,
                          metadata=metadata)
    runner.run(checkpoint_path=checkpoint_path, checkpoint_every=checkpoint_every)
    return runner.result(start)


def resume_method(checkpoint_path: Path | str, *, evaluator: Evaluator,
                  problem: SearchProblem | None = None,
                  checkpoint_out: Path | str | None = None,
                  checkpoint_every: int | None = 1) -> MethodResult:
    """Resume a checkpointed method run.

    What is guaranteed:

    * no episode that was already launched is launched again (a real evaluator
      launch is never charged zero times);
    * no seed or round index is reused, and the deterministic seed stream after
      the resume point is unchanged;
    * the episode accounting is unchanged;
    * an **episode-boundary** interruption resumes into a ledger equal to an
      uninterrupted run's (apart from wall-clock and other non-deterministic
      metadata).

    What is NOT guaranteed: an **in-flight** interruption (the process died after
    a durable reservation but before the outcome was persisted) leaves that
    episode sealed as ``interrupted``/error. Its budget slot stays consumed, its
    episode index/phase/scenario/seeds are preserved, and the evaluator is *not*
    called again -- so the resulting ledger differs from an uninterrupted run by
    that one row and is deliberately not claimed to be equivalent.
    """
    checkpoint = load_checkpoint(checkpoint_path)
    plan = SpendPlan.from_dict(checkpoint.plan)
    config = MethodConfig.from_dict(checkpoint.config)
    budget = checkpoint.restore_budget()
    if problem is None:
        problem = SearchProblem(policy=checkpoint.policy,
                                settings=SimSettings(**checkpoint.settings))
    start = time.perf_counter()
    runner = MethodRunner(checkpoint.method, budget=budget, plan=plan, problem=problem,
                          config=config, purpose=checkpoint.purpose,
                          evaluator=evaluator, metadata=checkpoint.metadata,
                          state=checkpoint.restore_candidates(),
                          run_state=checkpoint.restore_run_state())
    runner.run(checkpoint_path=checkpoint_out if checkpoint_out is not None else checkpoint_path,
               checkpoint_every=checkpoint_every)
    return runner.result(start)


# ------------------------------------------------------------- persistence
def write_method_files(out_dir: Path, result: MethodResult) -> dict[str, Any]:
    """Write one method's run into an EXISTING directory."""
    out_dir = Path(out_dir)
    write_json(out_dir / "manifest.json", {
        "schema": METHOD_RUN_SCHEMA,
        "method": result.method,
        "purpose": result.purpose,
        "seed": result.seed,
        "seed_policy": result.seed_policy,
        "policy": result.policy,
        "budget_total": result.budget_total,
        "plan": result.plan,
        "method_config": result.config,
        "metadata": result.metadata,
        "seed_streams": result.seed_streams,
        "notes": result.notes,
    })
    for row in result.ledger:
        append_jsonl(out_dir / "ledger.jsonl", row)
    write_json(out_dir / "candidates.json", result.candidates)
    write_json(out_dir / "summary.json", {
        "schema": METHOD_SUMMARY_SCHEMA,
        "method": result.method,
        "purpose": result.purpose,
        "seed": result.seed,
        "accounting": result.accounting,
        "search_stop": result.search_stop,
        "selected_for_audit": result.selected_for_audit,
        "audit": result.audit,
        "seed_streams": result.seed_streams,
        "search_collisions": result.search_collisions,
        "naive_collisions": result.naive_collisions,
        "wall_time_s": result.wall_time_s,
        "notes": result.notes,
    })
    return {"out": str(out_dir), "files": sorted(p.name for p in out_dir.iterdir())}


def write_method_output(out_dir: Path | str, result: MethodResult) -> dict[str, Any]:
    """Persist one finished method run; refuses to overwrite an existing run."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=False)
    return write_method_files(target, result)


def compare_methods(*, methods: list[str], total_budget: int, seed: int, policy: str,
                    search_fraction: float, audit_candidates: int, audit_repeats: int,
                    config: MethodConfig, evaluator: Evaluator,
                    settings: SimSettings | None = None,
                    purpose: str = "evaluation",
                    sampler: Callable[[int], Scenario] = sample_scenario,
                    perturb: Callable[[Scenario, int], Scenario] = perturb_scenario,
                    out: Path | str | None = None,
                    checkpoint_every: int | None = 1,
                    ) -> dict[str, Any]:
    """Run several methods under one identical budget plan and seed."""
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}, not {purpose!r}.")
    ensure_evaluation_seed(seed, purpose)
    for method in methods:
        if method not in METHODS:
            raise ValueError(f"Unknown method {method!r}; choose {METHODS}.")
    plan = plan_spending(total_budget, search_fraction=search_fraction,
                         audit_candidates=audit_candidates, audit_repeats=audit_repeats)
    settings = settings or SimSettings()
    problem = SearchProblem(policy=policy, settings=settings, sampler=sampler, perturb=perturb)
    metadata = storage_metadata()
    out_path = Path(out) if out is not None else None
    if out_path is not None:
        out_path.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, Any]] = []
    for method in methods:
        budget = EpisodeBudget(total=total_budget, seed=seed, policy=policy,
                               settings=settings, plan=plan)
        method_dir = (out_path / method) if out_path is not None else None
        result = run_method(method, out_budget=budget, plan=plan, problem=problem,
                            config=config, evaluator=evaluator, purpose=purpose,
                            checkpoint_path=(method_dir / "checkpoint.json") if method_dir else None,
                            checkpoint_every=checkpoint_every,
                            metadata=metadata)
        results.append(result.to_dict())
        if method_dir is not None:
            write_method_files(method_dir, result)
    payload = {
        "schema": COMPARISON_SCHEMA, "budget_total": total_budget, "seed": seed,
        "purpose": purpose, "policy": policy, "search_fraction": search_fraction,
        "plan": plan.to_dict(), "method_config": asdict(config),
        "seed_policy": seed_policy(seed, purpose), "metadata": metadata,
        "methods": results,
        "fairness_note": ("All methods use one budget counter, one seed and one "
                          "pre-registered audit. Differences come only from the "
                          "allocation rule."),
    }
    if out_path is not None:
        write_json(out_path / "comparison.json", payload)
        write_json(out_path / "manifest.json", {
            "schema": COMPARISON_SCHEMA, "purpose": purpose, "seed": seed,
            "seed_policy": seed_policy(seed, purpose), "policy": policy,
            "budget_total": total_budget, "plan": plan.to_dict(),
            "method_config": asdict(config), "methods": list(methods),
            "metadata": metadata, "notes": [
                "This directory is a v0.3 comparison run; it refuses to overwrite.",
                "Cross-method audit selection semantics remain an unfrozen protocol TODO.",
            ],
        })
    return payload
