"""Thin offline v0.3 scoring driver.

Reads already-saved v0.3 results and writes scores. It launches **no simulator
episode**: it never imports ``cornercaselab.simulator``, never builds an
evaluator, and never modifies the input run. The input run's own metadata is
kept separate from the metadata of this scoring pass.

Usage
-----
    .venv\\Scripts\\python.exe scripts\\score_v03.py
    .venv\\Scripts\\python.exe scripts\\score_v03.py --input runs\\<run-dir>

Output (a new unique directory under ``runs/``):
    candidate_scores.csv
    scoring_summary.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cornercaselab.scoring import (  # noqa: E402
    CSV_COLUMNS,
    SCORING_PROTOCOL_ID,
    ScoringDataError,
    candidate_csv_rows,
    planned_repeats_from_summary,
    run_is_complete,
    score_method,
)
from cornercaselab.storage import metadata as storage_metadata  # noqa: E402

DEFAULT_INPUT = ROOT / "runs" / "v03_smoke_dev_20261004_154547"
SIMULATOR_MODULE = "cornercaselab.simulator"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def method_directories(root: Path) -> list[Path]:
    """Method directories of a saved run (or the root itself if it is one)."""
    if (root / "summary.json").is_file():
        return [root]
    found = sorted(child for child in root.iterdir()
                   if child.is_dir() and (child / "summary.json").is_file())
    if not found:
        raise ScoringDataError(f"no method directory with summary.json under {root}")
    return found


def score_directory(method_dir: Path) -> tuple[dict, list[dict], dict]:
    """Score one saved method directory. Reads only; writes nothing."""
    summary = _load_json(method_dir / "summary.json")
    candidates = _load_json(method_dir / "candidates.json")
    checkpoint_file = method_dir / "checkpoint.json"
    checkpoint = _load_json(checkpoint_file) if checkpoint_file.is_file() else None
    manifest_file = method_dir / "manifest.json"
    manifest = _load_json(manifest_file) if manifest_file.is_file() else {}

    planned = planned_repeats_from_summary(summary)
    complete, complete_reason = run_is_complete(summary, checkpoint)
    score = score_method(method=str(summary.get("method", method_dir.name)),
                         candidates=candidates, planned_repeats=planned,
                         run_complete=complete,
                         selected_for_audit=summary.get("selected_for_audit"))
    score["run_complete_reason"] = complete_reason
    score["input_method_dir"] = str(method_dir)
    score["input_summary_schema"] = summary.get("schema")
    score["input_manifest_schema"] = manifest.get("schema")
    score["input_purpose"] = summary.get("purpose")
    score["input_seed"] = summary.get("seed")
    return score, candidates, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                        help="saved v0.3 run directory (default: the v0.3 smoke run)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: a new unique runs/ directory)")
    parser.add_argument("--label", default="v03_scoring",
                        help="prefix of the generated output directory name")
    args = parser.parse_args(argv)

    input_dir = args.input.resolve()
    if not input_dir.is_dir():
        print(f"BLOCKED: input directory not found: {input_dir}")
        return 1

    run_manifest_file = input_dir / "manifest.json"
    run_manifest = _load_json(run_manifest_file) if run_manifest_file.is_file() else {}

    if args.out is not None:
        out_dir = args.out.resolve()
        if out_dir.exists():
            print(f"BLOCKED: refusing to overwrite existing output directory: {out_dir}")
            return 1
        out_dir.mkdir(parents=True)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = ROOT / "runs" / f"{args.label}_{stamp}"
        suffix = 2
        while out_dir.exists():
            out_dir = ROOT / "runs" / f"{args.label}_{stamp}_{suffix}"
            suffix += 1
        out_dir.mkdir(parents=True)

    try:
        directories = method_directories(input_dir)
    except ScoringDataError as exc:
        (out_dir / "scoring_summary.json").write_text(json.dumps({
            "scoring_protocol_id": SCORING_PROTOCOL_ID,
            "status": "blocked", "input_dir": str(input_dir), "error": str(exc),
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"BLOCKED: {exc}")
        return 1

    csv_rows: list[dict] = []
    method_reports: list[dict] = []
    for method_dir in directories:
        try:
            score, candidates, manifest = score_directory(method_dir)
        except ScoringDataError as exc:
            print(f"SKIP {method_dir.name}: {exc}")
            method_reports.append({"method": method_dir.name,
                                   "input_method_dir": str(method_dir),
                                   "status": "blocked", "error": str(exc)})
            continue
        scenarios = {str(row["candidate_id"]): row["scenario"] for row in candidates
                     if isinstance(row, dict) and "candidate_id" in row and "scenario" in row}
        csv_rows.extend(candidate_csv_rows(score, scenarios))
        method_reports.append({**score, "status": "scored",
                               "input_metadata": manifest.get("metadata")})

    with (out_dir / "candidate_scores.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(row)

    report = {
        "scoring_protocol_id": SCORING_PROTOCOL_ID,
        "status": "ok",
        "input_dir": str(input_dir),
        "input_run": {
            "purpose": run_manifest.get("purpose"),
            "seed": run_manifest.get("seed"),
            "plan": run_manifest.get("plan"),
            "method_config": run_manifest.get("method_config"),
            "methods": run_manifest.get("methods"),
            "git_commit": (run_manifest.get("metadata") or {}).get("git", {}).get("commit"),
            "git_dirty": (run_manifest.get("metadata") or {}).get("git", {}).get("dirty"),
            "source_sha256": (run_manifest.get("metadata") or {}).get("source_sha256"),
            "created_utc": (run_manifest.get("metadata") or {}).get("created_utc"),
        },
        # Deliberately separate from the input run's metadata above.
        "scoring_metadata": storage_metadata(),
        "scoring_note": ("Offline scoring only. No simulator episode was launched and the "
                         "input run was not modified; the smoke run's audit_repeats=5 makes "
                         "the v03_scoring_v1 n=30 stability criterion inapplicable."),
        "simulator_module_loaded": SIMULATOR_MODULE in sys.modules,
        "methods": method_reports,
    }
    (out_dir / "scoring_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    header = (f"{'method':<26}{'planned_n':>10}{'applicable':>11}{'passed':>7}"
              f"{'incompl':>8}{'not_pass':>9}{'invalid':>8}{'nonoverlap':>11}"
              f"{'formal':>7}")
    print(f"input_dir={input_dir}")
    print(f"output_dir={out_dir}")
    print(f"scoring_protocol_id={SCORING_PROTOCOL_ID} "
          f"simulator_module_loaded={report['simulator_module_loaded']}")
    print()
    print(header)
    print("-" * len(header))
    for entry in method_reports:
        if entry.get("status") != "scored":
            print(f"{entry['method']:<26}{'blocked':>10}")
            continue
        print(f"{entry['method']:<26}{entry['planned_audit_repeats']:>10}"
              f"{str(entry['stability_criterion_applicable']):>11}"
              f"{entry['passed_candidate_count']:>7}"
              f"{entry['incomplete_candidate_count']:>8}"
              f"{entry['not_passed_candidate_count']:>9}"
              f"{entry['invalid_candidate_count']:>8}"
              f"{str(entry['nonoverlapping_passed_count']):>11}"
              f"{str(entry['formal_score']):>7}")
    print()
    for entry in method_reports:
        if entry.get("status") == "scored":
            print(f"{entry['method']}: {entry['eligibility_reason']}")
    print(f"\nwrote {out_dir / 'candidate_scores.csv'}")
    print(f"wrote {out_dir / 'scoring_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
