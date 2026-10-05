"""Explicit scenario parameters and deterministic sampling; standard library only."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import random
from typing import Any

SCHEMA = "merge-scenario-v1"
# Deliberate pilot bounds, NOT a real-world traffic distribution.
BOUNDS: dict[str, tuple[float, float]] = {
    "ego_speed": (20.0, 32.0),
    "front_gap": (18.0, 80.0),  # initial bumper-to-bumper distance in metres
    "front_speed": (15.0, 32.0),
    "ramp_x": (80.0, 140.0),   # distance along the initial straight ramp
    "ramp_speed": (12.0, 30.0),
    "ramp_target_speed": (20.0, 34.0),
}
# Frozen v0.2 local-neighbourhood half-widths (radius), one per scenario
# dimension and in that dimension's own unit: front_gap = 2 m, ramp_x = 2 m,
# and 1 for each of the other four dimensions (m/s).
# DO NOT change these: they define the frozen local-perturbation protocol.
PERTURBATION = {k: (2.0 if k in {"front_gap", "ramp_x"} else 1.0) for k in BOUNDS}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def check_seed(seed: int) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError("Seed must be an integer in [0, 2**32).")
    return seed


def derive_seed(master: int, *labels: object) -> int:
    """Separate search, simulation and confirmation streams using explicit labels."""
    check_seed(master)
    payload = canonical_json([master, *labels]).encode()
    return int.from_bytes(sha256(payload).digest()[:4], "big")


@dataclass(frozen=True)
class Scenario:
    ego_speed: float = 27.0
    front_gap: float = 45.0
    front_speed: float = 25.0
    ramp_x: float = 110.0
    ramp_speed: float = 20.0
    ramp_target_speed: float = 30.0

    def __post_init__(self) -> None:
        for name, (low, high) in BOUNDS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric, not {type(value).__name__}.")
            value = float(value)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name}={value} is outside [{low}, {high}].")
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @property
    def uid(self) -> str:
        # This identifies a configuration, NOT a distinct failure/root cause.
        payload = canonical_json({"schema": SCHEMA, "parameters": self.to_dict()})
        return sha256(payload.encode()).hexdigest()[:16]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Scenario:
        if set(value) != set(BOUNDS):
            raise ValueError(f"Scenario must contain exactly these keys: {list(BOUNDS)}")
        return cls(**value)


def sample_scenario(seed: int) -> Scenario:
    rng = random.Random(check_seed(seed))
    return Scenario(**{k: round(rng.uniform(a, b), 6) for k, (a, b) in BOUNDS.items()})


def perturb_scenario(base: Scenario, seed: int) -> Scenario:
    """Sample the frozen local-perturbation protocol around ``base``.

    Exactly what is implemented, stated so it cannot be misread:

    1. The six scenario dimensions are sampled **independently**, one
       ``random.Random(seed).uniform`` draw each, in ``BOUNDS`` insertion order.
    2. Each dimension is

           Uniform(max(domain_low, base_value - radius),
                   min(domain_high, base_value + radius))

       with radius taken from :data:`PERTURBATION`:
       ``front_gap = 2 m``, ``ramp_x = 2 m``, and ``1`` for each of
       ``ego_speed``, ``front_speed``, ``ramp_speed``, ``ramp_target_speed``
       (m/s). The distribution is continuous uniform; values are rounded to
       6 decimals and must satisfy the scenario bounds.
    3. The local neighbourhood is therefore a **truncated / intersected local
       hyperrectangle**, not a sample-then-clip and not rejection sampling:
       the sampling interval itself is intersected with the scenario domain,
       so no point mass is placed on a boundary and no draw is ever rejected or
       out of bounds. Near a domain bound the neighbourhood becomes one-sided
       and its effective half-width is smaller than the nominal radius.
    4. This defines a synthetic local distribution for stability measurement,
       NOT real-world road risk.

    Seeding: callers pass a derived local seed. Internal confirmation and the
    Final Audit use *different* local streams (``internal-local`` and
    ``audit-local``) and *different* nuisance streams (``internal-nuisance`` and
    ``audit-nuisance``); see ``cornercaselab.experiments``. The same master seed
    therefore reproduces the same perturbations.
    """
    rng = random.Random(check_seed(seed))
    values = {}
    for k, (low, high) in BOUNDS.items():
        x, radius = getattr(base, k), PERTURBATION[k]
        values[k] = round(rng.uniform(max(low, x-radius), min(high, x+radius)), 6)
    return Scenario(**values)
