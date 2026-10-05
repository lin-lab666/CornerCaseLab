"""Three deliberately simple observation-only pilot controllers, none trained.

Input rows: [presence, absolute_x, absolute_y, absolute_vx, absolute_vy].

The ``boundary`` controller is deliberately less conservative than ``reactive``.
It is used only to create a non-degenerate research benchmark. Its parameters
must be frozen before formal evaluation.
"""
from __future__ import annotations
from typing import Sequence

IDLE, FASTER, SLOWER = 1, 3, 4

POLICIES = ("reactive", "boundary", "cruise")

# Conservative reference controller.
REACTIVE_GAP_BASE_M = 8.0
REACTIVE_HEADWAY_S = 1.10
REACTIVE_TTC_S = 2.00

# Calibration-only intermediate controller.
# These values are deliberately fixed before running the first calibration.
BOUNDARY_GAP_BASE_M = 6.0
BOUNDARY_HEADWAY_S = 0.30
BOUNDARY_TTC_S = 0.90


def action_for(
    observation: Sequence[Sequence[float]],
    policy: str = "reactive",
) -> int:
    if policy not in POLICIES:
        raise ValueError(f"Unknown policy {policy!r}; choose {POLICIES}.")
    if len(observation) < 1 or len(observation[0]) != 5:
        raise ValueError("Expected an N x 5 absolute, unnormalised observation.")

    _, x, y, vx, _ = [float(v) for v in observation[0]]

    if policy != "cruise":
        if policy == "reactive":
            gap_base = REACTIVE_GAP_BASE_M
            headway = REACTIVE_HEADWAY_S
            ttc_limit = REACTIVE_TTC_S
        else:
            gap_base = BOUNDARY_GAP_BASE_M
            headway = BOUNDARY_HEADWAY_S
            ttc_limit = BOUNDARY_TTC_S

        for row in observation[1:]:
            present, ox, oy, ovx, _ = [float(v) for v in row]

            if present < 0.5 or ox <= x or abs(oy - y) > 2.2:
                continue

            gap = ox - x - 5.0
            closing = vx - ovx

            danger_by_gap = gap < gap_base + headway * max(vx, 0.0)
            danger_by_ttc = closing > 0.0 and gap / closing < ttc_limit

            if danger_by_gap or danger_by_ttc:
                return SLOWER

    # All pilot controllers stay in their lane and regulate toward ~30 m/s.
    if vx < 29.0:
        return FASTER
    if vx > 31.0:
        return SLOWER
    return IDLE
