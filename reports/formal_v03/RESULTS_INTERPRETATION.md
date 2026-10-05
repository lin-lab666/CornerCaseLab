# CornerCaseLab v0.3 — Formal Results Interpretation

Frozen protocol: `cornercaselab_v03_eval_v1` · Scoring protocol: `v03_scoring_v1`
Formal run root: `runs/v03_formal_eval_20261004_205611` · 20 paired seeds × 3 methods × 1000 allocated episode slots = 60,000 episodes.

**Provenance.** Every confirmatory number below was re-derived from the 20 × 3 raw SNY
values by two independent code paths — the frozen analysis implementation and a
standalone re-implementation of the percentile paired bootstrap — and both reproduce
`analysis/formal_analysis.json` exactly. Seeds 1–5 ran under commit `c7b2d01`, seeds
6–20 under `f580d02`; the two commits differ only in `scripts/run_v03_formal.py`
(orchestration). No simulator episode was launched while producing this document
(0 additional episodes; asserted programmatically).

Evidence labels are kept strictly separate: **CONFIRMATORY** (frozen, pre-registered),
**DESCRIPTIVE** (summaries of the same frozen data, no inference), and
**EXPLORATORY** (mechanism hypotheses, not pre-registered, not tested).

---

## A. Confirmatory findings

These are the frozen, pre-registered analyses. The primary success criterion is
**effect size with its uncertainty**; **no p-value is reported and none is used as the
primary criterion.**

### A.1 Primary metric

SNY = *Stable Neighborhood Yield* = `nonoverlapping_passed_count`: the exact maximum
number of pairwise non-overlapping neighbourhoods among the candidates that passed the
frozen Final Audit criterion (30 valid audit observations, Wilson two-sided 95 % without
continuity correction, lower bound > 0.5, i.e. ≥ 21/30).

| method | n | mean SNY | median | SD (ddof 1) | IQR | min | max |
|---|---|---|---|---|---|---|---|
| `random_search` | 20 | **4.75** | 5.00 | 1.4464 | 2.00 | 2 | 7 |
| `fixed_explore_confirm` | 20 | **6.05** | 6.00 | 1.4318 | 2.00 | 4 | 9 |
| `adaptive_explore_confirm` | 20 | **7.10** | 7.00 | 1.3727 | 0.50 | 5 | 10 |

All 20 raw per-seed values are in `raw_sny_by_seed.csv` and `figure_1`; none is hidden
and none is smoothed.

### A.2 Primary paired contrast

**Adaptive − Random = +2.35**, frozen 95 % paired percentile bootstrap CI
**[1.95, 2.75]** (10,000 resamples, bootstrap seed 314159265).

The interval lies entirely above zero on the frozen SNY scale. In this frozen protocol,
this is the confirmatory result: under the same paired master seed and the same 1000-slot
budget per method, adaptive explore/confirm attained a higher SNY than uniformly random
search at every one of the 20 seeds (see §B.1).

### A.3 Secondary paired contrasts

| contrast | mean difference | frozen 95 % CI |
|---|---|---|
| Adaptive − Fixed | **+1.05** | **[0.55, 1.55]** |
| Fixed − Random | **+1.30** | **[0.75, 1.85]** |

Both intervals also lie entirely above zero. The Adaptive − Random effect (+2.35) is
approximately the sum of the two secondary effects (+1.05 and +1.30), which is the
expected arithmetic relation between paired differences and is not an independent
finding.

---

## B. Descriptive findings

Summaries of the same frozen data. **No inference, no hypothesis test.** These describe
what the methods did, not why the SNY numbers differ.

### B.1 Paired win / tie / loss (per-seed SNY)

| contrast | wins | ties | losses |
|---|---|---|---|
| Adaptive − Random | **20** | 0 | 0 |
| Adaptive − Fixed | 13 | 6 | 1 |
| Fixed − Random | 14 | 5 | 1 |

Consistent with §A.2 and §A.3, the disadvantage relative to adaptive is dominated by
ties for Fixed and by losses for Random.

### B.2 Explore breadth — unique search collision candidates per seed

| method | mean | min | max | total |
|---|---|---|---|---|
| Random | **87.20** | 74 | 102 | 1744 |
| Fixed | **71.15** | 60 | 84 | 1423 |
| Adaptive | **67.70** | 54 | 83 | 1354 |

Random search spends its entire search pool on novel exploration; the two
explore/confirm methods divert search episodes into internal confirmation and therefore
surface fewer distinct search collision candidates.

### B.3 Nomination quality — Final Audit pass proportion

200 audited candidates per method (20 seeds × 10). Candidate selection is part of each
method, so these are *nomination* pass rates, not intrinsic scenario properties.

| method | passed / audited | pass rate |
|---|---|---|
| Random | 95/200 | **47.5%** |
| Fixed | 121/200 | **60.5%** |
| Adaptive | 142/200 | **71.0%** |

Audit `incomplete` and `invalid` counts were 0 for all three methods, so every audited
candidate contributed a 30-observation stability decision.

### B.4 Confirmation expenditure and budget allocation (mean per seed)

| method | Search | Internal confirmation | Final Audit | episodes spent |
|---|---|---|---|---|
| Random | 700.00 | 0.00 | 300 | 1000 |
| Fixed | 557.85 | 142.15 | 300 | 1000 |
| Adaptive | **525.00** | **175.00** | 300 | 1000 |

Fixed spent 2843 internal-confirmation episodes in total (mean 142.15, range 119–167);
Adaptive spent 3500 (mean 175.00 in every one of the 20 seeds, i.e. exactly the frozen
global cap `floor(0.25 × 700) = 175`). All three methods spent their full audit
allocation of 300 and their full 1000-slot budget, with zero audit allocation left
unspent.

### B.5 Cost, reliability and spatial deduplication

| method | mean wall time / seed | errors | interrupted | audit unspent |
|---|---|---|---|---|
| Random | 347.4 s | 0 | 0 | 0 |
| Fixed | 311.3 s | 0 | 0 | 0 |
| Adaptive | 319.5 s | 0 | 0 | 0 |

The three methods have comparable wall-clock cost per seed (Fixed and Adaptive are
slightly cheaper than Random because early-terminating confirmed runs replace long
unconfirmed searches), and the whole formal run completed with **zero errors, zero
interrupted episodes and zero incomplete candidates** across all 60 seed–method runs.

`passed_candidate_count − SNY` was **0 for every one of the 60 seed–method combinations**
(aggregate and per-seed; total removed = 0). In the frozen neighbourhood scale, this
formal experiment **did not observe any overlap among passed candidates that SNY's
spatial deduplication had to remove.** The two quantities coincide numerically here;
this says nothing about whether those candidates share or differ in root cause.

---

## C. Exploratory mechanism interpretation

> **EXPLORATORY — not pre-registered, not confirmatory.** The following are hypotheses
> suggested by the descriptive patterns. They were not tested by this protocol, the
> design cannot identify them causally, and they must not be presented as established
> evidence.

**C.1 Confirmation expenditure and nomination quality co-move.** Pass rate
(47.5% → 60.5% → 71.0%) and internal-confirmation expenditure (0 → 142.15 → 175.00) rise
together across the three methods, and SNY follows. A plausible hypothesis is that
spending part of the search budget to confirm candidate stability before nominating
raises the fraction of nominated candidates that survive the independent Final Audit.
This is *confounded by construction*: the three methods differ in more than one respect
(nomination rule, information set, and budget split), so the comparison cannot attribute
the effect to confirmation alone.

**C.2 Adaptive may trade breadth for stability.** Adaptive surfaced the fewest unique
search collision candidates (67.70, 22 % fewer than Random) yet achieved the highest pass
rate and SNY. A plausible hypothesis is that reallocating search episodes to internal
confirmation buys *nomination precision* at the cost of *exploration breadth*.

**C.3 Breadth alone does not predict SNY.** Within each method, the per-seed Pearson
correlation between unique search collision candidates and SNY is weak and negative
(Adaptive −0.056, Random −0.087, Fixed −0.209; descriptive only, n = 20, no test). This
*weakens* a "more search collisions ⇒ higher SNY" explanation and is at least consistent
with nomination quality mattering more than raw breadth. It is a correlation over 20
seeds and is not evidence of a mechanism.

**C.4 The adaptive confirmation policy was cap-limited, not stability-limited.** Because
Adaptive hit the 175-episode cap in **all 20 seeds**, its confirmation behaviour was
determined by the frozen parameterisation rather than by its own stopping logic for at
least part of every run. The measured +2.35 is therefore a property of *this* frozen
adaptivity rule; whether a larger cap would help, saturate or hurt is untested.

**C.5 Spatial deduplication was inert here.** Because `passed − SNY = 0` everywhere, SNY
and "number of passed candidates" are numerically interchangeable in this dataset. Any
future claim that SNY captured genuine spatial separation would need a regime where
deduplication actually removes something.

---

## D. Limitations

1. **SNY is a proxy, not a ground truth.** It counts non-overlapping *stable* candidate
   neighbourhoods in the frozen 6-D perturbation space. It is **not** the number of
   distinct root causes, not a crash probability, and not a real failure-region count.
   A scenario hash is not a root-cause identifier.
2. **20 paired seeds is the frozen protocol size, not a power calculation.** The
   bootstrap intervals quantify uncertainty across these 20 search runs, not across all
   seeds that could have been drawn.
3. **Nomination is part of the method.** Random search has no internal stability
   estimate and used the frozen first-seen fallback, whereas Fixed and Adaptive spent
   real simulator budget to nominate. The three methods therefore operated with
   different information sets by design; the contrast measures the *end-to-end* methods,
   not the nomination rules in isolation.
4. **Confounded comparison.** Method identity bundles the nomination rule, the budget
   split and the information set. This protocol cannot separate them.
5. **Simulation scope only.** Everything here comes from the simplified simulator and
   controller used in this repository. It carries no implication for real vehicles,
   real roads or real crash rates.
6. **Frozen parameterisation.** The BOUNDS, PERTURBATION, boundary controller, the
   confirmation cap (175), `confirm_repeats = 2`, `pool_fraction = 0.25` and the audit
   design are all frozen; conclusions are conditional on them.
7. **Derived seeds are 32-bit.** Stream separation is constructive, not a proof of
   statistical independence.
8. **One audit sample per candidate.** Stability is decided from exactly 30 Final Audit
   observations with no backfill; a candidate that failed is never re-drawn.
9. **Descriptive quantities are not inference.** Win/tie/loss counts, pass proportions,
   correlations and wall times are summaries; no significance test was added for them.

---

## E. Claims that are NOT supported

The following claims are **not** supported by this formal evaluation and must not be
made from it:

- ❌ That the passed candidates represent **distinct root causes**, or that SNY counts
  distinct root causes / true failure regions. `passed − SNY = 0` throughout, so this
  experiment provides no evidence either way about spatial or causal distinctness.
- ❌ Any claim of **real-world crash reduction**, real accident probability, or improved
  vehicle safety. The evidence is simulation-internal.
- ❌ That a **causal mechanism is proven**. §C is exploratory; confirmation improving
  nomination quality is a hypothesis, and the design confounds method identity with the
  budget split.
- ❌ That **Adaptive explore/confirm is universally superior**. The result is conditional
  on the frozen protocol, budget, parameterisation and 6-D perturbation space, and
  Adaptive's confirmation was cap-limited in every seed.
- ❌ Any **statistical significance / p-value** claim. The frozen plan reports effect size
  with bootstrap uncertainty; no p-value is primary or reported.
- ❌ That SNY is a **validated proxy** for scenario-space coverage in general. Its
  behaviour is demonstrated only within this frozen protocol, where spatial
  deduplication never triggered.
- ❌ That Random, Fixed and Adaptive differ **only** in their confirmation strategy.
- ❌ Any **ablation-style attribution** (e.g. "confirmation alone causes the gain"). That
  requires a separately pre-registered common-selection ablation, which is deliberately
  not part of this frozen protocol and was not run.
