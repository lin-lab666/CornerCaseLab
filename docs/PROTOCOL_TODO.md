# Protocol TODOs (explicitly NOT frozen)

This file records research-protocol decisions that are **deliberately still open**.
It exists so that no implementation detail is mistaken for a frozen methodological
choice. Adding an entry here is not a decision; only an explicit protocol freeze
(recorded with a date and the exact rule text) closes one.

Status of this file: engineering traceability for the v0.3 infrastructure. It is
not a research protocol and not a result.

**Update:** the v0.3 **formal evaluation protocol is now frozen** as
`cornercaselab_v03_eval_v1` in
[`EVALUATION_PROTOCOL_V03.md`](EVALUATION_PROTOCOL_V03.md) (config
`configs/eval_v03_formal.json`). That freeze closes sections 1, 2 and 3 below *for
that protocol*; the remaining open items are restated here. **Formal evaluation
has not been started**: no formal evaluation seed has ever been run.

## 1. Common-selection comparison — UNFROZEN (deliberately deferred)

`cornercaselab_v03_eval_v1` resolves the old selection question by design, not by
accident: nomination for the Final Audit is **part of the end-to-end method**, and
the three methods knowingly have different information sets. The frozen ranking
key is quoted in full, including its exact tie-break, in
[`EVALUATION_PROTOCOL_V03.md`](EVALUATION_PROTOCOL_V03.md) §6.

What remains **open and unimplemented** is the complementary experiment: auditing
every method under **one shared, method-independent ranking**. That may only be
run as a **separate ablation protocol** with its own protocol ID. It must not
retrospectively change `cornercaselab_v03_eval_v1`, and its results must not be
mixed with it.

Code marker: `candidates.AUDIT_SELECTION_SEMANTICS_FROZEN = False` and
`candidates.AUDIT_SELECTION_TODO` still flag that the *cross-method meaning* of the
ranking is a protocol choice rather than a settled default. The implementation is
unchanged and is asserted observable by
`tests/test_experiments_v03.py::ProtocolTodoTests::test_random_search_selection_is_not_silently_redefined`.

## 2. Complete evaluation protocol / primary metric — FROZEN for v1

Frozen in `cornercaselab_v03_eval_v1`:
[`EVALUATION_PROTOCOL_V03.md`](EVALUATION_PROTOCOL_V03.md). It fixes the budget
(1000 = 700 search + 300 audit), the audit reservation (10 candidates × 30
repeats), the method parameters, the 20 paired formal seeds and their derivation,
the primary metric **SNY** (`nonoverlapping_passed_count`), the paired contrasts
and the percentile paired bootstrap (10000 resamples, seed 314159265).

Still open: **nothing about this protocol may change** once formal evaluation
starts. Later protocols (a different budget, a common-selection ablation, a second
traffic setting) require their own protocol ID.

## 3. Stable-failure threshold — FROZEN for v1

For `v03_scoring_v1` / `cornercaselab_v03_eval_v1` the threshold is explicit: a
candidate is `passed` only when a planned n=30 run yields exactly 30 valid Final
Audit observations and the two-sided 95% Wilson lower bound (no continuity
correction, `z = 1.959963984540054`) is **strictly above 0.5** (so ≥ 21 of 30).
`incomplete` and `not_applicable` candidates carry `stable = null`, never `False`.
See [`SCORING_PROTOCOL_V03.md`](SCORING_PROTOCOL_V03.md).

Outside those two protocols, **no** threshold is frozen: the audit still only
reports `audit_stability` and `audit_wilson95`, and no implementation default
elsewhere may be read as a threshold.

## 4. Failure-region / diversity definition — NOT DEFINED

No failure-region definition exists, and SNY does **not** provide one. SNY is a
**parameter-space** count: the exact maximum number of pairwise non-overlapping
six-dimensional neighbourhoods among candidates that passed the Final Audit. It is
explicitly *not* a failure-region, root-cause, behavioural-distinctness or
connected-component count. Distinct configurations are still compared by
parameter hash only. Parameter diversity is not root-cause diversity.

## 5. Scoring layer — DEFINED (offline only)

`v03_scoring_v1` (`cornercaselab/scoring.py`, `scripts/score_v03.py`) is defined
and documented in [`SCORING_PROTOCOL_V03.md`](SCORING_PROTOCOL_V03.md). It reads
already-saved results, launches no simulator episode, modifies no input run, and
relabels nothing: the n=5 development smoke is scored `not_applicable` with a null
formal score. The analysis plan of the formal protocol is implemented in
`cornercaselab/analysis.py` and validated on synthetic data only
(`tests/test_analysis_v03.py`).

## Frozen for v0.3 (engineering invariants, not research claims)

These are implementation contracts, frozen so that a comparison is internally
consistent. They are not claims about driving risk:

* one budget counter; every launched episode is charged, including failures
  (`budget.EpisodeBudget`);
* an episode is reserved before its evaluator is launched and completed after,
  and a **resumable** run makes every reservation durable *before* launching
  (`EpisodeBudget.reserve` / `.complete`, `experiments.MethodRunner._checkpoint_reservation`);
* `search_pool + audit_pool == total` exactly (`budget.plan_spending`);
* `pool_fraction` caps internal confirmation **globally** at
  `floor(pool_fraction * search_pool)` launched attempts
  (`experiments.internal_confirm_cap`);
* the final-audit worklist is **exactly `selected x audit_repeats`** and is
  pre-computed before the first audit episode
  (`candidates.build_audit_worklist`); unused audit allocation is left unspent
  and reported, never backfilled (`candidates.AUDIT_UNSPENT_RULE`);
* calibration seed `20261003` is refused for `purpose="evaluation"`
  (`experiments.ensure_evaluation_seed`);
* the formal evaluation protocol `cornercaselab_v03_eval_v1` is frozen with an
  explicit start gate (`configs/eval_v03_formal.json`,
  `docs/EVALUATION_PROTOCOL_V03.md`). **No formal evaluation seed has been run**,
  and `formal_evaluation_started` stays `false` until the project owner says
  otherwise.

## Resume semantics — frozen (engineering)

Two interruption classes are distinguished, and only one of them is
result-equivalent:

* **Episode-boundary interruption** (the process died between episodes, after the
  previous episode was persisted). Resume continues the same deterministic seed
  stream, so the spliced result equals an uninterrupted run apart from wall-clock
  and other non-deterministic metadata.
* **In-flight interruption** (the process died after a durable reservation but
  before its outcome was persisted). The episode is sealed as
  `interrupted`/error with its budget slot still spent, its episode index, phase,
  scenario and seeds preserved, and its evaluator is **not** called again.
  A run that suffered this is deliberately **NOT** claimed to be equivalent to an
  uninterrupted run; it differs by that one row. This is the price of never
  launching an evaluator twice while charging it once (no free replay).

Code markers: `budget.interrupted_outcome`,
`budget.TERMINATION_INTERRUPTED`, `budget.accounting()["interrupted_episodes"]`,
`experiments.MethodRunner._seal_reserved`.

## Derived-seed collisions — guarded, not solved

`domain.derive_seed` returns only the first 4 bytes of a sha256, so the six
declared seed streams are separate by construction but are **not provably
disjoint**. A collision inside one stream would silently repeat a perturbation or
a nuisance draw, so v0.3 refuses it: `experiments.SeedStreamGuard` raises
`SeedCollisionError` before the duplicate episode is launched, and
`experiments.seed_stream_report` records the seeds each run consumed and any
duplicate it found. Changing the 32-bit seed width would change v0.2 replay
behaviour and is therefore **not** done; the mitigation is detection plus an
actionable error (change the master seed).

## Local-perturbation protocol — frozen description (documentation only)

Restating the implementation in `domain.perturb_scenario` so it cannot be
misread. No value or distribution is changed by this description:

* The six scenario dimensions are sampled **independently** (one `uniform` draw
  each, in `BOUNDS` order).
* Each dimension is
  `Uniform(max(domain_low, base_value - radius), min(domain_high, base_value + radius))`.
* `radius`: `front_gap = 2 m`, `ramp_x = 2 m`, and `1` for `ego_speed`,
  `front_speed`, `ramp_speed`, `ramp_target_speed` (m/s). These are the frozen
  v0.2 values and must not be changed.
* This is a **truncated / intersected local hyperrectangle**: the sampling
  interval is intersected with the scenario domain. It is **not**
  sample-then-clip and **not** rejection sampling, so there is no point mass on a
  boundary and no rejected draw. Near a domain bound the neighbourhood is
  one-sided and its effective half-width is smaller than the nominal radius.
* Internal confirmation and the Final Audit use **different** local streams
  (`internal-local` vs `audit-local`) and **different** nuisance streams
  (`internal-nuisance` vs `audit-nuisance`).
* The nuisance/simulator seed is handed to `highway_env` via `env.reset(seed=...)`,
  which seeds `env.np_random`; `MergeEnv._make_road` passes that generator to the
  `Road`, and `IDMVehicle.randomize_behavior()` draws `DELTA ~ Uniform(3.5, 4.5)`
  from `road.np_random`. Nuisance randomness is therefore a deterministic function
  of the derived `sim_seed`.
