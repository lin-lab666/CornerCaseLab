"""v0.3 offline scoring layer: fixed-n stability and neighbourhood dedup.

This module is **pure and offline**: it reads already-saved result dicts and
launches nothing. It does not touch the simulator, controller, budget, resume
logic, candidate selection, seeds, ``BOUNDS`` or ``PERTURBATION``, and it
changes no existing statistic.

Scoring protocol: ``v03_scoring_v1``. The authoritative description, including
its limitations, is ``docs/SCORING_PROTOCOL_V03.md``.

Two scores are produced
-----------------------
1. **Per-candidate stability** from the Final Audit only. A candidate is scored
   only if the run planned ``audit_repeats = 30`` and has exactly 30 *valid*
   Final Audit observations. The interval is the two-sided 95% Wilson score
   interval **without** continuity correction (``z = 1.959963984540054``), and a
   candidate passes only when its lower bound is strictly above 0.5. Reuses
   :func:`cornercaselab.metrics.wilson_interval` unchanged.
2. **Spatial dedup** over the passed candidates: the exact maximum number of
   pairwise non-overlapping six-dimensional neighbourhoods. This is *not* a
   count of distinct root causes, real failure regions or connected components.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .domain import BOUNDS, PERTURBATION, Scenario
from .metrics import wilson_interval

SCORING_PROTOCOL_ID = "v03_scoring_v1"

#: The protocol defines the stability criterion only for a planned per-candidate
#: audit length of exactly this many episodes.
REQUIRED_AUDIT_REPEATS = 30
#: Two-sided 95% normal quantile (matches ``metrics.wilson_interval``'s default).
WILSON_Z = 1.959963984540054
#: A candidate passes only when the Wilson LOWER bound is strictly above this.
PASS_LOWER_BOUND = 0.5
#: Exact maximum-non-overlapping-subset solving is supported for at most this
#: many audited candidates. Above it the run is reported as unsupported rather
#: than scored with an approximation.
SPATIAL_EXACT_LIMIT = 20

PASSED = "passed"
NOT_PASSED = "not_passed"
INCOMPLETE = "incomplete"
NOT_APPLICABLE = "not_applicable"
INVALID = "invalid"
CLASSIFICATIONS = (PASSED, NOT_PASSED, INCOMPLETE, NOT_APPLICABLE, INVALID)

#: Fields a saved candidate record must carry for the protocol to be applied.
REQUIRED_CANDIDATE_FIELDS = ("candidate_id", "scenario", "audit_attempts",
                             "audit_episodes", "audit_collapses", "audit_failures")


class ScoringDataError(ValueError):
    """Raised when saved results do not conform to the scoring protocol."""


# --------------------------------------------------------------------- Wilson
def wilson95(successes: int, n: int) -> tuple[float, float]:
    """Two-sided 95% Wilson interval WITHOUT continuity correction.

    Delegates to :func:`cornercaselab.metrics.wilson_interval`, which already
    implements exactly this interval (score interval, no continuity correction,
    ``z = 1.959963984540054``). The protocol therefore adds no competing
    implementation and cannot change any previously recorded statistic.
    """
    return wilson_interval(successes, n, z=WILSON_Z)


# ------------------------------------------------------------- neighbourhoods
def neighbourhood(scenario: Mapping[str, float] | Scenario
                  ) -> tuple[tuple[float, float], ...]:
    """The six-dimensional intersected neighbourhood of one scenario.

    Accepts either a :class:`~cornercaselab.domain.Scenario` or the plain
    six-key mapping that a saved candidate record stores.

    Per dimension ``[max(BOUNDS.low, x - radius), min(BOUNDS.high, x + radius)]``
    with ``radius = PERTURBATION[dimension]``, in ``BOUNDS`` order so the
    representation is deterministic. No bound, radius, sampling implementation or
    six-decimal convention is altered here.
    """
    if isinstance(scenario, Scenario):
        scenario = scenario.to_dict()
    bounds = []
    for name, (low, high) in BOUNDS.items():
        value = float(scenario[name])
        radius = PERTURBATION[name]
        bounds.append((max(low, value - radius), min(high, value + radius)))
    return tuple(bounds)


def neighbourhoods_overlap(first: Sequence[tuple[float, float]],
                           second: Sequence[tuple[float, float]]) -> bool:
    """True when the two neighbourhoods intersect in every dimension.

    A shared boundary counts as overlap (``<=`` on both sides), and a single
    strictly separated dimension is enough for "not overlapping".
    """
    return all(lo1 <= hi2 and lo2 <= hi1
               for (lo1, hi1), (lo2, hi2) in zip(first, second))


def _overlap_masks(items: Sequence[tuple[str, Any]]) -> list[int]:
    """Bit ``j`` of mask ``i`` is set when neighbourhoods i and j overlap."""
    count = len(items)
    masks = [0] * count
    for i in range(count):
        mask = 0
        for j in range(count):
            if neighbourhoods_overlap(items[i][1], items[j][1]):
                mask |= 1 << j
        masks[i] = mask
    return masks


def _max_independent_size(candidates: int, masks: Sequence[int],
                          cache: dict[int, int]) -> int:
    """Exact maximum independent-set size of the subgraph induced by a bitmask."""
    if candidates == 0:
        return 0
    cached = cache.get(candidates)
    if cached is not None:
        return cached
    vertex = (candidates & -candidates).bit_length() - 1
    without = _max_independent_size(candidates & ~(1 << vertex), masks, cache)
    with_it = 1 + _max_independent_size(candidates & ~masks[vertex], masks, cache)
    best = without if without >= with_it else with_it
    cache[candidates] = best
    return best


def maximum_nonoverlapping_subset(neighbourhoods: Mapping[str, Any]) -> list[str]:
    """Exact largest set of pairwise non-overlapping neighbourhoods.

    ``neighbourhoods`` maps candidate id to its neighbourhood. The size is solved
    exactly (branch and bound on the overlap graph, memoised) -- never a
    greedy or connected-component approximation -- and the answer is
    deterministic: among all maximum-size solutions, the lexicographically
    smallest sorted candidate-id list is returned, so input ordering cannot
    change the result.

    Raises :class:`ScoringDataError` when more than :data:`SPATIAL_EXACT_LIMIT`
    candidates are passed: this version does not solve that exactly, and an
    approximate answer is never returned silently.
    """
    ids = sorted(neighbourhoods)
    if len(ids) > SPATIAL_EXACT_LIMIT:
        raise ScoringDataError(
            f"exact maximum non-overlapping subset is supported for at most "
            f"{SPATIAL_EXACT_LIMIT} candidates; got {len(ids)}. Refusing to return an "
            f"approximation."
        )
    if not ids:
        return []
    items = [(candidate_id, neighbourhoods[candidate_id]) for candidate_id in ids]
    masks = _overlap_masks(items)
    cache: dict[int, int] = {}
    full = (1 << len(ids)) - 1
    target = _max_independent_size(full, masks, cache)
    chosen: list[str] = []
    available = full
    for index, candidate_id in enumerate(ids):
        if not (available >> index) & 1:
            continue
        remaining = target - len(chosen)
        if remaining <= 0:
            break
        # Greedy over ascending ids yields the lexicographically smallest
        # maximum solution: include the smallest feasible id whenever the
        # remainder can still reach the target size.
        if 1 + _max_independent_size(available & ~masks[index], masks, cache) >= remaining:
            chosen.append(candidate_id)
            available &= ~masks[index]
        else:
            available &= ~(1 << index)
    return chosen


# ------------------------------------------------------------------ stability
def run_eligibility(planned_repeats: int, run_complete: bool) -> tuple[bool, str]:
    """Whether the v03_scoring_v1 stability criterion applies to a run."""
    if planned_repeats != REQUIRED_AUDIT_REPEATS:
        return False, (
            f"planned audit_repeats={planned_repeats} (not {REQUIRED_AUDIT_REPEATS}); the "
            f"v03_scoring_v1 stability criterion is defined only for a planned n=30"
        )
    if not run_complete:
        return False, "run is not finished; a finished n=30 run is required"
    return True, f"compliant finished n={REQUIRED_AUDIT_REPEATS} run"


def _result(candidate_id: str | None, method: str | None, classification: str,
            stable: bool | None, planned: int, attempts: int | None,
            observations: int | None, collapses: int | None,
            interval: tuple[float, float] | None, reasons: Sequence[str]) -> dict[str, Any]:
    errors = None
    if attempts is not None and observations is not None:
        errors = attempts - observations
    rate = None
    if observations:
        rate = collapses / observations if collapses is not None else None
    return {
        "candidate_id": candidate_id,
        "method": method,
        "classification": classification,
        "stable": stable,
        "planned_audit_repeats": planned,
        "audit_attempts": attempts,
        "valid_observations": observations,
        "audit_errors": errors,
        "audit_collapses": collapses,
        "audit_collapse_rate": rate,
        "wilson_lower": interval[0] if interval else None,
        "wilson_upper": interval[1] if interval else None,
        "reasons": list(reasons),
    }


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer, got {type(value).__name__}")
    return value


def score_candidate(row: Mapping[str, Any], *, planned_repeats: int,
                    run_complete: bool = True,
                    method: str | None = None) -> dict[str, Any]:
    """Classify one audited candidate under ``v03_scoring_v1``.

    ``invalid`` means the saved data does not conform to the protocol (a missing
    field, a contradictory count, or more launches/observations than planned).
    Illegal data is reported, never truncated, guessed or passed silently.
    Only Final Audit fields are read; base collision, search and internal
    confirmation data are never mixed in.
    """
    candidate_id = row.get("candidate_id")
    candidate_id = str(candidate_id) if candidate_id is not None else None
    missing = [name for name in REQUIRED_CANDIDATE_FIELDS if name not in row]
    if missing:
        return _result(candidate_id, method, INVALID, None, planned_repeats, None, None,
                       None, None,
                       [f"missing required field(s): {', '.join(missing)}"])
    try:
        attempts = _as_int(row["audit_attempts"], "audit_attempts")
        observations = _as_int(row["audit_episodes"], "audit_episodes")
        collapses = _as_int(row["audit_collapses"], "audit_collapses")
        scenario = dict(row["scenario"])
        if set(scenario) != set(BOUNDS):
            raise ValueError(f"scenario keys must be exactly {list(BOUNDS)}")
        failures = row["audit_failures"]
        if not isinstance(failures, (list, tuple)):
            raise ValueError("audit_failures must be a list of booleans")
    except (TypeError, ValueError, KeyError) as exc:
        return _result(candidate_id, method, INVALID, None, planned_repeats, None, None,
                       None, None, [f"unreadable field: {exc}"])

    contradictions = []
    if attempts < 0 or observations < 0 or collapses < 0:
        contradictions.append("negative audit count")
    if observations > attempts:
        contradictions.append(
            f"valid observations ({observations}) exceed launched attempts ({attempts})")
    if collapses > observations:
        contradictions.append(
            f"collapses ({collapses}) exceed valid observations ({observations})")
    if len(failures) != observations:
        contradictions.append(
            f"audit_failures has {len(failures)} entries but audit_episodes is {observations}")
    if sum(1 for flag in failures if flag) != collapses:
        contradictions.append(
            f"audit_failures implies {sum(1 for flag in failures if flag)} collapses but "
            f"audit_collapses is {collapses}")
    if attempts > planned_repeats:
        contradictions.append(
            f"launched attempts ({attempts}) exceed the planned per-candidate audit "
            f"length ({planned_repeats})")
    if observations > planned_repeats:
        contradictions.append(
            f"valid observations ({observations}) exceed the planned per-candidate audit "
            f"length ({planned_repeats})")
    if contradictions:
        return _result(candidate_id, method, INVALID, None, planned_repeats, attempts,
                       observations, collapses, None,
                       ["data does not conform to the protocol: " + "; ".join(contradictions)])

    # Parse the neighbourhood here so a malformed scenario is reported, not guessed.
    try:
        neighbourhood(scenario)
    except (KeyError, TypeError, ValueError) as exc:
        return _result(candidate_id, method, INVALID, None, planned_repeats, attempts,
                       observations, collapses, None, [f"unusable scenario: {exc}"])

    eligible, reason = run_eligibility(planned_repeats, run_complete)
    if not eligible:
        return _result(candidate_id, method, NOT_APPLICABLE, None, planned_repeats,
                       attempts, observations, collapses, None, [reason])

    if observations != REQUIRED_AUDIT_REPEATS:
        return _result(candidate_id, method, INCOMPLETE, None, planned_repeats, attempts,
                       observations, collapses, None,
                       [f"planned n={REQUIRED_AUDIT_REPEATS} but only {observations} valid "
                        f"Final Audit observations ({attempts - observations} error(s)); "
                        f"stability is not assessed and must not be read as unstable"])

    low, high = wilson95(collapses, observations)
    stable = low > PASS_LOWER_BOUND
    classification = PASSED if stable else NOT_PASSED
    verdict = "passes" if stable else "does not pass"
    return _result(candidate_id, method, classification, stable, planned_repeats, attempts,
                   observations, collapses, (low, high),
                   [f"n={observations}, collapses={collapses}, Wilson 95% lower={low:.6f} "
                    f"upper={high:.6f}; {verdict} 'lower > {PASS_LOWER_BOUND}'"])


# ----------------------------------------------------------------- method/run
def score_method(*, method: str, candidates: Sequence[Mapping[str, Any]],
                 planned_repeats: int, run_complete: bool = True,
                 selected_for_audit: Sequence[str] | None = None) -> dict[str, Any]:
    """Score one method's saved candidates. Reads no files and runs no simulator.

    Only candidates that went through the Final Audit are scored: the explicit
    ``selected_for_audit`` list when given, otherwise the per-row
    ``selected_for_audit`` flag, otherwise every row. The spatial score is solved
    over **passed** candidates only -- ``incomplete`` candidates are excluded from
    the passing set and reported separately (which is not a claim that they are
    unstable).
    """
    rows = list(candidates)
    if selected_for_audit is not None:
        wanted = set(selected_for_audit)
        audited = [row for row in rows if row.get("candidate_id") in wanted]
    else:
        flagged = [row for row in rows if row.get("selected_for_audit") is True]
        audited = flagged if flagged else rows

    scores = [score_candidate(row, planned_repeats=planned_repeats,
                              run_complete=run_complete, method=method) for row in audited]
    by_class = {name: [s for s in scores if s["classification"] == name]
                for name in CLASSIFICATIONS}
    passed = by_class[PASSED]
    invalid = by_class[INVALID]
    eligible, reason = run_eligibility(planned_repeats, run_complete)
    data_conforms = not invalid

    neighbourhoods = {}
    for row in audited:
        candidate_id = row.get("candidate_id")
        if candidate_id is None:
            continue
        try:
            neighbourhoods[str(candidate_id)] = neighbourhood(dict(row["scenario"]))
        except (KeyError, TypeError, ValueError):
            continue
    passed_neighbourhoods = {s["candidate_id"]: neighbourhoods[s["candidate_id"]]
                             for s in passed if s["candidate_id"] in neighbourhoods}

    if not eligible:
        spatial = {"status": "not_scored", "nonoverlapping_passed_count": None,
                   "representative_candidate_ids": None, "reason": reason}
        formal_score = None
        formal_reason = reason
    elif not data_conforms:
        spatial = {"status": "not_scored", "nonoverlapping_passed_count": None,
                   "representative_candidate_ids": None,
                   "reason": "run data does not conform to the protocol"}
        formal_score = None
        formal_reason = (f"{len(invalid)} candidate record(s) do not conform to the "
                         f"protocol; refusing to report a formal score")
    elif len(audited) > SPATIAL_EXACT_LIMIT:
        spatial = {"status": "unsupported", "nonoverlapping_passed_count": None,
                   "representative_candidate_ids": None,
                   "reason": (f"{len(audited)} audited candidates exceed the exact-solve "
                              f"limit of {SPATIAL_EXACT_LIMIT}")}
        formal_score = None
        formal_reason = spatial["reason"]
    else:
        try:
            representatives = maximum_nonoverlapping_subset(passed_neighbourhoods)
            spatial = {"status": "ok",
                       "nonoverlapping_passed_count": len(representatives),
                       "representative_candidate_ids": representatives,
                       "reason": None}
            formal_score = len(representatives)
            formal_reason = None
        except ScoringDataError as exc:
            spatial = {"status": "unsupported", "nonoverlapping_passed_count": None,
                       "representative_candidate_ids": None, "reason": str(exc)}
            formal_score = None
            formal_reason = str(exc)

    return {
        "method": method,
        "scoring_protocol_id": SCORING_PROTOCOL_ID,
        "planned_audit_repeats": planned_repeats,
        "run_complete": run_complete,
        "stability_criterion_applicable": eligible,
        "eligibility_reason": reason,
        "data_conforms": data_conforms,
        "candidate_records_total": len(rows),
        "audited_candidate_count": len(audited),
        "not_selected_count": len(rows) - len(audited),
        "passed_candidate_count": len(passed),
        "not_passed_candidate_count": len(by_class[NOT_PASSED]),
        "incomplete_candidate_count": len(by_class[INCOMPLETE]),
        "incomplete_candidate_ids": sorted(s["candidate_id"] for s in by_class[INCOMPLETE]),
        "not_applicable_candidate_count": len(by_class[NOT_APPLICABLE]),
        "invalid_candidate_count": len(invalid),
        "invalid_candidate_ids": sorted(s["candidate_id"] for s in invalid
                                        if s["candidate_id"] is not None),
        "nonoverlapping_passed_count": spatial["nonoverlapping_passed_count"],
        "representative_candidate_ids": spatial["representative_candidate_ids"],
        "spatial": spatial,
        "formal_score": formal_score,
        "formal_score_reason": formal_reason,
        "metric_name": "audited non-overlapping passed candidate neighbourhoods",
        "metric_caveat": ("NOT a count of distinct root causes, real failure regions or "
                          "overlap-graph connected components."),
        "candidate_scores": scores,
    }


# ------------------------------------------------------------ format helpers
def planned_repeats_from_summary(summary: Mapping[str, Any]) -> int:
    """The planned per-candidate audit length recorded by a v0.3 run."""
    plan = summary.get("plan") or (summary.get("accounting") or {}).get("plan")
    if not isinstance(plan, Mapping) or "audit_repeats" not in plan:
        raise ScoringDataError(
            "saved run has no plan.audit_repeats; the planned n cannot be established")
    return int(plan["audit_repeats"])


def run_is_complete(summary: Mapping[str, Any],
                    checkpoint: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Whether a saved run finished, from its accounting and optional checkpoint."""
    pending = (summary.get("accounting") or {}).get("pending_unfinished_episodes")
    if pending is None:
        return False, "saved run has no accounting.pending_unfinished_episodes"
    if int(pending) != 0:
        return False, f"{int(pending)} reserved episode(s) never completed"
    if checkpoint is not None:
        stage = (checkpoint.get("run") or {}).get("stage")
        if stage is None:
            return False, "checkpoint has no run.stage; completion cannot be established"
        if stage != "complete":
            return False, f"checkpoint run.stage={stage!r}, not 'complete'"
    return True, "finished"


CSV_COLUMNS = (
    "method", "candidate_id", "classification", "stable",
    "planned_audit_repeats", "audit_attempts", "valid_observations", "audit_errors",
    "audit_collapses", "audit_collapse_rate", "wilson_lower", "wilson_upper",
    "nonoverlapping_representative", "reasons",
) + tuple(f"nb_{name}_{edge}" for name in BOUNDS for edge in ("low", "high"))


def candidate_csv_rows(method_score: Mapping[str, Any],
                       scenarios: Mapping[str, Mapping[str, float]]) -> list[dict[str, Any]]:
    """Flatten one method's candidate scores into CSV-ready rows.

    Neighbourhood bounds are included so the non-overlap claim can be re-checked
    offline from the CSV alone.
    """
    representatives = set(method_score.get("representative_candidate_ids") or [])
    rows = []
    for score in method_score["candidate_scores"]:
        row = {column: "" for column in CSV_COLUMNS}
        row["method"] = score["method"] or method_score["method"]
        row["candidate_id"] = score["candidate_id"]
        row["classification"] = score["classification"]
        row["stable"] = "" if score["stable"] is None else str(bool(score["stable"])).lower()
        row["planned_audit_repeats"] = score["planned_audit_repeats"]
        for field, column in (("audit_attempts", "audit_attempts"),
                              ("valid_observations", "valid_observations"),
                              ("audit_errors", "audit_errors"),
                              ("audit_collapses", "audit_collapses")):
            if score[field] is not None:
                row[column] = score[field]
        if score["audit_collapse_rate"] is not None:
            row["audit_collapse_rate"] = f"{score['audit_collapse_rate']:.6f}"
        if score["wilson_lower"] is not None:
            row["wilson_lower"] = f"{score['wilson_lower']:.6f}"
        if score["wilson_upper"] is not None:
            row["wilson_upper"] = f"{score['wilson_upper']:.6f}"
        row["nonoverlapping_representative"] = str(
            score["candidate_id"] in representatives).lower()
        row["reasons"] = " | ".join(score["reasons"])
        scenario = scenarios.get(score["candidate_id"])
        if scenario is not None:
            try:
                bounds = neighbourhood(scenario)
            except (KeyError, TypeError, ValueError):
                bounds = None
            if bounds is not None:
                for (name, _), (low, high) in zip(BOUNDS.items(), bounds):
                    row[f"nb_{name}_low"] = f"{low:.6f}"
                    row[f"nb_{name}_high"] = f"{high:.6f}"
        rows.append(row)
    return rows
