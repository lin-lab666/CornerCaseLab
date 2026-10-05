# CornerCaseLab v0.3.0 Public Release Provenance

This document records what this public repository is, what it deliberately does not
contain, and how the published formal results relate to the source code that produced
them. It is a provenance record, not a results document; the results are in
[`../reports/formal_v03/RESULTS_INTERPRETATION.md`](../reports/formal_v03/RESULTS_INTERPRETATION.md).

---

## A. Repository structure

* This public repository is the **sanitized v0.3.0 release snapshot** of CornerCaseLab.
  It was created as a **new repository with no inherited Git history**: its first and
  only commit is `Release CornerCaseLab v0.3.0`.
* The original research and development Git history is retained in a **private research
  archive**.
* That archive is **not published**, because metadata in its earlier commits contains
  the project owner's **personal email address**. Publishing it would disclose a
  personal address that is not relevant to the research.
* The public snapshot instead uses the owner's **GitHub noreply identity**:

  ```
  吴昊霖 <267212630+lin-lab666@users.noreply.github.com>
  ```

* The private archive's history was **not rewritten**. Its commits remain exactly as
  they were; only the public snapshot is new. Commit hashes quoted in this document
  therefore refer to commits that exist **only** in the private archive.
* Intentionally not copied into this repository: the private `.git` directory, `runs/`,
  `.venv/`, `.dsh_temp/`, reflogs, and all Git history.

---

## B. Formal evaluation provenance

The formal evaluation was executed in the **private research archive**, not in this
public repository. Within that archive:

| stage | private commit |
|---|---|
| formal evaluation, seeds 1–5 | `c7b2d01` |
| formal evaluation, seeds 6–20 | `f580d02` |
| formal reporting / analysis | `25c86cd` |

The three commits differ **only** in `scripts/run_v03_formal.py` (the orchestration
driver). The protocol, simulator, scoring and analysis implementations are unchanged
across them.

**These hashes will not resolve in this public repository.** That is intentional and is
a consequence of the privacy design in section A: the public snapshot is a new
repository with a single commit, and it does not pretend to contain those commits or to
claim them as part of its own history.

---

## C. Formal source fingerprint

The formal run recorded the source fingerprint of the package it executed:

```
source_sha256 = 060a05e38f4605d63e317afb131a62e18be3ca910a7de216edfb08a02539c387
```

This digest is computed over every `cornercaselab/*.py` file (file name plus content,
with `\r\n` normalised to `\n`) by `cornercaselab/storage.py::source_digest`, and it was
recorded in each formal run manifest.

This public repository publishes the material needed to read and re-derive the reported
results:

* the frozen configuration (`configs/eval_v03_formal.json`);
* the evaluation protocol (`docs/EVALUATION_PROTOCOL_V03.md`);
* the scoring protocol (`docs/SCORING_PROTOCOL_V03.md`);
* the analysis implementation (`cornercaselab/analysis.py`, `cornercaselab/scoring.py`);
* the raw per-seed SNY table (`reports/formal_v03/raw_sny_by_seed.csv`);
* the figures and tables (`reports/formal_v03/`);
* the formal-data SHA256 freeze manifest (`reports/formal_v03/FORMAL_DATA_FREEZE.json`
  and `formal_data_file_sha256.csv`).

The **original episode-level `runs/` directory is not in this public Git repository.**
It contains the raw ledgers, checkpoints and per-candidate records of the formal run
(218 MB) and is excluded by `.gitignore` by design.

**The public snapshot does not share the formal run's `source_sha256`.** The public
snapshot's digest necessarily differs from `060a05e3…` because the released package
carries an updated version string and an updated CLI description (section D). No claim
is made that the two digests are equal. What is claimed is narrower and is stated
exactly in section D.

---

## D. Source delta relative to the formal-run source

The delta was verified against the private research archive. It is confined to
`cornercaselab/` and has exactly two parts.

**1. Runtime version string** — `cornercaselab/__init__.py`:

```diff
-__version__ = "0.1.0.dev0"
+__version__ = "0.3.0"
```

This is the *only* difference between the formal-run package source and the private
archive's HEAD tree under `cornercaselab/`:

```
archive HEAD (d6e48f9) vs formal-run package source (1adc575), paths under cornercaselab/:
  cornercaselab/__init__.py | 2 +-
  1 file changed, 1 insertion(+), 1 deletion(-)
```

No other file under `cornercaselab/` differs between the formal-run source and the
archive HEAD.

**2. CLI descriptive string** — `cornercaselab/cli.py`, introduced by this public
release. The `argparse` description only:

```diff
-    parser = argparse.ArgumentParser(description="CornerCaseLab v0.1 research starter")
+    parser = argparse.ArgumentParser(
+        description="CornerCaseLab: fixed-budget autonomous-driving scenario testing")
```

Nothing else in `cli.py` changed: no CLI behaviour, no subcommand, no argument, no
default and no exit code.

### What this means

Relative to the source that produced the formal evaluation, the public v0.3.0 package
differs by exactly:

1. the **runtime version string**, and
2. the **CLI descriptive/help string**.

Neither is algorithmic. The search policy, the tested driving policy, the final audit
procedure, the scoring implementation, the analysis implementation, the frozen
configuration and the evaluation protocol are **identical** to the formal-run source.
No parameter, threshold, seed, budget or metric differs.

The delta was verified empirically before this document was written; no other
difference was found anywhere under `cornercaselab/`. The public snapshot is therefore
**not** byte-identical to the formal-run source, and the two `source_sha256` values
differ for exactly the two reasons above.

---

## E. Scope and limitations

* The formal evaluation comprises **60,000 real HighwayEnv episodes**
  (20 paired seeds × 3 methods × 1000 allocated episode slots), with **0 simulator
  errors**, **0 interrupted episodes** and **0 incomplete Final Audit candidates**.
* The simulator and controllers are simplified. Nothing in this repository is evidence
  about **real-road safety**, and no real vehicle is involved.
* SNY counts audited non-overlapping parameter-space neighbourhoods. It is **not** a
  root-cause count, a crash probability, or a count of real connected failure regions.
* Reproducibility is restricted to the recorded version, source fingerprint and
  environment. Cross-platform bitwise identity is not promised.
* Provenance of authorship and licensing is recorded separately in
  [`PROVENANCE.md`](PROVENANCE.md) and [`ATTRIBUTION.md`](ATTRIBUTION.md).
