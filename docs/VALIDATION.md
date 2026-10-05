# Validation record

## v0.3.0 validation run

Date: **2026-10-06** (local, UTC+08:00).

Recorded from the **public v0.3.0 snapshot root**, running the snapshot's own code. The
interpreter used was an existing project virtual environment; the private research
archive was **not** modified (it is read-only with respect to this record).

| item | value |
|---|---|
| command | `python -m unittest discover -s tests -v` |
| discovered | **274 tests** |
| passed | **274** |
| failures | **0** |
| errors | **0** |
| skipped | **0** |
| wall time | ~14 s |
| Python | **3.13.15** (CPython, MSC v.1944, 64-bit AMD64) |
| platform | **Windows 11** (`Windows-11-10.0.26200-SP0`) |
| HighwayEnv | **1.10.2** |
| Gymnasium | 1.2.2 |
| NumPy | 2.5.3 |
| Pygame | 2.6.1 |
| Pillow | 12.3.0 |
| pandas | 3.0.6 |
| SciPy | 1.18.1 |
| matplotlib | 3.11.2 |

**No test was skipped.** The four simulator integration tests
(`tests/test_integration.py`) executed against the real HighwayEnv: initial actor
conditions, same-seed trace replay, seed-controlled nuisance variation, and headless
GIF rendering. This is the difference from the earlier v0.1 preparation record below,
where a dependency-restricted environment could only skip them.

Coverage of the executed suite: parameter validation and deterministic sampling,
bounded perturbations and seed-stream separation, limited TTC/policy functions,
Wilson interval and stable-candidate boundaries, exact spatial de-duplication
(branch and bound, checked against brute force), strict budget/ledger accounting,
durable reserve-before-launch and checkpoint/resume, protocol/config/code
cross-validation, formal-run batching and integrity checking, and the frozen formal
analysis.

### Sandbox note (environment, not code)

Under a restricted-write sandbox, 8 of the 274 tests fail with
`PermissionError`/`[WinError 5]` because they create `tempfile` directories and one
writes a temporary GIF. These are **sandbox denials, not code or simulator failures**:
the same 274 tests pass with `0` failures and `0` errors when tempfile writes are
permitted. The values in the table above are from that complete run. No ACLs or
OS-wide security settings were modified to obtain them.

## Formal evaluation status

The v0.3 formal evaluation was executed under the frozen protocol
[`EVALUATION_PROTOCOL_V03.md`](EVALUATION_PROTOCOL_V03.md) (`cornercaselab_v03_eval_v1`)
with scoring protocol `v03_scoring_v1`:

* **20 paired formal seeds** × **3 methods** × **1000 allocated episode slots** per
  method per seed = **60,000 real HighwayEnv episodes**;
* **0 simulator errors**, **0 interrupted episodes**, and **0 incomplete Final Audit
  candidates** across all 60 seed–method runs;
* results are reported in
  [`../reports/formal_v03/RESULTS_INTERPRETATION.md`](../reports/formal_v03/RESULTS_INTERPRETATION.md).

No simulator episode was launched to produce this validation record.

## Scope of these claims

* A passing test suite and a completed evaluation mean **the recorded software and
  simulator executed as specified**. They are **not** evidence about real-road safety.
  No real vehicle or real road is involved anywhere in this project.
* The simulator and controllers are simplified. Observations here are properties of
  those models, not of driving in the physical world.
* Reproducibility is restricted to the recorded version, source fingerprint and
  environment, and to the documented rounded-trace definition. Cross-platform bitwise
  identity is **not** promised.
* This record is not edited to claim results that were not run.

## Superseded: v0.1 preparation record (Linux, dependency-restricted)

Kept for history. At that time the environment could not reach a package index, so the
simulator could not be installed.

* Date: 2026-10-03. Preparation environment: Linux, Python 3.13.5.
* `python -m unittest discover -s tests -v`: **39 discovered; 35 passed; 4 skipped**.
* The 35 executed tests covered parameter validation, deterministic sampling, bounded
  perturbations, seed separation, limited TTC/policy functions, fixed-n interval
  bounds, atomic storage, budget/error/interruption accounting using explicitly
  labelled mocks, and the dependency-free sampling CLI.
* `python -m compileall -q cornercaselab tests`: syntax check passed.
* `python -m cornercaselab sample ...`: generated three untested parameter records,
  packaged in `examples/untested_scenarios.json`.
* `python -m cornercaselab doctor`: executed and reported missing
  HighwayEnv/Gymnasium/Pygame dependencies.
* The 4 integration tests (initial actor conditions, same-seed replay, seed-controlled
  nuisance variation and headless GIF) were **skipped**. HighwayEnv integration,
  Windows execution and `start.ps1` were **unverified** at that point. Mock outputs
  lived only in transient unit-test directories and are **not** packaged as experiment
  results.

All of those gaps have since been closed by the v0.3.0 validation run above.
