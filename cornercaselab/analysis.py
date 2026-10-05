"""v0.3 formal reporting analysis plan.

Implements the frozen reporting plan of ``cornercaselab_v03_eval_v1``: per-method
summary statistics and paired percentile-bootstrap intervals for the SNY (Stable
Neighborhood Yield) score vector across the 20 paired formal seeds.

This module computes nothing by itself. It takes score vectors as input, so it is
validated on synthetic data during the protocol freeze and produces **no formal
statistic** until it is deliberately called on real formal results.

Standard library only, fully deterministic: every bootstrap resample is drawn
from a ``random.Random`` stream seeded with the frozen bootstrap seed, so the same
input always yields the same interval. The draw order is row-major -- for each
resample, ``n`` independent ``randrange(n)`` draws -- and is part of the frozen
plan.
"""
from __future__ import annotations

import math
import random
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_RESAMPLES = 10000
DEFAULT_BOOTSTRAP_SEED = 314159265
DEFAULT_CONFIDENCE = 0.95

PRIMARY_CONTRAST = ("adaptive_explore_confirm", "random_search")
SECONDARY_CONTRASTS = (
    ("adaptive_explore_confirm", "fixed_explore_confirm"),
    ("fixed_explore_confirm", "random_search"),
)


def percentile(sorted_values: Sequence[float], p: float) -> float:
    """Percentile by linear interpolation between order statistics.

    ``sorted_values`` must be ascending. ``p`` is in [0, 1]. This is the
    numpy-style definition, chosen so the reported interval does not depend on a
    truncation convention; it is part of the frozen plan.
    """
    if not sorted_values:
        raise ValueError("cannot take a percentile of an empty sequence")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in [0, 1]")
    count = len(sorted_values)
    if count == 1:
        return float(sorted_values[0])
    position = (count - 1) * p
    lower = int(math.floor(position))
    upper = min(lower + 1, count - 1)
    fraction = position - lower
    return float(sorted_values[lower]) * (1.0 - fraction) + float(sorted_values[upper]) * fraction


def _sample_standard_deviation(values: Sequence[float]) -> float | None:
    """Sample standard deviation (ddof = 1); None when fewer than two values."""
    count = len(values)
    if count < 2:
        return None
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    return math.sqrt(variance)


def summarise_scores(values: Iterable[float]) -> dict[str, Any]:
    """Mean, median, sample standard deviation and IQR of one score vector."""
    data = [float(value) for value in values]
    if not data:
        raise ValueError("cannot summarise an empty score vector")
    ordered = sorted(data)
    q1 = percentile(ordered, 0.25)
    q3 = percentile(ordered, 0.75)
    return {
        "n": len(data),
        "mean": sum(data) / len(data),
        "median": percentile(ordered, 0.5),
        "standard_deviation": _sample_standard_deviation(data),
        "standard_deviation_ddof": 1,
        "q1": q1,
        "q3": q3,
        "iqr": q3 - q1,
        "min": ordered[0],
        "max": ordered[-1],
    }


def paired_bootstrap_ci(differences: Iterable[float], *,
                        resamples: int = DEFAULT_RESAMPLES,
                        seed: int = DEFAULT_BOOTSTRAP_SEED,
                        confidence: float = DEFAULT_CONFIDENCE) -> dict[str, Any]:
    """Percentile paired bootstrap interval for the mean paired difference.

    ``differences`` are the per-seed paired differences ``A_i - B_i``. The point
    estimate is their mean; the interval is the percentile interval of the
    resampled means. p-values are deliberately not computed: the frozen plan
    reports effect size with uncertainty, not significance.
    """
    diffs = [float(value) for value in differences]
    if not diffs:
        raise ValueError("cannot bootstrap an empty difference vector")
    if resamples < 1:
        raise ValueError("resamples must be >= 1")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    count = len(diffs)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            total += diffs[rng.randrange(count)]
        means.append(total / count)
    means.sort()
    alpha = 1.0 - confidence
    return {
        "method": "percentile paired bootstrap",
        "paired": True,
        "n_pairs": count,
        "mean_difference": sum(diffs) / count,
        "ci_low": percentile(means, alpha / 2.0),
        "ci_high": percentile(means, 1.0 - alpha / 2.0),
        "confidence": confidence,
        "resamples": resamples,
        "bootstrap_seed": seed,
        "difference_draw_order": "row-major: per resample, n independent randrange(n) draws",
    }


def paired_contrasts(scores_by_method: Mapping[str, Sequence[float]], *,
                     contrasts: Sequence[tuple[str, str]] | None = None,
                     resamples: int = DEFAULT_RESAMPLES,
                     seed: int = DEFAULT_BOOTSTRAP_SEED,
                     confidence: float = DEFAULT_CONFIDENCE) -> list[dict[str, Any]]:
    """Paired contrasts between methods over the same ordered seeds."""
    chosen = (list(contrasts) if contrasts is not None
              else [PRIMARY_CONTRAST, *SECONDARY_CONTRASTS])
    lengths = {method: len(list(values)) for method, values in scores_by_method.items()}
    if len(set(lengths.values())) > 1:
        raise ValueError(f"all methods must have the same number of seeds: {lengths}")
    reports = []
    for first, second in chosen:
        if first not in scores_by_method or second not in scores_by_method:
            raise KeyError(f"contrast {first!r} - {second!r} needs both score vectors")
        left = [float(value) for value in scores_by_method[first]]
        right = [float(value) for value in scores_by_method[second]]
        if len(left) != len(right):
            raise ValueError(f"paired contrast {first} - {second} has mismatched lengths")
        differences = [a - b for a, b in zip(left, right)]
        interval = paired_bootstrap_ci(differences, resamples=resamples, seed=seed,
                                      confidence=confidence)
        reports.append({
            "contrast": f"{first} - {second}",
            "method_a": first,
            "method_b": second,
            "role": "primary" if (first, second) == PRIMARY_CONTRAST else "secondary",
            **interval,
        })
    return reports


def summarise_report(scores_by_method: Mapping[str, Sequence[float]], *,
                     contrasts: Sequence[tuple[str, str]] | None = None,
                     resamples: int = DEFAULT_RESAMPLES,
                     seed: int = DEFAULT_BOOTSTRAP_SEED,
                     confidence: float = DEFAULT_CONFIDENCE) -> dict[str, Any]:
    """The complete frozen reporting plan over one set of method score vectors.

    Kept as a pure function so it can be validated now and called unchanged once
    formal results exist.
    """
    return {
        "raw_scores": {method: [float(value) for value in values]
                       for method, values in scores_by_method.items()},
        "per_method": {method: summarise_scores(values)
                       for method, values in scores_by_method.items()},
        "paired_contrasts": paired_contrasts(scores_by_method, contrasts=contrasts,
                                             resamples=resamples, seed=seed,
                                             confidence=confidence),
        "primary_success_criterion": ("effect size with uncertainty; p-values are not the "
                                      "primary criterion"),
    }
