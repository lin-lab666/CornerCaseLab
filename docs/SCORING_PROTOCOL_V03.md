# v0.3 scoring protocol (`v03_scoring_v1`)

Status: **scoring layer defined** (this document). It is an offline scoring
convention for already-saved v0.3 results. It is **not** a complete evaluation
protocol, it is not a research result, and it does not settle the still-unfrozen
items listed at the end.

Implementation: `cornercaselab/scoring.py` (pure, offline).
Driver: `scripts/score_v03.py` (reads saved results; launches nothing).
Tests: `tests/test_scoring_v03.py`.

Scoring launches **zero simulator episodes**: it never imports
`cornercaselab.simulator`, never constructs an evaluator, and never modifies the
input run. `scripts/score_v03.py` records `simulator_module_loaded` in its output
so this is checkable rather than asserted.

`scoring_protocol_id = "v03_scoring_v1"`

---

## 1. Stability criterion (fixed n)

Only **Final Audit** data is used. Base collision, search and internal
confirmation episodes are never mixed in: the criterion reads a candidate's
Final Audit counters alone.

A candidate is scored only inside a **compliant run**:

| requirement | value |
|---|---|
| planned `audit_repeats` | exactly **30** |
| run state | finished (no reserved episode pending; checkpoint stage `complete`) |
| valid Final Audit observations | exactly **30** |

Interval: two-sided 95% **Wilson score interval without continuity correction**,
`z = 1.959963984540054`. This is the interval already implemented by
`cornercaselab.metrics.wilson_interval`, which is reused unchanged — the scoring
layer adds **no second implementation** and changes no previously recorded
statistic.

Decision rule: a candidate **passes** if and only if the Wilson **lower bound is
strictly greater than 0.5**. The calibrated boundary is:

| observations | passes | lower bound (approx.) |
|---|---|---|
| 20 / 30 | no | 0.4878 |
| 21 / 30 | yes | 0.5212 |

### Classification

| class | meaning |
|---|---|
| `passed` | exactly 30 valid observations and lower bound > 0.5 |
| `not_passed` | exactly 30 valid observations, criterion not met |
| `incomplete` | the run planned n=30 but fewer than 30 valid observations remain (e.g. audit errors) |
| `not_applicable` | the run was never an n=30 run (e.g. the n=5 development smoke), or it has not finished |
| `invalid` | the saved data does not conform to this protocol |

`stable` is `true` / `false` only for `passed` / `not_passed`. For `incomplete`
and `not_applicable` it is **null** — never `False`, because a shortfall or an
inapplicable run is not evidence that a candidate is unstable.

### Non-conforming data

The following are **reported as `invalid`**, never truncated, guessed or passed
silently:

* more launched attempts, or more valid observations, than the planned
  per-candidate audit length;
* contradictory counts (valid observations > launched attempts; collapses >
  valid observations; `audit_failures` length or sum disagreeing with
  `audit_episodes` / `audit_collapses`; negative counts);
* missing required fields (`candidate_id`, `scenario`, `audit_attempts`,
  `audit_episodes`, `audit_collapses`, `audit_failures`) or an unreadable
  scenario.

If any audited candidate is `invalid`, the method's **formal score is null** and
`data_conforms` is false. A run is never silently down-graded to the candidates
that happen to look fine.

Each scored candidate keeps its actual **launched attempts**, **valid
observations**, **errors**, **collision count**, the **Wilson interval** and the
**reason** text, in both `candidate_scores.csv` and `scoring_summary.json`.

---

## 2. Spatial dedup score

For each candidate `x`, the six-dimensional neighbourhood is

```
per dimension d:  [ max(BOUNDS.low[d], x[d] - radius[d]),
                    min(BOUNDS.high[d], x[d] + radius[d]) ]
```

`radius` is read directly from the existing `domain.PERTURBATION`
(`front_gap = 2 m`, `ramp_x = 2 m`, and `1` for the other four dimensions). No
bound, radius, sampling implementation or six-decimal convention is changed, and
nothing is re-sampled: the neighbourhood is a deterministic function of the saved
scenario.

Two neighbourhoods **overlap** when every dimension intersects; a **shared
boundary counts as overlap**. One strictly separated dimension is enough for
"not overlapping".

The score is the **exact maximum number of pairwise non-overlapping
neighbourhoods among the `passed` candidates**:

* solved exactly (branch and bound with memoisation on the overlap graph) —
  **never** a greedy approximation;
* **not** the number of connected components of the overlap graph;
* deterministic: when several maximum-size solutions exist, the
  lexicographically smallest sorted candidate-id list wins, so input ordering
  cannot change the answer;
* `incomplete` candidates are excluded from the passing set and reported
  separately (`incomplete_candidate_ids`), which is **not** a claim that they are
  unstable;
* candidates that were not selected for audit are not scored at all and are
  counted separately.

### Naming

This quantity is the **number of audited non-overlapping passed candidate
neighbourhoods**. It must not be called "distinct root causes", a "real failure
region count", or a bug count. Parameter-space non-overlap is not behavioural or
causal distinctness.

### Size limit

Exact solving is supported for at most **20 audited candidates**
(`scoring.SPATIAL_EXACT_LIMIT`). Beyond that the run is reported with
`spatial.status = "unsupported"` and `formal_score = null`; an approximate number
is never returned silently. Raising the limit is a deliberate, reviewable change,
not a configuration knob.

### Null vs zero

* A **compliant, finished n=30 run** with no passed candidates scores **0** — an
  empty set is a real (empty) answer.
* A run that is **not applicable**, **not finished**, **non-conforming**, or
  **beyond the exact-solve limit** gets **null**, with the reason recorded. An
  unscorable run is never written as a score of 0.

---

## 3. Outputs

`scripts/score_v03.py` writes a **new unique directory** under `runs/`
(`v03_scoring_<timestamp>`, refusing to reuse an existing directory):

* `candidate_scores.csv` — one row per audited candidate: method, candidate id,
  classification, stable, planned n, attempts, valid observations, errors,
  collapses, collapse rate, Wilson bounds, whether it is a spatial
  representative, the reason text, and the twelve neighbourhood bounds (so the
  non-overlap claim can be re-checked from the CSV alone).
* `scoring_summary.json` — `scoring_protocol_id`, the input directory, the
  **input run's own** purpose / seed / plan / git commit / git dirty /
  `source_sha256`, the **scoring pass's own** metadata kept under a separate
  `scoring_metadata` key, `simulator_module_loaded`, and per method: eligibility
  and its reason, `data_conforms`, the class counts, the spatial result with its
  representative ids, and the formal score with its reason.

The input run is opened read-only. No original `manifest.json`, ledger or
candidate file is rewritten, and the smoke run remains a development smoke run —
it is **not** relabelled as a formal research result.

---

## 4. Worked example: the n=5 development smoke

`runs/v03_smoke_dev_20261004_154547` has `audit_repeats = 5`, so the n=30
criterion cannot be applied. Scoring it yields, per method,
`stability_criterion_applicable = false`, `formal_score = null`, and
`not_applicable` for each audited candidate with `stable` left null. The real
observations (5 launches, 5 valid observations, 0 errors, and the actual
collision counts of 3/5 and 2/5) are still recorded in the CSV. The run is **not**
re-simulated to 30 repeats and nothing is back-filled.

---

## 5. Limitations

1. `audit_repeats = 30` here is a convention of this scoring layer, not a power
   calculation.
2. The Wilson interval assumes a fixed-n iid Bernoulli sample of *independent
   local draws*. Audit errors reduce the achieved n; the layer reports
   `incomplete` rather than padding or re-running.
3. Independence is within the declared local-perturbation and nuisance
   distribution, around a **search-selected centre**. It says nothing about real
   road risk.
4. Neighbourhood non-overlap is a property of the six declared parameter
   dimensions only. It is not behavioural, causal or root-cause separation.
5. Derived seeds are 32-bit (`derive_seed` keeps 4 bytes); the runtime guard
   detects collisions, it does not make the streams provably disjoint.
6. The exact spatial solve is capped at 20 audited candidates.
7. The audit **selection** rule is still a protocol TODO: `random_search` falls
   back to first-seen order while fixed/adaptive rank by internal stability, so
   *which* candidates get audited is not yet method-neutral.

---

## 6. Still NOT frozen (unchanged by this document)

* the complete evaluation protocol (primary metric, comparison procedure,
  independent multi-seed evaluation);
* the Final Audit **candidate selection** semantics across methods;
* any failure-region / diversity definition beyond the parameter-space
  neighbourhood count above.

See `docs/PROTOCOL_TODO.md`.
