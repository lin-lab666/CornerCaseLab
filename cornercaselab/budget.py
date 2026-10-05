"""v0.3 unified episode budget: one counter, every phase charged.

Research-integrity contract implemented here
-------------------------------------------
1. One counter for the whole comparison. Search, internal-confirmation and
   final-audit episodes all go through :class:`EpisodeBudget`, so a confirmation
   is never free and can never be dropped from the denominator.
2. The counter cannot exceed its declared total or a phase pool; over-spending
   raises :class:`BudgetExhausted` instead of silently truncating or rounding.
3. An episode is *reserved* (charged) BEFORE its evaluator/simulator is launched
   and *completed* afterwards. A launched episode therefore always has budget
   already occupied, and an evaluator that raises still leaves a charged,
   recorded episode rather than an invisible failure.
4. A spend plan must close exactly: ``search_pool + audit_pool == total``. No
   declared episode budget may be left unconsumable.
5. Seeds are derived from one master seed with explicit labels, so different
   methods consume independent, reproducible streams.
6. Every recorded outcome uses one fixed schema, so downstream analysis never
   has to special-case a phase or a failure mode.

This module is standard-library only and performs no simulation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .domain import Scenario, canonical_json, check_seed, derive_seed
from .simulator import SimSettings

SEARCH = "search"
INTERNAL_CONFIRM = "internal_confirm"
FINAL_AUDIT = "final_audit"

PHASES = (SEARCH, INTERNAL_CONFIRM, FINAL_AUDIT)
#: Phases that re-run an already discovered scenario and therefore cost budget
#: for the same reason confirmation does.
CONFIRMATION_PHASES = (INTERNAL_CONFIRM, FINAL_AUDIT)

STATUS_RESERVED = "reserved"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"
STATUSES = (STATUS_RESERVED, STATUS_COMPLETED, STATUS_ERROR)

TERMINATION_ERROR = "error"
#: A durable reservation whose evaluator never completed. It is sealed, never
#: re-launched, and never counted as a valid observation.
TERMINATION_INTERRUPTED = "interrupted"

#: The one outcome schema shared by every phase.
OUTCOME_FIELDS = (
    "ego_collision", "goal_reached", "npc_collisions", "termination",
    "min_forward_ttc_sampled_s", "sim_time_s", "error", "note",
)

#: ``SpendPlan`` constructor fields, used when restoring a plan from JSON.
SPEND_PLAN_FIELDS = ("total", "search_pool", "audit_pool", "audit_candidates",
                     "audit_repeats", "search_fraction")


def error_outcome(exc: BaseException) -> dict[str, Any]:
    """The single error outcome shape, identical in every phase."""
    return {
        "ego_collision": False,
        "goal_reached": False,
        "npc_collisions": 0,
        "termination": TERMINATION_ERROR,
        "min_forward_ttc_sampled_s": None,
        "sim_time_s": None,
        "error": f"{type(exc).__name__}: {exc}",
        "note": "evaluator raised: episode charged, excluded from stability",
    }


def normalise_outcome(outcome: Mapping[str, Any]) -> dict[str, Any]:
    """Force one evaluator result into :data:`OUTCOME_FIELDS`.

    A missing ``termination``/``goal_reached``/``npc_collisions`` is filled in
    rather than left absent, so a downstream reader never sees a phase-dependent
    shape. A raised evaluator is expected to arrive as :func:`error_outcome`.
    """
    row: dict[str, Any] = {name: outcome.get(name) for name in OUTCOME_FIELDS}
    row["ego_collision"] = bool(outcome.get("ego_collision"))
    row["goal_reached"] = bool(outcome.get("goal_reached"))
    row["npc_collisions"] = int(outcome.get("npc_collisions") or 0)
    if not isinstance(row["termination"], str):
        row["termination"] = "error" if row["error"] is not None else "unknown"
    if row["note"] is None:
        row["note"] = ""
    return row


@dataclass(frozen=True)
class SpendPlan:
    """How a fixed total budget is divided *before* any episode is run."""
    total: int
    search_pool: int
    audit_pool: int
    audit_candidates: int
    audit_repeats: int
    search_fraction: float

    @property
    def audit_per_candidate(self) -> int:
        return self.audit_repeats

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "audit_per_candidate": self.audit_per_candidate}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SpendPlan:
        return cls(**{name: value[name] for name in SPEND_PLAN_FIELDS})


def plan_spending(total: int, *, search_fraction: float,
                  audit_candidates: int, audit_repeats: int) -> SpendPlan:
    """Reserve the final-audit pool, then hand the exact remainder to search.

    ``search_pool = floor(total * search_fraction)`` keeps ``search_fraction``
    meaningful instead of silently ignoring it, but the plan must close exactly:
    ``search_pool + audit_pool == total``. A plan that would leave episodes it
    can never spend is rejected rather than silently shrinking search, audit or
    the declared total.
    """
    if not isinstance(total, int) or isinstance(total, bool) or total < 1:
        raise ValueError("total budget must be a positive integer.")
    if not 0.0 < search_fraction < 1.0:
        raise ValueError("search_fraction must be strictly between 0 and 1.")
    if not isinstance(audit_candidates, int) or isinstance(audit_candidates, bool) or audit_candidates < 1:
        raise ValueError("audit_candidates must be a positive integer.")
    if not isinstance(audit_repeats, int) or isinstance(audit_repeats, bool) or audit_repeats < 1:
        raise ValueError("audit_repeats must be a positive integer.")
    audit_pool = audit_candidates * audit_repeats
    search_pool = int(total * search_fraction)
    if search_pool + audit_pool != total:
        raise ValueError(
            f"Spend plan does not close: search_pool ({search_pool} = "
            f"floor({total} * {search_fraction})) + audit_pool ({audit_pool} = "
            f"{audit_candidates} * {audit_repeats}) = {search_pool + audit_pool} "
            f"!= total ({total}). Adjust search_fraction, audit_candidates or "
            f"audit_repeats so the whole episode budget is consumable; do not "
            f"silently shrink the audit."
        )
    return SpendPlan(total=total, search_pool=search_pool, audit_pool=audit_pool,
                     audit_candidates=audit_candidates, audit_repeats=audit_repeats,
                     search_fraction=search_fraction)


class BudgetExhausted(RuntimeError):
    """Raised instead of silently exceeding a declared budget."""


@dataclass
class EpisodeRecord:
    """One launched simulator call of any phase.

    ``status`` moves ``reserved -> completed|error``. A ``reserved`` record is a
    launched episode whose budget is already spent but whose evaluator has not
    reported back yet; that is exactly what makes an interrupted run resumable.
    """
    episode_index: int
    phase: str
    scenario: dict[str, float]
    scenario_id: str
    sim_seed: int
    policy: str
    settings: dict[str, Any]
    candidate_id: str | None = None
    note: str | None = None
    status: str = STATUS_RESERVED
    outcome: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def interrupted_outcome(record: EpisodeRecord) -> dict[str, Any]:
    """The outcome of a DURABLE reservation whose evaluator never completed.

    Frozen resume semantics: once an episode is durably reserved its budget slot
    is consumed, so the episode must never be re-launched. Re-running it would
    make one ledger row correspond to two real evaluator launches (a free
    replay). It is sealed here instead, keeping its episode index, phase,
    scenario and seeds, and it is never attributed a stability observation.

    The narrow window "durably reserved but the evaluator had not actually been
    entered yet" is charged conservatively for the same reason.
    """
    return {
        "ego_collision": False,
        "goal_reached": False,
        "npc_collisions": 0,
        "termination": TERMINATION_INTERRUPTED,
        "min_forward_ttc_sampled_s": None,
        "sim_time_s": None,
        "error": (f"Interrupted: {record.phase} episode {record.episode_index} was "
                  f"durably reserved but never completed; sealed, not relaunched."),
        "note": "durable reservation sealed as interrupted: budget spent, no free replay",
    }


class EpisodeBudget:
    """The single counter every phase must reserve through.

    ``globally_unique`` mirrors ``run_trials``: exact replay of a fixed
    (scenario, sim_seed, policy) tuple is a reproducibility check, not a new
    independent episode, but its simulator call still costs budget.
    """

    def __init__(self, *, total: int, seed: int, policy: str,
                 settings: SimSettings, plan: SpendPlan | None = None,
                 globally_unique: bool = True) -> None:
        if not isinstance(total, int) or isinstance(total, bool) or total < 1:
            raise ValueError("total budget must be a positive integer.")
        self.total = total
        self.seed = check_seed(seed)
        self.policy = policy
        self.settings = settings
        self.plan = plan
        if plan is not None and plan.total != total:
            raise ValueError("plan.total must equal the declared total budget.")
        self.globally_unique = bool(globally_unique)
        self._entries: list[EpisodeRecord] = []
        self._spent: dict[str, int] = {phase: 0 for phase in PHASES}
        self._seen: set[tuple] = set()

    # ------------------------------------------------------------------ read
    @property
    def entries(self) -> list[EpisodeRecord]:
        return list(self._entries)

    @property
    def spent(self) -> int:
        return len(self._entries)

    @property
    def remaining(self) -> int:
        return self.total - self.spent

    @property
    def reserved_unfinished(self) -> list[EpisodeRecord]:
        return [e for e in self._entries if e.status == STATUS_RESERVED]

    def phase_counts(self) -> dict[str, int]:
        return dict(self._spent)

    def phase_remaining(self, phase: str) -> int:
        if phase not in PHASES:
            raise ValueError(f"Unknown phase {phase!r}; choose {PHASES}.")
        if self.plan is None:
            return self.remaining
        pool = {SEARCH: self.plan.search_pool, INTERNAL_CONFIRM: self.plan.search_pool,
                FINAL_AUDIT: self.plan.audit_pool}[phase]
        if phase == FINAL_AUDIT:
            return pool - self._spent[phase]
        # Search and internal confirmation share the search pool.
        return pool - self._spent[SEARCH] - self._spent[INTERNAL_CONFIRM]

    def can_spend(self, phase: str) -> bool:
        return self.remaining > 0 and self.phase_remaining(phase) > 0

    def seed_for(self, *labels: object) -> int:
        return derive_seed(self.seed, *labels)

    # ----------------------------------------------------------------- write
    def reserve(self, phase: str, scenario: Scenario, *, sim_seed: int,
                policy: str, candidate_id: str | None = None,
                note: str | None = None) -> EpisodeRecord:
        """Occupy one episode slot BEFORE the simulator is launched.

        The reserved record is immediately part of the ledger and immediately
        counted as spent, so an interruption between launch and completion can
        never leave an unaccounted simulator call.
        """
        if phase not in PHASES:
            raise ValueError(f"Unknown phase {phase!r}; choose {PHASES}.")
        if self.remaining <= 0:
            raise BudgetExhausted(
                f"Total budget of {self.total} episodes is exhausted; "
                f"refusing to launch another {phase} episode."
            )
        if self.phase_remaining(phase) <= 0:
            raise BudgetExhausted(
                f"Pool for phase {phase!r} is exhausted; refusing to borrow "
                f"from another phase's pool."
            )
        key = (canonical_json(scenario.to_dict()), sim_seed, policy)
        if self.globally_unique and key in self._seen:
            raise BudgetExhausted(
                "Duplicate (scenario, sim_seed, policy) episode. An exact rerun is a "
                "reproducibility check, not a fresh independent episode; set "
                "globally_unique=False only when you intend to spend budget on replay."
            )
        self._seen.add(key)
        record = EpisodeRecord(
            episode_index=len(self._entries), phase=phase,
            scenario=scenario.to_dict(), scenario_id=scenario.uid, sim_seed=sim_seed,
            policy=policy, settings=asdict(self.settings),
            candidate_id=candidate_id, note=note,
        )
        self._entries.append(record)
        self._spent[phase] += 1
        return record

    def complete(self, episode_index: int, outcome: Mapping[str, Any]) -> EpisodeRecord:
        """Attach the evaluator result to an already-reserved episode."""
        if not 0 <= episode_index < len(self._entries):
            raise IndexError(f"No reserved episode {episode_index}.")
        record = self._entries[episode_index]
        if record.status != STATUS_RESERVED:
            raise ValueError(
                f"Episode {episode_index} is already {record.status!r}; "
                f"completing it twice would falsify the ledger."
            )
        normalised = normalise_outcome(outcome)
        record.outcome = normalised
        record.status = STATUS_ERROR if normalised["error"] is not None else STATUS_COMPLETED
        return record

    def spend(self, phase: str, scenario: Scenario, *, sim_seed: int,
              policy: str, outcome: Mapping[str, Any], candidate_id: str | None = None,
              note: str | None = None) -> EpisodeRecord:
        """Convenience: reserve and complete one episode atomically.

        Callers that must survive an interruption use :meth:`reserve` and
        :meth:`complete` separately, so the charge exists before the launch.
        """
        record = self.reserve(phase, scenario, sim_seed=sim_seed, policy=policy,
                              candidate_id=candidate_id, note=note)
        return self.complete(record.episode_index, outcome)

    # ----------------------------------------------------------- persistence
    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "seed": self.seed,
            "policy": self.policy,
            "settings": asdict(self.settings),
            "plan": self.plan.to_dict() if self.plan else None,
            "globally_unique": self.globally_unique,
            "entries": [e.to_dict() for e in self._entries],
            "spent": dict(self._spent),
            "seen": [list(key) for key in sorted(self._seen)],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EpisodeBudget:
        plan = SpendPlan.from_dict(value["plan"]) if value.get("plan") else None
        budget = cls(total=int(value["total"]), seed=int(value["seed"]),
                     policy=value["policy"], settings=SimSettings(**value["settings"]),
                     plan=plan, globally_unique=bool(value["globally_unique"]))
        entries = list(value.get("entries", []))
        for position, row in enumerate(entries):
            if int(row["episode_index"]) != position:
                raise ValueError(
                    "Checkpoint ledger is not a dense, ordered episode list; "
                    f"row {position} claims episode_index {row['episode_index']}."
                )
            budget._entries.append(EpisodeRecord(
                episode_index=int(row["episode_index"]), phase=row["phase"],
                scenario=dict(row["scenario"]), scenario_id=row["scenario_id"],
                sim_seed=int(row["sim_seed"]), policy=row["policy"],
                settings=dict(row["settings"]), candidate_id=row.get("candidate_id"),
                note=row.get("note"), status=row.get("status", STATUS_RESERVED),
                outcome=dict(row["outcome"]) if row.get("outcome") is not None else None,
            ))
        budget._spent = {phase: int(value["spent"][phase]) for phase in PHASES}
        budget._seen = {tuple(key) for key in value.get("seen", [])}
        for phase, count in budget._spent.items():
            observed = sum(1 for e in budget._entries if e.phase == phase)
            if observed != count:
                raise ValueError(
                    f"Checkpoint phase count for {phase!r} is {count} but the "
                    f"ledger holds {observed} rows."
                )
        if budget.spent != sum(budget._spent.values()):
            raise ValueError("Checkpoint spent total disagrees with the phase counts.")
        return budget

    # ---------------------------------------------------------------- report
    def ledger_rows(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    def accounting(self) -> dict[str, Any]:
        counts = self.phase_counts()
        completed = [e for e in self._entries if e.status != STATUS_RESERVED]
        errors = sum(1 for e in completed
                     if e.outcome is not None and e.outcome.get("error") is not None)
        interrupted = sum(1 for e in completed
                          if e.outcome is not None
                          and e.outcome.get("termination") == TERMINATION_INTERRUPTED)
        pending = len(self._entries) - len(completed)
        return {
            "budget_total": self.total,
            "episodes_spent": self.spent,
            "episodes_remaining": self.remaining,
            "phase_episodes": counts,
            "confirmation_episodes": sum(counts[p] for p in CONFIRMATION_PHASES),
            "search_episodes": counts[SEARCH],
            # Rule: budget, pool caps and launch limits are counted on launched
            # attempts; stability intervals are counted on valid observations.
            "launched_attempts": self.spent,
            "valid_observations": len(completed) - errors,
            "errors": errors,
            # Durable reservations sealed as interrupted: charged, never re-run.
            "interrupted_episodes": interrupted,
            "pending_unfinished_episodes": pending,
            "collisions": sum(1 for e in completed
                              if e.outcome is not None and e.outcome.get("ego_collision")),
            "plan": self.plan.to_dict() if self.plan else None,
            "note": ("Every launched simulator call, including confirmation, replay and "
                     "failed calls, is charged to the same counter."),
        }


@dataclass
class FakeOutcome:
    """Test helper only: a labelled mock result, never a simulator result."""
    ego_collision: bool = False
    termination: str = "goal"
    error: str | None = None
    note: str = field(default="UNIT TEST MOCK: not a simulator result")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
