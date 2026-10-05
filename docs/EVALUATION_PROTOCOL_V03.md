# v0.3 Formal Evaluation Protocol

## POST-EXECUTION STATUS NOTE

**Added after the formal evaluation. This note is not part of the frozen rules below
and does not modify them.**

The protocol text that follows was frozen **before** the formal evaluation began.
Every statement in that text describing the formal evaluation as *not started* — in
particular the block reading "Formal evaluation has NOT been started. No formal
evaluation seed has ever been run." — is a **freeze-time status record**. It was
accurate when written and is retained verbatim; it is not a description of the
current state of the project.

Subsequent to that freeze, the formal evaluation was carried out strictly under this
protocol:

* **20 paired formal seeds**, each run by **all 3 methods**;
* **1000 allocated episode slots per method per seed**;
* **60,000 real HighwayEnv episodes** in total (20 × 3 × 1000).

The budget, seed set, stability threshold, metric definition and reporting rules were
**not** altered in the light of results at any point during execution.

The formal results are reported in
`../reports/formal_v03/RESULTS_INTERPRETATION.md`.

Because the sections below are a frozen document, the "not started" wording is left
in place exactly as frozen, rather than edited to match the outcome.

---

**Protocol ID: `cornercaselab_v03_eval_v1`**

Status: **FROZEN**. Frozen against code commit
`ce3a9b0767ada2d91d2cd99bafd3fe2114892ad3`, 2026-10-04.

Machine-readable config: [`configs/eval_v03_formal.json`](../configs/eval_v03_formal.json).
Validator and code cross-check: `cornercaselab/protocol.py`.
Protocol tests: `tests/test_eval_protocol_v03.py`.
Scoring layer: [`SCORING_PROTOCOL_V03.md`](SCORING_PROTOCOL_V03.md) (`v03_scoring_v1`).
Analysis plan: `cornercaselab/analysis.py` (`tests/test_analysis_v03.py`).

> **Formal evaluation has NOT been started.** No formal evaluation seed has ever
> been run. This document freezes *how* the formal evaluation will be run and
> reported. Starting it requires an explicit instruction from the project owner.
>
> The calibration/development seed **20261003** may only be used with
> `purpose="development"`. `experiments.ensure_evaluation_seed` refuses it for
> `purpose="evaluation"`, and `cornercaselab/protocol.py` enforces that refusal.

---

## 1. Scope and unit of analysis

Three search/allocation methods are compared **end to end** under one identical
budget, one identical controller and one identical final audit:

| method id | description |
|---|---|
| `random_search` | pure exploration; spends no budget on internal confirmation |
| `fixed_explore_confirm` | after each discovered candidate, spends a fixed number of internal confirmations on it |
| `adaptive_explore_confirm` | allocates each next episode between exploration and confirmation by its own observed internal stability, under a global confirmation cap |

Policy (frozen): **`boundary`**.
Purpose (frozen): **`evaluation`**.

The unit of analysis is one **paired master seed**: for seed *i*, all three
methods are run with exactly that seed and their SNY values are differenced
pairwise. Seeds are paired, not pooled.

---

## 2. Budget

Per method, per seed:

| item | value |
|---|---|
| total episode budget | **1000** allocated episode slots |
| search pool | **700** |
| audit pool | **300** |
| `search_fraction` | 0.7 |

Closure is mandatory: `search_pool + audit_pool == total_episode_budget`
(700 + 300 = 1000). `budget.plan_spending` rejects any plan that does not close,
so no episode slot is left unconsumable and none is double-counted.

* Every launched simulator episode is charged to the same counter, **including**
  failures and audit episodes.
* Search and internal confirmation **share** the search pool; neither may borrow
  from the audit pool.
* The audit pool is reserved before the search phase and is never converted back
  into search episodes.

### Final Audit reservation

| item | value |
|---|---|
| `audit_candidates` | **10** |
| `audit_repeats` | **30** |
| `audit_pool` | 10 × 30 = **300** |
| backfill | **none** |

If fewer than 10 candidates are selected for audit, the unused reservation is
reported as `audit_reserved_unspent` and is **left unspent**. It is never
backfilled onto an already selected candidate (no candidate may receive a 31st
audit), never returned to search, and never replaced by fabricated episodes.

### Budget basis for the primary score

The primary score's budget basis is always **1000 allocated episode slots**.
When a candidate shortfall leaves `audit_reserved_unspent > 0`, the score is
**not** renormalised onto a smaller number of actual launches. All three methods
receive the same allocation.

---

## 3. Method parameters

One `MethodConfig` is used for all three methods; parameters a method does not
use are inert for that method.

| parameter | value | applies to |
|---|---|---|
| `confirm_repeats` | **2** | fixed |
| `pool_fraction` | **0.25** | adaptive |
| `min_internal_per_candidate` | **1** | adaptive |
| `max_internal_per_candidate` | **5** | adaptive |
| global internal confirmation cap | `floor(0.25 × 700)` = **175** | adaptive |

* **Random search** performs **0** internal confirmations. It spends the whole
  search pool on fresh scenarios.
* **Fixed explore/confirm** applies `confirm_repeats = 2` to each newly
  discovered candidate, drawing from the shared search pool only.
* **Adaptive explore/confirm** keeps the implemented schedule: pull every
  candidate up to `min_internal_per_candidate` first, then confirm the candidate
  with the highest internal Wilson lower bound, capped by
  `max_internal_per_candidate` per candidate and by the global **175** launched
  internal confirmations in total. Once the global allowance is exhausted, only
  exploration continues. If the `min_internal_per_candidate` target cannot be
  reached, confirmation stops explicitly and the unmet candidates are reported in
  `search_stop.min_internal_unmet` — it never loops or mis-ranks silently.

---

## 4. Stable candidate criterion

A candidate's stability comes from **Final Audit data only**. Base collisions,
search episodes and internal confirmation episodes are never mixed into it.

| requirement | value |
|---|---|
| planned Final Audit n | **30** |
| valid Final Audit observations | exactly **30** |
| interval | two-sided 95% **Wilson score interval, no continuity correction** |
| `z` | **1.959963984540054** |
| decision | pass iff the Wilson **lower bound > 0.5** |
| resulting boundary | **≥ 21 of 30** collapses pass; 20 of 30 does not |

The interval is the one already implemented by `cornercaselab.metrics.wilson_interval`,
reused unchanged; no second implementation is introduced and no previously
recorded statistic changes.

| class | condition | `stable` |
|---|---|---|
| `passed` | 30 valid observations, lower bound > 0.5 | `true` |
| `not_passed` | 30 valid observations, criterion not met | `false` |
| `incomplete` | planned n = 30 but fewer than 30 valid observations | `null` |
| `not_applicable` | planned n ≠ 30, or the run is unfinished | `null` |
| `invalid` | saved data does not conform to the protocol | `null` |

`incomplete` and `not_applicable` are **never** `false`: a shortfall or an
inapplicable run is not evidence that a candidate is unstable. `invalid` data
(repeated/contradictory counts, more launches than planned, missing fields) is
reported explicitly and never truncated or guessed; if any audited candidate is
`invalid`, that method's formal score is `null`.

---

## 5. Primary metric — Stable Neighborhood Yield (SNY)

**Field:** `nonoverlapping_passed_count`
**Name:** Stable Neighborhood Yield (SNY)

Definition: among the candidates that **pass** the Final Audit stability
criterion, the **exact maximum number of pairwise non-overlapping
six-dimensional local neighbourhoods**.

For each candidate `x`, dimension `d`:

```
[max(BOUNDS.low[d], x[d] - radius[d]), min(BOUNDS.high[d], x[d] + radius[d])]
```

`radius` is read directly from the existing `domain.PERTURBATION`
(`front_gap = 2 m`, `ramp_x = 2 m`, `1` for the other four dimensions). No bound,
radius, sampling implementation or six-decimal convention is altered.

Two neighbourhoods overlap iff they intersect in **every** dimension; a shared
boundary counts as overlap, and one strictly separated dimension is enough for
"not overlapping".

* Solved **exactly** (branch and bound on the overlap graph), never by a greedy
  approximation and never as the number of connected components of the overlap
  graph.
* Deterministic: among maximum-size solutions the lexicographically smallest
  sorted candidate-id list is retained, so input order cannot change the answer.
* `incomplete` candidates are **excluded** from the passing set (and reported
  separately). Excluding them is not a claim that they are unstable.
* Exact solving is supported for at most **20** audited candidates. With
  `audit_candidates = 10` the frozen protocol is well inside that limit. Beyond
  the limit the run is reported as unsupported with a null score; an approximate
  number is never returned silently.
* A compliant, finished run with no passing candidate scores **0** (a real empty
  answer). A run that is not applicable, unfinished, non-conforming or beyond the
  solver limit scores **null**, with the reason recorded. An unscorable run is
  never written as 0.

SNY must **not** be interpreted as **distinct root causes**, as a real crash
**probability**, or as the number of real connected **failure region**s. It is a
parameter-space count of audited non-overlapping neighbourhoods.

---

## 6. Final Audit selection semantics

Nomination for the Final Audit is treated as **part of the end-to-end method**.
The three methods knowingly have different information sets, and that difference
is part of what is being compared.

The implementation is **frozen and unchanged**
(`candidates.CandidateState.select_for_audit`,
`rule_id = rank_by_internal_stability_desc_then_first_seen_asc_v1`). Its exact
source ordering key is:

```python
key=lambda c: (-(c.internal_stability if c.internal_stability is not None else -1.0),
               c.first_seen_episode,
               c.candidate_id)
```

Read precisely:

1. **descending `internal_stability`**; a candidate with **no** internal
   observation has `internal_stability is None`, which becomes `-1.0` before
   negation, i.e. `+1.0` after it, so such a candidate ranks **after** every
   candidate with at least one observation — including a candidate with
   `internal_stability == 0.0`;
2. ties broken by **ascending `first_seen_episode`**;
3. remaining ties broken by **ascending `candidate_id`**.

* **`random_search`** spends no budget on internal confirmation, so all of its
  candidates have `None` stability and the ranking degenerates to the frozen
  **first-seen fallback** (ascending `first_seen_episode`, then ascending
  `candidate_id`). This fallback is retained exactly as implemented.
* **`fixed_explore_confirm`** and **`adaptive_explore_confirm`** may use the
  internal stability they actually obtained through real simulator budget, with
  the same deterministic tie-break.

The first `audit_candidates = 10` candidates of that ranking are audited. The
selection is computed **before** any audit episode, and no audit outcome
influences the schedule.

**Ablation rule.** A future *common-selection* comparison (one shared ranking for
all methods) may only be run as a **separate ablation protocol** with its own
protocol ID. It must not retrospectively change this protocol, and its results
must not be mixed with this one.

---

## 7. Formal master seeds

**Exactly 20 paired seeds**, used in this fixed order by all three methods.

Derivation (must be recorded verbatim):

```
seed_i = first four bytes, big-endian, SHA256("CornerCaseLab-v0.3-formal-{i}")
i = 0..19
```

```
 0  3885243241
 1  1704487720
 2   592379183
 3  1151400128
 4  3272561144
 5  3391624141
 6  3736130552
 7  3065082847
 8  2983125598
 9   142216931
10  1007851318
11  3069633350
12  2272084942
13  3735670268
14  4175222997
15   614416336
16  4287176670
17  2634949118
18  4292498487
19   416355318
```

Verified properties: exactly 20 values; all unique; every value in
`[0, 2**32)`; the calibration seed **20261003 is not among them**; the stored
order equals the derived order. `cornercaselab/protocol.py` re-derives the list
and refuses any config whose values or order differ.

---

## 8. Reporting plan

Per method, over the 20 seeds:

* all **raw** SNY values are kept and reported (no seed is dropped);
* **mean**, **median**, **standard deviation** (sample, `ddof = 1`) and **IQR**.

### Paired contrasts

Pairwise, per seed, on SNY:

| role | contrast |
|---|---|
| **primary** | `adaptive_explore_confirm - random_search` |
| secondary | `adaptive_explore_confirm - fixed_explore_confirm` |
| secondary | `fixed_explore_confirm - random_search` |

Interval: **percentile paired bootstrap** over the per-seed paired differences,
**10000** resamples, **bootstrap seed 314159265**, **95%** confidence. Bounds are
taken by linear interpolation between order statistics of the resampled means;
resampling is row-major (per resample, `n` independent `randrange(n)` draws) from
one `random.Random(314159265)` stream, so the interval is reproducible.

The primary success criterion is **effect size with uncertainty**. **p-values are
not the primary criterion** and no significance threshold is part of this
protocol.

### Descriptive / secondary quantities

`passed_candidate_count`, unique search collision candidates, raw search
collision samples, internal-confirm launches, audit incomplete count,
`audit_reserved_unspent`, actual simulator launches, errors, wall time.

### Reproduction metadata

Every saved run must record the environment it actually ran under: git commit,
git dirty state, source fingerprint (`source_sha256`), Python and dependency
versions, platform, the master seed, the full config and the full ledger. The
metadata of an **analysis/scoring pass** is recorded separately from the metadata
of the run it analysed. Historical manifests are never rewritten.

---

## 9. What this protocol does not claim

* SNY is not a root-cause count, not a crash probability and not a real
  failure-region count.
* The Wilson interval assumes a fixed-n iid Bernoulli sample of independent local
  draws around a **search-selected centre**; audit errors reduce the achieved n
  and are reported as `incomplete`, never padded or re-run.
* Dependency and nuisance randomness are a deterministic function of derived
  seeds, but those seeds are 32-bit: independence is by construction, not proven.
* The simulator and controller are simplified; nothing here is evidence about
  real-road safety, and no real vehicle is involved.
* `audit_repeats = 30` is a protocol convention, not a power calculation.

---

## 10. Start gate

Formal evaluation may begin **only** on an explicit instruction from the project
owner. Until then the protocol stays frozen and unused, `formal_evaluation_started`
stays `false`, and the only permitted runs are `purpose="development"` runs on the
calibration seed 20261003.

Readiness is reported with exactly one of two verdicts:

* **READY FOR FORMAL EVALUATION**
* **NOT READY FOR FORMAL EVALUATION**
