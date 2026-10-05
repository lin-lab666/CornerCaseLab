"""Render the frozen v0.3 formal results: freeze, reproduce, figures, tables.

Offline and read-only with respect to the formal run. This script:

* reads ONLY the existing formal artifacts under
  ``runs/v03_formal_eval_20261004_205611`` (never writes into it);
* launches **zero** simulator episodes and imports no simulator;
* re-derives the frozen confirmatory numbers from the 20x3 raw SNY values and
  refuses to continue if they disagree with ``formal_analysis.json``;
* writes a data freeze (per-file SHA256), paper-ready figures (PNG + PDF),
  result tables (CSV) and a descriptive analysis (JSON) into
  ``reports/formal_v03/``.

The confirmatory analysis itself is frozen: this script does not redefine SNY,
the stability criterion, the contrasts, the bootstrap or any threshold. The
mechanism-oriented material it emits is explicitly labelled EXPLORATORY /
DESCRIPTIVE and is not pre-registered confirmatory evidence.

Usage:
    .venv\\Scripts\\python.exe scripts\\render_formal_results_v03.py
    .venv\\Scripts\\python.exe scripts\\render_formal_results_v03.py --check-only
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from cornercaselab.analysis import summarise_report  # noqa: E402

FORMAL_ROOT = ROOT / "runs" / "v03_formal_eval_20261004_205611"
REPORT_DIR = ROOT / "reports" / "formal_v03"

METHODS = ("random_search", "fixed_explore_confirm", "adaptive_explore_confirm")
LABELS = {
    "random_search": "Random search",
    "fixed_explore_confirm": "Fixed explore/confirm",
    "adaptive_explore_confirm": "Adaptive explore/confirm",
}
SHORT = {
    "random_search": "Random",
    "fixed_explore_confirm": "Fixed",
    "adaptive_explore_confirm": "Adaptive",
}
COLORS = {
    "random_search": "#4C72B0",
    "fixed_explore_confirm": "#DD8452",
    "adaptive_explore_confirm": "#55A868",
}
MARKERS = {"random_search": "o", "fixed_explore_confirm": "s",
           "adaptive_explore_confirm": "^"}

BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 314159265
CONFIDENCE = 0.95

#: The confirmatory numbers exactly as reported in the completion report. The
#: recomputation must reproduce these, not merely agree with a file.
EXPECTED = {
    "mean": {"random_search": 4.75, "fixed_explore_confirm": 6.05,
             "adaptive_explore_confirm": 7.10},
    "median": {"random_search": 5.0, "fixed_explore_confirm": 6.0,
               "adaptive_explore_confirm": 7.0},
    "sd": {"random_search": 1.446411166701189,
           "fixed_explore_confirm": 1.4317821063276355,
           "adaptive_explore_confirm": 1.3726654823065194},
    "iqr": {"random_search": 2.0, "fixed_explore_confirm": 2.0,
            "adaptive_explore_confirm": 0.5},
    "contrasts": {
        "adaptive_explore_confirm - random_search": (2.35, 1.95, 2.75),
        "adaptive_explore_confirm - fixed_explore_confirm": (1.05, 0.55, 1.55),
        "fixed_explore_confirm - random_search": (1.30, 0.75, 1.85),
    },
}
#: Descriptive totals stated in the completion report.
EXPECTED_DESCRIPTIVE = {
    "passed_candidate_count": {"random_search": 95, "fixed_explore_confirm": 121,
                               "adaptive_explore_confirm": 142},
    "audit_launched": {"random_search": 6000, "fixed_explore_confirm": 6000,
                       "adaptive_explore_confirm": 6000},
    "internal_confirm": {"random_search": 0, "fixed_explore_confirm": 2843,
                         "adaptive_explore_confirm": 3500},
    "search_collisions": {"random_search": 1744, "fixed_explore_confirm": 1423,
                          "adaptive_explore_confirm": 1354},
    "episodes_spent": {"random_search": 20000, "fixed_explore_confirm": 20000,
                       "adaptive_explore_confirm": 20000},
}
AUDIT_CANDIDATES_PER_METHOD = 200  # 20 seeds x 10 audited candidates

#: (label, expected formatted value) pairs that must appear in the narrative.
NARRATIVE_NUMBERS = (
    ("Random mean", "4.75"), ("Fixed mean", "6.05"), ("Adaptive mean", "7.10"),
    ("primary diff", "+2.35"), ("primary CI", "[1.95, 2.75]"),
    ("adaptive-fixed diff", "+1.05"), ("adaptive-fixed CI", "[0.55, 1.55]"),
    ("fixed-random diff", "+1.30"), ("fixed-random CI", "[0.75, 1.85]"),
    ("random pass", "95"), ("fixed pass", "121"), ("adaptive pass", "142"),
    ("random pass rate", "47.5%"), ("fixed pass rate", "60.5%"),
    ("adaptive pass rate", "71.0%"),
    ("random breadth", "87.20"), ("fixed breadth", "71.15"),
    ("adaptive breadth", "67.70"),
    ("fixed confirm mean", "142.15"), ("adaptive confirm mean", "175.00"),
    ("fixed search mean", "557.85"), ("adaptive search mean", "525"),
    ("audit allocation", "300"),
)


# ------------------------------------------------------------------ helpers
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def mean(values):
    return sum(values) / len(values)


def percentile(sorted_values, fraction):
    """Linear-interpolation percentile, implemented independently of the library."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower = int(math.floor(position))
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def pearson(xs, ys):
    n = len(xs)
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def write_csv(path: Path, columns, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


# ------------------------------------------------------------------- phase 1
def collect(formal_root: Path) -> dict:
    """Read every formal artifact. Read-only."""
    manifest = read_json(formal_root / "formal_manifest.json")
    completion = read_json(formal_root / "formal_completion.json")
    analysis = read_json(formal_root / "analysis" / "formal_analysis.json")
    seeds = list(manifest["seed_order"])
    batches = [list(batch) for batch in manifest["batches"]]
    per_seed = []
    for batch_index, batch in enumerate(batches, start=1):
        for seed in batch:
            seed_dir = formal_root / f"batch_{batch_index:02d}" / f"seed_{seed}"
            seed_manifest = read_json(seed_dir / "manifest.json")
            scoring = read_json(seed_dir / "scoring" / "scoring_summary.json")
            methods = {}
            for method in METHODS:
                summary = read_json(seed_dir / method / "summary.json")
                candidates = read_json(seed_dir / method / "candidates.json")
                scored = next(entry for entry in scoring["methods"]
                              if entry["method"] == method)
                methods[method] = {
                    "accounting": summary["accounting"],
                    "audit": summary["audit"],
                    "search_collisions": summary["search_collisions"],
                    "wall_time_s": summary["wall_time_s"],
                    "candidate_count": len(candidates),
                    "scored": scored,
                }
            per_seed.append({
                "seed": seed,
                "batch": batch_index,
                "seed_index": seeds.index(seed) + 1,
                "purpose": seed_manifest["purpose"],
                "git_commit": seed_manifest["metadata"]["git"]["commit"],
                "git_dirty": seed_manifest["metadata"]["git"]["dirty"],
                "source_sha256": seed_manifest["metadata"]["source_sha256"],
                "scoring_protocol_id": scoring["scoring_protocol_id"],
                "methods": methods,
            })
    return {"manifest": manifest, "completion": completion, "analysis": analysis,
            "seeds": seeds, "batches": batches, "per_seed": per_seed}


def freeze(formal_root: Path, data: dict, root_hashes: dict) -> dict:
    files = sorted(p for p in formal_root.rglob("*") if p.is_file())
    per_file = {str(p.relative_to(formal_root)).replace("\\", "/"): sha256_file(p)
                for p in files}
    manifest = data["manifest"]
    payload = {
        "schema": "ccl-v03-formal-data-freeze-v1",
        "formal_run_root": str(formal_root),
        "protocol_id": manifest["protocol_id"],
        "scoring_protocol_id": manifest["scoring_protocol_id"],
        "formal_evaluation_started": manifest["formal_evaluation_started"],
        "formal_started_utc": manifest["formal_started_utc"],
        "run_completion_utc": data["completion"]["formal_completed_utc"],
        "seed_count": len(data["seeds"]),
        "seeds": data["seeds"],
        "batches": data["batches"],
        "method_order": manifest["methods"],
        "budget": manifest["budget"],
        "audit": manifest["audit"],
        "method_parameters": manifest["method_parameters"],
        "scoring": manifest["scoring"],
        "reporting": manifest["reporting"],
        "allocated_episode_slots": data["completion"]["allocated_episode_slots"],
        "integrity": data["completion"]["integrity"],
        "key_artifact_sha256": {
            "formal_manifest.json": per_file["formal_manifest.json"],
            "formal_completion.json": per_file["formal_completion.json"],
            "frozen_config.json": per_file["frozen_config.json"],
            "frozen_config.sha256": per_file["frozen_config.sha256"],
            "analysis/formal_analysis.json": per_file["analysis/formal_analysis.json"],
            "formal_run_progress.jsonl": per_file["formal_run_progress.jsonl"],
        },
        "analysis_sha256": per_file["analysis/formal_analysis.json"],
        "frozen_config_sha256": per_file["frozen_config.json"],
        "current_git_commit": current_commit(),
        "provenance": {
            "seeds_1_5_commit": "c7b2d01db0fa01f8412a8198cdbd4c8a2807c2e2",
            "seeds_6_20_commit": "f580d0225a30f8b15cc7d6510f5c6df977516c39",
            "reporting_analysis_commit": "25c86cd02e945ecd750d9f8249ac7dff2d6fcce6",
            "note": ("Seeds 1-5 were produced under the first driver commit; seeds "
                     "6-20 under the orchestration-fix commit. The three formal "
                     "commits differ only in scripts/run_v03_formal.py."),
        },
        "file_count": len(per_file),
        "file_sha256": per_file,
        "note": ("Immutable input snapshot. The formal run root is treated as "
                 "read-only; this file is written into the report directory only."),
    }
    (REPORT_DIR / "FORMAL_DATA_FREEZE.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(REPORT_DIR / "formal_data_file_sha256.csv",
              ["relative_path", "sha256"],
              [[name, digest] for name, digest in sorted(per_file.items())])
    payload["_self_check_root_hashes"] = root_hashes
    return payload


def current_commit() -> str:
    head = ROOT / ".git" / "HEAD"
    if not head.is_file():
        return "unknown"
    text = head.read_text(encoding="utf-8").strip()
    if text.startswith("ref: "):
        ref = ROOT / ".git" / text[5:].strip()
        if ref.is_file():
            return ref.read_text(encoding="utf-8").strip()
        packed = ROOT / ".git" / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(text[5:].strip()):
                    return line.split()[0]
    return text


# ------------------------------------------------------------------- phase 2
def sny_by_method(data: dict) -> dict:
    scores = {method: [] for method in METHODS}
    for record in data["per_seed"]:
        for method in METHODS:
            scores[method].append(
                record["methods"][method]["scored"]["nonoverlapping_passed_count"])
    return scores


def independent_bootstrap(differences, resamples, seed, confidence):
    """Re-implementation of the frozen percentile paired bootstrap."""
    n = len(differences)
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += differences[rng.randrange(n)]
        draws.append(total / n)
    draws.sort()
    tail = 1.0 - confidence
    return mean(differences), percentile(draws, tail / 2.0), percentile(draws, 1.0 - tail / 2.0)


def reproduce(data: dict) -> dict:
    scores = sny_by_method(data)
    frozen = summarise_report(dict(scores), resamples=BOOTSTRAP_RESAMPLES,
                              seed=BOOTSTRAP_SEED, confidence=CONFIDENCE)
    problems = []
    for method in METHODS:
        stats = frozen["per_method"][method]
        if abs(stats["mean"] - EXPECTED["mean"][method]) > 1e-12:
            problems.append(f"{method}: mean {stats['mean']} != "
                            f"{EXPECTED['mean'][method]}")
        if abs(stats["median"] - EXPECTED["median"][method]) > 1e-12:
            problems.append(f"{method}: median {stats['median']} != "
                            f"{EXPECTED['median'][method]}")
        if abs(stats["standard_deviation"] - EXPECTED["sd"][method]) > 1e-9:
            problems.append(f"{method}: sd {stats['standard_deviation']} != "
                            f"{EXPECTED['sd'][method]}")
        if abs(stats["iqr"] - EXPECTED["iqr"][method]) > 1e-12:
            problems.append(f"{method}: iqr {stats['iqr']} != {EXPECTED['iqr'][method]}")
        if stats["n"] != 20:
            problems.append(f"{method}: n {stats['n']} != 20")

    contrast_rows = []
    for contrast in frozen["paired_contrasts"]:
        key = contrast["contrast"]
        if key not in EXPECTED["contrasts"]:
            problems.append(f"unexpected contrast {key}")
            continue
        want_mean, want_low, want_high = EXPECTED["contrasts"][key]
        for name, got, want in (("mean", contrast["mean_difference"], want_mean),
                                ("ci_low", contrast["ci_low"], want_low),
                                ("ci_high", contrast["ci_high"], want_high)):
            if abs(got - want) > 1e-12:
                problems.append(f"{key}: {name} {got} != {want}")
        differences = [data["per_seed"][i]["methods"][contrast["method_a"]]["scored"]
                       ["nonoverlapping_passed_count"]
                       - data["per_seed"][i]["methods"][contrast["method_b"]]["scored"]
                       ["nonoverlapping_passed_count"]
                       for i in range(len(data["per_seed"]))]
        ind_mean, ind_low, ind_high = independent_bootstrap(
            differences, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED, CONFIDENCE)
        for name, got, want in (("mean", ind_mean, contrast["mean_difference"]),
                                ("ci_low", ind_low, contrast["ci_low"]),
                                ("ci_high", ind_high, contrast["ci_high"])):
            if abs(got - want) > 1e-12:
                problems.append(f"{key}: independent {name} {got} != frozen {want}")
        wins = sum(1 for d in differences if d > 0)
        losses = sum(1 for d in differences if d < 0)
        ties = len(differences) - wins - losses
        contrast_rows.append({
            "contrast": key, "role": contrast["role"],
            "method_a": contrast["method_a"], "method_b": contrast["method_b"],
            "n_pairs": contrast["n_pairs"],
            "mean_difference": contrast["mean_difference"],
            "bootstrap_ci_low": contrast["ci_low"],
            "bootstrap_ci_high": contrast["ci_high"],
            "resamples": contrast["resamples"],
            "bootstrap_seed": contrast["bootstrap_seed"],
            "confidence": contrast["confidence"],
            "differences": differences,
            "wins": wins, "losses": losses, "ties": ties,
            "independent_mean": ind_mean, "independent_ci_low": ind_low,
            "independent_ci_high": ind_high,
        })

    # cross-check against the frozen file on disk
    file_contrasts = {entry["contrast"]: entry
                      for entry in data["analysis"]["paired_contrasts"]}
    for row in contrast_rows:
        stored = file_contrasts.get(row["contrast"])
        if stored is None:
            problems.append(f"{row['contrast']}: missing from formal_analysis.json")
            continue
        for name in ("mean_difference", "ci_low", "ci_high"):
            if abs(stored[name] - row[{"mean_difference": "mean_difference",
                                       "ci_low": "bootstrap_ci_low",
                                       "ci_high": "bootstrap_ci_high"}[name]]) > 1e-12:
                problems.append(f"{row['contrast']}: {name} differs from "
                                f"formal_analysis.json")
    for method in METHODS:
        stored = data["analysis"]["raw_scores"][method]
        if [float(v) for v in stored] != [float(v) for v in scores[method]]:
            problems.append(f"{method}: raw SNY differs from formal_analysis.json")
        stored_stats = data["analysis"]["per_method"][method]
        if abs(stored_stats["mean"] - frozen["per_method"][method]["mean"]) > 1e-12:
            problems.append(f"{method}: mean differs from formal_analysis.json")

    return {"scores": scores, "frozen": frozen, "contrasts": contrast_rows,
            "problems": problems}


# ------------------------------------------------------------------- phase 3
def save_figure(fig, name: str) -> list:
    written = []
    for extension in ("png", "pdf"):
        target = REPORT_DIR / f"{name}.{extension}"
        fig.savefig(target, bbox_inches="tight", dpi=200)
        written.append(target)
    plt.close(fig)
    return written


def style():
    plt.rcParams.update({
        "font.size": 11,
        "axes.titlesize": 12,
        "axes.labelsize": 11,
        "figure.dpi": 200,
        "savefig.dpi": 200,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def figure_paired_by_seed(scores: dict) -> list:
    fig, ax = plt.subplots(figsize=(9.0, 4.4))
    indices = list(range(1, len(scores[METHODS[0]]) + 1))
    for method in METHODS:
        values = scores[method]
        ax.plot(indices, values, color=COLORS[method], marker=MARKERS[method],
                markersize=5, linewidth=1.1, alpha=0.9, label=LABELS[method])
    ax.set_xticks(indices)
    ax.set_xlabel("Paired master seed (frozen order, index 1-20)")
    ax.set_ylabel("SNY (non-overlapping passed\ncandidate neighbourhoods)")
    ax.set_title("Paired Stable Neighborhood Yield by seed "
                 f"(n = {len(indices)} frozen seeds, 1000 episodes/method)")
    ax.legend(frameon=False, ncols=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.16))
    ax.set_ylim(0, max(max(scores[m]) for m in METHODS) + 1.5)
    return save_figure(fig, "figure_1_paired_sny_by_seed")


def figure_distribution(scores: dict) -> list:
    fig, ax = plt.subplots(figsize=(7.0, 4.4))
    positions = list(range(1, len(METHODS) + 1))
    values = [scores[m] for m in METHODS]
    box = ax.boxplot(values, positions=positions, widths=0.42, patch_artist=True,
                     medianprops={"color": "black", "linewidth": 1.4},
                     whiskerprops={"color": "#555555"},
                     capprops={"color": "#555555"},
                     flierprops={"marker": ""})
    for patch, method in zip(box["boxes"], METHODS):
        patch.set_facecolor(COLORS[method])
        patch.set_alpha(0.25)
        patch.set_edgecolor(COLORS[method])
    rng = random.Random(20261003)
    for position, method in zip(positions, METHODS):
        jitter = [position + rng.uniform(-0.13, 0.13) for _ in scores[method]]
        ax.scatter(jitter, scores[method], s=34, color=COLORS[method],
                   edgecolor="white", linewidth=0.6, zorder=3,
                   label="raw per-seed SNY" if position == 1 else None)
        ax.scatter([position], [mean(scores[method])], marker="D", s=46,
                   color="black", zorder=4,
                   label="mean" if position == 1 else None)
    ax.set_xticks(positions)
    ax.set_xticklabels([LABELS[m] for m in METHODS])
    ax.set_ylabel("SNY (non-overlapping passed\ncandidate neighbourhoods)")
    ax.set_title("SNY distribution by method (all 20 raw observations per method)")
    ax.legend(frameon=False, loc="upper left")
    return save_figure(fig, "figure_2_sny_distribution_by_method")


def figure_paired_differences(contrasts: list) -> list:
    fig, axes = plt.subplots(len(contrasts), 1, figsize=(8.4, 9.2))
    for ax, row in zip(axes, contrasts):
        primary = row["role"] == "primary"
        indices = list(range(1, len(row["differences"]) + 1))
        ax.axhspan(row["bootstrap_ci_low"], row["bootstrap_ci_high"],
                   color=COLORS[row["method_a"]], alpha=0.18, zorder=1,
                   label="frozen 95% paired bootstrap CI")
        ax.axhline(0.0, color="#444444", linewidth=1.0, linestyle="--", zorder=2,
                   label="no difference")
        ax.axhline(row["mean_difference"], color=COLORS[row["method_a"]],
                   linewidth=2.0, zorder=4, label="mean difference")
        ax.scatter(indices, row["differences"],
                   color="#333333" if primary else "#777777",
                   marker="o", s=34, zorder=5, label="raw per-seed difference")
        title = (f"{SHORT[row['method_a']]} - {SHORT[row['method_b']]}"
                 f"  [{'PRIMARY' if primary else 'secondary'} contrast]")
        ax.set_title(title, fontweight="bold" if primary else "normal")
        ax.set_ylabel("SNY difference")
        ax.set_xticks(indices)
        ax.set_xlabel("Paired master seed (frozen order, index 1-20)")
        ax.text(0.985, 0.05,
                f"mean {row['mean_difference']:+.2f}   95% CI "
                f"[{row['bootstrap_ci_low']:+.2f}, {row['bootstrap_ci_high']:+.2f}]"
                f"   W/T/L {row['wins']}/{row['ties']}/{row['losses']}",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=9,
                bbox={"facecolor": "white", "edgecolor": "#cccccc", "alpha": 0.9})
        ax.legend(frameon=False, fontsize=8, loc="upper left", ncols=2)
    fig.suptitle("Paired SNY differences (percentile paired bootstrap, "
                 f"{BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED})",
                 y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.98))
    return save_figure(fig, "figure_3_paired_differences")


def figure_funnel(descriptive: dict) -> list:
    stages = ["Mean unique search\ncollision candidates", "Audited\ncandidates",
              "Mean passed\ncandidates", "Mean SNY"]
    fig, ax = plt.subplots(figsize=(9.0, 4.4))
    width = 0.26
    for offset, method in enumerate(METHODS):
        values = [
            descriptive["explore_breadth"][method]["mean"],
            descriptive["nomination_quality"][method]["audited_per_seed_mean"],
            descriptive["nomination_quality"][method]["passed_per_seed_mean"],
            descriptive["nomination_quality"][method]["sny_mean"],
        ]
        positions = [i + (offset - 1) * width for i in range(len(stages))]
        bars = ax.bar(positions, values, width=width, color=COLORS[method],
                      alpha=0.85, label=LABELS[method])
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 1.4,
                    f"{value:.2f}" if value % 1 else f"{value:.0f}",
                    ha="center", va="bottom", fontsize=8.5)
    ax.set_xticks(range(len(stages)))
    ax.set_xticklabels(stages)
    ax.set_ylabel("Mean count per seed")
    ax.set_title("Search-to-audit funnel (descriptive, mean per seed over 20 seeds)")
    ax.legend(frameon=False, ncols=3)
    ax.set_ylim(0, descriptive["explore_breadth"]["random_search"]["mean"] * 1.22)
    return save_figure(fig, "figure_4_search_to_audit_funnel")


def figure_budget(descriptive: dict) -> list:
    budget = descriptive["confirmation_expenditure"]
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    positions = list(range(len(METHODS)))
    search = [budget[m]["search_mean"] for m in METHODS]
    internal = [budget[m]["internal_confirm_mean"] for m in METHODS]
    audit = [budget[m]["audit_mean"] for m in METHODS]
    ax.bar(positions, search, 0.5, color="#8C8C8C", label="Search")
    ax.bar(positions, internal, 0.5, bottom=search, color="#C44E52",
           label="Internal confirmation")
    ax.bar(positions, audit, 0.5, bottom=[s + i for s, i in zip(search, internal)],
           color="#8172B2", label="Final Audit")
    for position, (s, i, a) in enumerate(zip(search, internal, audit)):
        if i:
            ax.text(position, s + i / 2, f"{i:.2f}", ha="center", va="center",
                    color="white", fontsize=9)
        ax.text(position, s / 2, f"{s:.2f}", ha="center", va="center",
                color="white", fontsize=9)
        ax.text(position, s + i + a / 2, f"{a:.0f}", ha="center", va="center",
                color="white", fontsize=9)
    ax.set_xticks(positions)
    ax.set_xticklabels([LABELS[m] for m in METHODS])
    ax.set_ylabel("Mean episodes per seed\n(allocated 1000 slots per method)")
    ax.set_title("Budget allocation per seed by phase (search pool 700 + audit 300)")
    ax.legend(frameon=False, ncols=3, loc="lower center", bbox_to_anchor=(0.5, -0.26))
    ax.set_ylim(0, 1050)
    return save_figure(fig, "figure_5_budget_allocation")


def figure_audit_pass(descriptive: dict) -> list:
    quality = descriptive["nomination_quality"]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    positions = list(range(len(METHODS)))
    passed = [quality[m]["passed_total"] for m in METHODS]
    failed = [quality[m]["audited_total"] - quality[m]["passed_total"] for m in METHODS]
    ax.bar(positions, passed, 0.5, color=[COLORS[m] for m in METHODS],
           label="Passed Final Audit")
    ax.bar(positions, failed, 0.5, bottom=passed, color="#D9D9D9",
           edgecolor="#9A9A9A", label="Did not pass")
    for position, method in zip(positions, METHODS):
        rate = quality[method]["pass_rate"]
        ax.text(position, quality[method]["audited_total"] + 4,
                f"{passed[position]}/{quality[method]['audited_total']}\n{rate:.1%}",
                ha="center", va="bottom", fontsize=9.5)
    ax.set_xticks(positions)
    ax.set_xticklabels([LABELS[m] for m in METHODS])
    ax.set_ylabel("Audited candidates (20 seeds x 10)")
    ax.set_title("Final Audit pass proportion (descriptive; no hypothesis test)")
    ax.legend(frameon=False, loc="lower right")
    ax.set_ylim(0, max(quality[m]["audited_total"] for m in METHODS) * 1.22)
    return save_figure(fig, "figure_6_audit_pass_rate")


def render_figures(data: dict, reproduction: dict, descriptive: dict) -> list:
    style()
    written = []
    written += figure_paired_by_seed(reproduction["scores"])
    written += figure_distribution(reproduction["scores"])
    written += figure_paired_differences(reproduction["contrasts"])
    written += figure_funnel(descriptive)
    written += figure_budget(descriptive)
    written += figure_audit_pass(descriptive)
    return written


# ------------------------------------------------------------------- phase 4
def descriptive_analysis(data: dict, reproduction: dict) -> dict:
    records = data["per_seed"]
    n = len(records)

    breadth = {}
    for method in METHODS:
        values = [r["methods"][method]["candidate_count"] for r in records]
        breadth[method] = {
            "per_seed": values, "mean": mean(values), "min": min(values),
            "max": max(values), "total": sum(values),
        }

    quality = {}
    for method in METHODS:
        passed = [r["methods"][method]["scored"]["passed_candidate_count"]
                  for r in records]
        audited = [r["methods"][method]["scored"]["audited_candidate_count"]
                   for r in records]
        incomplete = [r["methods"][method]["scored"]["incomplete_candidate_count"]
                      for r in records]
        invalid = [r["methods"][method]["scored"]["invalid_candidate_count"]
                   for r in records]
        sny = reproduction["scores"][method]
        quality[method] = {
            "per_seed_passed": passed, "per_seed_audited": audited,
            "passed_total": sum(passed), "audited_total": sum(audited),
            "passed_per_seed_mean": mean(passed),
            "audited_per_seed_mean": mean(audited),
            "pass_rate": sum(passed) / sum(audited),
            "sny_mean": mean(sny),
            "incomplete_total": sum(incomplete), "invalid_total": sum(invalid),
        }

    expenditure = {}
    for method in METHODS:
        internal = [r["methods"][method]["accounting"]["phase_episodes"]
                    ["internal_confirm"] for r in records]
        search = [r["methods"][method]["accounting"]["phase_episodes"]["search"]
                  for r in records]
        audit = [r["methods"][method]["accounting"]["phase_episodes"]["final_audit"]
                 for r in records]
        spent = [r["methods"][method]["accounting"]["episodes_spent"] for r in records]
        wall = [r["methods"][method]["wall_time_s"] for r in records]
        expenditure[method] = {
            "per_seed_internal_confirm": internal,
            "internal_confirm_total": sum(internal),
            "internal_confirm_mean": mean(internal),
            "internal_confirm_min": min(internal), "internal_confirm_max": max(internal),
            "search_mean": mean(search), "audit_mean": mean(audit),
            "episodes_spent_total": sum(spent), "episodes_spent_mean": mean(spent),
            "wall_time_total_s": sum(wall), "wall_time_mean_s": mean(wall),
        }

    cap = expenditure["adaptive_explore_confirm"]
    cap_behaviour = {
        "global_cap": 175,
        "all_seeds_at_cap": all(v == 175 for v in cap["per_seed_internal_confirm"]),
        "per_seed": cap["per_seed_internal_confirm"],
        "distinct_values": sorted(set(cap["per_seed_internal_confirm"])),
        "total": cap["internal_confirm_total"],
        "note": ("Frozen global cap = floor(0.25 * 700) = 175 internal confirmation "
                 "episodes per seed."),
    }

    dedup = {}
    for method in METHODS:
        gaps = [r["methods"][method]["scored"]["passed_candidate_count"]
                - r["methods"][method]["scored"]["nonoverlapping_passed_count"]
                for r in records]
        dedup[method] = {
            "per_seed_passed_minus_sny": gaps,
            "all_zero": all(value == 0 for value in gaps),
            "total_removed": sum(gaps),
        }

    tradeoff = {}
    for method in METHODS:
        candidates = [r["methods"][method]["candidate_count"] for r in records]
        sny = reproduction["scores"][method]
        tradeoff[method] = {
            "mean_candidates": mean(candidates),
            "pass_rate": quality[method]["pass_rate"],
            "mean_sny": mean(sny),
            "pearson_candidates_vs_sny": pearson(candidates, sny),
        }

    wlt = {row["contrast"]: {"wins": row["wins"], "ties": row["ties"],
                             "losses": row["losses"]}
           for row in reproduction["contrasts"]}

    return {
        "status": "EXPLORATORY / DESCRIPTIVE",
        "not_confirmatory": (
            "Everything in this section is descriptive or exploratory. It is NOT "
            "pre-registered confirmatory evidence; no hypothesis test was added."
        ),
        "n_seeds": n,
        "explore_breadth": breadth,
        "nomination_quality": quality,
        "confirmation_expenditure": expenditure,
        "adaptive_cap_behaviour": cap_behaviour,
        "spatial_deduplication": dedup,
        "breadth_stability_tradeoff": tradeoff,
        "win_tie_loss": wlt,
        "audit_candidates_per_method": AUDIT_CANDIDATES_PER_METHOD,
    }


# ------------------------------------------------------------------- phase 5
def write_tables(data: dict, reproduction: dict, descriptive: dict) -> list:
    written = []
    stats = reproduction["frozen"]["per_method"]
    rows = [[method, stats[method]["n"], f"{stats[method]['mean']:.4f}",
             f"{stats[method]['median']:.4f}",
             f"{stats[method]['standard_deviation']:.6f}",
             f"{stats[method]['q1']:.4f}", f"{stats[method]['q3']:.4f}",
             f"{stats[method]['min']:.4f}", f"{stats[method]['max']:.4f}"]
            for method in METHODS]
    path = REPORT_DIR / "table_primary_results.csv"
    write_csv(path, ["method", "n_seeds", "mean_sny", "median_sny", "sd_sny",
                     "q1", "q3", "min", "max"], rows)
    written.append(path)

    rows = [[row["contrast"], row["role"], row["n_pairs"],
             f"{row['mean_difference']:.4f}", f"{row['bootstrap_ci_low']:.4f}",
             f"{row['bootstrap_ci_high']:.4f}"] for row in reproduction["contrasts"]]
    path = REPORT_DIR / "table_paired_contrasts.csv"
    write_csv(path, ["contrast", "role", "n_pairs", "mean_difference",
                     "bootstrap_ci_low", "bootstrap_ci_high"], rows)
    written.append(path)

    rows = []
    for method in METHODS:
        rows.append([
            method,
            f"{descriptive['explore_breadth'][method]['mean']:.4f}",
            f"{descriptive['confirmation_expenditure'][method]['internal_confirm_mean']:.4f}",
            descriptive["nomination_quality"][method]["audited_total"],
            descriptive["nomination_quality"][method]["passed_total"],
            f"{descriptive['nomination_quality'][method]['pass_rate']:.4f}",
            f"{descriptive['nomination_quality'][method]['sny_mean']:.4f}",
            sum(r["methods"][method]["accounting"]["errors"] for r in data["per_seed"]),
            f"{descriptive['confirmation_expenditure'][method]['wall_time_mean_s']:.3f}",
        ])
    path = REPORT_DIR / "table_descriptive_metrics.csv"
    write_csv(path, ["method", "mean_unique_search_candidates", "mean_internal_confirm",
                     "audited_candidates_total", "passed_candidates_total",
                     "audit_pass_rate", "mean_sny", "total_errors",
                     "mean_wall_time_s"], rows)
    written.append(path)

    rows = []
    for index, record in enumerate(data["per_seed"], start=1):
        r = reproduction["scores"]["random_search"][index - 1]
        f = reproduction["scores"]["fixed_explore_confirm"][index - 1]
        a = reproduction["scores"]["adaptive_explore_confirm"][index - 1]
        rows.append([index, record["seed"], f"{r:.0f}", f"{f:.0f}", f"{a:.0f}",
                     f"{a - r:+.0f}", f"{a - f:+.0f}", f"{f - r:+.0f}"])
    path = REPORT_DIR / "raw_sny_by_seed.csv"
    write_csv(path, ["seed_index", "master_seed", "random_sny", "fixed_sny",
                     "adaptive_sny", "adaptive_minus_random", "adaptive_minus_fixed",
                     "fixed_minus_random"], rows)
    written.append(path)
    return written


# ------------------------------------------------------------------- phase 7
def sanity_checks(data: dict, reproduction: dict, descriptive: dict,
                  root_hashes_before: dict, formal_root: Path) -> dict:
    problems = []
    warnings = []
    seeds = data["seeds"]

    if len(seeds) != 20:
        problems.append(f"expected 20 seeds, found {len(seeds)}")
    if len(set(seeds)) != len(seeds):
        problems.append("duplicate seeds in the frozen order")
    if 20261003 in seeds:
        problems.append("calibration seed 20261003 present in the formal seeds")

    pairs = set()
    for record in data["per_seed"]:
        for method in METHODS:
            pairs.add((record["seed"], method))
        if set(record["methods"]) != set(METHODS):
            problems.append(f"seed {record['seed']}: missing methods")
        if record["purpose"] != "evaluation":
            problems.append(f"seed {record['seed']}: purpose {record['purpose']!r}")
        if record["git_dirty"] is not False:
            problems.append(f"seed {record['seed']}: dirty source")
        if record["scoring_protocol_id"] != data["manifest"]["scoring_protocol_id"]:
            problems.append(f"seed {record['seed']}: scoring protocol mismatch")
    if len(pairs) != 60:
        problems.append(f"expected 60 unique (seed, method) pairs, found {len(pairs)}")

    for method in METHODS:
        values = reproduction["scores"][method]
        if len(values) != 20:
            problems.append(f"{method}: {len(values)} SNY values, expected 20")
        if any(value is None for value in values):
            problems.append(f"{method}: null SNY value")
        audited = descriptive["nomination_quality"][method]["audited_total"]
        if audited != AUDIT_CANDIDATES_PER_METHOD:
            problems.append(f"{method}: {audited} audited candidates, expected "
                            f"{AUDIT_CANDIDATES_PER_METHOD}")

    if reproduction["problems"]:
        problems.append("confirmatory reproduction failed: see reproduction problems")

    if "cornercaselab.simulator" in sys.modules:
        problems.append("the simulator module was imported by this script")
    if "highway_env" in sys.modules:
        problems.append("highway_env was imported by this script")

    after = {str(p.relative_to(formal_root)).replace("\\", "/"): sha256_file(p)
             for p in sorted(formal_root.rglob("*")) if p.is_file()}
    if after != root_hashes_before:
        changed = sorted(set(after.items()) ^ set(root_hashes_before.items()))
        problems.append(f"the formal run root changed during this run: {changed[:5]}")

    narrative = REPORT_DIR / "RESULTS_INTERPRETATION.md"
    if not narrative.is_file():
        problems.append("RESULTS_INTERPRETATION.md is missing")
    else:
        text = narrative.read_text(encoding="utf-8")
        for label, expected in NARRATIVE_NUMBERS:
            if expected not in text:
                problems.append(f"narrative ({label}) does not contain {expected!r}")
        for required in ("CONFIRMATORY", "DESCRIPTIVE", "EXPLORATORY", "Limitations",
                         "NOT supported"):
            if required not in text:
                problems.append(f"narrative is missing the section marker {required!r}")

    for record in data["per_seed"]:
        seed_dir = (formal_root / f"batch_{record['batch']:02d}" /
                    f"seed_{record['seed']}")
        if "development" in str(seed_dir).lower():
            problems.append("development data referenced")

    return {"problems": problems, "warnings": warnings,
            "formal_root": str(formal_root),
            "seeds": len(seeds), "pairs": len(pairs),
            "simulator_module_imported": "cornercaselab.simulator" in sys.modules,
            "formal_root_unchanged": after == root_hashes_before}


# ---------------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check-only", action="store_true",
                        help="recompute and check without rewriting figures/tables")
    args = parser.parse_args(argv)

    if not FORMAL_ROOT.is_dir():
        print(f"STOP: formal run root not found: {FORMAL_ROOT}")
        return 2
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    root_hashes_before = {str(p.relative_to(FORMAL_ROOT)).replace("\\", "/"):
                          sha256_file(p)
                          for p in sorted(FORMAL_ROOT.rglob("*")) if p.is_file()}
    data = collect(FORMAL_ROOT)

    print("=== phase 2: reproduce the frozen confirmatory analysis ===")
    reproduction = reproduce(data)
    if reproduction["problems"]:
        print("STOP: the frozen confirmatory numbers did NOT reproduce.")
        for problem in reproduction["problems"]:
            print("  -", problem)
        return 3
    for method in METHODS:
        stats = reproduction["frozen"]["per_method"][method]
        print(f"  {method:<26} mean={stats['mean']:.2f} median={stats['median']:.2f} "
              f"sd={stats['standard_deviation']:.4f} iqr={stats['iqr']:.2f}")
    for row in reproduction["contrasts"]:
        print(f"  {row['contrast']:<56} {row['mean_difference']:+.2f} "
              f"[{row['bootstrap_ci_low']:.2f}, {row['bootstrap_ci_high']:.2f}] "
              f"W/T/L {row['wins']}/{row['ties']}/{row['losses']}")
    print("  all frozen numbers reproduced (frozen implementation + independent "
          "re-implementation)")

    descriptive = descriptive_analysis(data, reproduction)

    if not args.check_only:
        print("=== phase 1: freeze the formal data ===")
        freeze(FORMAL_ROOT, data, root_hashes_before)
        print(f"  FORMAL_DATA_FREEZE.json + formal_data_file_sha256.csv "
              f"({len(data['seeds'])} seeds)")

        print("=== phase 3: render figures ===")
        for path in render_figures(data, reproduction, descriptive):
            print(f"  wrote {path.relative_to(ROOT)}")

        print("=== phase 4/5: descriptive analysis and tables ===")
        (REPORT_DIR / "descriptive_analysis.json").write_text(
            json.dumps(descriptive, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"  wrote {(REPORT_DIR / 'descriptive_analysis.json').relative_to(ROOT)}")
        for path in write_tables(data, reproduction, descriptive):
            print(f"  wrote {path.relative_to(ROOT)}")

    print("=== phase 7: sanity checks ===")
    outcome = sanity_checks(data, reproduction, descriptive, root_hashes_before,
                            FORMAL_ROOT)
    print(f"  seeds={outcome['seeds']} pairs={outcome['pairs']} "
          f"simulator_imported={outcome['simulator_module_imported']} "
          f"formal_root_unchanged={outcome['formal_root_unchanged']}")
    if outcome["problems"]:
        print("SANITY FAILURES:")
        for problem in outcome["problems"]:
            print("  -", problem)
        return 4
    print("  all sanity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
