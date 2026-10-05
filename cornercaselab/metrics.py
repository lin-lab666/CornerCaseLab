"""Limited diagnostic metrics; not safety certification."""
from __future__ import annotations
import math
from typing import Sequence


def longitudinal_ttc(observation: Sequence[Sequence[float]]) -> float | None:
    """Smallest forward, longitudinal TTC among laterally overlapping cars.

    Absolute kinematics, constant x-velocity assumption, 5 m vehicle length.
    It is NOT a curved-path/side-impact TTC or a collision detector. The caller
    samples at decision frequency, so this is not the continuous-time minimum.
    None means no eligible closing pair, NOT 'certified safe'.
    """
    _, x, y, vx, _ = [float(v) for v in observation[0]]
    candidates = []
    for row in observation[1:]:
        present, ox, oy, ovx, _ = [float(v) for v in row]
        if present < 0.5 or ox <= x or abs(oy-y) > 2.0:
            continue
        gap, closing = ox-x-5.0, vx-ovx
        if gap <= 0:
            candidates.append(0.0)
        elif closing > 0:
            candidates.append(gap/closing)
    return min(candidates) if candidates else None


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Approximate 95% interval for a FIXED-n, iid Bernoulli sample.

    Do not apply it to adaptively stopped/selected trials as an anytime bound.
    The confirmation command uses fresh independent local draws and fixed n.
    """
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("Require n > 0 and 0 <= successes <= n.")
    p, denom = successes/n, 1+z*z/n
    centre = (p+z*z/(2*n))/denom
    half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    return max(0.0, centre-half), min(1.0, centre+half)
