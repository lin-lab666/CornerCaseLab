"""v0.3 candidate state and the pre-registered final independent audit."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .budget import (
    FINAL_AUDIT,
    INTERNAL_CONFIRM,
    STATUS_COMPLETED,
    STATUS_ERROR,
    BudgetExhausted,
    EpisodeBudget,
    EpisodeRecord,
    error_outcome,
)
from .domain import Scenario, perturb_scenario
from .metrics import wilson_interval
from .simulator import SimSettings

Evaluator = Callable[[Scenario, int, str, SimSettings], dict[str, Any]]

# Frozen before any method runs. Changing it invalidates every recorded result.
AUDIT_SELECTION_RULE = "rank_by_internal_stability_desc_then_first_seen_asc_v1"
#: Frozen in advance: ``audit_repeats`` is a per-candidate MAXIMUM. When fewer
#: candidates were selected than the audit reservation assumed, the unused audit
#: allocation is simply left unspent and reported. It is never redirected onto an
#: already selected candidate (no backfill) and never turned back into search.
AUDIT_UNSPENT_RULE = "no_backfill_leave_unused_audit_reservation_unspent_v1"

#: PROTOCOL TODO -- NOT FROZEN.
#: The *cross-method* meaning of the audit selection rule is deliberately still
#: open. ``random_search`` spends no budget on internal confirmation, so every
#: one of its candidates has ``internal_stability is None`` and the frozen
#: ranking degenerates to first-seen order, while fixed/adaptive select their
#: most internally stable candidates. Whether that asymmetry is the intended
#: protocol, or whether every method must share one method-independent ranking,
#: is a research-protocol decision that must be frozen explicitly. Do not change
#: it silently, and do not treat the current behaviour as a settled definition.
AUDIT_SELECTION_SEMANTICS_FROZEN = False
AUDIT_SELECTION_TODO = (
    "UNFROZEN cross-method audit selection semantics: random search ranks by "
    "first-seen (no internal stability available), fixed/adaptive rank by "
    "internal stability. Decide and freeze this explicitly before any formal "
    "comparison; the current rule is a placeholder, not a protocol decision."
)

#: Raw dataclass fields of :class:`Candidate`, used for checkpoint round-trips.
CANDIDATE_FIELDS = (
    "candidate_id", "first_seen_episode", "scenario", "search_termination",
    "internal_attempts", "internal_episodes", "internal_collapses",
    "internal_failures", "audit_attempts", "audit_episodes", "audit_collapses",
    "audit_failures", "selected_for_audit", "audit_complete",
)


@dataclass
class Candidate:
    """Everything known about one discovered collision candidate.

    ``candidate_id`` is the scenario hash: it identifies a parameter
    configuration, NOT a distinct root cause or independent bug.

    ``*_attempts`` counts every *launched* episode (including evaluator errors)
    and is what budget/pool/launch caps are measured against. ``*_episodes``
    counts only *valid observations* and is what stability is measured on.
    """
    candidate_id: str
    first_seen_episode: int
    scenario: dict[str, float]
    search_termination: str | None = None
    internal_attempts: int = 0
    internal_episodes: int = 0
    internal_collapses: int = 0
    internal_failures: list[bool] = field(default_factory=list)
    audit_attempts: int = 0
    audit_episodes: int = 0
    audit_collapses: int = 0
    audit_failures: list[bool] = field(default_factory=list)
    selected_for_audit: bool = False
    audit_complete: bool = False

    @property
    def internal_stability(self) -> float | None:
        if self.internal_episodes == 0:
            return None
        return self.internal_collapses / self.internal_episodes

    @property
    def internal_wilson95(self) -> tuple[float, float] | None:
        if self.internal_episodes == 0:
            return None
        return wilson_interval(self.internal_collapses, self.internal_episodes)

    @property
    def audit_stability(self) -> float | None:
        if self.audit_episodes == 0:
            return None
        return self.audit_collapses / self.audit_episodes

    @property
    def audit_wilson95(self) -> tuple[float, float] | None:
        if self.audit_episodes == 0:
            return None
        return wilson_interval(self.audit_collapses, self.audit_episodes)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["internal_stability"] = self.internal_stability
        row["internal_wilson95"] = self.internal_wilson95
        row["audit_stability"] = self.audit_stability
        row["audit_wilson95"] = self.audit_wilson95
        return row

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Candidate:
        return cls(**{name: value[name] for name in CANDIDATE_FIELDS})


class CandidateState:
    """Append-only candidate bookkeeping shared by all three methods.

    The class deliberately does not choose which candidate to probe next; that
    is the method's policy. It only guarantees that every episode is attributed
    to at most one candidate and that the counters stay consistent.
    """

    def __init__(self) -> None:
        self._items: dict[str, Candidate] = {}
        self._assignment: dict[int, str] = {}
        self.misattributed_episodes = 0

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, candidate_id: str) -> bool:
        return candidate_id in self._items

    @property
    def items(self) -> list[Candidate]:
        return list(self._items.values())

    def ids(self) -> list[str]:
        return list(self._items)

    def get(self, candidate_id: str) -> Candidate:
        return self._items[candidate_id]

    def register(self, scenario: Scenario, *, episode_index: int,
                 termination: str | None = None) -> Candidate:
        """Add a discovered collision candidate, or return the known one."""
        cid = scenario.uid
        known = self._items.get(cid)
        if known is not None:
            return known
        candidate = Candidate(candidate_id=cid, first_seen_episode=episode_index,
                              scenario=scenario.to_dict(), search_termination=termination)
        self._items[cid] = candidate
        return candidate

    def record_attempt(self, candidate_id: str, *, phase: str) -> None:
        """Count one LAUNCHED episode for a candidate, including evaluator errors.

        Launch limits and pool shares are measured on this counter, so a failing
        evaluator can neither buy a free retry nor hide a launched episode.
        """
        if candidate_id not in self._items:
            raise KeyError(f"Unknown candidate {candidate_id!r}; register it first.")
        candidate = self._items[candidate_id]
        if phase == INTERNAL_CONFIRM:
            candidate.internal_attempts += 1
        elif phase == FINAL_AUDIT:
            candidate.audit_attempts += 1
        else:
            raise ValueError(f"Phase {phase!r} cannot be attributed to a candidate.")

    def record_assignment(self, candidate_id: str, *, episode_index: int,
                          phase: str, collapsed: bool) -> None:
        """Attribute one VALID observation to one candidate (stability evidence)."""
        if candidate_id not in self._items:
            raise KeyError(f"Unknown candidate {candidate_id!r}; register it first.")
        if episode_index in self._assignment:
            raise ValueError(
                f"Episode {episode_index} is already attributed to "
                f"{self._assignment[episode_index]!r}; double-counting is forbidden."
            )
        self._assignment[episode_index] = candidate_id
        candidate = self._items[candidate_id]
        if phase == INTERNAL_CONFIRM:
            candidate.internal_episodes += 1
            candidate.internal_failures.append(bool(collapsed))
            candidate.internal_collapses += int(bool(collapsed))
        elif phase == FINAL_AUDIT:
            candidate.audit_episodes += 1
            candidate.audit_failures.append(bool(collapsed))
            candidate.audit_collapses += int(bool(collapsed))
        else:
            raise ValueError(f"Phase {phase!r} cannot be attributed to a candidate.")

    def rebuild_launch_counts(self, budget: EpisodeBudget) -> None:
        """Recompute launched-attempt counters from the authoritative ledger.

        Called once when a run (or a resumed run) starts, so a checkpoint that
        was written mid-episode cannot leave the launch counters disagreeing
        with the episodes that actually exist.
        """
        for candidate in self._items.values():
            candidate.internal_attempts = 0
            candidate.audit_attempts = 0
        for record in budget.entries:
            candidate_id = record.candidate_id
            if candidate_id is None or candidate_id not in self._items:
                continue
            if record.phase == INTERNAL_CONFIRM:
                self._items[candidate_id].internal_attempts += 1
            elif record.phase == FINAL_AUDIT:
                self._items[candidate_id].audit_attempts += 1

    # ------------------------------------------------------------ final audit
    def select_for_audit(self, n: int) -> list[str]:
        """Apply the frozen selection rule; call this BEFORE any audit episode.

        PROTOCOL TODO: see :data:`AUDIT_SELECTION_TODO`. The ranking below is
        frozen for v0.3 but its cross-method meaning is not.
        """
        if n < 0:
            raise ValueError("n must be non-negative.")
        ranked = sorted(
            self._items.values(),
            key=lambda c: (-(c.internal_stability if c.internal_stability is not None else -1.0),
                           c.first_seen_episode, c.candidate_id),
        )
        chosen = [c.candidate_id for c in ranked[:n]]
        for cid in chosen:
            self._items[cid].selected_for_audit = True
        return chosen

    def audit_worklist(self, selected: Iterable[str], rounds: int) -> list[tuple[str, int]]:
        """Round-robin (candidate, round) audit plan; deterministic and complete."""
        chosen = list(selected)
        for cid in chosen:
            if cid not in self._items:
                raise KeyError(f"Unknown candidate {cid!r} in audit worklist.")
        return [(cid, r) for r in range(rounds) for cid in chosen]

    # ----------------------------------------------------------- persistence
    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [c.to_dict() for c in self._items.values()],
            "assignment": {str(k): v for k, v in self._assignment.items()},
            "misattributed_episodes": self.misattributed_episodes,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CandidateState:
        state = cls()
        for row in value["items"]:
            candidate = Candidate.from_dict(row)
            state._items[candidate.candidate_id] = candidate
        state._assignment = {int(k): v for k, v in value["assignment"].items()}
        state.misattributed_episodes = int(value.get("misattributed_episodes", 0))
        return state


def build_audit_worklist(selected: Iterable[str], rounds: int) -> list[tuple[str, int]]:
    """Pre-compute the ENTIRE audit plan before launching any audit episode.

    The plan is exactly ``selected x rounds``: one entry per selected candidate
    per round, so ``rounds`` (``audit_repeats``) is a hard per-candidate maximum
    and no candidate can ever receive a 31st audit when ``audit_repeats == 30``.

    There is deliberately no backfill. When fewer candidates were selected than
    the audit reservation assumed, the unused audit allocation is left unspent
    and reported (:data:`AUDIT_UNSPENT_RULE`); it is never redirected onto an
    already selected candidate, never converted back into search episodes, and
    never turned into fabricated simulations.

    Round indices are unique per candidate because they are simply
    ``range(rounds)``, so no round index (hence no seed) is reused. Because the
    whole plan is computed up front, an interrupted audit can be resumed from a
    cursor without recomputing -- or silently changing -- its own schedule, and
    no audit outcome can influence how many times a candidate is sampled.
    """
    if rounds < 1:
        raise ValueError("rounds must be >= 1.")
    chosen = list(selected)
    return [(cid, r) for r in range(rounds) for cid in chosen]


def audit_episode(state: CandidateState, budget: EpisodeBudget, candidate_id: str,
                  round_index: int, evaluator: Evaluator) -> EpisodeRecord:
    """Launch and charge exactly one fresh audit draw for one candidate.

    The episode is *reserved* (budget spent) BEFORE the evaluator runs, then
    *completed*. A raised evaluator still leaves a charged episode with the
    shared error outcome. The draw is a fresh local perturbation of the
    candidate's own scenario with a fresh nuisance seed, both derived from the
    round index; round indices are unique across the whole pre-computed plan.
    """
    if not budget.can_spend(FINAL_AUDIT):
        raise BudgetExhausted(
            "Audit episode requested with no remaining audit-pool capacity."
        )
    candidate = state.get(candidate_id)
    scenario = perturb_scenario(Scenario.from_dict(candidate.scenario),
                                budget.seed_for("audit-local", candidate_id, round_index))
    sim_seed = budget.seed_for("audit-nuisance", candidate_id, round_index)
    record = budget.reserve(FINAL_AUDIT, scenario, sim_seed=sim_seed,
                            policy=budget.policy, candidate_id=candidate_id,
                            note=f"final_audit_round_{round_index}")
    state.record_attempt(candidate_id, phase=FINAL_AUDIT)
    try:
        outcome: dict[str, Any] = dict(evaluator(
            scenario, sim_seed, budget.policy, budget.settings))
    except Exception as exc:  # counted, never hidden in a safe denominator
        outcome = error_outcome(exc)
    budget.complete(record.episode_index, outcome)
    if outcome.get("error") is None:
        state.record_assignment(candidate_id, episode_index=record.episode_index,
                                phase=FINAL_AUDIT,
                                collapsed=bool(outcome.get("ego_collision")))
    return record


def audit_report(state: CandidateState, budget: EpisodeBudget, *,
                 selected: Sequence[str], rounds: int,
                 work: Sequence[tuple[str, int]]) -> dict[str, Any]:
    """The one definition of the Final Audit report, read back from the ledger.

    ``audit_repeats`` is a per-candidate MAXIMUM (``audit_max_attempts_per_candidate``
    can never exceed it). Valid observations may be fewer than that maximum: an
    evaluator error is charged, is never re-run, and leaves no observation, so
    the candidate is reported in ``audit_incomplete_candidates``. Whatever audit
    allocation could not be used because fewer candidates were selected is
    reported as ``audit_reserved_unspent`` and is not reused anywhere.
    """
    plan = budget.plan
    audit_reserved = plan.audit_pool if plan is not None else budget.total
    audit_candidates = plan.audit_candidates if plan is not None else len(selected)
    rows = [e for e in budget.entries if e.phase == FINAL_AUDIT]
    attempts = {cid: state.get(cid).audit_attempts for cid in selected}
    return {
        "audit_selection_rule": AUDIT_SELECTION_RULE,
        "audit_unspent_rule": AUDIT_UNSPENT_RULE,
        "audit_candidates": audit_candidates,
        "audit_repeats": rounds,
        "selected_candidate_count": len(selected),
        "audit_reserved": audit_reserved,
        "audit_launched": len(rows),
        "audit_completed": sum(1 for e in rows if e.status == STATUS_COMPLETED),
        "audit_errors": sum(1 for e in rows if e.status == STATUS_ERROR),
        "audit_reserved_unspent": max(0, audit_reserved - len(rows)),
        "audit_max_attempts_per_candidate": max(attempts.values()) if attempts else 0,
        "audit_incomplete_candidates": sorted(
            cid for cid in selected if state.get(cid).audit_episodes < rounds),
        "audit_worklist": [[cid, int(r)] for cid, r in work],
        "audit_selection_semantics_frozen": AUDIT_SELECTION_SEMANTICS_FROZEN,
        "audit_selection_todo": AUDIT_SELECTION_TODO,
    }


def run_final_audit(state: CandidateState, budget: EpisodeBudget, *, selected: list[str],
                    rounds: int, evaluator: Evaluator,
                    work: list[tuple[str, int]] | None = None) -> dict[str, Any]:
    """Spend the reserved audit allocation on FRESH local draws, at most ``rounds``
    times per selected candidate.

    Every audit episode is charged through the same budget counter as search and
    internal confirmation. A failed audit call still consumes budget and is
    excluded from the interval, never counted as safe, and never replaced.

    ``rounds`` (``audit_repeats``) is a hard per-candidate maximum: the worklist
    is exactly ``selected x rounds``. When fewer candidates were selected than
    the reservation assumed, the unused allocation is left unspent and reported
    (``audit_reserved_unspent``) rather than redistributed onto the candidates
    that do exist. No audit outcome influences how many times anything is
    sampled, because the whole schedule is fixed before the first audit launch.
    """
    selected = list(selected)
    if work is None:
        work = build_audit_worklist(selected, rounds)
    work = [(cid, int(r)) for cid, r in work]
    for candidate_id, round_index in work:
        if budget.remaining <= 0 or budget.phase_remaining(FINAL_AUDIT) <= 0:
            break
        if not budget.can_spend(FINAL_AUDIT):
            break
        audit_episode(state, budget, candidate_id, round_index, evaluator)
    for cid in selected:
        candidate = state.get(cid)
        candidate.audit_complete = candidate.audit_episodes >= rounds
    return audit_report(state, budget, selected=selected, rounds=rounds, work=work)
