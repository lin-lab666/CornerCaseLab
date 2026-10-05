"""v0.3 FORMAL evaluation batch driver -- orchestration only.

Reads the frozen protocol ``cornercaselab_v03_eval_v1`` and runs the 20 paired
formal seeds in their frozen order, three methods per seed, calling the existing
``compare_methods`` and the existing scoring driver. **No research algorithm is
implemented here**: the budget arithmetic, the scheduler, the Final Audit, the
stability criterion and SNY all come from the frozen config, the engine and the
scoring layer.

Guarantees and non-negotiables
------------------------------
* Seeds are consumed strictly in the frozen order, in four batches of five.
* Every seed uses ``purpose="evaluation"``; the calibration seed is refused by the
  engine (`ensure_evaluation_seed`). The driver refuses to start if the frozen
  config's seed list has changed at all (byte-level digest check).
* Each seed's outputs are persisted before the next seed starts. A completed seed
  (a ``seed_manifest.json``) is **never** re-run; a partially written seed
  directory is a blocker, not something to redo.
* ``formal_manifest.json`` and the frozen-config snapshot are written once, before
  the first seed, and are never rewritten. Progress is appended to an
  append-only ``formal_run_progress.jsonl``. No historical manifest is edited.
* During batches 1-3 only integrity is reported: no SNY value, no per-method
  comparison and no summary statistic is printed or written until all 20 seeds
  are complete.

Usage:
    .venv\\Scripts\\python.exe scripts\\run_v03_formal.py
    .venv\\Scripts\\python.exe scripts\\run_v03_formal.py --analyse-only
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cornercaselab.analysis import (  # noqa: E402
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_CONFIDENCE,
    DEFAULT_RESAMPLES,
    summarise_report,
)
from cornercaselab.experiments import compare_methods, make_simulator_evaluator  # noqa: E402
from cornercaselab.protocol import (  # noqa: E402
    FORMAL_PROTOCOL_ID,
    formal_config_path,
    formal_doc_path,
    formal_method_config,
    formal_plan,
    formal_seeds,
    load_formal_config,
    validate_formal_config,
)
from cornercaselab.scoring import SCORING_PROTOCOL_ID  # noqa: E402
from cornercaselab.storage import metadata as storage_metadata  # noqa: E402

BATCH_SIZE = 5
PROGRESS_EVERY = 1000
COMPARISON_SCHEMA = "ccl-method-comparison-v2"

#: The scoring driver must only read saved results. These tokens would mean it can
#: launch or import a simulator, which would make the scoring pass an experiment.
SCORING_DRIVER = ROOT / "scripts" / "score_v03.py"
FORBIDDEN_SCORING_TOKENS = ("make_simulator_evaluator", "simulator.evaluate", "highway_env")
DESCRIPTIVE_FIELDS = (
    "passed_candidate_count",
    "unique_search_collision_candidates",
    "raw_search_collision_samples",
    "internal_confirm_launches",
    "audit_incomplete_count",
    "audit_reserved_unspent",
    "actual_simulator_launches",
    "errors",
    "wall_time_s",
)


# ------------------------------------------------------------- pure helpers
def config_digest(path: Path | str | None = None) -> str:
    """SHA256 of the frozen config file bytes."""
    target = Path(path) if path is not None else formal_config_path()
    return hashlib.sha256(target.read_bytes()).hexdigest()


def batched_seeds(seeds: list[int], size: int = BATCH_SIZE) -> list[list[int]]:
    """Split the frozen seed order into consecutive batches."""
    if size < 1:
        raise ValueError("batch size must be >= 1")
    if len(seeds) % size:
        raise ValueError(f"{len(seeds)} seeds do not divide into batches of {size}")
    return [list(seeds[start:start + size]) for start in range(0, len(seeds), size)]


def expected_seed_order(config: dict) -> list[int]:
    """The frozen formal seed order, checked against its derivation."""
    stored = list(config["seeds"]["values"])
    derived = formal_seeds(config)
    if stored != derived:
        raise ValueError("frozen seed list does not match its documented derivation")
    return derived


def seed_directory(root: Path, batch_index: int, seed: int) -> Path:
    return root / f"batch_{batch_index:02d}" / f"seed_{seed}"


def batch_directory(root: Path, batch_index: int) -> Path:
    return root / f"batch_{batch_index:02d}"


# --------------------------------------------------------- progress logging
class ProgressLog:
    """Append-only JSONL event log. Never rewritten."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: str, **fields) -> None:
        record = {"event": event, "utc": datetime.now(timezone.utc).isoformat(), **fields}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _write_once(path: Path, payload: dict) -> None:
    """Write a manifest exactly once; refuse to overwrite an existing one."""
    if path.exists():
        raise RuntimeError(f"refusing to rewrite an existing manifest: {path}")
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _record_once(path: Path, payload: dict, *, resume: bool) -> None:
    """Write a once-only artifact; on a resume, leave existing history untouched."""
    if resume and path.exists():
        return
    _write_once(path, payload)


# ------------------------------------------------------------------ scoring
def _load_scoring_driver():
    """Import the existing scoring driver so its code path is reused verbatim."""
    spec = importlib.util.spec_from_file_location("ccl_score_v03",
                                                  ROOT / "scripts" / "score_v03.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score_seed(seed_dir: Path) -> int:
    """Score one seed's saved results with the existing driver."""
    scorer = _load_scoring_driver()
    return scorer.main(["--input", str(seed_dir), "--out", str(seed_dir / "scoring")])


def scoring_driver_problems() -> list[str]:
    """The scoring driver must be read-only: no evaluator and no simulator."""
    if not SCORING_DRIVER.is_file():
        return [f"missing scoring driver {SCORING_DRIVER}"]
    source = SCORING_DRIVER.read_text(encoding="utf-8")
    return [f"scoring driver references {token!r}" for token in FORBIDDEN_SCORING_TOKENS
            if token in source]


def episode_fingerprint(seed_dir: Path, methods: list[str]) -> dict:
    """Persisted episode counts per method: (ledger rows, episodes spent).

    Scoring must not change these. This is the real check that the scoring pass
    launched nothing, replacing the former (incorrect) ``sys.modules`` gate.
    """
    fingerprint = {}
    for method in methods:
        summary = _read_json(seed_dir / method / "summary.json")
        with (seed_dir / method / "ledger.jsonl").open(encoding="utf-8") as handle:
            rows = sum(1 for _ in handle)
        fingerprint[method] = (rows, summary["accounting"]["episodes_spent"])
    return fingerprint


# --------------------------------------------------------------- integrity
def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def seed_integrity(seed_dir: Path, seed: int, methods: list[str]) -> dict:
    """Structural and protocol checks for one seed. No method comparison."""
    problems: list[str] = []
    details: dict = {"seed": seed, "seed_directory": str(seed_dir)}

    for name in ("comparison.json", "manifest.json"):
        if not (seed_dir / name).is_file():
            problems.append(f"missing {name}")
    if problems:
        return {"seed": seed, "ok": False, "problems": problems, "details": details}

    comparison = _read_json(seed_dir / "comparison.json")
    manifest = _read_json(seed_dir / "manifest.json")
    details["purpose"] = manifest.get("purpose")
    details["seed_recorded"] = manifest.get("seed")
    details["git_commit"] = (manifest.get("metadata") or {}).get("git", {}).get("commit")
    details["git_dirty"] = (manifest.get("metadata") or {}).get("git", {}).get("dirty")
    details["source_sha256"] = (manifest.get("metadata") or {}).get("source_sha256")

    if manifest.get("schema") != COMPARISON_SCHEMA:
        problems.append(f"manifest schema is {manifest.get('schema')!r}")
    if manifest.get("purpose") != "evaluation":
        problems.append(f"purpose is {manifest.get('purpose')!r}, expected 'evaluation'")
    if manifest.get("seed") != seed:
        problems.append(f"wrong seed recorded: {manifest.get('seed')} != {seed}")
    if comparison.get("purpose") != "evaluation":
        problems.append(f"comparison purpose is {comparison.get('purpose')!r}")

    seen_methods = set()
    per_method = {}
    for method in comparison.get("methods", []):
        name = method["method"]
        seen_methods.add(name)
        acct = method["accounting"]
        plan = method.get("plan") or acct.get("plan") or {}
        phases = acct["phase_episodes"]
        row = {
            "budget_total": acct["budget_total"],
            "search_pool": plan.get("search_pool"),
            "audit_pool": plan.get("audit_pool"),
            "search": phases["search"],
            "internal_confirm": phases["internal_confirm"],
            "final_audit": phases["final_audit"],
            "episodes_spent": acct["episodes_spent"],
            "errors": acct["errors"],
            "interrupted": acct["interrupted_episodes"],
            "pending": acct["pending_unfinished_episodes"],
            "audit_launched": method["audit"]["audit_launched"],
            "audit_errors": method["audit"]["audit_errors"],
            "audit_max_attempts": method["audit"]["audit_max_attempts_per_candidate"],
            "audit_reserved_unspent": method["audit"]["audit_reserved_unspent"],
            "selected_candidates": method["audit"]["selected_candidate_count"],
        }
        per_method[name] = row
        if row["budget_total"] != 1000:
            problems.append(f"{name}: budget_total={row['budget_total']} != 1000")
        if row["search"] + row["internal_confirm"] != 700:
            problems.append(f"{name}: search+internal="
                            f"{row['search'] + row['internal_confirm']} != 700")
        if row["final_audit"] > 300:
            problems.append(f"{name}: final_audit={row['final_audit']} > audit_pool 300")
        if row["audit_max_attempts"] > 30:
            problems.append(f"{name}: audit_max_attempts={row['audit_max_attempts']} > 30")
        if row["selected_candidates"] * 30 != row["final_audit"]:
            problems.append(f"{name}: selected*30={row['selected_candidates'] * 30} != "
                            f"final_audit={row['final_audit']}")
        if row["pending"] != 0:
            problems.append(f"{name}: {row['pending']} reserved episode(s) never finished")
        if row["episodes_spent"] != row["search"] + row["internal_confirm"] + row["final_audit"]:
            problems.append(f"{name}: phase sum does not equal episodes_spent")
        for artifact in ("summary.json", "candidates.json", "ledger.jsonl",
                         "manifest.json", "checkpoint.json"):
            if not (seed_dir / name / artifact).is_file():
                problems.append(f"{name}: missing {artifact}")
    details["per_method"] = per_method

    if seen_methods != set(methods):
        problems.append(f"methods present {sorted(seen_methods)} != {sorted(methods)}")

    scoring_file = seed_dir / "scoring" / "scoring_summary.json"
    scoring_csv = seed_dir / "scoring" / "candidate_scores.csv"
    if not scoring_file.is_file():
        problems.append("missing scoring/scoring_summary.json")
    elif not scoring_csv.is_file():
        problems.append("missing scoring/candidate_scores.csv")
    else:
        try:
            scoring = _read_json(scoring_file)
        except (OSError, ValueError) as exc:
            problems.append(f"unreadable scoring/scoring_summary.json: {exc}")
            scoring = None
        if scoring is not None:
            details["scoring_protocol_id"] = scoring.get("scoring_protocol_id")
            # `simulator_module_loaded` only reports whether `cornercaselab.simulator`
            # happens to be in the scoring *process*'s sys.modules. The formal driver
            # imports the engine in-process, so that was true for every seed and the
            # old "must be False" gate was a false positive. It is recorded as
            # information only; the checks below test the scoring *results* instead.
            details["scoring_simulator_module_seen"] = scoring.get("simulator_module_loaded")
            if scoring.get("scoring_protocol_id") != SCORING_PROTOCOL_ID:
                problems.append(f"scoring protocol id {scoring.get('scoring_protocol_id')!r}")
            recorded_dir = scoring.get("input_dir")
            if not recorded_dir or Path(str(recorded_dir)).resolve() != seed_dir.resolve():
                problems.append(f"scoring input_dir {recorded_dir!r} is not this seed directory")
            recorded_commit = (scoring.get("input_run") or {}).get("git_commit")
            run_commit = (manifest.get("metadata") or {}).get("git", {}).get("commit")
            if recorded_commit != run_commit:
                problems.append("scoring input_run git_commit does not match the run manifest")
            scored = [entry for entry in scoring.get("methods", [])
                      if entry.get("status") == "scored"]
            if len(scored) != len(methods):
                problems.append(f"scoring produced {len(scored)} method reports, expected "
                                f"{len(methods)}")
            for entry in scored:
                if entry.get("data_conforms") is not True:
                    problems.append(f"{entry['method']}: scoring data_conforms is not true")
                if entry.get("invalid_candidate_count"):
                    problems.append(f"{entry['method']}: "
                                    f"{entry['invalid_candidate_count']} invalid candidate(s)")
                if entry.get("nonoverlapping_passed_count") is None:
                    problems.append(f"{entry['method']}: SNY is null")
                # The scoring output must describe exactly the run that is on disk: the
                # same audited candidates, and audit launches/observations that add up
                # to what the run's own accounting recorded. Any extra episode launched
                # during scoring would break these identities.
                run_row = per_method.get(entry["method"])
                if run_row is None:
                    continue
                if entry.get("audited_candidate_count") != run_row["selected_candidates"]:
                    problems.append(f"{entry['method']}: scoring audited "
                                    f"{entry.get('audited_candidate_count')} candidates but the "
                                    f"run selected {run_row['selected_candidates']}")
                rows = entry.get("candidate_scores", [])
                attempts = sum(int(candidate.get("audit_attempts") or 0) for candidate in rows)
                observations = sum(int(candidate.get("valid_observations") or 0)
                                   for candidate in rows)
                if attempts != run_row["audit_launched"]:
                    problems.append(f"{entry['method']}: scoring counted {attempts} audit "
                                    f"attempts but the run launched "
                                    f"{run_row['audit_launched']}")
                if observations != run_row["audit_launched"] - run_row["audit_errors"]:
                    problems.append(f"{entry['method']}: scoring reports {observations} valid "
                                    f"observations but the run recorded "
                                    f"{run_row['audit_launched'] - run_row['audit_errors']}")
            scored_names = {entry["method"] for entry in scored}
            if scored_names != set(methods):
                problems.append(f"scoring methods {sorted(scored_names)} != {sorted(methods)}")

    return {"seed": seed, "ok": not problems, "problems": problems, "details": details}


def batch_integrity(root: Path, batch_index: int, seeds: list[int],
                    methods: list[str]) -> dict:
    results = [seed_integrity(seed_directory(root, batch_index, seed), seed, methods)
               for seed in seeds]
    driver_problems = scoring_driver_problems()
    return {
        "batch": batch_index,
        "seeds": seeds,
        "ok": all(result["ok"] for result in results) and not driver_problems,
        "seeds_ok": sum(1 for result in results if result["ok"]),
        "seeds_total": len(results),
        "scoring_driver_problems": driver_problems,
        "problems": [{"seed": r["seed"], "problems": r["problems"]}
                     for r in results if not r["ok"]],
        "seed_details": [r["details"] for r in results],
    }


# --------------------------------------------------------------------- runs
def progress_evaluator(inner, every: int = PROGRESS_EVERY):
    counter = {"episodes": 0}

    def evaluator(scenario, sim_seed, policy, settings):
        counter["episodes"] += 1
        if every and counter["episodes"] % every == 0:
            print(f"      ... {counter['episodes']} episodes launched", flush=True)
        return inner(scenario, sim_seed, policy, settings)

    return evaluator, counter


def run_seed(root: Path, batch_index: int, seed: int, seed_index: int, total: int,
             config: dict, plan, method_config, progress: ProgressLog) -> dict:
    seed_dir = seed_directory(root, batch_index, seed)
    if (seed_dir / "seed_manifest.json").is_file():
        print(f"[seed {seed_index}/{total}] already complete, skipping", flush=True)
        progress.write("seed_skipped_already_complete", seed=seed, batch=batch_index,
                       seed_directory=str(seed_dir))
        return {"seed": seed, "status": "already_complete", "seed_directory": str(seed_dir)}
    if seed_dir.exists():
        return {"seed": seed, "status": "blocked",
                "reason": "seed directory exists without a completion manifest; "
                          "refusing to redo a partially written seed",
                "seed_directory": str(seed_dir)}

    started = datetime.now(timezone.utc).isoformat()
    progress.write("seed_started", seed=seed, batch=batch_index, seed_index=seed_index,
                   formal_evaluation_started=True)
    print(f"[seed {seed_index}/{total}] seed={seed} batch={batch_index} starting",
          flush=True)

    evaluator, counter = progress_evaluator(make_simulator_evaluator())
    payload = compare_methods(
        methods=list(config["methods"]),
        total_budget=plan.total,
        seed=seed,
        policy=config["policy"],
        search_fraction=config["budget"]["search_fraction"],
        audit_candidates=plan.audit_candidates,
        audit_repeats=plan.audit_repeats,
        config=method_config,
        evaluator=evaluator,
        purpose="evaluation",
        out=seed_dir,
        checkpoint_every=1,
    )
    comparison_manifest = _read_json(seed_dir / "manifest.json")
    # Snapshot the persisted episode counts BEFORE scoring so the scoring pass can be
    # shown not to have launched anything.
    before_scoring = episode_fingerprint(seed_dir, list(config["methods"]))

    if score_seed(seed_dir) != 0:
        progress.write("seed_scoring_failed", seed=seed, batch=batch_index)
        return {"seed": seed, "status": "blocked", "reason": "scoring driver failed",
                "seed_directory": str(seed_dir)}
    after_scoring = episode_fingerprint(seed_dir, list(config["methods"]))
    if after_scoring != before_scoring:
        progress.write("blocker", seed=seed, batch=batch_index,
                       reason="scoring changed the run's episode counts")
        return {"seed": seed, "status": "blocked",
                "reason": "scoring changed the run's ledger rows or episode counts: "
                          f"{before_scoring} -> {after_scoring}",
                "seed_directory": str(seed_dir)}

    completed = datetime.now(timezone.utc).isoformat()
    _write_once(seed_dir / "seed_manifest.json", {
        "protocol_id": FORMAL_PROTOCOL_ID,
        "scoring_protocol_id": SCORING_PROTOCOL_ID,
        "formal_evaluation_started": True,
        "seed": seed,
        "seed_index": seed_index,
        "batch": batch_index,
        "seed_started_utc": started,
        "seed_completed_utc": completed,
        "episodes_launched": counter["episodes"],
        "git_commit": (comparison_manifest.get("metadata") or {}).get("git", {}).get("commit"),
        "git_dirty": (comparison_manifest.get("metadata") or {}).get("git", {}).get("dirty"),
        "source_sha256": (comparison_manifest.get("metadata") or {}).get("source_sha256"),
        "authoritative_manifest": "manifest.json",
        "note": ("Formal evaluation seed. Written once; never back-filled."),
    })
    progress.write("seed_completed", seed=seed, batch=batch_index,
                   seed_index=seed_index, episodes_launched=counter["episodes"])
    print(f"[seed {seed_index}/{total}] seed={seed} compare+scoring complete, "
          f"{counter['episodes']} episodes", flush=True)
    return {"seed": seed, "status": "completed", "seed_directory": str(seed_dir),
            "episodes_launched": counter["episodes"],
            "methods": [m["method"] for m in payload["methods"]]}


# ------------------------------------------------------------------ analysis
def collect_sny(root: Path, batches: list[list[int]], methods: list[str]) -> dict:
    """SNY per method, ordered by the frozen seed order."""
    scores = {method: [] for method in methods}
    order = []
    for batch_index, seeds in enumerate(batches, start=1):
        for seed in seeds:
            seed_dir = seed_directory(root, batch_index, seed)
            scoring = _read_json(seed_dir / "scoring" / "scoring_summary.json")
            by_method = {entry["method"]: entry for entry in scoring["methods"]}
            order.append(seed)
            for method in methods:
                scores[method].append(by_method[method]["nonoverlapping_passed_count"])
    return {"seed_order": order, "scores": scores}


def collect_descriptive(root: Path, batches: list[list[int]], methods: list[str]) -> dict:
    totals = {method: {field: [] for field in DESCRIPTIVE_FIELDS} for method in methods}
    for batch_index, seeds in enumerate(batches, start=1):
        for seed in seeds:
            seed_dir = seed_directory(root, batch_index, seed)
            scoring = _read_json(seed_dir / "scoring" / "scoring_summary.json")
            by_method = {entry["method"]: entry for entry in scoring["methods"]}
            for method in methods:
                summary = _read_json(seed_dir / method / "summary.json")
                candidates = _read_json(seed_dir / method / "candidates.json")
                acct = summary["accounting"]
                entry = by_method[method]
                totals[method]["passed_candidate_count"].append(
                    entry["passed_candidate_count"])
                totals[method]["unique_search_collision_candidates"].append(len(candidates))
                totals[method]["raw_search_collision_samples"].append(
                    summary["search_collisions"])
                totals[method]["internal_confirm_launches"].append(
                    acct["phase_episodes"]["internal_confirm"])
                totals[method]["audit_incomplete_count"].append(
                    entry["incomplete_candidate_count"])
                # The unspent audit allocation is recorded by the run's own audit
                # report, not by the scoring pass; read it from the right place.
                totals[method]["audit_reserved_unspent"].append(
                    summary["audit"]["audit_reserved_unspent"])
                totals[method]["actual_simulator_launches"].append(acct["episodes_spent"])
                totals[method]["errors"].append(acct["errors"])
                totals[method]["wall_time_s"].append(summary["wall_time_s"])
    report = {}
    for method in methods:
        report[method] = {}
        for field in DESCRIPTIVE_FIELDS:
            values = totals[method][field]
            report[method][field] = {
                "total": sum(values),
                "mean": sum(values) / len(values),
                "min": min(values),
                "max": max(values),
                "per_seed": values,
            }
    return report


def analyse(root: Path, batches: list[list[int]], config: dict) -> dict:
    methods = list(config["methods"])
    collected = collect_sny(root, batches, methods)
    missing = [method for method, values in collected["scores"].items()
               if any(value is None for value in values)]
    if missing:
        return {"status": "blocked",
                "reason": f"null SNY for method(s) {missing}; formal scoring is not valid"}
    analysis = summarise_report(collected["scores"],
                                resamples=DEFAULT_RESAMPLES,
                                seed=DEFAULT_BOOTSTRAP_SEED,
                                confidence=DEFAULT_CONFIDENCE)
    analysis["seed_order"] = collected["seed_order"]
    analysis["descriptive_metrics"] = collect_descriptive(root, batches, methods)
    analysis["status"] = "ok"
    analysis["protocol_id"] = FORMAL_PROTOCOL_ID
    analysis["scoring_protocol_id"] = SCORING_PROTOCOL_ID
    analysis["bootstrap"] = {"method": "percentile paired bootstrap",
                             "resamples": DEFAULT_RESAMPLES,
                             "seed": DEFAULT_BOOTSTRAP_SEED,
                             "confidence": DEFAULT_CONFIDENCE}
    analysis_dir = root / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    target = analysis_dir / "formal_analysis.json"
    if target.exists():
        print(f"analysis already exists, not rewriting: {target}")
        return _read_json(target)
    target.write_text(json.dumps(analysis, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return analysis


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None,
                        help="formal run root (default: a new runs/v03_formal_eval_<ts>)")
    parser.add_argument("--analyse-only", action="store_true",
                        help="only run the frozen analysis over an existing root")
    args = parser.parse_args(argv)

    config = load_formal_config()
    validate_formal_config(config)
    plan = formal_plan(config)
    method_config = formal_method_config(config)
    seeds = expected_seed_order(config)
    batches = batched_seeds(seeds, BATCH_SIZE)
    methods = list(config["methods"])
    digest = config_digest()

    if args.analyse_only:
        if args.root is None:
            print("BLOCKED: --analyse-only needs --root")
            return 1
        root = args.root.resolve()
        analysis = analyse(root, batches, config)
        print(json.dumps({k: v for k, v in analysis.items()
                          if k not in ("raw_scores", "descriptive_metrics")},
                         indent=2, ensure_ascii=False))
        return 0 if analysis.get("status") == "ok" else 1

    if args.root is not None:
        root = args.root.resolve()
        if root.exists():
            # Resume mode: continue an interrupted formal run in the SAME root. The
            # existing formal_manifest.json is the authority and is never rewritten.
            manifest_file = root / "formal_manifest.json"
            if not manifest_file.is_file():
                print(f"BLOCKED: root exists but has no formal_manifest.json: {root}")
                return 1
            existing = _read_json(manifest_file)
            if existing.get("protocol_id") != FORMAL_PROTOCOL_ID:
                print(f"BLOCKED: existing root protocol_id "
                      f"{existing.get('protocol_id')!r} != {FORMAL_PROTOCOL_ID!r}")
                return 1
            if list(existing.get("seed_order", [])) != seeds:
                print("BLOCKED: existing root has a different seed order")
                return 1
            if existing.get("frozen_config_sha256") != digest:
                print("BLOCKED: the frozen config no longer matches the one this root "
                      "started with")
                return 1
            resume = True
            formal_started = existing.get("formal_started_utc")
            print(f"resuming existing formal root (started {formal_started})")
        else:
            root.mkdir(parents=True)
            resume = False
            formal_started = None
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = ROOT / "runs" / f"v03_formal_eval_{stamp}"
        root.mkdir(parents=True)
        resume = False
        formal_started = None

    progress = ProgressLog(root / "formal_run_progress.jsonl")
    source = load_formal_config()  # snapshot, byte-identical
    if not (root / "frozen_config.json").exists():
        (root / "frozen_config.json").write_text(
            json.dumps(source, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not (root / "frozen_config.sha256").exists():
        (root / "frozen_config.sha256").write_text(digest + "\n", encoding="utf-8")
    if formal_started is None:
        formal_started = datetime.now(timezone.utc).isoformat()
    _record_once(root / "formal_manifest.json", {
        "protocol_id": FORMAL_PROTOCOL_ID,
        "scoring_protocol_id": SCORING_PROTOCOL_ID,
        "formal_evaluation_started": True,
        "formal_started_utc": formal_started,
        "purpose": "evaluation",
        "policy": config["policy"],
        "seed_count": len(seeds),
        "seed_order": seeds,
        "batches": batches,
        "batch_size": BATCH_SIZE,
        "methods": methods,
        "budget": config["budget"],
        "audit": config["audit"],
        "method_parameters": config["method_parameters"],
        "scoring": config["scoring"],
        "reporting": config["reporting"],
        "frozen_config_path": str(formal_config_path()),
        "frozen_config_sha256": digest,
        "frozen_protocol_document": str(formal_doc_path()),
        "metadata": storage_metadata(),
        "note": ("Formal evaluation run. Written once before the first seed; never "
                 "back-filled. The frozen protocol may not change after seed 1 starts."),
    }, resume=resume)
    progress.write("formal_run_resumed" if resume else "formal_run_started",
                   root=str(root), seed_order=seeds,
                   frozen_config_sha256=digest, formal_evaluation_started=True,
                   resume=resume)

    print(f"root={root}")
    print(f"protocol={FORMAL_PROTOCOL_ID} scoring={SCORING_PROTOCOL_ID}")
    print(f"seeds={len(seeds)} batches={len(batches)} methods={methods}")
    print(f"budget per method per seed={plan.total} (search {plan.search_pool} + "
          f"audit {plan.audit_pool})")
    print(f"frozen config sha256={digest}")
    sys.stdout.flush()

    for batch_index, batch in enumerate(batches, start=1):
        if config_digest() != digest:
            progress.write("blocker", reason="frozen config changed", batch=batch_index)
            print("BLOCKED: the frozen config changed mid-run.")
            return 1
        batch_dir = batch_directory(root, batch_index)
        batch_dir.mkdir(parents=True, exist_ok=True)
        _record_once(batch_dir / "batch_manifest.json", {
            "protocol_id": FORMAL_PROTOCOL_ID,
            "formal_evaluation_started": True,
            "formal_started_utc": formal_started,
            "batch": batch_index,
            "batch_size": len(batch),
            "seeds": batch,
            "frozen_config_sha256": digest,
            "note": "Written once at batch start; never back-filled.",
        }, resume=resume)
        progress.write("batch_started", batch=batch_index, seeds=batch)
        print(f"[batch {batch_index}/{len(batches)}] seeds={batch}", flush=True)

        for seed in batch:
            seed_index = seeds.index(seed) + 1
            try:
                outcome = run_seed(root, batch_index, seed, seed_index, len(seeds),
                                   config, plan, method_config, progress)
            except BaseException:
                detail = traceback.format_exc()
                progress.write("seed_error", seed=seed, batch=batch_index,
                               traceback=detail)
                print(f"\nBLOCKED: seed {seed} raised.\n{detail}")
                return 1
            if outcome["status"] == "blocked":
                progress.write("blocker", seed=seed, reason=outcome["reason"])
                print(f"\nBLOCKED: seed {seed}: {outcome['reason']}")
                return 1

        integrity = batch_integrity(root, batch_index, batch, methods)
        integrity_file = batch_dir / "batch_integrity.json"
        if integrity_file.exists():
            # A historical verdict already exists (e.g. from the interrupted first
            # attempt). Never rewrite it; record this re-check separately instead.
            recheck = batch_dir / "batch_integrity_recheck.json"
            if not recheck.exists():
                recheck.write_text(json.dumps(integrity, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
        else:
            _write_once(integrity_file, integrity)
        progress.write("batch_integrity_checked", batch=batch_index,
                       ok=integrity["ok"], seeds_ok=integrity["seeds_ok"],
                       seeds_total=integrity["seeds_total"])
        print(f"[batch {batch_index}/{len(batches)}] integrity: "
              f"{integrity['seeds_ok']}/{integrity['seeds_total']} seeds ok "
              f"(ok={integrity['ok']})", flush=True)
        if not integrity["ok"]:
            progress.write("blocker", reason="batch integrity failed",
                           batch=batch_index, problems=integrity["problems"])
            print("\nBLOCKED: batch integrity failed; stopping without touching the "
                  "frozen protocol.")
            print(json.dumps(integrity["problems"], indent=2, ensure_ascii=False))
            return 1

    all_results = []
    for batch_index, batch in enumerate(batches, start=1):
        all_results.extend(seed_integrity(seed_directory(root, batch_index, seed), seed,
                                          methods)
                           for seed in batch)
    overall = {"seeds_total": len(all_results),
               "seeds_ok": sum(1 for r in all_results if r["ok"]),
               "ok": all(r["ok"] for r in all_results),
               "problems": [{"seed": r["seed"], "problems": r["problems"]}
                            for r in all_results if not r["ok"]]}
    _record_once(root / "formal_completion.json", {
        "protocol_id": FORMAL_PROTOCOL_ID,
        "scoring_protocol_id": SCORING_PROTOCOL_ID,
        "formal_evaluation_started": True,
        "formal_started_utc": formal_started,
        "formal_completed_utc": datetime.now(timezone.utc).isoformat(),
        "seed_count": len(seeds),
        "seed_order": seeds,
        "batches": len(batches),
        "methods": methods,
        "integrity": overall,
        "allocated_episode_slots": len(seeds) * len(methods) * plan.total,
        "note": "Written once at completion; never back-filled.",
    }, resume=resume)
    progress.write("formal_run_completed", ok=overall["ok"], **{
        "seeds_ok": overall["seeds_ok"]})
    print(f"\nall batches complete: {overall['seeds_ok']}/{overall['seeds_total']} "
          f"seeds ok")
    if not overall["ok"]:
        print("BLOCKED: completion integrity failed.")
        return 1

    analysis = analyse(root, batches, config)
    if analysis.get("status") != "ok":
        print("BLOCKED: analysis failed:")
        print(json.dumps(analysis, indent=2, ensure_ascii=False))
        return 1
    print(f"\nanalysis written: {root / 'analysis' / 'formal_analysis.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
